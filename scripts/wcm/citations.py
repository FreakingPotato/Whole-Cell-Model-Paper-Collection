"""Citation-count lookups with an OpenAlex → Semantic Scholar fallback.

Adapted from ``Knowledge_Graph_Agent/scripts/kgbuild/citations.py``
(https://github.com/FreakingPotato/Knowledge_Graph_Agent). The default
``enrich`` stage already pulls ``cited_by_count`` from OpenAlex, but it returns
no count when OpenAlex hasn't indexed the work yet (preprints, brand-new
papers, some niche journals). This module adds a polite Semantic Scholar
fallback so those rows still get a count, and persists the result in
``metadata/citation_counts.json`` so it can be layered in at export time
without a DB schema change.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

import requests

from .models import METADATA_DIR


CITATION_COUNTS_JSON = METADATA_DIR / "citation_counts.json"

USER_AGENT = "wcm-graph-builder/1.0 (mailto: ke.ding@anu.edu.au)"
HTTP_TIMEOUT = 10.0
DEFAULT_RATE_SEC = 1.0

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"

SOURCE_OPENALEX = "openalex"
SOURCE_SEM_SCHOLAR = "semantic_scholar"
SOURCE_UNAVAILABLE = "unavailable"


@dataclass
class CitationLookupResult:
    paper_id: str
    count: int | None = None
    source: str = SOURCE_UNAVAILABLE
    status: str = STATUS_UNAVAILABLE
    error: str | None = None
    api_url: str | None = None
    fetched_at: str | None = None


_LAST_CALL_AT: dict[str, float] = {}


def _polite_sleep(host: str, rate_sec: float) -> None:
    if rate_sec <= 0:
        return
    last = _LAST_CALL_AT.get(host)
    now = time.monotonic()
    if last is not None and (now - last) < rate_sec:
        time.sleep(rate_sec - (now - last))
    _LAST_CALL_AT[host] = time.monotonic()


def _http_get_json(url: str, host: str, rate_sec: float,
                   session: requests.Session | None = None
                   ) -> tuple[int, dict[str, Any] | None, str | None]:
    _polite_sleep(host, rate_sec)
    sess = session or requests
    try:
        resp = sess.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return 0, None, "timeout"
    except requests.exceptions.ConnectionError as exc:
        return 0, None, f"network_error: {exc.__class__.__name__}"
    except requests.exceptions.RequestException as exc:
        return 0, None, f"request_error: {exc.__class__.__name__}"
    if resp.status_code != 200:
        return resp.status_code, None, f"http_{resp.status_code}"
    try:
        return 200, resp.json(), None
    except ValueError:
        return resp.status_code, None, "invalid_json"


def _openalex_url(doi: str | None, arxiv_id: str | None) -> str | None:
    if doi:
        return f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='/.')}"
    if arxiv_id:
        return ("https://api.openalex.org/works/https://doi.org/"
                f"10.48550/arXiv.{quote(arxiv_id, safe='.')}")
    return None


def _semscholar_url(doi: str | None, arxiv_id: str | None) -> str | None:
    base = "https://api.semanticscholar.org/graph/v1/paper"
    if doi:
        return f"{base}/DOI:{quote(doi, safe='/.')}?fields=citationCount"
    if arxiv_id:
        return f"{base}/arXiv:{quote(arxiv_id, safe='.')}?fields=citationCount"
    return None


def lookup(paper_id: str, doi: str | None = None, arxiv_id: str | None = None,
           *, rate_sec: float = DEFAULT_RATE_SEC,
           session: requests.Session | None = None) -> CitationLookupResult:
    """OpenAlex first, then Semantic Scholar. Both unauthenticated."""
    doi = (doi or "").strip().lower() or None
    arxiv_id = (arxiv_id or "").strip() or None
    if not (doi or arxiv_id):
        return CitationLookupResult(paper_id=paper_id, error="no_identifier")

    oa_url = _openalex_url(doi, arxiv_id)
    if oa_url:
        status, payload, err = _http_get_json(oa_url, "api.openalex.org", rate_sec, session)
        if status == 200 and payload is not None and isinstance(payload.get("cited_by_count"), int):
            return CitationLookupResult(
                paper_id=paper_id,
                count=int(payload["cited_by_count"]),
                source=SOURCE_OPENALEX,
                status=STATUS_OK,
                api_url=oa_url,
            )
        oa_error = err or f"http_{status}"
    else:
        oa_error = "no_identifier_for_openalex"

    s2_url = _semscholar_url(doi, arxiv_id)
    if s2_url:
        status, payload, err = _http_get_json(s2_url, "api.semanticscholar.org", rate_sec, session)
        if status == 200 and payload is not None and isinstance(payload.get("citationCount"), int):
            return CitationLookupResult(
                paper_id=paper_id,
                count=int(payload["citationCount"]),
                source=SOURCE_SEM_SCHOLAR,
                status=STATUS_OK,
                api_url=s2_url,
            )
        s2_error = err or f"http_{status}"
    else:
        s2_error = "no_identifier_for_semantic_scholar"

    return CitationLookupResult(
        paper_id=paper_id,
        error=f"openalex={oa_error}; semantic_scholar={s2_error}",
    )


def load_cache() -> dict[str, dict[str, Any]]:
    if not CITATION_COUNTS_JSON.is_file():
        return {}
    try:
        data = json.loads(CITATION_COUNTS_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    CITATION_COUNTS_JSON.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def refresh_all(conn: sqlite3.Connection, *, rate_sec: float = DEFAULT_RATE_SEC,
                only_missing: bool = False) -> dict[str, Any]:
    """Refresh citations for every paper that has a DOI.

    With ``only_missing=True``, skip papers that already have a successful
    OpenAlex/Semantic Scholar entry in the cache. Returns a small summary.
    """
    rows = conn.execute(
        "SELECT paper_id, doi, openalex_id FROM papers ORDER BY paper_id"
    ).fetchall()
    cache = load_cache()
    session = requests.Session()
    summary = {"checked": 0, "ok": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    now = __import__("datetime").datetime.now(__import__("datetime").UTC)\
        .replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for row in rows:
        paper_id = row["paper_id"]
        doi = (row["doi"] or "").strip().lower()
        if not doi:
            summary["skipped"] += 1
            continue
        if only_missing and cache.get(paper_id, {}).get("status") == STATUS_OK:
            summary["unchanged"] += 1
            continue
        result = lookup(paper_id, doi=doi, rate_sec=rate_sec, session=session)
        result.fetched_at = now
        summary["checked"] += 1
        if result.status == STATUS_OK:
            summary["ok"] += 1
            cache[paper_id] = asdict(result)
        else:
            summary["failed"] += 1
            existing = cache.get(paper_id, {})
            if existing.get("status") != STATUS_OK:
                cache[paper_id] = asdict(result)
    save_cache(cache)
    return summary


def counts_by_paper_id() -> dict[str, dict[str, Any]]:
    """Return ``{paper_id: {"count": int, "source": str}}`` for cached OK rows."""
    out: dict[str, dict[str, Any]] = {}
    for paper_id, payload in load_cache().items():
        if payload.get("status") == STATUS_OK and payload.get("count") is not None:
            out[paper_id] = {
                "count": int(payload["count"]),
                "source": payload.get("source", SOURCE_OPENALEX),
            }
    return out
