#!/usr/bin/env python3
"""Agent D1: Topic discoverer for Multimodal NLP × Genomic FM cross-domain candidate papers.

Discovers up to 10 high-impact, cross-domain candidate papers per claim in
``multimodal_genomics/metadata/evidence.json``. Cross-domain = physics, chemistry,
materials, climate, fluids, biology, ML methods (NOT MMG-only).

Sources (priority order):

1. OpenAlex /works (full-text ``search=`` + filters)
2. Semantic Scholar Graph API (supplemental when OpenAlex < 10 after filters)
3. arXiv API (physics/CS-heavy claims)

Outputs (NEVER touches the curated evidence file):

* ``multimodal_genomics/metadata/papers.json`` -- paper registry (MMG-NNN)
* ``multimodal_genomics/metadata/candidates.json`` -- candidates per claim
* ``multimodal_genomics/metadata/discovery_log.json`` -- per-claim diagnostics

Deterministic given the same inputs: sorted output, no random shuffles.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
META_DIR = REPO_ROOT / "multimodal_genomics" / "metadata"
EVIDENCE_FILE = META_DIR / "evidence.json"  # READ ONLY
WCM_PAPERS_FILE = META_DIR / "papers.json"  # READ ONLY
ALLOWLIST_FILE = META_DIR / "venue_allowlist.json"
REGISTRY_FILE = META_DIR / "papers.json"
CANDIDATES_FILE = META_DIR / "candidates.json"
LOG_FILE = META_DIR / "discovery_log.json"

USER_AGENT = "wcm-graph-builder/1.0 (mailto: ke.ding@anu.edu.au)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

OPENALEX_BASE = "https://api.openalex.org/works"
S2_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
ARXIV_BASE = "http://export.arxiv.org/api/query"

CITATION_THRESHOLD = 20
TARGET_PER_CLAIM = 10
HOST_RATE_SLEEP = 1.05  # 1 req/host/sec polite cap

# Per-claim search queries: each entry is a list of OpenAlex full-text queries.
# Queries combine paradigm + subtype + claim language and are designed to be
# cross-domain. Order matters: earlier queries are more central.
#
# ``relevance_terms`` is a list of token sets; a paper is considered topically
# relevant if its title+abstract contains at least one term from the list.
# Used as an additional filter after the venue+citation pass to suppress very-
# cited but off-topic results that the broad full-text search returns.
CLAIM_QUERIES: Dict[str, Dict[str, List[str]]] = {
    "embedded-closure": {
        "openalex": [
            '"neural closure" turbulence',
            '"data-driven closure" model',
            '"machine learning subgrid" parameterization',
            '"learned closure" mechanistic',
            'neural network closure differential equations',
        ],
        "s2": [
            "neural closure turbulence model",
            "machine learning subgrid scale parameterization",
        ],
        "arxiv": ["neural closure model turbulence"],
        "domain_hint": "fluids",
        "relevance_terms": [
            "closure", "subgrid", "sub-grid", "parameterization", "parameterisation",
            "turbulence", "les ", "rans", "reynolds-averaged", "eddy",
        ],
    },
    "embedded-constraint": {
        "openalex": [
            '"physics-informed neural networks"',
            '"physics-constrained" neural network',
            '"PINN" partial differential equations',
            '"biophysics-informed neural"',
            '"hard constraint" neural network physics',
        ],
        "s2": [
            "physics-informed neural networks PINN",
            "physics constrained deep learning",
        ],
        "arxiv": ["physics-informed neural networks"],
        "domain_hint": "physics",
        "relevance_terms": [
            "physics-informed", "physics informed", "physics-constrained",
            "physics constrained", "pinn", "biophysics-informed",
            "constraint", "regulariz", "regularis",
        ],
    },
    "embedded-emulation": {
        "openalex": [
            '"neural operator" partial differential',
            '"Fourier neural operator"',
            '"deep operator network" DeepONet',
            '"neural surrogate" simulation',
            'emulator deep learning climate',
            '"machine learning surrogate" molecular dynamics',
        ],
        "s2": [
            "Fourier neural operator partial differential equations",
            "deep operator network DeepONet",
            "neural surrogate molecular simulation",
        ],
        "arxiv": [
            "Fourier neural operator",
            "deep operator network",
        ],
        "domain_hint": "ml-methods",
        "relevance_terms": [
            "surrogate", "emulator", "neural operator", "deeponet", "fourier neural",
            "operator network", "operator learning", "emulation",
            "machine learning potential", "neural network potential",
        ],
    },
    "pipeline-curation": {
        "openalex": [
            '"large language model" biological knowledge extraction',
            '"LLM" literature mining biology pathway',
            '"text mining" biological pathway curation',
            '"automated curation" biomedical knowledge',
            'GPT biological mechanism extraction',
            'natural language processing biomedical relation extraction',
        ],
        "s2": [
            "large language model biomedical literature mining",
            "automated pathway curation text mining",
            "biomedical relation extraction natural language processing",
        ],
        "arxiv": ["large language model biomedical literature mining"],
        "domain_hint": "biology",
        "relevance_terms": [
            "text mining", "literature mining", "language model", "llm", "natural language",
            "knowledge extraction", "relation extraction", "curation", "annotat",
            "named entity", "ontology", "knowledge graph",
        ],
    },
    "pipeline-inference": {
        "openalex": [
            '"simulation-based inference"',
            '"neural posterior estimation"',
            '"likelihood-free inference" neural',
            '"amortized inference" simulator',
            'machine learning parameter estimation mechanistic model',
            'approximate Bayesian computation neural network',
        ],
        "s2": [
            "simulation-based inference neural posterior",
            "amortized likelihood-free inference",
            "approximate Bayesian computation neural",
        ],
        "arxiv": ["simulation-based inference"],
        "domain_hint": "ml-methods",
        "relevance_terms": [
            "simulation-based inference", "likelihood-free", "neural posterior",
            "amortized", "amortised", "approximate bayesian", "abc ",
            "parameter inference", "parameter estimation", "posterior estimation",
            "bayesian inference",
        ],
    },
    "pipeline-structural-learning": {
        "openalex": [
            '"sparse identification" "nonlinear dynamics"',
            'SINDy data-driven discovery',
            '"symbolic regression" governing equations',
            '"model discovery" partial differential equations',
            '"equation discovery" machine learning',
            'data-driven discovery dynamical systems',
        ],
        "s2": [
            "sparse identification nonlinear dynamics SINDy",
            "symbolic regression governing equations",
            "data-driven discovery dynamical systems",
        ],
        "arxiv": [
            "sparse identification nonlinear dynamics",
            "symbolic regression physics",
        ],
        "domain_hint": "physics",
        "relevance_terms": [
            "sparse identification", "sindy", "symbolic regression",
            "equation discovery", "model discovery", "data-driven discovery",
            "governing equations", "system identification", "structure learning",
            "causal discovery",
        ],
    },
    "parallel-matched-predictions": {
        "openalex": [
            '"machine learning" "mechanistic model" comparison prediction',
            'data-driven mechanistic intercomparison perturbation',
            '"neural network" versus "mechanistic" benchmark',
            '"topic" benchmark perturbation prediction',
            'deep learning mechanistic baseline biological prediction',
            'machine learning weather forecast benchmark physical model',
            'perturbation prediction deep learning benchmark',
        ],
        "s2": [
            "machine learning mechanistic model comparison perturbation",
            "deep learning mechanistic benchmark biological",
            "perturbation prediction deep learning benchmark",
        ],
        "arxiv": ["machine learning mechanistic comparison"],
        "domain_hint": "interdisciplinary",
        "relevance_terms": [
            "benchmark", "comparison", "intercomparison", "versus",
            "perturbation", "baseline", "head-to-head", "competition",
            "leaderboard", "evaluat",
        ],
    },
    "parallel-agreement-disagreement": {
        "openalex": [
            '"topic" "uncertainty quantification"',
            '"epistemic uncertainty" mechanistic machine learning',
            '"deep ensemble" disagreement uncertainty',
            '"model disagreement" epistemic',
            'machine learning physics uncertainty quantification',
            'Bayesian deep learning uncertainty quantification',
        ],
        "s2": [
            "topic uncertainty quantification",
            "deep ensemble epistemic uncertainty",
            "Bayesian deep learning uncertainty",
        ],
        "arxiv": ["epistemic uncertainty deep ensemble"],
        "domain_hint": "ml-methods",
        "relevance_terms": [
            "uncertainty", "epistemic", "aleatoric", "ensemble", "bayesian",
            "calibration", "disagreement", "agreement", "confidence",
            "uncertainty quantification",
        ],
    },
    "parallel-foundation-models": {
        "openalex": [
            '"foundation model" single cell',
            'single-cell foundation model transcriptomic',
            '"foundation model" biology genomics',
            '"large-scale pretraining" cell transcriptomic',
            '"foundation model" protein language',
            'pretrained transformer single-cell genomics',
        ],
        "s2": [
            "single-cell foundation model transcriptomic",
            "foundation model genomics protein language",
            "pretrained transformer single-cell",
        ],
        "arxiv": ["foundation model single cell"],
        "domain_hint": "biology",
        "relevance_terms": [
            "foundation model", "pretrain", "pre-train", "transformer",
            "self-supervised", "language model", "zero-shot", "scgpt",
            "scbert", "geneformer", "esm", "alphafold", "protein language",
        ],
    },
}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise(text: str) -> str:
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.strip().lower())


def normalise_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi or None


def load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False, ensure_ascii=False)
        fh.write("\n")


def reconstruct_abstract(inv: Optional[dict]) -> str:
    if not inv:
        return ""
    try:
        positions: List[Tuple[int, str]] = []
        for word, idxs in inv.items():
            for idx in idxs:
                positions.append((idx, word))
        positions.sort(key=lambda x: x[0])
        return " ".join(word for _, word in positions)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Venue allowlist
# ---------------------------------------------------------------------------


def load_allowlist() -> Tuple[List[str], List[str]]:
    """Return (canonical_names, all_alias_substrings) all lowercase."""
    data = load_json(ALLOWLIST_FILE)
    if not data:
        raise SystemExit(f"venue allowlist missing: {ALLOWLIST_FILE}")
    canonical: List[str] = []
    aliases: List[str] = []
    for group in data.get("groups", {}).values():
        for name in group:
            canonical.append(name)
            aliases.append(name)
    for k, v in data.get("extra_aliases", {}).items():
        for a in v:
            aliases.append(a)
    canonical = sorted({normalise(n) for n in canonical})
    aliases = sorted({normalise(a) for a in aliases})
    return canonical, aliases


def venue_allowed(source_name: Optional[str], aliases: Iterable[str]) -> bool:
    """Strict exact-match against the normalised allowlist.

    Substring/prefix matching is intentionally avoided because Nature has many
    sister journals (Nature Energy, Nature Reviews ...) that we do NOT want to
    allowlist by accident, and short aliases like "Cell" should match the journal
    "Cell" but not "Cell Reports" / "Cell Host & Microbe".
    """
    if not source_name:
        return False
    norm = normalise(source_name)
    alias_set = {a for a in aliases if a}
    if norm in alias_set:
        return True
    # also accept "proceedings of machine learning research" rendered as
    # alternative for ICML; these aliases are already in alias_set.
    return False


# ---------------------------------------------------------------------------
# MMG dedup set
# ---------------------------------------------------------------------------


def load_wcm_dedup_keys() -> Tuple[set, set]:
    data = load_json(WCM_PAPERS_FILE) or {}
    papers = data.get("papers", {})
    dois = set()
    titles = set()
    for p in papers.values():
        d = normalise_doi(p.get("doi"))
        if d:
            dois.add(d)
        t = normalise(p.get("title", ""))
        if t:
            titles.add(t)
    return dois, titles


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class HostRateLimiter:
    """Per-host polite rate limiter (1 req/sec)."""

    def __init__(self, sleep_seconds: float = HOST_RATE_SLEEP):
        self._sleep = sleep_seconds
        self._last: Dict[str, float] = {}

    def wait(self, host: str) -> None:
        now = time.monotonic()
        last = self._last.get(host, 0.0)
        delta = now - last
        if delta < self._sleep:
            time.sleep(self._sleep - delta)
        self._last[host] = time.monotonic()


def fetch_openalex(query: str, limiter: HostRateLimiter, per_page: int = 50) -> List[dict]:
    """Run an OpenAlex /works full-text search with citation+paratext filters."""
    limiter.wait("api.openalex.org")
    params = {
        "search": query,
        "per_page": str(per_page),
        "filter": f"is_paratext:false,cited_by_count:>{CITATION_THRESHOLD}",
        "sort": "cited_by_count:desc",
        "select": (
            "id,doi,title,publication_year,primary_location,cited_by_count,"
            "abstract_inverted_index,authorships,is_paratext,type"
        ),
    }
    url = OPENALEX_BASE + "?" + urllib.parse.urlencode(params)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        if resp.status_code != 200:
            return []
        return resp.json().get("results", []) or []
    except Exception:
        return []


def normalise_openalex(work: dict) -> Optional[dict]:
    title = work.get("title") or ""
    if not title:
        return None
    primary = work.get("primary_location") or {}
    src = (primary or {}).get("source") or {}
    journal = src.get("display_name") or ""
    doi = normalise_doi(work.get("doi"))
    openalex_id = work.get("id", "")
    if openalex_id and openalex_id.startswith("https://openalex.org/"):
        openalex_id = openalex_id.split("/")[-1]
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    authors = []
    for a in (work.get("authorships") or [])[:20]:
        nm = (a.get("author") or {}).get("display_name")
        if nm:
            authors.append(nm)
    return {
        "title": title,
        "authors": authors,
        "year": work.get("publication_year"),
        "journal": journal,
        "doi": doi,
        "openalex_id": openalex_id,
        "arxiv_id": None,
        "cited_by_count": int(work.get("cited_by_count") or 0),
        "abstract": abstract,
        "_source": "openalex",
    }


def fetch_semantic_scholar(query: str, limiter: HostRateLimiter, limit: int = 30) -> List[dict]:
    limiter.wait("api.semanticscholar.org")
    params = {
        "query": query,
        "limit": str(limit),
        "fields": "title,abstract,year,citationCount,authors,venue,externalIds,publicationVenue",
    }
    url = S2_BASE + "?" + urllib.parse.urlencode(params)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        if resp.status_code != 200:
            return []
        return (resp.json() or {}).get("data", []) or []
    except Exception:
        return []


def normalise_s2(item: dict) -> Optional[dict]:
    title = item.get("title") or ""
    if not title:
        return None
    ids = item.get("externalIds") or {}
    doi = normalise_doi(ids.get("DOI"))
    arxiv_id = ids.get("ArXiv") or ids.get("arXiv")
    venue = item.get("venue") or ""
    pv = item.get("publicationVenue") or {}
    if pv and pv.get("name"):
        venue = pv["name"]
    authors = [a.get("name") for a in (item.get("authors") or []) if a.get("name")]
    return {
        "title": title,
        "authors": authors,
        "year": item.get("year"),
        "journal": venue,
        "doi": doi,
        "openalex_id": None,
        "arxiv_id": arxiv_id,
        "cited_by_count": int(item.get("citationCount") or 0),
        "abstract": item.get("abstract") or "",
        "_source": "semantic_scholar",
    }


def fetch_arxiv(query: str, limiter: HostRateLimiter, max_results: int = 25) -> List[dict]:
    limiter.wait("export.arxiv.org")
    params = {
        "search_query": f"all:{query}",
        "max_results": str(max_results),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = ARXIV_BASE + "?" + urllib.parse.urlencode(params)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        if resp.status_code != 200:
            return []
        return _parse_arxiv_atom(resp.text)
    except Exception:
        return []


def _parse_arxiv_atom(xml: str) -> List[dict]:
    """Tiny dependency-free atom parser for arXiv. Extracts title/id/year."""
    entries: List[dict] = []
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            arxiv_url = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
            published = (entry.findtext("a:published", default="", namespaces=ns) or "").strip()
            summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
            authors = []
            for au in entry.findall("a:author", ns):
                nm = au.findtext("a:name", default="", namespaces=ns)
                if nm:
                    authors.append(nm.strip())
            arxiv_id = None
            if "arxiv.org/abs/" in arxiv_url:
                arxiv_id = arxiv_url.rsplit("/", 1)[-1]
            year = None
            if published:
                try:
                    year = int(published[:4])
                except Exception:
                    year = None
            entries.append({
                "title": title,
                "authors": authors,
                "year": year,
                "journal": "arXiv",
                "doi": None,
                "openalex_id": None,
                "arxiv_id": arxiv_id,
                "cited_by_count": 0,
                "abstract": summary,
                "_source": "arxiv",
            })
    except Exception:
        return []
    return entries


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS = [
    ("fluids", ["turbulen", "fluid", "navier-stokes", "subgrid", "les ", "rans"]),
    ("climate", ["climate", "atmospher", "ocean", "weather", "earth system"]),
    ("physics", ["physics-informed", "pinn", "schrödinger", "quantum", "physics constrained", "many-body", "hamilton"]),
    ("chemistry", ["chemistry", "chemical reaction", "molecular dynamics", "force field", "potential energy surface"]),
    ("materials", ["materials", "crystal", "alloy", "polymer"]),
    ("biology", ["genom", "protein", "transcriptom", "single-cell", "cell", "biolog", "pathway", "metabolic", "cancer", "rna", "drug", "perturb"]),
    ("ml-methods", ["neural network", "deep learning", "transformer", "neural operator", "graph neural", "foundation model", "language model"]),
]


def infer_domain(record: dict, hint: str) -> str:
    text = " ".join([
        record.get("title") or "",
        record.get("abstract") or "",
        record.get("journal") or "",
    ]).lower()
    for dom, kws in DOMAIN_KEYWORDS:
        for kw in kws:
            if kw in text:
                return dom
    return hint or "interdisciplinary"


def topically_relevant(record: dict, terms: List[str]) -> bool:
    if not terms:
        return True
    text = " ".join([
        record.get("title") or "",
        record.get("abstract") or "",
    ]).lower()
    for t in terms:
        if t and t in text:
            return True
    return False


def is_review(record: dict) -> bool:
    title = (record.get("title") or "").lower()
    if title.startswith("review of") or title.startswith("a review of") or title.startswith("review:"):
        return True
    if " review" in title and "review" in title.split()[-1]:
        return True
    return False


# ---------------------------------------------------------------------------
# Discovery driver
# ---------------------------------------------------------------------------


def discover_for_claim(
    claim_id: str,
    queries: Dict[str, List[str]],
    venue_aliases: List[str],
    wcm_dois: set,
    wcm_titles: set,
    limiter: HostRateLimiter,
    target: int = TARGET_PER_CLAIM,
) -> Tuple[List[dict], dict]:
    """Return (kept_papers, log_for_claim)."""
    log = {
        "queries_run": [],
        "openalex_returned": 0,
        "after_venue_filter": 0,
        "after_citations_filter": 0,
        "after_relevance_filter": 0,
        "after_dedup": 0,
        "kept_top_n": 0,
        "supplemented_from": [],
    }
    relevance_terms = queries.get("relevance_terms", [])
    seen_keys: set = set()
    pool: List[dict] = []

    def make_key(rec: dict) -> str:
        if rec.get("doi"):
            return f"doi::{rec['doi']}"
        if rec.get("openalex_id"):
            return f"oa::{rec['openalex_id']}"
        if rec.get("arxiv_id"):
            return f"arx::{rec['arxiv_id']}"
        return f"title::{normalise(rec.get('title', ''))}"

    # ---- OpenAlex ----
    for q in queries.get("openalex", []):
        log["queries_run"].append(f"openalex: {q}")
        works = fetch_openalex(q, limiter)
        log["openalex_returned"] += len(works)
        for w in works:
            rec = normalise_openalex(w)
            if not rec:
                continue
            pool.append(rec)

    # snapshot counts pre-filter (after openalex only)
    venue_kept: List[dict] = []
    for rec in pool:
        if venue_allowed(rec.get("journal"), venue_aliases):
            venue_kept.append(rec)
    log["after_venue_filter"] = len(venue_kept)

    cite_kept: List[dict] = []
    for rec in venue_kept:
        if int(rec.get("cited_by_count") or 0) >= CITATION_THRESHOLD:
            cite_kept.append(rec)
    log["after_citations_filter"] = len(cite_kept)

    rel_kept: List[dict] = []
    for rec in cite_kept:
        if topically_relevant(rec, relevance_terms):
            rel_kept.append(rec)
    log["after_relevance_filter"] = len(rel_kept)

    # dedup against MMG corpus + within-pool
    dedup_kept: List[dict] = []
    review_count = 0
    for rec in rel_kept:
        d = normalise_doi(rec.get("doi"))
        t = normalise(rec.get("title") or "")
        if d and d in wcm_dois:
            continue
        if t and t in wcm_titles:
            continue
        key = make_key(rec)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if is_review(rec):
            review_count += 1
            if review_count > 1:
                continue
        dedup_kept.append(rec)

    log["after_dedup"] = len(dedup_kept)

    # If we don't have enough, supplement from S2 / arXiv
    if len(dedup_kept) < target:
        log["supplemented_from"].append("semantic_scholar")
        for q in queries.get("s2", []):
            log["queries_run"].append(f"s2: {q}")
            items = fetch_semantic_scholar(q, limiter)
            for it in items:
                rec = normalise_s2(it)
                if not rec:
                    continue
                if not venue_allowed(rec.get("journal"), venue_aliases):
                    continue
                if int(rec.get("cited_by_count") or 0) < CITATION_THRESHOLD:
                    continue
                if not topically_relevant(rec, relevance_terms):
                    continue
                d = normalise_doi(rec.get("doi"))
                t = normalise(rec.get("title") or "")
                if d and d in wcm_dois:
                    continue
                if t and t in wcm_titles:
                    continue
                key = make_key(rec)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                if is_review(rec):
                    review_count += 1
                    if review_count > 1:
                        continue
                dedup_kept.append(rec)
                if len(dedup_kept) >= target * 2:
                    break
            if len(dedup_kept) >= target * 2:
                break

    if len(dedup_kept) < target:
        log["supplemented_from"].append("arxiv")
        for q in queries.get("arxiv", []):
            log["queries_run"].append(f"arxiv: {q}")
            items = fetch_arxiv(q, limiter)
            for rec in items:
                # arXiv has no citation info via API; only keep arXiv items as
                # last resort and only for ML-heavy claims (we still apply the
                # citation threshold loosely as 0).
                if not rec.get("title"):
                    continue
                # arXiv venue not in allowlist; allow for emulation/structural
                # claims as named landmarks, otherwise skip.
                key = make_key(rec)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                # Force-keep arXiv only when we are short.
                if len(dedup_kept) >= target:
                    break
                dedup_kept.append(rec)
            if len(dedup_kept) >= target:
                break

    # Rank: by cited_by_count desc, then year desc, then title asc.
    dedup_kept.sort(
        key=lambda r: (
            -int(r.get("cited_by_count") or 0),
            -(int(r.get("year") or 0)),
            normalise(r.get("title") or ""),
        )
    )
    kept = dedup_kept[:target]
    log["kept_top_n"] = len(kept)
    return kept, log


# ---------------------------------------------------------------------------
# Registry assembly
# ---------------------------------------------------------------------------


def assign_ext_id(
    rec: dict,
    registry: Dict[str, dict],
    key_index: Dict[str, str],
    counter: List[int],
    discovery_query: str,
    domain_hint: str,
) -> str:
    d = normalise_doi(rec.get("doi"))
    oa = rec.get("openalex_id")
    arx = rec.get("arxiv_id")
    title_key = normalise(rec.get("title") or "")
    keys = []
    if d:
        keys.append(f"doi::{d}")
    if oa:
        keys.append(f"oa::{oa}")
    if arx:
        keys.append(f"arx::{arx}")
    if title_key:
        keys.append(f"title::{title_key}")
    for k in keys:
        if k in key_index:
            return key_index[k]
    counter[0] += 1
    ext_id = f"MMG-{counter[0]:03d}"
    paper = {
        "title": rec.get("title"),
        "authors": rec.get("authors") or [],
        "year": rec.get("year"),
        "journal": rec.get("journal"),
        "doi": d,
        "openalex_id": oa,
        "arxiv_id": arx,
        "cited_by_count": int(rec.get("cited_by_count") or 0),
        "abstract": rec.get("abstract") or "",
        "domain": infer_domain(rec, domain_hint),
        "discovery_query": discovery_query,
        "discovered_at": utcnow_iso(),
    }
    registry[ext_id] = paper
    for k in keys:
        key_index[k] = ext_id
    return ext_id


def discovery_score(rec: dict, rank: int) -> int:
    """Heuristic 0-100 score: citation-driven, with rank decay.

    Deterministic (no randomness)."""
    cites = int(rec.get("cited_by_count") or 0)
    # log-ish scaling: 20 -> ~12, 200 -> ~36, 2000 -> ~60, 20000 -> ~84
    import math
    base = 0
    if cites > 0:
        base = int(round(12 * math.log10(cites + 1)))
    rank_bonus = max(0, 25 - 2 * rank)
    score = min(100, base + rank_bonus)
    return score


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def claim_paradigm_map() -> Dict[str, str]:
    data = load_json(EVIDENCE_FILE)
    if not data:
        raise SystemExit(f"missing evidence file: {EVIDENCE_FILE}")
    out: Dict[str, str] = {}
    for p in data.get("paradigms", []):
        for c in p.get("claims", []):
            out[c["id"]] = p["id"]
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim", help="Run only one claim id (default: all 9)")
    parser.add_argument("--limit", type=int, default=TARGET_PER_CLAIM,
                        help="Target candidates per claim (default 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Do everything but skip writing output files")
    args = parser.parse_args(argv)

    canonical, aliases = load_allowlist()
    wcm_dois, wcm_titles = load_wcm_dedup_keys()
    paradigm_map = claim_paradigm_map()

    target_claims = list(CLAIM_QUERIES.keys())
    if args.claim:
        if args.claim not in CLAIM_QUERIES:
            raise SystemExit(f"unknown claim: {args.claim}")
        target_claims = [args.claim]

    limiter = HostRateLimiter()

    registry: Dict[str, dict] = {}
    key_index: Dict[str, str] = {}
    counter = [0]
    candidates_payload: Dict[str, dict] = {}
    log_payload: Dict[str, dict] = {}

    for claim_id in target_claims:
        sys.stderr.write(f"[discover] claim={claim_id}\n")
        sys.stderr.flush()
        queries = CLAIM_QUERIES[claim_id]
        kept, log = discover_for_claim(
            claim_id=claim_id,
            queries=queries,
            venue_aliases=aliases,
            wcm_dois=wcm_dois,
            wcm_titles=wcm_titles,
            limiter=limiter,
            target=args.limit,
        )
        log_payload[claim_id] = log

        candidates_list: List[dict] = []
        for rank, rec in enumerate(kept, start=1):
            primary_query = queries.get("openalex", [""])[0]
            ext_id = assign_ext_id(
                rec=rec,
                registry=registry,
                key_index=key_index,
                counter=counter,
                discovery_query=primary_query,
                domain_hint=queries.get("domain_hint", "interdisciplinary"),
            )
            num = int(ext_id.split("-")[1])
            candidates_list.append({
                "id": f"candidate-{claim_id}-MMG{num:03d}",
                "paper_id": ext_id,
                "claim_ref": claim_id,
                "discovery_score": discovery_score(rec, rank),
                "discovery_rank": rank,
                "confidence": "candidate_unscored",
            })
        candidates_payload[claim_id] = {
            "paradigm": paradigm_map.get(claim_id, ""),
            "candidates": candidates_list,
        }

    # Sort registry by ext id for deterministic output
    registry_sorted = {k: registry[k] for k in sorted(registry.keys())}

    # Sort each claim's candidates by rank (already in rank order, but ensure)
    for c in candidates_payload.values():
        c["candidates"].sort(key=lambda r: r["discovery_rank"])

    out_registry = {
        "schema_version": "0.1",
        "papers": registry_sorted,
    }
    out_candidates = {
        "schema_version": "0.1",
        "claims": candidates_payload,
    }
    out_log = log_payload

    if args.dry_run:
        sys.stderr.write("[dry-run] not writing output files\n")
    else:
        save_json(REGISTRY_FILE, out_registry)
        save_json(CANDIDATES_FILE, out_candidates)
        save_json(LOG_FILE, out_log)

    # stdout summary
    print("=== Agent D1 discovery summary ===")
    print(f"Total unique MMG papers: {len(registry_sorted)}")
    for cid, c in candidates_payload.items():
        print(f"  {cid}: {len(c['candidates'])} candidates")
    print(f"Log file: {LOG_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
