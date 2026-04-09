from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import fetch_papers, utc_now
from . import zotero_legacy


def sync_remote(conn: sqlite3.Connection, pdf_dir: Path, state_path: Path) -> dict[str, list[str]]:
    rows = []
    for paper in fetch_papers(conn):
        asset = paper.get("primary_pdf_asset") or {}
        rows.append(
            {
                "paper_id": paper["paper_id"],
                "paper_title": paper["title"],
                "year": paper["year"] or "",
                "doi": paper["doi"] or "",
                "pdf_file": (asset.get("relative_path") or "").split("/")[-1] if asset else "",
                "pdf_status": asset.get("status") or "landing_page_only",
            }
        )
    state = zotero_legacy.sync_from_zotero(rows, pdf_dir, state_path)
    state = zotero_legacy.sync_to_zotero(rows, pdf_dir, state_path)
    if not (state or {}).get("configured") and not (state or {}).get("enabled"):
        return {"changed_papers": []}
    now = utc_now()
    changed_papers: set[str] = set()
    conn.execute("DELETE FROM remote_links WHERE provider = 'zotero'")
    for item in (state or {}).get("remote_items", []):
        paper_id = item.get("paper_id")
        if not paper_id:
            continue
        changed_papers.add(paper_id)
        status = "zotero_present" if item.get("has_pdf_attachment") else "zotero_missing_pdf"
        conn.execute(
            """
            INSERT INTO remote_links (paper_id, provider, remote_key, attachment_keys_json, status, metadata_json, updated_at)
            VALUES (?, 'zotero', ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                item.get("parent_key", ""),
                json.dumps(item.get("attachment_keys", [])),
                status,
                json.dumps(item),
                now,
            ),
        )
    conn.commit()
    return {"changed_papers": sorted(changed_papers)}
