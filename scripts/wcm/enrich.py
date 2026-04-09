from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from .db import normalize_title, utc_now
from . import legacy_graph_builder as legacy
from .models import OPENALEX_CACHE_TTL_DAYS


def cache_is_stale(path) -> bool:
    if not path.exists():
        return True
    cutoff = datetime.now(UTC) - timedelta(days=OPENALEX_CACHE_TTL_DAYS)
    return datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff


def enrich_papers(conn: sqlite3.Connection, *, full_rebuild: bool = False) -> dict[str, list[str]]:
    changed: set[str] = set()
    rows = conn.execute(
        """
        SELECT paper_id, title, doi, summary, journal, year, landing_page_url, openalex_id
        FROM papers
        ORDER BY paper_id
        """
    ).fetchall()
    now = utc_now()
    for row in rows:
        summary = None
        doi = (row["doi"] or "").strip().lower()
        try:
            if doi:
                cache_path = legacy.cache_path(doi)
                if full_rebuild or cache_is_stale(cache_path) or not row["openalex_id"]:
                    summary = legacy.openalex_work_summary(doi)
                elif cache_path.exists():
                    summary = legacy.openalex_work_summary(doi)
            if summary is None and row["title"]:
                summary = legacy.openalex_work_summary_by_title(row["title"])
        except Exception:
            summary = None
        if not summary:
            continue
        conn.execute(
            """
            UPDATE papers
            SET normalized_title = ?,
                year = COALESCE(?, year),
                journal = CASE WHEN ? != '' THEN ? ELSE journal END,
                landing_page_url = CASE WHEN ? != '' THEN ? ELSE landing_page_url END,
                summary = CASE WHEN summary = '' THEN ? ELSE summary END,
                openalex_id = CASE WHEN ? != '' THEN ? ELSE openalex_id END,
                updated_at = ?
            WHERE paper_id = ?
            """,
            (
                normalize_title(summary.get("title") or row["title"]),
                summary.get("year"),
                summary.get("journal") or "",
                summary.get("journal") or "",
                summary.get("landing_page_url") or "",
                summary.get("landing_page_url") or "",
                summary.get("abstract") or row["summary"] or "",
                summary.get("openalex_id") or "",
                summary.get("openalex_id") or "",
                now,
                row["paper_id"],
            ),
        )
        changed.add(row["paper_id"])
    conn.commit()
    return {"changed_papers": sorted(changed)}

