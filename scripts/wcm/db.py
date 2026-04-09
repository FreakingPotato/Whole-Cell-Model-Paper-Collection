from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .classify import classify_completeness_key, classify_method_class_key
from .models import (
    CLASS_TABLE,
    COMPLETENESS_TABLE,
    DB_PATH,
    DEFAULT_CLASS_LABEL_TO_KEY,
    DEFAULT_COMPLETENESS_LEVELS,
    DEFAULT_METHOD_CLASSES,
    MASTER_TABLE,
    ORGANISM_TABLE,
    CURATED_COMPLETENESS_OVERRIDES,
    ZOTERO_SYNC_STATE,
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_title(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS method_classes (
            key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            definition TEXT NOT NULL,
            color TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS completeness_levels (
            key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            definition TEXT NOT NULL,
            color TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS papers (
            paper_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            year INTEGER,
            journal TEXT,
            doi TEXT,
            landing_page_url TEXT,
            summary TEXT,
            method_summary TEXT,
            contribution TEXT,
            limitation TEXT,
            future_work TEXT,
            openalex_id TEXT,
            organism TEXT,
            organism_group TEXT,
            method_class_key TEXT NOT NULL,
            wcm_completeness_key TEXT NOT NULL DEFAULT 'related',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(method_class_key) REFERENCES method_classes(key),
            FOREIGN KEY(wcm_completeness_key) REFERENCES completeness_levels(key)
        );
        CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
        CREATE INDEX IF NOT EXISTS idx_papers_normalized_title ON papers(normalized_title);

        CREATE TABLE IF NOT EXISTS pdf_assets (
            pdf_id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT,
            relative_path TEXT UNIQUE NOT NULL,
            file_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            status TEXT NOT NULL,
            page_count INTEGER,
            parse_error TEXT NOT NULL DEFAULT '',
            parser_version TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_parsed_at TEXT,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
        );
        CREATE INDEX IF NOT EXISTS idx_pdf_assets_paper_id ON pdf_assets(paper_id);
        CREATE INDEX IF NOT EXISTS idx_pdf_assets_file_hash ON pdf_assets(file_hash);

        CREATE TABLE IF NOT EXISTS pdf_parse_cache (
            file_hash TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            parsed INTEGER NOT NULL,
            profile_json TEXT,
            error TEXT NOT NULL DEFAULT '',
            page_count INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(file_hash, parser_version)
        );

        CREATE TABLE IF NOT EXISTS classification_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT NOT NULL,
            proposed_key TEXT NOT NULL,
            final_key TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL NOT NULL,
            rationale TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
        );
        CREATE INDEX IF NOT EXISTS idx_classification_events_paper_id ON classification_events(paper_id, event_id DESC);

        CREATE TABLE IF NOT EXISTS completeness_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT NOT NULL,
            proposed_key TEXT NOT NULL,
            final_key TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL NOT NULL,
            rationale TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
        );
        CREATE INDEX IF NOT EXISTS idx_completeness_events_paper_id ON completeness_events(paper_id, event_id DESC);

        CREATE TABLE IF NOT EXISTS remote_links (
            remote_link_id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            remote_key TEXT,
            attachment_keys_json TEXT,
            status TEXT NOT NULL,
            metadata_json TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(paper_id) REFERENCES papers(paper_id)
        );
        CREATE INDEX IF NOT EXISTS idx_remote_links_paper_provider ON remote_links(paper_id, provider);

        CREATE TABLE IF NOT EXISTS build_runs (
            build_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            mode TEXT NOT NULL,
            stages_json TEXT NOT NULL,
            changed_papers_json TEXT NOT NULL,
            stats_json TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT ''
        );
        """
    )
    seed_method_classes(conn)
    seed_completeness_levels(conn)
    ensure_column(conn, "papers", "wcm_completeness_key", "TEXT NOT NULL DEFAULT 'related'")
    conn.commit()


def seed_method_classes(conn: sqlite3.Connection) -> None:
    now = utc_now()
    for seed in DEFAULT_METHOD_CLASSES:
        conn.execute(
            """
            INSERT INTO method_classes (key, display_name, definition, color, sort_order, active, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(key) DO UPDATE SET
                display_name = COALESCE(method_classes.display_name, excluded.display_name),
                definition = COALESCE(method_classes.definition, excluded.definition),
                color = COALESCE(method_classes.color, excluded.color),
                sort_order = excluded.sort_order,
                updated_at = excluded.updated_at
            """,
            (seed.key, seed.display_name, seed.definition, seed.color, seed.sort_order, now),
        )


def seed_completeness_levels(conn: sqlite3.Connection) -> None:
    now = utc_now()
    for seed in DEFAULT_COMPLETENESS_LEVELS:
        conn.execute(
            """
            INSERT INTO completeness_levels (key, display_name, definition, color, sort_order, active, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(key) DO UPDATE SET
                display_name = COALESCE(completeness_levels.display_name, excluded.display_name),
                definition = COALESCE(completeness_levels.definition, excluded.definition),
                color = COALESCE(completeness_levels.color, excluded.color),
                sort_order = excluded.sort_order,
                updated_at = excluded.updated_at
            """,
            (seed.key, seed.display_name, seed.definition, seed.color, seed.sort_order, now),
        )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def bootstrap_from_legacy_files(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT COUNT(*) AS count FROM papers").fetchone()["count"]
    if existing:
        return
    if not MASTER_TABLE.exists():
        return

    class_rows = {}
    if CLASS_TABLE.exists():
        with CLASS_TABLE.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                class_rows[row["paper_id"]] = row

    organism_rows = {}
    if ORGANISM_TABLE.exists():
        with ORGANISM_TABLE.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                organism_rows[row["paper_id"]] = row

    now = utc_now()
    with MASTER_TABLE.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            paper_id = row["paper id"]
            class_row = class_rows.get(paper_id, {})
            raw_label = class_row.get("method_class", "Mechanistic models")
            method_class_key = DEFAULT_CLASS_LABEL_TO_KEY.get(raw_label)
            if not method_class_key:
                method_class_key, _, _ = classify_method_class_key(
                    row.get("paper title", ""),
                    row.get("summary", ""),
                    row.get("journal", ""),
                )
            if paper_id in CURATED_COMPLETENESS_OVERRIDES:
                completeness_key = CURATED_COMPLETENESS_OVERRIDES[paper_id]
                completeness_source = "manual_override"
                completeness_confidence = 1.0
                completeness_rationale = "Curated completeness assignment for the existing corpus."
            else:
                completeness_key, completeness_confidence, completeness_rationale = classify_completeness_key(
                    row.get("paper title", ""),
                    row.get("summary", ""),
                    row.get("journal", ""),
                )
                completeness_source = "heuristic"
            organism = organism_rows.get(paper_id, {}).get("organism", "")
            conn.execute(
                """
                INSERT INTO papers (
                    paper_id, title, normalized_title, year, journal, doi, landing_page_url,
                    summary, method_summary, contribution, limitation, future_work,
                    openalex_id, organism, organism_group, method_class_key, wcm_completeness_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, '', ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    row.get("paper title", ""),
                    normalize_title(row.get("paper title", "")),
                    int(row["year"]) if row.get("year") else None,
                    row.get("journal", ""),
                    (row.get("doi") or "").lower(),
                    row.get("landing page url", ""),
                    row.get("summary", ""),
                    row.get("method summary", ""),
                    row.get("contribution", ""),
                    row.get("limitation", ""),
                    row.get("future work", ""),
                    organism,
                    method_class_key,
                    completeness_key,
                    now,
                    now,
                ),
            )
            rationale = class_row.get("rationale") or "Imported from legacy curated assignments."
            conn.execute(
                """
                INSERT INTO classification_events (
                    paper_id, proposed_key, final_key, source, confidence, rationale, created_at
                ) VALUES (?, ?, ?, 'manual_override', ?, ?, ?)
                """,
                (paper_id, method_class_key, method_class_key, 1.0, rationale, now),
            )
            conn.execute(
                """
                INSERT INTO completeness_events (
                    paper_id, proposed_key, final_key, source, confidence, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    completeness_key,
                    completeness_key,
                    completeness_source,
                    completeness_confidence,
                    completeness_rationale,
                    now,
                ),
            )

    import_remote_links_from_legacy_state(conn)
    conn.commit()


def import_remote_links_from_legacy_state(conn: sqlite3.Connection) -> None:
    if not ZOTERO_SYNC_STATE.exists():
        return
    try:
        payload = json.loads(ZOTERO_SYNC_STATE.read_text(encoding="utf-8"))
    except Exception:
        return
    now = utc_now()
    for item in payload.get("remote_items", []):
        paper_id = item.get("paper_id")
        if not paper_id:
            continue
        attachment_keys = item.get("attachment_keys", [])
        remote_key = item.get("parent_key", "")
        status = "zotero_present" if item.get("has_pdf_attachment") else "zotero_missing_pdf"
        conn.execute(
            """
            INSERT INTO remote_links (paper_id, provider, remote_key, attachment_keys_json, status, metadata_json, updated_at)
            VALUES (?, 'zotero', ?, ?, ?, ?, ?)
            """,
            (paper_id, remote_key, json.dumps(attachment_keys), status, json.dumps(item), now),
        )


def fetch_method_classes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT key, display_name, definition, color, sort_order, active, updated_at FROM method_classes ORDER BY sort_order, key"
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_completeness_levels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT key, display_name, definition, color, sort_order, active, updated_at FROM completeness_levels ORDER BY sort_order, key"
    ).fetchall()
    return [dict(row) for row in rows]


def latest_classification_by_paper(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.paper_id, e.proposed_key, e.final_key, e.source, e.confidence, e.rationale, e.created_at
        FROM classification_events e
        JOIN (
            SELECT paper_id, MAX(event_id) AS event_id
            FROM classification_events
            GROUP BY paper_id
        ) latest ON latest.event_id = e.event_id
        """
    ).fetchall()
    return {row["paper_id"]: dict(row) for row in rows}


def latest_completeness_by_paper(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.paper_id, e.proposed_key, e.final_key, e.source, e.confidence, e.rationale, e.created_at
        FROM completeness_events e
        JOIN (
            SELECT paper_id, MAX(event_id) AS event_id
            FROM completeness_events
            GROUP BY paper_id
        ) latest ON latest.event_id = e.event_id
        """
    ).fetchall()
    return {row["paper_id"]: dict(row) for row in rows}


def latest_remote_links_by_paper(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.paper_id, r.provider, r.remote_key, r.attachment_keys_json, r.status, r.metadata_json, r.updated_at
        FROM remote_links r
        JOIN (
            SELECT paper_id, provider, MAX(remote_link_id) AS remote_link_id
            FROM remote_links
            GROUP BY paper_id, provider
        ) latest ON latest.remote_link_id = r.remote_link_id
        """
    ).fetchall()
    data = {}
    for row in rows:
        item = dict(row)
        for key in ["attachment_keys_json", "metadata_json"]:
            try:
                item[key] = json.loads(item[key] or "null")
            except Exception:
                item[key] = [] if "attachment" in key else {}
        data[f"{row['paper_id']}::{row['provider']}"] = item
    return data


def fetch_pdf_assets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT pdf_id, paper_id, relative_path, file_hash, size_bytes, mtime_ns, status,
               page_count, parse_error, parser_version, first_seen_at, last_seen_at, last_parsed_at
        FROM pdf_assets
        ORDER BY relative_path
        """
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_pdf_parse_cache(conn: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT file_hash, parser_version, parsed, profile_json, error, page_count, updated_at
        FROM pdf_parse_cache
        """
    ).fetchall()
    data: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        try:
            item["profile"] = json.loads(item["profile_json"]) if item["profile_json"] else None
        except Exception:
            item["profile"] = None
        data[(row["file_hash"], row["parser_version"])] = item
    return data


def fetch_papers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.paper_id, p.title, p.normalized_title, p.year, p.journal, p.doi, p.landing_page_url,
               p.summary, p.method_summary, p.contribution, p.limitation, p.future_work,
               p.openalex_id, p.organism, p.organism_group, p.method_class_key, p.wcm_completeness_key, p.created_at, p.updated_at,
               mc.display_name AS method_class_label, mc.definition AS method_class_definition, mc.color AS method_class_color,
               cl.display_name AS wcm_completeness_label, cl.definition AS wcm_completeness_definition, cl.color AS wcm_completeness_color
        FROM papers p
        JOIN method_classes mc ON mc.key = p.method_class_key
        JOIN completeness_levels cl ON cl.key = p.wcm_completeness_key
        ORDER BY p.paper_id
        """
    ).fetchall()
    classifications = latest_classification_by_paper(conn)
    completeness = latest_completeness_by_paper(conn)
    remote = latest_remote_links_by_paper(conn)
    pdf_assets = fetch_pdf_assets(conn)
    assets_by_paper: dict[str, list[dict[str, Any]]] = {}
    for asset in pdf_assets:
        if asset["paper_id"]:
            assets_by_paper.setdefault(asset["paper_id"], []).append(asset)
    result = []
    for row in rows:
        item = dict(row)
        primary = primary_pdf_asset_for_paper(assets_by_paper.get(row["paper_id"], []))
        item["primary_pdf_asset"] = primary
        class_event = classifications.get(row["paper_id"], {})
        item["classification_source"] = class_event.get("source", "")
        item["classification_confidence"] = class_event.get("confidence", "")
        item["classification_rationale"] = class_event.get("rationale", "")
        completeness_event = completeness.get(row["paper_id"], {})
        item["wcm_completeness_source"] = completeness_event.get("source", "")
        item["wcm_completeness_confidence"] = completeness_event.get("confidence", "")
        item["wcm_completeness_rationale"] = completeness_event.get("rationale", "")
        item["remote_link"] = remote.get(f"{row['paper_id']}::zotero")
        result.append(item)
    return result


def primary_pdf_asset_for_paper(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not assets:
        return None
    status_rank = {
        "parsed": 5,
        "matched": 4,
        "renamed": 3,
        "discovered": 2,
        "failed_parse": 1,
        "missing": 0,
        "rejected": -1,
    }
    return sorted(
        assets,
        key=lambda asset: (
            status_rank.get(asset["status"], 0),
            asset.get("last_parsed_at") or "",
            asset.get("last_seen_at") or "",
            asset["relative_path"],
        ),
        reverse=True,
    )[0]


def next_auto_paper_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT paper_id FROM papers WHERE paper_id LIKE 'WCM-A%' ORDER BY paper_id").fetchall()
    if not rows:
        return "WCM-A001"
    last = rows[-1]["paper_id"]
    return f"WCM-A{int(last.split('A')[-1]) + 1:03d}"


def start_build_run(conn: sqlite3.Connection, mode: str, stages: list[str]) -> int:
    cursor = conn.execute(
        """
        INSERT INTO build_runs (started_at, finished_at, mode, stages_json, changed_papers_json, stats_json, status, error)
        VALUES (?, NULL, ?, ?, '[]', '{}', 'running', '')
        """,
        (utc_now(), mode, json.dumps(stages)),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_build_run(
    conn: sqlite3.Connection,
    build_run_id: int,
    *,
    status: str,
    changed_papers: list[str],
    stats: dict[str, Any],
    error: str = "",
) -> None:
    conn.execute(
        """
        UPDATE build_runs
        SET finished_at = ?, changed_papers_json = ?, stats_json = ?, status = ?, error = ?
        WHERE build_run_id = ?
        """,
        (utc_now(), json.dumps(sorted(set(changed_papers))), json.dumps(stats, sort_keys=True), status, error, build_run_id),
    )
    conn.commit()


def status_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    papers = fetch_papers(conn)
    assets = fetch_pdf_assets(conn)
    parsed = sum(1 for asset in assets if asset["status"] == "parsed")
    failed = sum(1 for asset in assets if asset["status"] == "failed_parse")
    return {
        "db_path": str(DB_PATH),
        "papers": len(papers),
        "pdf_assets": len(assets),
        "parsed_pdf_assets": parsed,
        "failed_pdf_assets": failed,
        "method_classes": [
            {"key": row["key"], "display_name": row["display_name"], "color": row["color"]}
            for row in fetch_method_classes(conn)
        ],
        "complete_wcm_papers": sum(1 for paper in papers if paper.get("wcm_completeness_key") == "complete"),
    }
