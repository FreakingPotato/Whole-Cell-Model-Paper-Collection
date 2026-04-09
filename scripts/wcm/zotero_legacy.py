#!/usr/bin/env python3
"""Zotero synchronization helpers for the Whole Cell Model corpus."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests


API_ROOT = "https://api.zotero.org"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def target_pdf_name(paper_id: str, title: str, year: int | str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{paper_id}_{year}_{title[:80]}").strip("_")
    return base + ".pdf"


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_zotero_config() -> dict[str, Any] | None:
    user_id = (os.environ.get("ZOTERO_USER_ID") or "").strip()
    api_key = (os.environ.get("ZOTERO_API_KEY") or "").strip()
    if not user_id or not api_key:
        return None
    return {
        "user_id": user_id,
        "api_key": api_key,
        "collection_name": (os.environ.get("ZOTERO_COLLECTION_NAME") or "Whole Cell Model").strip(),
        "collection_key": (os.environ.get("ZOTERO_COLLECTION_KEY") or "").strip(),
        "upload_local": env_flag("ZOTERO_UPLOAD_LOCAL", default=False),
        "dry_run": env_flag("ZOTERO_DRY_RUN", default=False),
    }


class ZoteroClient:
    def __init__(self, user_id: str, api_key: str):
        self.user_id = str(user_id)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Zotero-API-Key": api_key,
                "Zotero-API-Version": "3",
                "User-Agent": "WCMGraphBuilder/2.0 (+https://github.com/FreakingPotato/Whole-Cell-Model-Paper-Collection)",
            }
        )

    def _url(self, path: str) -> str:
        return f"{API_ROOT}{path}"

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = self.session.request(method, self._url(path), timeout=60, **kwargs)
        response.raise_for_status()
        return response

    def get_json(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs).json()

    def post_json(self, path: str, payload: Any) -> Any:
        headers = {"Content-Type": "application/json", "Zotero-Write-Token": secrets.token_hex(16)}
        response = self.request("POST", path, json=payload, headers=headers)
        return response.json() if response.content else {}

    def patch_json(self, path: str, payload: Any, *, version: int | str | None = None) -> Any:
        headers = {"Content-Type": "application/json", "Zotero-Write-Token": secrets.token_hex(16)}
        if version is not None:
            headers["If-Unmodified-Since-Version"] = str(version)
        response = self.request("PATCH", path, json=payload, headers=headers)
        return response.json() if response.content else {}

    def iter_collection_items(self, collection_key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        start = 0
        limit = 100
        while True:
            batch = self.get_json(
                f"/users/{self.user_id}/collections/{collection_key}/items",
                params={"format": "json", "limit": limit, "start": start},
            )
            items.extend(batch)
            if len(batch) < limit:
                break
            start += limit
        return items

    def list_collections(self) -> list[dict[str, Any]]:
        return self.get_json(f"/users/{self.user_id}/collections", params={"format": "json", "limit": 100})

    def get_item(self, item_key: str) -> dict[str, Any]:
        return self.get_json(f"/users/{self.user_id}/items/{item_key}")

    def key_info(self) -> dict[str, Any]:
        return self.get_json("/keys/current")

    def download_attachment_file(self, attachment_key: str, target: Path, mtime_ms: int | None = None) -> None:
        response = self.request("GET", f"/users/{self.user_id}/items/{attachment_key}/file", stream=True)
        tmp_path = target.with_suffix(target.suffix + ".zotero-tmp")
        with tmp_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        tmp_path.replace(target)
        if mtime_ms:
            timestamp = int(mtime_ms) / 1000.0
            os.utime(target, (timestamp, timestamp))

    def find_collection(self, collection_name: str, collection_key: str = "") -> dict[str, Any]:
        if collection_key:
            return self.get_json(f"/users/{self.user_id}/collections/{collection_key}")
        collections = self.list_collections()
        for collection in collections:
            if (collection.get("data") or {}).get("name") == collection_name:
                return collection
        raise RuntimeError(f"Could not find Zotero collection '{collection_name}' in user library {self.user_id}.")

    def extract_created_key(self, response_payload: dict[str, Any], index: int = 0) -> str:
        successful = response_payload.get("successful") or {}
        key = str(index)
        payload = successful.get(key) or successful.get(index)
        if isinstance(payload, dict):
            if payload.get("key"):
                return payload["key"]
            data = payload.get("data") or {}
            if data.get("key"):
                return data["key"]
        raise RuntimeError(f"Could not extract created Zotero item key from response: {response_payload}")


def row_lookup(rows: list[dict[str, str]]) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    doi_map = {}
    title_map = {}
    for row in rows:
        doi = (row.get("doi") or "").strip().lower()
        if doi:
            doi_map[doi] = row
        title = normalize_title(row.get("paper_title", ""))
        if title:
            title_map[title] = row
    return doi_map, title_map


def choose_local_target(row: dict[str, str] | None, parent: dict[str, Any], attachment: dict[str, Any], pdf_dir: Path) -> Path:
    attachment_data = attachment.get("data") or {}
    if row is not None:
        existing = sorted(pdf_dir.glob(f"{row['paper_id']}_*.pdf"))
        if existing:
            return existing[0]
        return pdf_dir / target_pdf_name(row["paper_id"], row["paper_title"], row["year"])
    filename = (attachment_data.get("filename") or "").strip()
    if filename and filename.lower().endswith(".pdf"):
        return pdf_dir / filename
    parent_title = (parent.get("data") or {}).get("title") or parent.get("key") or "zotero_attachment"
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", parent_title[:80]).strip("_") or "zotero_attachment"
    return pdf_dir / f"{fallback}.pdf"


def local_matches_remote(path: Path, attachment_data: dict[str, Any]) -> bool:
    if not path.exists():
        return False
    remote_md5 = (attachment_data.get("md5") or "").strip().lower()
    if not remote_md5:
        return False
    try:
        return file_md5(path) == remote_md5
    except Exception:
        return False


def match_remote_parent(parent: dict[str, Any], rows: list[dict[str, str]]) -> dict[str, str] | None:
    doi_map, title_map = row_lookup(rows)
    data = parent.get("data") or {}
    doi = (data.get("DOI") or "").strip().lower()
    if doi and doi in doi_map:
        return doi_map[doi]
    title = normalize_title(data.get("title") or "")
    if title and title in title_map:
        return title_map[title]
    return None


def build_collection_index(items: list[dict[str, Any]], rows: list[dict[str, str]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    parents = {item["key"]: item for item in items if (item.get("data") or {}).get("itemType") != "attachment"}
    attachments_by_parent: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        data = item.get("data") or {}
        if data.get("itemType") != "attachment":
            continue
        parent_key = data.get("parentItem")
        if not parent_key:
            continue
        attachments_by_parent.setdefault(parent_key, []).append(item)
    records = []
    for parent_key, parent in parents.items():
        row = match_remote_parent(parent, rows)
        pdf_attachments = [
            attachment
            for attachment in attachments_by_parent.get(parent_key, [])
            if (attachment.get("data") or {}).get("contentType") == "application/pdf"
        ]
        records.append(
            {
                "parent_key": parent_key,
                "title": (parent.get("data") or {}).get("title", ""),
                "doi": ((parent.get("data") or {}).get("DOI") or "").strip().lower(),
                "paper_id": row["paper_id"] if row else "",
                "attachments": pdf_attachments,
                "parent": parent,
            }
        )
    return parents, records


def load_sync_state(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "enabled": False,
        "configured": False,
        "collection": {},
        "downloads": [],
        "uploads": [],
        "remote_items": [],
        "run_history": [],
    }


def save_sync_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def merge_run_into_state(state: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    downloads = run.get("downloads")
    uploads = run.get("uploads")
    if run.get("mode") == "push":
        downloads = state.get("downloads", []) if not downloads else downloads
    if run.get("mode") == "pull":
        uploads = state.get("uploads", []) if not uploads else uploads
    state.update(
        {
            "enabled": run.get("enabled", False),
            "configured": run.get("configured", False),
            "collection": run.get("collection", {}),
            "permissions": run.get("permissions", state.get("permissions", {})),
            "downloads": downloads or [],
            "uploads": uploads or [],
            "remote_items": run.get("remote_items", []),
            "last_run": run.get("timestamp", ""),
            "last_mode": run.get("mode", ""),
            "upload_local_enabled": run.get("upload_local", False),
            "dry_run": run.get("dry_run", False),
        }
    )
    history = list(state.get("run_history", []))
    history.append(
        {
            "timestamp": run.get("timestamp", ""),
            "mode": run.get("mode", ""),
            "downloads": len(run.get("downloads", [])),
            "uploads": len(run.get("uploads", [])),
            "dry_run": run.get("dry_run", False),
        }
    )
    state["run_history"] = history[-20:]
    return state


def sync_from_zotero(rows: list[dict[str, str]], pdf_dir: Path, state_path: Path) -> dict[str, Any]:
    config = load_zotero_config()
    run = {
        "timestamp": utc_now(),
        "configured": bool(config),
        "enabled": bool(config),
        "mode": "pull",
        "upload_local": bool(config and config["upload_local"]),
        "dry_run": bool(config and config["dry_run"]),
        "collection": {},
        "downloads": [],
        "uploads": [],
        "remote_items": [],
    }
    if not config:
        state = merge_run_into_state(load_sync_state(state_path), run)
        save_sync_state(state_path, state)
        return state

    client = ZoteroClient(config["user_id"], config["api_key"])
    key_info = client.key_info()
    user_access = ((key_info.get("access") or {}).get("user") or {})
    can_write_user_library = bool(user_access.get("write"))
    collection = client.find_collection(config["collection_name"], config["collection_key"])
    collection_key = (collection.get("data") or {}).get("key") or config["collection_key"]
    collection_name = (collection.get("data") or {}).get("name") or config["collection_name"]
    items = client.iter_collection_items(collection_key)
    _parents, records = build_collection_index(items, rows)

    run["collection"] = {"key": collection_key, "name": collection_name, "item_count": len(items)}
    run["permissions"] = {
        "user_library": bool(user_access.get("library")),
        "user_files": bool(user_access.get("files")),
        "user_write": can_write_user_library,
    }
    for record in records:
        attachments = record["attachments"]
        attachment_keys = [attachment["key"] for attachment in attachments]
        first_attachment = attachments[0] if attachments else None
        attachment_data = (first_attachment or {}).get("data") or {}
        local_path = choose_local_target(record.get("parent") and match_remote_parent(record["parent"], rows), record["parent"], first_attachment or {"data": {}}, pdf_dir) if first_attachment else None
        remote_item = {
            "paper_id": record["paper_id"],
            "title": record["title"],
            "doi": record["doi"],
            "parent_key": record["parent_key"],
            "attachment_keys": attachment_keys,
            "has_pdf_attachment": bool(attachments),
            "remote_filename": attachment_data.get("filename", ""),
            "local_file": local_path.name if local_path else "",
        }
        run["remote_items"].append(remote_item)
        if not first_attachment or local_path is None:
            continue
        if local_matches_remote(local_path, attachment_data):
            run["downloads"].append(
                {
                    "paper_id": record["paper_id"],
                    "title": record["title"],
                    "parent_key": record["parent_key"],
                    "attachment_key": first_attachment["key"],
                    "local_file": local_path.name,
                    "status": "up_to_date",
                }
            )
            continue
        if run["dry_run"]:
            run["downloads"].append(
                {
                    "paper_id": record["paper_id"],
                    "title": record["title"],
                    "parent_key": record["parent_key"],
                    "attachment_key": first_attachment["key"],
                    "local_file": local_path.name,
                    "status": "would_download",
                }
            )
            continue
        try:
            client.download_attachment_file(first_attachment["key"], local_path, attachment_data.get("mtime"))
        except requests.HTTPError as exc:
            status = "remote_file_not_available"
            response = getattr(exc, "response", None)
            if response is not None and response.status_code != 404:
                status = f"http_error_{response.status_code}"
            run["downloads"].append(
                {
                    "paper_id": record["paper_id"],
                    "title": record["title"],
                    "parent_key": record["parent_key"],
                    "attachment_key": first_attachment["key"],
                    "local_file": local_path.name,
                    "status": status,
                }
            )
            continue
        run["downloads"].append(
            {
                "paper_id": record["paper_id"],
                "title": record["title"],
                "parent_key": record["parent_key"],
                "attachment_key": first_attachment["key"],
                "local_file": local_path.name,
                "status": "downloaded",
            }
        )

    state = merge_run_into_state(load_sync_state(state_path), run)
    save_sync_state(state_path, state)
    return state


def parent_payload(row: dict[str, str], collection_key: str) -> dict[str, Any]:
    return {
        "itemType": "journalArticle",
        "title": row["paper_title"],
        "creators": [],
        "abstractNote": row.get("summary", ""),
        "publicationTitle": row.get("journal", ""),
        "date": str(row.get("year", "")),
        "DOI": row.get("doi", ""),
        "url": row.get("landing_page_url", ""),
        "accessDate": "",
        "tags": [],
        "collections": [collection_key],
        "relations": {},
    }


def attachment_payload(parent_key: str, filename: str) -> dict[str, Any]:
    return {
        "itemType": "attachment",
        "parentItem": parent_key,
        "linkMode": "imported_file",
        "title": "PDF",
        "accessDate": "",
        "url": "",
        "note": "",
        "tags": [],
        "relations": {},
        "contentType": "application/pdf",
        "charset": "",
        "filename": filename,
        "md5": None,
        "mtime": None,
    }


def authorize_upload(client: ZoteroClient, attachment_key: str, local_path: Path, *, remote_md5: str = "", new_file: bool = True) -> dict[str, Any]:
    local_md5 = file_md5(local_path)
    stat = local_path.stat()
    headers = {}
    if new_file:
        headers["If-None-Match"] = "*"
    else:
        headers["If-Match"] = remote_md5
    response = client.request(
        "POST",
        f"/users/{client.user_id}/items/{attachment_key}/file",
        headers=headers,
        data={
            "md5": local_md5,
            "filename": local_path.name,
            "filesize": str(stat.st_size),
            "mtime": str(int(stat.st_mtime_ns // 1_000_000)),
        },
    )
    if not response.content:
        return {}
    payload = response.json()
    payload["local_md5"] = local_md5
    return payload


def upload_authorized_file(upload_info: dict[str, Any], local_path: Path) -> None:
    if upload_info.get("exists"):
        return
    content = upload_info["prefix"].encode("utf-8") + local_path.read_bytes() + upload_info["suffix"].encode("utf-8")
    response = requests.post(upload_info["url"], data=content, headers={"Content-Type": upload_info["contentType"]}, timeout=120)
    response.raise_for_status()


def register_upload(client: ZoteroClient, attachment_key: str, upload_key: str, *, remote_md5: str = "", new_file: bool = True) -> None:
    headers = {}
    if new_file:
        headers["If-None-Match"] = "*"
    else:
        headers["If-Match"] = remote_md5
    client.request(
        "POST",
        f"/users/{client.user_id}/items/{attachment_key}/file",
        headers=headers,
        data={"upload": upload_key},
    )


def sync_to_zotero(rows: list[dict[str, str]], pdf_dir: Path, state_path: Path) -> dict[str, Any]:
    config = load_zotero_config()
    run = {
        "timestamp": utc_now(),
        "configured": bool(config),
        "enabled": bool(config),
        "mode": "push",
        "upload_local": bool(config and config["upload_local"]),
        "dry_run": bool(config and config["dry_run"]),
        "collection": {},
        "downloads": [],
        "uploads": [],
        "remote_items": [],
    }
    if not config:
        state = merge_run_into_state(load_sync_state(state_path), run)
        save_sync_state(state_path, state)
        return state

    client = ZoteroClient(config["user_id"], config["api_key"])
    key_info = client.key_info()
    user_access = ((key_info.get("access") or {}).get("user") or {})
    can_write_user_library = bool(user_access.get("write"))
    collection = client.find_collection(config["collection_name"], config["collection_key"])
    collection_key = (collection.get("data") or {}).get("key") or config["collection_key"]
    collection_name = (collection.get("data") or {}).get("name") or config["collection_name"]
    items = client.iter_collection_items(collection_key)
    _parents, records = build_collection_index(items, rows)
    parent_by_doi = {record["doi"]: record for record in records if record["doi"]}
    parent_by_title = {normalize_title(record["title"]): record for record in records if record["title"]}

    run["collection"] = {"key": collection_key, "name": collection_name, "item_count": len(items)}
    run["permissions"] = {
        "user_library": bool(user_access.get("library")),
        "user_files": bool(user_access.get("files")),
        "user_write": can_write_user_library,
    }
    run["remote_items"] = [
        {
            "paper_id": record["paper_id"],
            "title": record["title"],
            "doi": record["doi"],
            "parent_key": record["parent_key"],
            "attachment_keys": [attachment["key"] for attachment in record["attachments"]],
            "has_pdf_attachment": bool(record["attachments"]),
        }
        for record in records
    ]

    for row in rows:
        pdf_file = (row.get("pdf_file") or "").strip()
        if not pdf_file:
            continue
        local_path = pdf_dir / pdf_file
        if not local_path.exists():
            continue
        doi = (row.get("doi") or "").strip().lower()
        title_norm = normalize_title(row.get("paper_title", ""))
        record = parent_by_doi.get(doi) or parent_by_title.get(title_norm)
        if record is None:
            if not config["upload_local"]:
                run["uploads"].append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["paper_title"],
                        "local_file": pdf_file,
                        "status": "local_only_upload_disabled",
                    }
                )
                continue
            if not can_write_user_library:
                run["uploads"].append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["paper_title"],
                        "local_file": pdf_file,
                        "status": "user_library_write_permission_missing",
                    }
                )
                continue
            if run["dry_run"]:
                run["uploads"].append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["paper_title"],
                        "local_file": pdf_file,
                        "status": "would_create_parent_and_upload",
                    }
                )
                continue
            try:
                created = client.post_json(f"/users/{client.user_id}/items", [parent_payload(row, collection_key)])
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                run["uploads"].append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["paper_title"],
                        "local_file": pdf_file,
                        "status": f"create_parent_failed_{response.status_code if response is not None else 'http'}",
                    }
                )
                continue
            parent_key = client.extract_created_key(created)
            parent_item = client.get_item(parent_key)
            record = {
                "paper_id": row["paper_id"],
                "title": row["paper_title"],
                "doi": doi,
                "parent_key": parent_key,
                "attachments": [],
                "parent": parent_item,
            }
            parent_by_title[title_norm] = record
            if doi:
                parent_by_doi[doi] = record

        existing_attachment = None
        local_hash = file_md5(local_path)
        for attachment in record["attachments"]:
            attachment_data = attachment.get("data") or {}
            if attachment_data.get("contentType") != "application/pdf":
                continue
            remote_hash = (attachment_data.get("md5") or "").strip().lower()
            if remote_hash and remote_hash == local_hash:
                existing_attachment = attachment
                break
        if existing_attachment is not None:
            run["uploads"].append(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "parent_key": record["parent_key"],
                    "attachment_key": existing_attachment["key"],
                    "local_file": pdf_file,
                    "status": "already_synced",
                }
            )
            continue

        if not can_write_user_library:
            run["uploads"].append(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "parent_key": record["parent_key"] if record else "",
                    "local_file": pdf_file,
                    "status": "user_library_write_permission_missing",
                }
            )
            continue

        if not config["upload_local"]:
            run["uploads"].append(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "parent_key": record["parent_key"],
                    "local_file": pdf_file,
                    "status": "upload_disabled",
                }
            )
            continue

        if run["dry_run"]:
            run["uploads"].append(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "parent_key": record["parent_key"],
                    "local_file": pdf_file,
                    "status": "would_upload_pdf",
                }
            )
            continue

        attachment_to_update = None
        for attachment in record["attachments"]:
            attachment_data = attachment.get("data") or {}
            if attachment_data.get("contentType") == "application/pdf":
                attachment_to_update = attachment
                break

        if attachment_to_update is None:
            try:
                created = client.post_json(
                    f"/users/{client.user_id}/items",
                    [attachment_payload(record["parent_key"], local_path.name)],
                )
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                run["uploads"].append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["paper_title"],
                        "parent_key": record["parent_key"],
                        "local_file": pdf_file,
                        "status": f"create_attachment_failed_{response.status_code if response is not None else 'http'}",
                    }
                )
                continue
            attachment_key = client.extract_created_key(created)
            try:
                upload_info = authorize_upload(client, attachment_key, local_path, new_file=True)
                if not upload_info.get("exists"):
                    upload_authorized_file(upload_info, local_path)
                    register_upload(client, attachment_key, upload_info["uploadKey"], new_file=True)
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                run["uploads"].append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["paper_title"],
                        "parent_key": record["parent_key"],
                        "attachment_key": attachment_key,
                        "local_file": pdf_file,
                        "status": f"upload_new_attachment_failed_{response.status_code if response is not None else 'http'}",
                    }
                )
                continue
            run["uploads"].append(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "parent_key": record["parent_key"],
                    "attachment_key": attachment_key,
                    "local_file": pdf_file,
                    "status": "uploaded_new_attachment",
                }
            )
        else:
            attachment_key = attachment_to_update["key"]
            remote_md5 = ((attachment_to_update.get("data") or {}).get("md5") or "").strip().lower()
            if not remote_md5:
                run["uploads"].append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["paper_title"],
                        "parent_key": record["parent_key"],
                        "attachment_key": attachment_key,
                        "local_file": pdf_file,
                        "status": "remote_attachment_missing_md5_skipped",
                    }
                )
                continue
            try:
                upload_info = authorize_upload(client, attachment_key, local_path, remote_md5=remote_md5, new_file=False)
                if not upload_info.get("exists"):
                    upload_authorized_file(upload_info, local_path)
                    register_upload(client, attachment_key, upload_info["uploadKey"], remote_md5=remote_md5, new_file=False)
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                run["uploads"].append(
                    {
                        "paper_id": row["paper_id"],
                        "title": row["paper_title"],
                        "parent_key": record["parent_key"],
                        "attachment_key": attachment_key,
                        "local_file": pdf_file,
                        "status": f"update_attachment_failed_{response.status_code if response is not None else 'http'}",
                    }
                )
                continue
            run["uploads"].append(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "parent_key": record["parent_key"],
                    "attachment_key": attachment_key,
                    "local_file": pdf_file,
                    "status": "updated_existing_attachment",
                }
            )

    state = merge_run_into_state(load_sync_state(state_path), run)
    save_sync_state(state_path, state)
    return state
