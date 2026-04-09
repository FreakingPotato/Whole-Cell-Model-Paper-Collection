from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from .classify import classify_completeness_key, classify_method_class_key
from .db import (
    connect,
    fetch_papers,
    finish_build_run,
    init_db,
    latest_classification_by_paper,
    latest_completeness_by_paper,
    next_auto_paper_id,
    normalize_title,
    start_build_run,
    status_summary,
    utc_now,
    bootstrap_from_legacy_files,
)
from .discovery import discover_local_pdfs
from .enrich import enrich_papers
from .export import export_graph_and_metadata
from .models import CURATED_COMPLETENESS_OVERRIDES, DB_PATH, PDF_DIR, REJECTED_PDF_DIR, ROOT, ZOTERO_SYNC_STATE
from .parse import parse_assets, parsed_profiles_by_path
from .zotero import sync_remote
from . import legacy_graph_builder as legacy


def run_match_stage(conn: sqlite3.Connection) -> dict[str, list[str]]:
    papers = fetch_papers(conn)
    profiles_by_path = parsed_profiles_by_path(conn)
    changed: set[str] = set()
    paper_rows = [
        {
            "paper_id": paper["paper_id"],
            "paper_title": paper["title"],
            "doi": paper["doi"] or "",
            "pdf_status": "downloaded" if paper.get("primary_pdf_asset") else "landing_page_only",
            "pdf_file": (paper.get("primary_pdf_asset") or {}).get("relative_path", "").split("/")[-1] if paper.get("primary_pdf_asset") else "",
        }
        for paper in papers
    ]
    profiles = []
    for relative_path, profile in profiles_by_path.items():
        current = dict(profile)
        current["path"] = str(ROOT / relative_path)
        current["file_name"] = relative_path.split("/")[-1]
        profiles.append(current)

    matched_paths: set[str] = set()
    for row in paper_rows:
        profile = legacy.find_best_pdf_match(row, profiles)
        if not profile:
            continue
        relative_path = (ROOT / profile["path"]).relative_to(ROOT).as_posix()
        matched_paths.add(relative_path)
        conn.execute(
            """
            UPDATE pdf_assets
            SET paper_id = ?, status = CASE WHEN status = 'parsed' THEN 'parsed' ELSE 'matched' END
            WHERE relative_path = ?
            """,
            (row["paper_id"], relative_path),
        )
        changed.add(row["paper_id"])

    known_dois = {paper["doi"]: paper["paper_id"] for paper in papers if paper["doi"]}
    for relative_path, profile in profiles_by_path.items():
        if relative_path in matched_paths:
            continue
        doi = (profile.get("doi") or "").lower()
        summary = None
        try:
            if doi:
                summary = legacy.openalex_work_summary(doi)
            if summary is None:
                title = profile.get("metadata_title") or ""
                if title:
                    summary = legacy.openalex_work_summary_by_title(title)
        except Exception:
            summary = None
        if not summary:
            continue
        summary_doi = (summary.get("doi") or doi or "").lower()
        if summary_doi and summary_doi in known_dois:
            paper_id = known_dois[summary_doi]
            conn.execute("UPDATE pdf_assets SET paper_id = ?, status = 'matched' WHERE relative_path = ?", (paper_id, relative_path))
            changed.add(paper_id)
            continue
        paper_id = next_auto_paper_id(conn)
        method_class_key, confidence, rationale = classify_method_class_key(
            summary.get("title") or profile.get("metadata_title") or "",
            summary.get("abstract") or "",
            summary.get("journal") or "",
        )
        completeness_key, completeness_confidence, completeness_rationale = classify_completeness_key(
            summary.get("title") or profile.get("metadata_title") or "",
            summary.get("abstract") or "",
            summary.get("journal") or "",
        )
        organism = legacy.heuristic_organism(summary.get("title") or "", summary.get("abstract") or "")
        now = utc_now()
        conn.execute(
            """
            INSERT INTO papers (
                paper_id, title, normalized_title, year, journal, doi, landing_page_url,
                summary, method_summary, contribution, limitation, future_work,
                openalex_id, organism, organism_group, method_class_key, wcm_completeness_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                summary.get("title") or profile.get("metadata_title") or relative_path.split("/")[-1],
                normalize_title(summary.get("title") or profile.get("metadata_title") or relative_path.split("/")[-1]),
                summary.get("year"),
                summary.get("journal") or "",
                summary_doi,
                summary.get("landing_page_url") or (f"https://doi.org/{summary_doi}" if summary_doi else ""),
                (summary.get("abstract") or "").split(". ")[0].strip() + ("." if summary.get("abstract") else ""),
                "Auto-ingested from a locally discovered PDF; detailed annotation still needs review.",
                "Added automatically from a PDF dropped into the local corpus for later curation.",
                "Auto-ingested metadata may require manual verification against the full paper.",
                "Review and curate this paper in the master table if it is central to the WCM corpus.",
                summary.get("openalex_id") or "",
                organism,
                legacy.organism_group_for_label(organism),
                method_class_key,
                completeness_key,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO classification_events (
                paper_id, proposed_key, final_key, source, confidence, rationale, created_at
            ) VALUES (?, ?, ?, 'heuristic', ?, ?, ?)
            """,
            (paper_id, method_class_key, method_class_key, confidence, rationale, now),
        )
        conn.execute(
            """
            INSERT INTO completeness_events (
                paper_id, proposed_key, final_key, source, confidence, rationale, created_at
            ) VALUES (?, ?, ?, 'heuristic', ?, ?, ?)
            """,
            (paper_id, completeness_key, completeness_key, completeness_confidence, completeness_rationale, now),
        )
        conn.execute("UPDATE pdf_assets SET paper_id = ?, status = 'matched' WHERE relative_path = ?", (paper_id, relative_path))
        known_dois[summary_doi] = paper_id
        changed.add(paper_id)

    conn.commit()
    return {"changed_papers": sorted(changed)}


def run_normalize_stage(conn: sqlite3.Connection) -> dict[str, list[str]]:
    changed: set[str] = set()
    rows = conn.execute(
        """
        SELECT a.pdf_id, a.relative_path, a.paper_id, p.title, p.year
        FROM pdf_assets a
        JOIN papers p ON p.paper_id = a.paper_id
        WHERE a.status NOT IN ('rejected', 'missing')
        """
    ).fetchall()
    for row in rows:
        current_path = ROOT / row["relative_path"]
        target_name = legacy.target_pdf_name(row["paper_id"], row["title"], row["year"] or "")
        target_path = PDF_DIR / target_name
        if current_path == target_path:
            continue
        if target_path.exists():
            continue
        current_path.rename(target_path)
        conn.execute(
            "UPDATE pdf_assets SET relative_path = ?, status = 'renamed' WHERE pdf_id = ?",
            (target_path.relative_to(ROOT).as_posix(), row["pdf_id"]),
        )
        changed.add(row["paper_id"])
    parse_assets(conn, ROOT, full_rebuild=False)
    conn.commit()
    return {"changed_papers": sorted(changed)}


def run_classify_stage(conn: sqlite3.Connection) -> dict[str, list[str]]:
    latest = latest_classification_by_paper(conn)
    latest_completeness = latest_completeness_by_paper(conn)
    changed: set[str] = set()
    rows = conn.execute(
        "SELECT paper_id, title, summary, journal, method_class_key, wcm_completeness_key FROM papers"
    ).fetchall()
    for row in rows:
        now = utc_now()
        latest_event = latest.get(row["paper_id"])
        if latest_event and latest_event.get("source") == "manual_override":
            final_key = latest_event["final_key"]
            if final_key != row["method_class_key"]:
                conn.execute(
                    "UPDATE papers SET method_class_key = ?, updated_at = ? WHERE paper_id = ?",
                    (final_key, now, row["paper_id"]),
                )
                changed.add(row["paper_id"])
        else:
            proposed_key, confidence, rationale = classify_method_class_key(
                row["title"],
                row["summary"] or "",
                row["journal"] or "",
            )
            if latest_event and latest_event.get("source") == "heuristic" and latest_event.get("final_key") == proposed_key:
                if row["method_class_key"] != proposed_key:
                    conn.execute(
                        "UPDATE papers SET method_class_key = ?, updated_at = ? WHERE paper_id = ?",
                        (proposed_key, now, row["paper_id"]),
                    )
                    changed.add(row["paper_id"])
            else:
                conn.execute(
                    """
                    INSERT INTO classification_events (paper_id, proposed_key, final_key, source, confidence, rationale, created_at)
                    VALUES (?, ?, ?, 'heuristic', ?, ?, ?)
                    """,
                    (row["paper_id"], proposed_key, proposed_key, confidence, rationale, now),
                )
                conn.execute(
                    "UPDATE papers SET method_class_key = ?, updated_at = ? WHERE paper_id = ?",
                    (proposed_key, now, row["paper_id"]),
                )
                changed.add(row["paper_id"])

        completeness_event = latest_completeness.get(row["paper_id"])
        if row["paper_id"] in CURATED_COMPLETENESS_OVERRIDES:
            curated_key = CURATED_COMPLETENESS_OVERRIDES[row["paper_id"]]
            if (
                row["wcm_completeness_key"] != curated_key
                or not completeness_event
                or completeness_event.get("source") != "manual_override"
                or completeness_event.get("final_key") != curated_key
            ):
                conn.execute(
                    """
                    INSERT INTO completeness_events (paper_id, proposed_key, final_key, source, confidence, rationale, created_at)
                    VALUES (?, ?, ?, 'manual_override', 1.0, ?, ?)
                    """,
                    (row["paper_id"], curated_key, curated_key, "Curated completeness assignment for the existing corpus.", now),
                )
                conn.execute(
                    "UPDATE papers SET wcm_completeness_key = ?, updated_at = ? WHERE paper_id = ?",
                    (curated_key, now, row["paper_id"]),
                )
                changed.add(row["paper_id"])
        elif completeness_event and completeness_event.get("source") == "manual_override":
            final_key = completeness_event["final_key"]
            if final_key != row["wcm_completeness_key"]:
                conn.execute(
                    "UPDATE papers SET wcm_completeness_key = ?, updated_at = ? WHERE paper_id = ?",
                    (final_key, now, row["paper_id"]),
                )
                changed.add(row["paper_id"])
        else:
            proposed_completeness, completeness_confidence, completeness_rationale = classify_completeness_key(
                row["title"],
                row["summary"] or "",
                row["journal"] or "",
            )
            if (
                completeness_event
                and completeness_event.get("source") == "heuristic"
                and completeness_event.get("final_key") == proposed_completeness
            ):
                if row["wcm_completeness_key"] != proposed_completeness:
                    conn.execute(
                        "UPDATE papers SET wcm_completeness_key = ?, updated_at = ? WHERE paper_id = ?",
                        (proposed_completeness, now, row["paper_id"]),
                    )
                    changed.add(row["paper_id"])
            else:
                conn.execute(
                    """
                    INSERT INTO completeness_events (paper_id, proposed_key, final_key, source, confidence, rationale, created_at)
                    VALUES (?, ?, ?, 'heuristic', ?, ?, ?)
                    """,
                    (
                        row["paper_id"],
                        proposed_completeness,
                        proposed_completeness,
                        completeness_confidence,
                        completeness_rationale,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE papers SET wcm_completeness_key = ?, updated_at = ? WHERE paper_id = ?",
                    (proposed_completeness, now, row["paper_id"]),
                )
                changed.add(row["paper_id"])
    conn.commit()
    return {"changed_papers": sorted(changed)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and maintain the Whole-Cell Model corpus graph.")
    parser.add_argument("--status", action="store_true", help="Print a database-backed status summary and exit.")
    parser.add_argument("--incremental", action="store_true", default=True, help="Run the default incremental pipeline.")
    parser.add_argument("--full-rebuild", action="store_true", help="Force reparsing and metadata refresh.")
    parser.add_argument(
        "--stage",
        action="append",
        choices=["discover", "sync", "match", "normalize", "enrich", "classify", "parse", "export", "report"],
        help="Run only the selected stage(s) in pipeline order.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(DB_PATH)
    init_db(conn)
    bootstrap_from_legacy_files(conn)
    if args.status:
        print(json.dumps(status_summary(conn), indent=2))
        return 0

    requested = args.stage or ["discover", "sync", "parse", "match", "normalize", "enrich", "classify", "export", "report"]
    ordered = [stage for stage in ["discover", "sync", "parse", "match", "normalize", "enrich", "classify", "export", "report"] if stage in requested]
    build_run_id = start_build_run(conn, "full-rebuild" if args.full_rebuild else "incremental", ordered)
    changed_papers: list[str] = []
    stats: dict[str, object] = {"stages": ordered}
    try:
        for stage in ordered:
            if stage == "discover":
                result = discover_local_pdfs(conn, PDF_DIR, REJECTED_PDF_DIR)
                stats["discover"] = result
            elif stage == "sync":
                result = sync_remote(conn, PDF_DIR, ZOTERO_SYNC_STATE)
                changed_papers.extend(result.get("changed_papers", []))
                stats["sync"] = result
                discover_local_pdfs(conn, PDF_DIR, REJECTED_PDF_DIR)
            elif stage == "parse":
                result = parse_assets(conn, ROOT, full_rebuild=args.full_rebuild)
                changed_papers.extend(result.get("changed_papers", []))
                stats["parse"] = result
            elif stage == "match":
                result = run_match_stage(conn)
                changed_papers.extend(result.get("changed_papers", []))
                stats["match"] = result
            elif stage == "normalize":
                result = run_normalize_stage(conn)
                changed_papers.extend(result.get("changed_papers", []))
                stats["normalize"] = result
                discover_local_pdfs(conn, PDF_DIR, REJECTED_PDF_DIR)
            elif stage == "enrich":
                result = enrich_papers(conn, full_rebuild=args.full_rebuild)
                changed_papers.extend(result.get("changed_papers", []))
                stats["enrich"] = result
            elif stage == "classify":
                result = run_classify_stage(conn)
                changed_papers.extend(result.get("changed_papers", []))
                stats["classify"] = result
            elif stage in {"export", "report"}:
                result = export_graph_and_metadata(conn)
                stats["export"] = result
        finish_build_run(conn, build_run_id, status="success", changed_papers=changed_papers, stats=stats)
        if "export" in stats:
            print(json.dumps(stats["export"], indent=2))
        else:
            print(json.dumps({"status": "ok", "stages": ordered, "changed_papers": sorted(set(changed_papers))}, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        finish_build_run(conn, build_run_id, status="failed", changed_papers=changed_papers, stats=stats, error=str(exc))
        raise
