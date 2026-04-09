from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .db import utc_now


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_local_pdfs(conn: sqlite3.Connection, pdf_dir: Path, rejected_dir: Path) -> dict[str, list[str]]:
    now = utc_now()
    changed_assets: list[str] = []
    seen_paths: set[str] = set()

    for directory, status in [(pdf_dir, "discovered"), (rejected_dir, "rejected")]:
        if not directory.exists():
            continue
        for pdf_path in sorted(directory.glob("*.pdf")):
            relative_path = pdf_path.relative_to(pdf_dir.parent).as_posix()
            seen_paths.add(relative_path)
            stat = pdf_path.stat()
            content_hash = file_hash(pdf_path)
            existing = conn.execute(
                """
                SELECT pdf_id, relative_path, file_hash, size_bytes, mtime_ns, status, paper_id
                FROM pdf_assets
                WHERE relative_path = ?
                """,
                (relative_path,),
            ).fetchone()
            hash_match = conn.execute(
                """
                SELECT pdf_id, relative_path, paper_id, status
                FROM pdf_assets
                WHERE file_hash = ?
                ORDER BY pdf_id
                LIMIT 1
                """,
                (content_hash,),
            ).fetchone()
            if existing:
                if status == "rejected":
                    next_status = "rejected"
                elif existing["file_hash"] != content_hash or existing["size_bytes"] != stat.st_size or existing["mtime_ns"] != stat.st_mtime_ns:
                    next_status = "discovered"
                elif existing["status"] in {"missing", "rejected"}:
                    next_status = "discovered"
                else:
                    next_status = existing["status"]
                if (
                    existing["file_hash"] != content_hash
                    or existing["size_bytes"] != stat.st_size
                    or existing["mtime_ns"] != stat.st_mtime_ns
                    or existing["status"] != next_status
                ):
                    conn.execute(
                        """
                        UPDATE pdf_assets
                        SET file_hash = ?, size_bytes = ?, mtime_ns = ?, status = ?, last_seen_at = ?
                        WHERE pdf_id = ?
                        """,
                        (content_hash, stat.st_size, stat.st_mtime_ns, next_status, now, existing["pdf_id"]),
                    )
                    changed_assets.append(relative_path)
                else:
                    conn.execute("UPDATE pdf_assets SET last_seen_at = ? WHERE pdf_id = ?", (now, existing["pdf_id"]))
                continue

            if hash_match and hash_match["relative_path"] != relative_path:
                next_status = "renamed" if status != "rejected" else "rejected"
                conn.execute(
                    """
                    UPDATE pdf_assets
                    SET relative_path = ?, size_bytes = ?, mtime_ns = ?, status = ?, last_seen_at = ?
                    WHERE pdf_id = ?
                    """,
                    (relative_path, stat.st_size, stat.st_mtime_ns, next_status, now, hash_match["pdf_id"]),
                )
                changed_assets.append(relative_path)
                continue

            conn.execute(
                """
                INSERT INTO pdf_assets (
                    paper_id, relative_path, file_hash, size_bytes, mtime_ns, status,
                    page_count, parse_error, parser_version, first_seen_at, last_seen_at, last_parsed_at
                ) VALUES (NULL, ?, ?, ?, ?, ?, NULL, '', NULL, ?, ?, NULL)
                """,
                (relative_path, content_hash, stat.st_size, stat.st_mtime_ns, status, now, now),
            )
            changed_assets.append(relative_path)

    for row in conn.execute("SELECT pdf_id, relative_path FROM pdf_assets").fetchall():
        if row["relative_path"] not in seen_paths:
            conn.execute("UPDATE pdf_assets SET status = 'missing' WHERE pdf_id = ?", (row["pdf_id"],))

    conn.commit()
    return {"changed_assets": changed_assets}
