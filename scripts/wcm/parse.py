from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import utc_now
from .models import PARSER_VERSION
from . import legacy_graph_builder as legacy


def parse_pdf_profile(path: Path) -> dict[str, Any]:
    return legacy.extract_pdf_profile(path)


def parse_assets(conn: sqlite3.Connection, root: Path, *, full_rebuild: bool = False) -> dict[str, list[str]]:
    changed_papers: set[str] = set()
    rows = conn.execute(
        """
        SELECT pdf_id, paper_id, relative_path, file_hash, status
        FROM pdf_assets
        WHERE status NOT IN ('rejected', 'missing')
        ORDER BY relative_path
        """
    ).fetchall()
    now = utc_now()
    for row in rows:
        cache = conn.execute(
            """
            SELECT parsed, profile_json, error, page_count
            FROM pdf_parse_cache
            WHERE file_hash = ? AND parser_version = ?
            """,
            (row["file_hash"], PARSER_VERSION),
        ).fetchone()
        if cache and not full_rebuild:
            parsed = bool(cache["parsed"])
            status = "parsed" if parsed else "failed_parse"
            conn.execute(
                """
                UPDATE pdf_assets
                SET status = ?, page_count = ?, parse_error = ?, parser_version = ?, last_parsed_at = COALESCE(last_parsed_at, ?)
                WHERE pdf_id = ?
                """,
                (status, cache["page_count"], cache["error"], PARSER_VERSION, now, row["pdf_id"]),
            )
            if row["paper_id"]:
                changed_papers.add(row["paper_id"])
            continue

        pdf_path = root / row["relative_path"]
        try:
            profile = parse_pdf_profile(pdf_path)
            page_count = int(profile.get("page_count") or 0)
            conn.execute(
                """
                INSERT INTO pdf_parse_cache (file_hash, parser_version, parsed, profile_json, error, page_count, updated_at)
                VALUES (?, ?, 1, ?, '', ?, ?)
                ON CONFLICT(file_hash, parser_version) DO UPDATE SET
                    parsed = excluded.parsed,
                    profile_json = excluded.profile_json,
                    error = '',
                    page_count = excluded.page_count,
                    updated_at = excluded.updated_at
                """,
                (row["file_hash"], PARSER_VERSION, json.dumps(profile), page_count, now),
            )
            conn.execute(
                """
                UPDATE pdf_assets
                SET status = 'parsed', page_count = ?, parse_error = '', parser_version = ?, last_parsed_at = ?
                WHERE pdf_id = ?
                """,
                (page_count, PARSER_VERSION, now, row["pdf_id"]),
            )
        except Exception as exc:  # noqa: BLE001
            conn.execute(
                """
                INSERT INTO pdf_parse_cache (file_hash, parser_version, parsed, profile_json, error, page_count, updated_at)
                VALUES (?, ?, 0, NULL, ?, NULL, ?)
                ON CONFLICT(file_hash, parser_version) DO UPDATE SET
                    parsed = excluded.parsed,
                    profile_json = NULL,
                    error = excluded.error,
                    page_count = NULL,
                    updated_at = excluded.updated_at
                """,
                (row["file_hash"], PARSER_VERSION, str(exc), now),
            )
            conn.execute(
                """
                UPDATE pdf_assets
                SET status = 'failed_parse', page_count = NULL, parse_error = ?, parser_version = ?, last_parsed_at = ?
                WHERE pdf_id = ?
                """,
                (str(exc), PARSER_VERSION, now, row["pdf_id"]),
            )
        if row["paper_id"]:
            changed_papers.add(row["paper_id"])

    conn.commit()
    return {"changed_papers": sorted(changed_papers)}


def parsed_profiles_by_path(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.relative_path, c.profile_json
        FROM pdf_assets a
        JOIN pdf_parse_cache c
          ON c.file_hash = a.file_hash
         AND c.parser_version = a.parser_version
        WHERE a.status = 'parsed' AND c.parsed = 1 AND c.profile_json IS NOT NULL
        """
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            result[row["relative_path"]] = json.loads(row["profile_json"])
        except Exception:
            continue
    return result

