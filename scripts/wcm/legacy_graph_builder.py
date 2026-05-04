#!/usr/bin/env python3
"""Build a richer Graphify-based knowledge graph for the WCM paper collection."""

from __future__ import annotations

import csv
import json
import re
import textwrap
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

import networkx as nx
import requests
from bs4 import BeautifulSoup
from graphify.analyze import god_nodes, suggest_questions, surprising_connections
from graphify.cluster import score_all
from graphify.export import to_graphml, to_html, to_json, to_svg
from graphify.report import generate
from pypdf import PdfReader
from .zotero_legacy import sync_from_zotero, sync_to_zotero


ROOT = Path(__file__).resolve().parents[2]
METADATA_DIR = ROOT / "metadata"
GRAPHIFY_OUT = ROOT / "graphify-out"
CORPUS_DIR = ROOT / "graphify_corpus"
PAPER_NOTES_DIR = CORPUS_DIR / "papers"
CACHE_DIR = METADATA_DIR / "openalex_cache"
PDF_DIR = ROOT / "pdfs"
REJECTED_PDF_DIR = PDF_DIR / "_rejected"
MASTER_TABLE = METADATA_DIR / "whole_cell_model_papers_master_table.csv"
INVENTORY_TABLE = METADATA_DIR / "curated_papers_inventory.csv"
LIVE_INVENTORY_TABLE = METADATA_DIR / "live_paper_inventory.csv"
SIDECAR_REVIEW_QUEUE = METADATA_DIR / "pdf_sidecar_review_queue.csv"
SIDECAR_REJECTED_QUEUE = METADATA_DIR / "pdf_sidecar_rejected.csv"
SIDECAR_SUMMARY = METADATA_DIR / "pdf_sidecar_summary.md"
CLASS_TABLE = METADATA_DIR / "wcm_method_classes.csv"
ORGANISM_TABLE = METADATA_DIR / "wcm_organisms.csv"
PAPER_METADATA_JSON = METADATA_DIR / "wcm_paper_metadata.json"
AUTO_INGEST_JSON = METADATA_DIR / "auto_ingested_papers.json"
PDF_PARSE_CACHE_JSON = METADATA_DIR / "pdf_parse_cache.json"
PDF_PROCESSING_STATUS = METADATA_DIR / "pdf_processing_status.csv"
ZOTERO_SYNC_STATE = METADATA_DIR / "zotero_sync_state.json"
MAILTO = "stark@example.com"


CLASS_IDS = {
    "Mechanistic models": 0,
    "Machine Learning Model": 1,
    "Hybrid architectures": 2,
}

CLASS_DEFINITIONS = {
    "Mechanistic models": (
        "Rule-based, kinetic, stochastic, physical, or simulation-first studies "
        "that represent cellular processes explicitly."
    ),
    "Machine Learning Model": (
        "Data-driven or AI-first work where predictive learning is the central modeling strategy."
    ),
    "Hybrid architectures": (
        "Workflows that combine mechanistic simulation with data integration, structural constraints, "
        "or AI-assisted components."
    ),
}

THEME_KEYWORDS = {
    "whole_cell": ["whole-cell", "whole cell", "virtual cell"],
    "minimal_cell": ["minimal cell", "minimal genome", "jcvi", "syn3", "synthetic genome", "mycoplasma"],
    "metabolism": ["metabolism", "metabolic", "flux", "proteome", "growth rate"],
    "gene_expression": ["gene expression", "transcription", "translation", "ribosome", "expressome", "rna polymerase"],
    "chromosome": ["chromosome", "segregation", "condensin", "smc", "parabs", "loop extrusion", "nucleoid"],
    "spatial": ["spatial", "geometry", "3d", "4d", "structural", "tomogram", "imaging", "molecular dynamics"],
    "cell_division": ["division", "cell cycle", "cytokinesis", "replication"],
    "methods": ["model", "simulation", "kinetic", "stochastic", "modular", "integrative"],
}

OBJECT_LABELS = {
    "whole_cell": "Whole-cell integration",
    "minimal_cell": "Minimal-cell platform",
    "chromosome": "Chromosome organization",
    "gene_expression": "Gene-expression machinery",
    "metabolism": "Metabolism",
    "spatial": "Spatial structure and dynamics",
    "cell_division": "Cell division and cell cycle",
    "methods": "Modeling framework",
}

OBJECT_PRIORITY = [
    "whole_cell",
    "minimal_cell",
    "chromosome",
    "gene_expression",
    "metabolism",
    "spatial",
    "cell_division",
    "methods",
]

LAYOUT_COLORS = {
    0: "#4E79A7",
    1: "#F28E2B",
    2: "#E15759",
}


def ordered_class_items() -> list[tuple[str, int]]:
    return sorted(CLASS_IDS.items(), key=lambda item: item[1])

ORGANISM_GROUP_ORDER = [
    "Whole-cell general",
    "General bacteria",
    "Escherichia coli",
    "Mycoplasma / Mollicutes",
    "JCVI minimal cell",
    "Chromatin / condensin",
    "Reaction-diffusion methods",
]

ORGANISM_GROUP_MAP = {
    "Bacillus subtilis": "General bacteria",
    "Bacterial cell general": "General bacteria",
    "Bacterial chromosome systems": "General bacteria",
    "Chromatin / multi-system": "Chromatin / condensin",
    "Condensin / chromatin": "Chromatin / condensin",
    "Escherichia coli": "Escherichia coli",
    "General bacteria": "General bacteria",
    "General reaction-diffusion": "Reaction-diffusion methods",
    "JCVI minimal genome": "JCVI minimal cell",
    "JCVI minimal organism": "JCVI minimal cell",
    "JCVI-syn3A / minimal cell": "JCVI minimal cell",
    "JCVI-syn3B": "JCVI minimal cell",
    "Mesoplasma florum": "Mycoplasma / Mollicutes",
    "Mycoplasma capricolum": "Mycoplasma / Mollicutes",
    "Mycoplasma genitalium": "Mycoplasma / Mollicutes",
    "Mycoplasma pneumoniae": "Mycoplasma / Mollicutes",
    "Mycoplasma whole-cell structure": "Mycoplasma / Mollicutes",
    "Saccharomyces cerevisiae": "Whole-cell general",
    "Synthetic Mycoplasma cell": "Mycoplasma / Mollicutes",
    "Human / pan-cancer": "Whole-cell general",
    "Whole-cell general": "Whole-cell general",
}

ORGANISM_GROUP_LABELS = {
    "Whole-cell general": "Whole-cell",
    "General bacteria": "General bacteria",
    "Escherichia coli": "E. coli",
    "Mycoplasma / Mollicutes": "Mycoplasma / Mollicutes",
    "JCVI minimal cell": "JCVI minimal cell",
    "Chromatin / condensin": "Chromatin / condensin",
    "Reaction-diffusion methods": "Reaction-diffusion",
}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def sentence_case(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def abstract_from_inverted_index(inverted: dict | None) -> str:
    if not inverted:
        return ""
    words: dict[int, str] = {}
    for token, positions in inverted.items():
        for position in positions:
            words[position] = token
    return " ".join(words[i] for i in sorted(words))


def openalex_json(url: str) -> dict:
    delimiter = "&" if "?" in url else "?"
    request = urllib.request.Request(
        f"{url}{delimiter}mailto={urllib.parse.quote(MAILTO)}",
        headers={"User-Agent": f"WCMGraphBuilder/2.0 ({MAILTO})"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_master_table() -> list[dict[str, str]]:
    rows = load_csv(MASTER_TABLE)
    for row in rows:
        row["paper_id"] = row["paper id"]
        row["paper_title"] = row["paper title"]
        row["method_summary"] = row["method summary"]
        row["future_work"] = row["future work"]
        row["pdf_status"] = row["pdf status"]
        row["pdf_file"] = row["pdf file"]
        row["landing_page_url"] = row["landing page url"]
    return rows


def read_inventory_urls() -> dict[str, str]:
    if not INVENTORY_TABLE.exists():
        return {}
    urls = {}
    for row in load_csv(INVENTORY_TABLE):
        paper_id = row.get("paper_id", "").strip()
        pdf_url = row.get("pdf_url", "").strip()
        if paper_id and pdf_url:
            urls[paper_id] = pdf_url
    return urls


def read_classes() -> dict[str, dict[str, str]]:
    return {row["paper_id"]: row for row in load_csv(CLASS_TABLE)}


def read_organisms() -> dict[str, dict[str, str]]:
    return {row["paper_id"]: row for row in load_csv(ORGANISM_TABLE)}


def ensure_dirs() -> None:
    GRAPHIFY_OUT.mkdir(parents=True, exist_ok=True)
    PAPER_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REJECTED_PDF_DIR.mkdir(parents=True, exist_ok=True)


def paper_note_path(paper_id: str) -> Path:
    return PAPER_NOTES_DIR / f"{paper_id}.md"


def root_relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def graph_relative(path: Path) -> str:
    return (Path("..") / path.relative_to(ROOT)).as_posix()


def normalize_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def make_display_label(authors: list[str], year: int | str, fallback_title: str) -> str:
    if authors:
        surname = authors[0].split()[-1]
        return f"{surname} {year}"
    short = fallback_title.split(":")[0].split(",")[0]
    return f"{short[:24]} {year}".strip()


def organism_group_for_label(label: str) -> str:
    return ORGANISM_GROUP_MAP.get(label, "General bacteria")


def auto_ingest_payload() -> dict:
    if AUTO_INGEST_JSON.exists():
        return json.loads(AUTO_INGEST_JSON.read_text(encoding="utf-8"))
    return {"papers": []}


def save_auto_ingest_payload(payload: dict) -> None:
    AUTO_INGEST_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def doi_regex() -> re.Pattern[str]:
    return re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


# Section-extraction heuristics ported from Knowledge_Graph_Agent/scripts/parse_pdfs.py
# (https://github.com/FreakingPotato/Knowledge_Graph_Agent). Run on the head of the
# document for sectioned excerpts and on the full text for references.
_PDF_ABSTRACT_RE = re.compile(
    r"\babstract\b\s*[:\-]?\s*(.{50,4000}?)(?:\n\s*\n|\n[A-Z]\w+\s*\n|\n1\s+introduction)",
    re.DOTALL | re.IGNORECASE,
)
_PDF_INTRO_RE = re.compile(
    r"\b(?:1\.?\s*introduction|introduction)\b\s*\n?(.{50,4000}?)(?:\n\s*\n\s*[2-9]\.|\n\s*[A-Z]{2,})",
    re.DOTALL | re.IGNORECASE,
)
_PDF_METHODS_RE = re.compile(
    r"\b(?:method(?:s|ology)?|approach)\b\s*\n?(.{50,3000}?)(?:\n\s*\n\s*[A-Z][a-z]|\n\s*\d\.)",
    re.DOTALL | re.IGNORECASE,
)
_PDF_REFS_RE = re.compile(r"\breferences\b\s*\n(.+)$", re.DOTALL | re.IGNORECASE)
_PDF_DOI_IN_TEXT = re.compile(r"\b10\.\d{4,}/[^\s,;]+", re.IGNORECASE)


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _extract_pdf_excerpt(pattern: re.Pattern[str], text: str, *, cap: int = 2000) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    return _collapse_ws(match.group(1))[:cap]


def _extract_pdf_references(full_text: str, *, max_refs: int = 80) -> list[str]:
    """Best-effort references list from a PDF's full text."""
    match = _PDF_REFS_RE.search(full_text)
    if not match:
        return []
    tail = match.group(1)
    candidates = re.split(r"\n(?:\[\d+\]|\d+\.|\d+\))\s*", tail)
    if len(candidates) <= 1:
        candidates = [c for c in tail.split("\n\n") if c.strip()]
    cleaned = [_collapse_ws(c) for c in candidates if c.strip()]
    cleaned = [c for c in cleaned if 30 <= len(c) <= 800]
    if cleaned:
        return cleaned[:max_refs]
    dois = list(dict.fromkeys(_PDF_DOI_IN_TEXT.findall(tail)))
    return dois[:max_refs]


def extract_pdf_profile(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path))
    metadata = reader.metadata or {}
    page_texts: list[str] = []
    doi = ""
    title = (metadata.get("/Title") or "").strip()
    author = (metadata.get("/Author") or "").strip()
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        page_texts.append(text)
        if not doi and text:
            match = doi_regex().search(text[:5000])
            if match:
                doi = match.group(0).rstrip(").,;")
        if not title and text:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                title = lines[0][:300]
    head_text = "\n".join(page_texts[:3])
    full_text = "\n".join(page_texts)
    return {
        "path": str(pdf_path),
        "file_name": pdf_path.name,
        "page_count": len(reader.pages),
        "metadata_title": title,
        "metadata_author": author,
        "doi": doi.lower(),
        "page_texts": page_texts,
        "full_text": full_text,
        "abstract_excerpt": _extract_pdf_excerpt(_PDF_ABSTRACT_RE, head_text),
        "intro_excerpt": _extract_pdf_excerpt(_PDF_INTRO_RE, head_text),
        "methods_excerpt": _extract_pdf_excerpt(_PDF_METHODS_RE, head_text, cap=2000),
        "references_excerpt": _extract_pdf_references(full_text[:200_000]),
    }


def pdf_file_signature(pdf_path: Path) -> dict[str, int]:
    stat = pdf_path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def load_pdf_parse_cache() -> dict:
    if PDF_PARSE_CACHE_JSON.exists():
        try:
            payload = json.loads(PDF_PARSE_CACHE_JSON.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
                return payload
        except Exception:
            pass
    return {"version": 1, "files": {}}


def save_pdf_parse_cache(payload: dict) -> None:
    PDF_PARSE_CACHE_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def scan_pdf_profiles() -> tuple[list[dict], dict]:
    cache = load_pdf_parse_cache()
    files_cache = cache.get("files", {})
    next_cache: dict[str, dict] = {}
    profiles: list[dict] = []

    for pdf_path in sorted(PDF_DIR.glob("*.pdf")):
        signature = pdf_file_signature(pdf_path)
        cached_entry = files_cache.get(pdf_path.name)
        if cached_entry and cached_entry.get("signature") == signature:
            entry = dict(cached_entry)
        else:
            try:
                profile = extract_pdf_profile(pdf_path)
                cached_profile = dict(profile)
                cached_profile["path"] = pdf_path.name
                entry = {
                    "signature": signature,
                    "parsed": True,
                    "profile": cached_profile,
                    "error": "",
                    "updated_at": utc_now(),
                }
            except Exception as exc:
                entry = {
                    "signature": signature,
                    "parsed": False,
                    "profile": None,
                    "error": str(exc),
                    "updated_at": utc_now(),
                }
        next_cache[pdf_path.name] = entry
        if entry.get("parsed") and entry.get("profile"):
            profile = dict(entry["profile"])
            profile["path"] = str(pdf_path)
            profiles.append(profile)

    payload = {"version": 1, "files": next_cache}
    save_pdf_parse_cache(payload)
    return profiles, payload


def fetch_url(url: str, *, allow_redirects: bool = True) -> requests.Response | None:
    try:
        return requests.get(
            url,
            timeout=20,
            allow_redirects=allow_redirects,
            headers={"User-Agent": "Mozilla/5.0 WCMGraphBuilder/2.0"},
        )
    except Exception:
        return None


def resolve_meta_refresh(html: str, base_url: str) -> str | None:
    match = re.search(r'url=["\\\']?([^"\\\'>]+)', html, flags=re.IGNORECASE)
    if not match:
        return None
    return urllib.parse.urljoin(base_url, match.group(1))


def landing_page_pdf_candidates(url: str) -> list[str]:
    response = fetch_url(url)
    if response is None:
        return []
    html = response.text or ""
    if "http-equiv=\"REFRESH\"" in html or "http-equiv=REFRESH" in html:
        refresh_url = resolve_meta_refresh(html, response.url)
        if refresh_url:
            response = fetch_url(refresh_url)
            if response is None:
                return []
            html = response.text or ""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        content = (meta.get("content") or "").strip()
        if not content:
            continue
        if name == "citation_pdf_url" or "pdf" in name:
            candidates.append(urllib.parse.urljoin(response.url, content))
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if ".pdf" in href.lower():
            candidates.append(urllib.parse.urljoin(response.url, href))
    deduped = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def download_pdf(url: str, target: Path) -> bool:
    try:
        response = requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 WCMGraphBuilder/2.0",
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
            },
            allow_redirects=True,
        )
    except Exception:
        return False
    if response.status_code >= 400:
        return False
    content_type = (response.headers.get("content-type") or "").lower()
    data = response.content
    if "pdf" not in content_type and not data.startswith(b"%PDF"):
        return False
    target.write_bytes(data)
    return True


def target_pdf_name(paper_id: str, title: str, year: int | str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{paper_id}_{year}_{title[:80]}").strip("_")
    return base + ".pdf"


def heuristic_method_class(title: str, abstract: str) -> str:
    text = f"{title} {abstract}".lower()
    if any(token in text for token in ["artificial intelligence", "machine learning", "deep learning", "foundation model"]):
        return "Machine Learning Model"
    if any(token in text for token in ["integrative", "hybrid", "cross-evaluation", "multi-modal", "structural models"]):
        return "Hybrid architectures"
    return "Mechanistic models"


def heuristic_organism(title: str, abstract: str) -> str:
    text = f"{title} {abstract}".lower()
    organism_map = [
        ("jcvi-syn3", "JCVI-syn3A / minimal cell"),
        ("minimal cell", "JCVI-syn3A / minimal cell"),
        ("escherichia coli", "Escherichia coli"),
        ("e. coli", "Escherichia coli"),
        ("mycoplasma genitalium", "Mycoplasma genitalium"),
        ("mycoplasma pneumoniae", "Mycoplasma pneumoniae"),
        ("mycoplasma capricolum", "Mycoplasma capricolum"),
        ("mesoplasma florum", "Mesoplasma florum"),
        ("bacillus subtilis", "Bacillus subtilis"),
        ("yeast", "Yeast / chromatin"),
        ("cohesin", "Yeast / chromatin"),
        ("condensin", "Yeast / chromatin"),
    ]
    for token, label in organism_map:
        if token in text:
            return label
    if "bacterial" in text or "bacteria" in text:
        return "General bacteria"
    return "General / multi-system"


def plausible_pdf_title(text: str) -> bool:
    cleaned = " ".join(text.split())
    if len(cleaned) < 20:
        return False
    if len(re.findall(r"[A-Za-z]", cleaned)) < 12:
        return False
    if re.fullmatch(r"[A-Z0-9./ -]+", cleaned) and sum(ch.isdigit() for ch in cleaned) >= 4:
        return False
    lowered = cleaned.lower()
    blocked = [
        "all rights reserved",
        "preprint",
        "journal pre-proof",
        "accepted manuscript",
        "contents lists available",
    ]
    return not any(token in lowered for token in blocked)


def likely_non_article_pdf(profile: dict) -> bool:
    sample = " ".join(
        part
        for part in [
            profile.get("metadata_title", ""),
            *(profile.get("page_texts", [])[:2]),
        ]
        if part
    ).lower()
    blocked = [
        "patent application publication",
        "supplementary information",
        "supplementary fig",
        "sheet 1 of",
        "sheet 1 /",
        "in format provided by",
        "journal pre-proof",
    ]
    return any(token in sample for token in blocked)


def title_token_overlap(title: str, candidate: str) -> float:
    title_tokens = {token for token in normalize_title(title).split() if len(token) >= 4}
    candidate_tokens = set(normalize_title(candidate).split())
    if not title_tokens:
        return 0.0
    return len(title_tokens & candidate_tokens) / len(title_tokens)


def title_evidence(row: dict[str, str], profile: dict) -> tuple[float, float]:
    row_title = normalize_title(row["paper_title"])
    first_page = profile.get("page_texts", [""])[0] if profile.get("page_texts") else ""
    candidates = [
        profile.get("metadata_title", ""),
        first_page[:3500],
    ]
    best_ratio = 0.0
    best_overlap = 0.0
    for candidate in candidates:
        candidate_norm = normalize_title(candidate)
        if not candidate_norm:
            continue
        best_overlap = max(best_overlap, title_token_overlap(row["paper_title"], candidate))
        best_ratio = max(best_ratio, SequenceMatcher(None, row_title, candidate_norm[: max(len(row_title), 1) * 2]).ratio())
    return best_overlap, best_ratio


def profile_match_score(row: dict[str, str], profile: dict) -> float:
    expected_doi = (row.get("doi") or "").lower()
    exact_doi = expected_doi and profile.get("doi") == expected_doi
    best_overlap, best_ratio = title_evidence(row, profile)
    score = best_ratio + best_overlap
    if exact_doi:
        score += 1.8
    if likely_non_article_pdf(profile) and score < 1.6:
        score -= 1.5
    return score


def profile_matches_paper(row: dict[str, str], profile: dict) -> bool:
    expected_doi = (row.get("doi") or "").lower()
    exact_doi = expected_doi and profile.get("doi") == expected_doi
    best_overlap, best_ratio = title_evidence(row, profile)
    if likely_non_article_pdf(profile):
        return best_overlap >= 0.55
    if exact_doi and best_overlap >= 0.35:
        return True
    return best_overlap >= 0.55 or best_ratio >= 0.9


def quarantine_pdf(profile: dict) -> None:
    source = Path(profile["path"])
    if not source.exists():
        return
    target = REJECTED_PDF_DIR / source.name
    counter = 1
    while target.exists():
        target = REJECTED_PDF_DIR / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    source.rename(target)


def quarantine_mismatched_profiles(rows: list[dict[str, str]], profiles: list[dict]) -> bool:
    row_by_id = {row["paper_id"]: row for row in rows}
    moved_any = False
    for profile in profiles:
        prefix = profile["file_name"].split("_")[0]
        row = row_by_id.get(prefix)
        if not row:
            continue
        if profile_matches_paper(row, profile):
            continue
        quarantine_pdf(profile)
        moved_any = True
    return moved_any


def find_best_pdf_match(row: dict[str, str], profiles: list[dict]) -> dict | None:
    for profile in profiles:
        if profile["file_name"].startswith(row["paper_id"]) and profile_matches_paper(row, profile):
            return profile
    if row["doi"]:
        for profile in profiles:
            if profile["doi"] == row["doi"].lower() and profile_matches_paper(row, profile):
                return profile
    return None


def openalex_work_summary_by_title(title: str) -> dict | None:
    if not plausible_pdf_title(title):
        return None
    encoded = urllib.parse.quote(title)
    payload = openalex_json(f"https://api.openalex.org/works?search={encoded}&per-page=5")
    results = payload.get("results") or []
    if not results:
        return None
    normalized = normalize_title(title)
    best = None
    best_score = 0.0
    for candidate in results:
        candidate_title = candidate.get("display_name") or ""
        score = SequenceMatcher(None, normalized, normalize_title(candidate_title)).ratio()
        if score > best_score:
            best = candidate
            best_score = score
    if best is None or best_score < 0.84:
        return None
    abstract = abstract_from_inverted_index(best.get("abstract_inverted_index"))
    return {
        "doi": (best.get("doi") or "").removeprefix("https://doi.org/").lower(),
        "openalex_id": best.get("id", ""),
        "referenced_works": best.get("referenced_works", []),
        "abstract": abstract,
        "authors": [
            (authorship.get("author") or {}).get("display_name", "")
            for authorship in best.get("authorships") or []
            if (authorship.get("author") or {}).get("display_name")
        ],
        "concepts": [
            concept.get("display_name", "")
            for concept in best.get("concepts") or []
            if concept.get("display_name")
        ],
        "cited_by_count": best.get("cited_by_count", 0),
        "title": best.get("display_name", title),
        "year": best.get("publication_year") or 0,
        "journal": ((best.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
        "landing_page_url": ((best.get("primary_location") or {}).get("landing_page_url") or best.get("doi") or ""),
        "pdf_urls": [
            loc.get("pdf_url")
            for loc in (best.get("locations") or [])
            if loc.get("pdf_url")
        ],
    }


def unpaywall_pdf_candidates(doi: str) -> list[str]:
    try:
        response = requests.get(
            f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}",
            params={"email": MAILTO},
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 WCMGraphBuilder/2.0"},
        )
    except Exception:
        return []
    if response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except Exception:
        return []
    candidates: list[str] = []
    best = payload.get("best_oa_location") or {}
    for key in ["url_for_pdf", "url"]:
        value = (best.get(key) or "").strip()
        if value:
            candidates.append(value)
    for location in payload.get("oa_locations") or []:
        for key in ["url_for_pdf", "url"]:
            value = (location.get(key) or "").strip()
            if value:
                candidates.append(value)
    deduped = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def cache_path(doi: str) -> Path:
    return CACHE_DIR / f"{slugify(doi)}.json"


def openalex_work_summary(doi: str) -> dict:
    path = cache_path(doi)
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "openalex_id",
            "referenced_works",
            "abstract",
            "authors",
            "concepts",
            "cited_by_count",
            "title",
            "year",
            "journal",
            "landing_page_url",
            "pdf_urls",
        }
        if required.issubset(cached):
            return cached

    quoted = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
    payload = openalex_json(f"https://api.openalex.org/works/{quoted}")
    summary = {
        "doi": doi,
        "openalex_id": payload.get("id", ""),
        "referenced_works": payload.get("referenced_works", []),
        "abstract": abstract_from_inverted_index(payload.get("abstract_inverted_index")),
        "authors": [
            (authorship.get("author") or {}).get("display_name", "")
            for authorship in payload.get("authorships") or []
            if (authorship.get("author") or {}).get("display_name")
        ],
        "concepts": [
            concept.get("display_name", "")
            for concept in payload.get("concepts") or []
            if concept.get("display_name")
        ],
        "cited_by_count": payload.get("cited_by_count", 0),
        "title": payload.get("display_name", ""),
        "year": payload.get("publication_year") or 0,
        "journal": ((payload.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
        "landing_page_url": ((payload.get("primary_location") or {}).get("landing_page_url") or payload.get("doi") or ""),
        "pdf_urls": [
            location.get("pdf_url")
            for location in (payload.get("locations") or [])
            if location.get("pdf_url")
        ],
    }
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def sync_rows_with_pdfs(
    rows: list[dict[str, str]],
    classes: dict[str, dict[str, str]],
    organisms: dict[str, dict[str, str]],
    inventory_urls: dict[str, str],
) -> tuple[list[dict[str, str]], dict[str, dict[str, str]], dict[str, dict[str, str]], list[dict], dict]:
    rows = [dict(row) for row in rows]
    classes = dict(classes)
    organisms = dict(organisms)
    profiles, parse_cache = scan_pdf_profiles()
    if quarantine_mismatched_profiles(rows, profiles):
        profiles, parse_cache = scan_pdf_profiles()

    for row in rows:
        profile = find_best_pdf_match(row, profiles)
        if profile:
            row["pdf_file"] = profile["file_name"]
            row["pdf_status"] = "downloaded"

    for row in rows:
        if row["pdf_file"]:
            continue
        summary = openalex_work_summary(row["doi"].lower())
        candidates = []
        if inventory_urls.get(row["paper_id"]):
            candidates.append(inventory_urls[row["paper_id"]])
        candidates.extend(unpaywall_pdf_candidates(row["doi"].lower()))
        candidates.extend(url for url in summary.get("pdf_urls", []) if url)
        candidates.extend(landing_page_pdf_candidates(summary.get("landing_page_url") or row["landing_page_url"]))
        candidates.extend(landing_page_pdf_candidates(row["landing_page_url"]))
        seen = set()
        ordered = []
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
        target = PDF_DIR / target_pdf_name(row["paper_id"], row["paper_title"], row["year"])
        for candidate in ordered:
            if not download_pdf(candidate, target):
                continue
            profiles, parse_cache = scan_pdf_profiles()
            downloaded_profile = find_best_pdf_match(row, profiles)
            if downloaded_profile is None:
                target.unlink(missing_ok=True)
                profiles, parse_cache = scan_pdf_profiles()
                continue
            row["pdf_file"] = target.name
            row["pdf_status"] = "downloaded"
            break

    profiles, parse_cache = scan_pdf_profiles()
    known_dois = {row["doi"].lower(): row["paper_id"] for row in rows if row["doi"]}
    existing_payload = auto_ingest_payload()
    assigned_pdf_files = {row["pdf_file"] for row in rows if row.get("pdf_file")}
    existing_entries = [
        entry
        for entry in existing_payload.get("papers", [])
        if entry.get("pdf_file")
        and (PDF_DIR / entry["pdf_file"]).exists()
        and entry["pdf_file"] not in assigned_pdf_files
        and entry.get("doi", "").lower() not in known_dois
    ]
    next_index = 1
    if existing_entries:
        next_index = max(int(entry["paper_id"].split("-")[-1]) for entry in existing_entries) + 1

    for profile in profiles:
        if profile["file_name"] in assigned_pdf_files:
            continue
        if profile["doi"] and profile["doi"] in known_dois:
            continue
        if any(entry.get("pdf_file") == profile["file_name"] for entry in existing_entries):
            continue
        openalex_summary = None
        if profile["doi"]:
            try:
                openalex_summary = openalex_work_summary(profile["doi"])
            except Exception:
                openalex_summary = None
        if openalex_summary is None and profile["metadata_title"]:
            openalex_summary = openalex_work_summary_by_title(profile["metadata_title"])
        if not openalex_summary:
            continue
        doi = (openalex_summary.get("doi") or profile["doi"]).lower()
        if doi and doi in known_dois:
            matched_id = known_dois[doi]
            for row in rows:
                if row["paper_id"] == matched_id:
                    row["pdf_file"] = profile["file_name"]
                    row["pdf_status"] = "downloaded"
            continue
        paper_id = f"WCM-A{next_index:03d}"
        next_index += 1
        title = openalex_summary.get("title") or profile["metadata_title"] or profile["file_name"]
        abstract = openalex_summary.get("abstract") or ""
        method_class = heuristic_method_class(title, abstract)
        organism_label = heuristic_organism(title, abstract)
        row = {
            "paper id": paper_id,
            "paper title": title,
            "summary": abstract.split(". ")[0].strip() + ("." if abstract and not abstract.strip().endswith(".") else ""),
            "method summary": "Auto-ingested from a manually added PDF; detailed annotation still needs review.",
            "contribution": "Added automatically from a PDF dropped into the local corpus for later curation.",
            "limitation": "Auto-ingested metadata may require manual verification against the full paper.",
            "future work": "Review and curate this paper in the master table if it is central to the WCM corpus.",
            "year": str(openalex_summary.get("year") or 0),
            "journal": openalex_summary.get("journal") or "",
            "doi": doi,
            "pdf status": "downloaded",
            "pdf file": profile["file_name"],
            "landing page url": openalex_summary.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else ""),
        }
        row["paper_id"] = row["paper id"]
        row["paper_title"] = row["paper title"]
        row["method_summary"] = row["method summary"]
        row["future_work"] = row["future work"]
        row["pdf_status"] = row["pdf status"]
        row["pdf_file"] = row["pdf file"]
        row["landing_page_url"] = row["landing page url"]
        rows.append(row)
        classes[paper_id] = {"paper_id": paper_id, "method_class": method_class, "rationale": "Auto-classified from title and abstract during PDF ingest."}
        organisms[paper_id] = {"paper_id": paper_id, "organism": organism_label}
        known_dois[doi] = paper_id
        existing_entries.append(
            {
                "paper_id": paper_id,
                "title": title,
                "doi": doi,
                "pdf_file": profile["file_name"],
                "method_class": method_class,
                "organism": organism_label,
            }
        )

    save_auto_ingest_payload({"papers": existing_entries})
    return rows, classes, organisms, profiles, parse_cache


def pdf_profile_map(profiles: list[dict]) -> dict[str, dict]:
    return {profile["file_name"]: profile for profile in profiles}


def write_pdf_processing_status(rows: list[dict[str, str]], parse_cache: dict, zotero_state: dict | None) -> None:
    remote_by_paper: dict[str, dict] = {}
    for item in (zotero_state or {}).get("remote_items", []):
        paper_id = item.get("paper_id", "")
        if paper_id:
            remote_by_paper[paper_id] = item

    fieldnames = [
        "paper_id",
        "title",
        "doi",
        "pdf_file",
        "pdf_exists",
        "pdf_status",
        "parsed",
        "page_count",
        "parse_error",
        "cache_updated_at",
        "zotero_has_pdf",
        "zotero_parent_key",
        "zotero_attachment_keys",
    ]
    cache_files = parse_cache.get("files", {})
    with PDF_PROCESSING_STATUS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item["paper_id"]):
            cache_entry = cache_files.get(row.get("pdf_file", ""), {})
            remote = remote_by_paper.get(row["paper_id"], {})
            writer.writerow(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "doi": row["doi"],
                    "pdf_file": row.get("pdf_file", ""),
                    "pdf_exists": "yes" if row.get("pdf_file") and (PDF_DIR / row["pdf_file"]).exists() else "no",
                    "pdf_status": row.get("pdf_status", ""),
                    "parsed": "yes" if cache_entry.get("parsed") else "no",
                    "page_count": ((cache_entry.get("profile") or {}).get("page_count") or ""),
                    "parse_error": cache_entry.get("error", ""),
                    "cache_updated_at": cache_entry.get("updated_at", ""),
                    "zotero_has_pdf": "yes" if remote.get("has_pdf_attachment") else "no",
                    "zotero_parent_key": remote.get("parent_key", ""),
                    "zotero_attachment_keys": ";".join(remote.get("attachment_keys", [])),
                }
            )
        known_pdfs = {row.get("pdf_file", "") for row in rows}
        for file_name, cache_entry in sorted(cache_files.items()):
            if file_name in known_pdfs:
                continue
            writer.writerow(
                {
                    "paper_id": "",
                    "title": "",
                    "doi": "",
                    "pdf_file": file_name,
                    "pdf_exists": "yes" if (PDF_DIR / file_name).exists() else "no",
                    "pdf_status": "unassigned_local_pdf",
                    "parsed": "yes" if cache_entry.get("parsed") else "no",
                    "page_count": ((cache_entry.get("profile") or {}).get("page_count") or ""),
                    "parse_error": cache_entry.get("error", ""),
                    "cache_updated_at": cache_entry.get("updated_at", ""),
                    "zotero_has_pdf": "",
                    "zotero_parent_key": "",
                    "zotero_attachment_keys": "",
                }
            )


def anchor_terms(query: str, fallback_terms: list[str]) -> list[str]:
    terms = [term for term in fallback_terms if len(term) >= 4]
    terms.extend(word for word in normalize_title(query).split() if len(word) >= 5)
    deduped = []
    seen = set()
    for term in terms:
        if term not in seen:
            deduped.append(term)
            seen.add(term)
    return deduped[:20]


def clean_heading_candidate(line: str) -> str:
    text = " ".join(line.split())
    return re.sub(r"^page\s+\d+\s*", "", text, flags=re.IGNORECASE).strip()


def heading_quality(line: str) -> float:
    line = clean_heading_candidate(line)
    lowered = line.lower()
    if len(line) < 6 or len(line) > 140:
        return -1.0
    if line.endswith("."):
        return -0.5
    if re.search(r"\bdoi\b", lowered):
        return -0.5
    if re.search(r"\b(all rights reserved|copyright|creativecommons|cc-by|preprint|journal pre-proof|supplementary information|in format provided by)\b", lowered):
        return -1.0
    if re.fullmatch(r"[A-Z0-9./ -]+", line) and sum(ch.isdigit() for ch in line) >= 4:
        return -1.0
    score = 0.0
    if any(token in lowered for token in ["abstract", "discussion", "conclusion", "limitations", "future", "results", "methods", "introduction"]):
        score += 0.8
    if line == line.title():
        score += 0.35
    if any(ch.islower() for ch in line) and any(ch.isupper() for ch in line):
        score += 0.2
    if re.match(r"^\d+(\.\d+)*\s+[A-Za-z]", line):
        score += 0.4
    return score


def find_heading_on_page(page_text: str) -> str | None:
    lines = [clean_heading_candidate(line) for line in page_text.splitlines() if line.strip()]
    ranked = sorted(lines[:40], key=heading_quality, reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    return best if heading_quality(best) > 0 else None


def best_excerpt(page_text: str, terms: list[str]) -> str | None:
    text = " ".join(page_text.split())
    lowered = text.lower()
    if not text:
        return None
    best_index = None
    for term in terms:
        idx = lowered.find(term.lower())
        if idx >= 0:
            best_index = idx
            break
    if best_index is None:
        return None
    start = max(0, best_index - 80)
    end = min(len(text), best_index + 180)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt += "..."
    return excerpt


def best_pdf_anchor(profile: dict, query: str, fallback_terms: list[str]) -> tuple[int | None, str | None, str | None]:
    if not profile:
        return None, None, None
    query_norm = normalize_title(query)
    terms = anchor_terms(query, fallback_terms)
    best_page = None
    best_score = 0.0
    for index, page_text in enumerate(profile.get("page_texts", []), start=1):
        page_norm = normalize_title(page_text[:12000])
        score = 0.0
        if query_norm and query_norm in page_norm:
            score = 1.0
        else:
            hits = sum(1 for term in terms if term and term in page_norm)
            if terms:
                score = hits / len(terms)
        if any(marker in page_norm for marker in ["discussion", "conclusion", "limitations", "future work"]):
            score += 0.08
        if "abstract" in page_norm and index <= 3:
            score += 0.06
        if score > best_score:
            best_score = score
            best_page = index
    if best_page is None:
        return None, None, None
    page_text = profile["page_texts"][best_page - 1]
    heading = find_heading_on_page(page_text)
    excerpt = best_excerpt(page_text, terms)
    return best_page, heading, excerpt


def pdf_refs_for_text(
    *,
    profile: dict | None,
    pdf_href: str,
    query: str,
    fallback_terms: list[str],
    section_name: str,
) -> list[dict[str, str]]:
    if not profile or not pdf_href:
        return []
    page, heading, excerpt = best_pdf_anchor(profile, query, fallback_terms)
    if page is None:
        return []
    section = f"Page {page}" + (f" · {heading}" if heading else "")
    note = "Approximate page anchor derived from local PDF text parsing."
    if excerpt:
        note += f" Matched excerpt: {excerpt}"
    return [
        {
            "label": f"Parsed PDF anchor: {section_name}",
            "kind": "parsed_pdf_anchor",
            "href": f"{pdf_href}#page={page}",
            "section": section,
            "note": note,
        }
    ]


def themes_for_text(parts: list[str]) -> set[str]:
    text = " ".join(part for part in parts if part).lower()
    themes = set()
    for theme, keywords in THEME_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            themes.add(theme)
    return themes


def object_labels_for_themes(themes: set[str]) -> list[str]:
    labels = [OBJECT_LABELS[key] for key in OBJECT_PRIORITY if key in themes]
    return labels or ["General WCM context"]


def split_parallel_items(text: str) -> list[str]:
    cleaned = text.strip().rstrip(".")
    patterns = [
        r"\bdepends on (?P<items>.+)$",
        r"\bwith (?P<items>.+)$",
        r"\binto (?P<items>.+)$",
        r"\bto (?P<items>.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        items = match.group("items")
        parts = [part.strip(" .") for part in re.split(r",|\band\b", items) if part.strip(" .")]
        if len(parts) >= 2:
            return [sentence_case(part) for part in parts]
    return [sentence_case(cleaned)]


def make_refs(
    *,
    row: dict[str, str],
    section_name: str,
    note: str,
    note_path_str: str,
    pdf_href: str,
    source_kind: str,
) -> list[dict[str, str]]:
    refs = [
        {
            "label": f"Curated note section: {section_name}",
            "kind": source_kind,
            "href": note_path_str,
            "section": section_name,
            "note": note,
        }
    ]
    if pdf_href:
        refs.append(
            {
                "label": "Local PDF",
                "kind": "local_pdf",
                "href": pdf_href,
                "section": "PDF file",
                "note": "Open the local PDF to inspect the paper directly.",
            }
        )
    refs.append(
        {
            "label": "DOI / landing page",
            "kind": "paper_record",
            "href": row["landing_page_url"] or f"https://doi.org/{row['doi']}",
            "section": "Paper record",
            "note": "Publisher or repository landing page for the paper.",
        }
    )
    return refs


def build_section_payload(
    *,
    row: dict[str, str],
    abstract: str,
    note_href: str,
    pdf_href: str,
    pdf_profile: dict | None,
) -> dict[str, dict]:
    section_note = "Curated summary note for this paper in the local WCM corpus."
    abstract_text = abstract or row["summary"]
    limitations = [
        {
            "text": bullet,
            "refs": pdf_refs_for_text(
                profile=pdf_profile,
                pdf_href=pdf_href,
                query=bullet,
                fallback_terms=[word for word in normalize_title(bullet).split() if len(word) >= 5],
                section_name="Limitation",
            )
            + make_refs(
                row=row,
                section_name="Limitation",
                note=section_note,
                note_path_str=note_href,
                pdf_href=pdf_href,
                source_kind="curated_limitation",
            ),
        }
        for bullet in split_parallel_items(row["limitation"])
    ]
    future_work = [
        {
            "text": bullet,
            "refs": pdf_refs_for_text(
                profile=pdf_profile,
                pdf_href=pdf_href,
                query=bullet,
                fallback_terms=[word for word in normalize_title(bullet).split() if len(word) >= 5] + ["future", "discussion", "conclusion"],
                section_name="Future Work",
            )
            + make_refs(
                row=row,
                section_name="Future Work",
                note=section_note,
                note_path_str=note_href,
                pdf_href=pdf_href,
                source_kind="curated_future_work",
            ),
        }
        for bullet in split_parallel_items(row["future_work"])
    ]
    return {
        "overview": {
            "kind": "text",
            "title": "Overview",
            "content": row["summary"],
            "refs": make_refs(
                row=row,
                section_name="Summary",
                note=section_note,
                note_path_str=note_href,
                pdf_href=pdf_href,
                source_kind="curated_summary",
            ),
        },
        "abstract": {
            "kind": "text",
            "title": "Abstract",
            "content": abstract_text,
            "refs": pdf_refs_for_text(
                profile=pdf_profile,
                pdf_href=pdf_href,
                query=abstract_text[:220],
                fallback_terms=["abstract"] + [word for word in normalize_title(abstract_text).split()[:8] if len(word) >= 5],
                section_name="Abstract",
            ) + [
                {
                    "label": "OpenAlex abstract metadata",
                    "kind": "metadata_abstract",
                    "href": row["landing_page_url"] or f"https://doi.org/{row['doi']}",
                    "section": "Abstract metadata",
                    "note": "Abstract text is taken from OpenAlex metadata when available.",
                },
                {
                    "label": "DOI / landing page",
                    "kind": "paper_record",
                    "href": row["landing_page_url"] or f"https://doi.org/{row['doi']}",
                    "section": "Paper record",
                    "note": "Open the paper record for the source article.",
                },
            ],
        },
        "methods_summary": {
            "kind": "text",
            "title": "Methods Summary",
            "content": row["method_summary"],
            "refs": make_refs(
                row=row,
                section_name="Method Summary",
                note=section_note,
                note_path_str=note_href,
                pdf_href=pdf_href,
                source_kind="curated_methods",
            ),
        },
        "limitations": {
            "kind": "bullets",
            "title": "Limitations",
            "items": limitations,
        },
        "future_work": {
            "kind": "bullets",
            "title": "Future Work",
            "items": future_work,
        },
    }


def build_paper_metadata(
    rows: list[dict[str, str]],
    classes: dict[str, dict[str, str]],
    organisms: dict[str, dict[str, str]],
    profiles: list[dict],
) -> tuple[dict[str, dict], dict[str, dict]]:
    summaries = {row["paper_id"]: openalex_work_summary(row["doi"].lower()) for row in rows}
    paper_meta: dict[str, dict] = {}
    profiles_by_file = pdf_profile_map(profiles)
    # Layer in any explicit citation refreshes (OpenAlex → Semantic Scholar
    # fallback) persisted by `python scripts/build_wcm_graph.py --refresh-citations`.
    try:
        from .citations import counts_by_paper_id
        refreshed_counts = counts_by_paper_id()
    except Exception:
        refreshed_counts = {}

    for row in rows:
        summary = summaries[row["paper_id"]]
        class_name = classes[row["paper_id"]]["method_class"]
        organism = organisms.get(row["paper_id"], {}).get("organism", heuristic_organism(row["paper_title"], summary.get("abstract") or row["summary"]))
        organism_group = organism_group_for_label(organism)
        note_path = paper_note_path(row["paper_id"])
        note_href = graph_relative(note_path)
        pdf_href = graph_relative(ROOT / "pdfs" / row["pdf_file"]) if row["pdf_file"] else ""
        pdf_profile = profiles_by_file.get(row["pdf_file"], None) if row["pdf_file"] else None
        pdf_abstract = (pdf_profile or {}).get("abstract_excerpt") or ""
        abstract = summary.get("abstract") or pdf_abstract or row["summary"]
        themes = themes_for_text(
            [
                row["paper_title"],
                row["summary"],
                row["method_summary"],
                row["contribution"],
                abstract,
            ]
        )
        modeled_objects = object_labels_for_themes(themes)
        sections = build_section_payload(
            row=row,
            abstract=abstract,
            note_href=note_href,
            pdf_href=pdf_href,
            pdf_profile=pdf_profile,
        )
        meta = {
            "paper_id": row["paper_id"],
            "title": row["paper_title"],
            "journal": row["journal"],
            "year": int(row["year"]),
            "doi": row["doi"],
            "authors": summary.get("authors", []),
            "display_label": make_display_label(summary.get("authors", []), row["year"], row["paper_title"]),
            "method_class": class_name,
            "organism": organism,
            "organism_group": organism_group,
            "pdf_status": row["pdf_status"],
            "pdf_file": row["pdf_file"],
            "pdf_href": pdf_href,
            "landing_page_url": row["landing_page_url"] or summary.get("landing_page_url") or f"https://doi.org/{row['doi']}",
            "abstract": abstract,
            "summary": row["summary"],
            "contribution": row["contribution"],
            "modeled_objects": modeled_objects,
            "primary_object": modeled_objects[0],
            "themes": sorted(themes),
            "sections": sections,
            "source_file": root_relative(note_path),
            "source_href": note_href,
            "concepts": summary.get("concepts", []),
            "cited_by_count": summary.get("cited_by_count", 0),
            "citation_source": "openalex" if summary.get("cited_by_count") else "",
            "openalex_id": summary.get("openalex_id", ""),
            "referenced_works": summary.get("referenced_works", []),
        }
        refreshed = refreshed_counts.get(row["paper_id"])
        if refreshed:
            meta["cited_by_count"] = refreshed["count"]
            meta["citation_source"] = refreshed["source"]
        paper_meta[row["paper_id"]] = meta

    return paper_meta, summaries


def write_paper_notes(paper_meta: dict[str, dict]) -> None:
    for paper_id, meta in paper_meta.items():
        note = textwrap.dedent(
            f"""\
            # {meta['title']}

            - Paper ID: `{paper_id}`
            - Year: {meta['year']}
            - Journal: {meta['journal']}
            - DOI: {meta['doi']}
            - Method class: {meta['method_class']}
            - Organism: {meta['organism']}
            - PDF status: {meta['pdf_status']}
            - Modeled objects: {", ".join(meta['modeled_objects'])}

            ## Overview
            {meta['sections']['overview']['content']}

            ## Abstract
            {meta['sections']['abstract']['content']}

            ## Methods Summary
            {meta['sections']['methods_summary']['content']}

            ## Contribution
            {meta['contribution']}

            ## Limitations
            """
        ).rstrip()
        for item in meta["sections"]["limitations"]["items"]:
            note += f"\n- {item['text']}"
        note += "\n\n## Future Work"
        for item in meta["sections"]["future_work"]["items"]:
            note += f"\n- {item['text']}"
        note += "\n"
        paper_note_path(paper_id).write_text(note, encoding="utf-8")

    method_lines = ["# WCM Method Classes", ""]
    for class_name, _cid in ordered_class_items():
        method_lines.extend([f"## {class_name}", CLASS_DEFINITIONS.get(class_name, ""), ""])
    method_md = "\n".join(method_lines).rstrip() + "\n"
    (CORPUS_DIR / "method_classes.md").write_text(method_md, encoding="utf-8")


def build_graph(
    rows: list[dict[str, str]],
    classes: dict[str, dict[str, str]],
    paper_meta: dict[str, dict],
) -> tuple[nx.Graph, dict[int, list[str]], dict[int, str]]:
    G = nx.Graph()
    community_labels = {cid: label for label, cid in CLASS_IDS.items()}
    communities = {cid: [] for cid in CLASS_IDS.values()}
    openalex_to_id = {
        meta["openalex_id"]: paper_id
        for paper_id, meta in paper_meta.items()
        if meta.get("openalex_id")
    }

    for class_name, cid in CLASS_IDS.items():
        node_id = f"class::{slugify(class_name)}"
        G.add_node(
            node_id,
            label=class_name,
            file_type="document",
            source_file="graphify_corpus/method_classes.md",
            source_location="L1",
            definition=CLASS_DEFINITIONS[class_name],
            sections={
                "definition": {
                    "kind": "text",
                    "title": "Definition",
                    "content": CLASS_DEFINITIONS[class_name],
                    "refs": [],
                }
            },
        )
        communities[cid].append(node_id)

    paper_themes: dict[str, set[str]] = {}
    for row in rows:
        meta = paper_meta[row["paper_id"]]
        class_name = meta["method_class"]
        cid = CLASS_IDS[class_name]
        paper_themes[row["paper_id"]] = set(meta["themes"])
        G.add_node(
            row["paper_id"],
            label=meta["title"],
            file_type="paper",
            source_file=meta["source_file"],
            source_location="L1",
            doi=meta["doi"],
            year=meta["year"],
            journal=meta["journal"],
            authors=meta["authors"],
            display_label=meta["display_label"],
            method_class_key=meta.get("method_class_key", ""),
            method_class_label=meta.get("method_class_label", meta["method_class"]),
            method_class=meta["method_class"],
            wcm_completeness_key=meta.get("wcm_completeness_key", ""),
            wcm_completeness_label=meta.get("wcm_completeness_label", ""),
            wcm_completeness_definition=meta.get("wcm_completeness_definition", ""),
            wcm_completeness_source=meta.get("wcm_completeness_source", ""),
            wcm_completeness_confidence=meta.get("wcm_completeness_confidence", ""),
            organism=meta["organism"],
            organism_group=meta["organism_group"],
            pdf_status=meta["pdf_status"],
            pdf_file=meta["pdf_file"],
            pdf_href=meta["pdf_href"],
            landing_page_url=meta["landing_page_url"],
            abstract=meta["abstract"],
            summary=meta["summary"],
            contribution=meta["contribution"],
            modeled_objects=meta["modeled_objects"],
            primary_object=meta["primary_object"],
            themes=meta["themes"],
            sections=meta["sections"],
            cited_by_count=meta["cited_by_count"],
            citation_source=meta.get("citation_source", ""),
            openalex_id=meta["openalex_id"],
            source_href=meta["source_href"],
        )
        communities[cid].append(row["paper_id"])
        G.add_edge(
            row["paper_id"],
            f"class::{slugify(class_name)}",
            relation="belongs_to_method_class",
            confidence="EXTRACTED",
            confidence_score=1.0,
            source_file="metadata/wcm_method_classes.csv",
            source_location=None,
            weight=1.0,
        )

    for row in rows:
        source_id = row["paper_id"]
        refs = paper_meta[source_id]["referenced_works"]
        for ref_work in refs:
            target_id = openalex_to_id.get(ref_work)
            if not target_id or target_id == source_id:
                continue
            if G.has_edge(source_id, target_id) and G.edges[source_id, target_id].get("relation") == "belongs_to_method_class":
                continue
            G.add_edge(
                source_id,
                target_id,
                relation="cites",
                confidence="EXTRACTED",
                confidence_score=1.0,
                source_file=paper_meta[source_id]["source_file"],
                source_location=None,
                weight=1.0,
            )

    inferred_candidates: list[tuple[float, str, str, set[str]]] = []
    for left, right in combinations(rows, 2):
        left_id = left["paper_id"]
        right_id = right["paper_id"]
        if G.has_edge(left_id, right_id):
            continue
        shared = paper_themes[left_id] & paper_themes[right_id]
        same_class = classes[left_id]["method_class"] == classes[right_id]["method_class"]
        score = len(shared) + (1 if same_class else 0)
        if len(shared) >= 2 or score >= 3:
            inferred_candidates.append((score + len(shared) * 0.1, left_id, right_id, shared))

    inferred_candidates.sort(reverse=True)
    inferred_degree = Counter()
    for raw_score, left_id, right_id, shared in inferred_candidates:
        if inferred_degree[left_id] >= 4 or inferred_degree[right_id] >= 4:
            continue
        confidence_score = min(0.92, 0.45 + 0.12 * raw_score)
        G.add_edge(
            left_id,
            right_id,
            relation="semantically_similar_to",
            confidence="INFERRED",
            confidence_score=round(confidence_score, 2),
            source_file="metadata/whole_cell_model_papers_master_table.csv",
            source_location=None,
            weight=round(confidence_score, 2),
            note="shared themes: " + ", ".join(sorted(shared)),
        )
        inferred_degree[left_id] += 1
        inferred_degree[right_id] += 1

    G.graph["hyperedges"] = [
        {
            "id": f"hyper::{slugify(class_name)}",
            "label": class_name,
            "nodes": [node_id for node_id in communities[cid] if node_id.startswith("WCM-")],
            "relation": "participate_in",
            "confidence": "EXTRACTED",
            "confidence_score": 1.0,
            "source_file": "metadata/wcm_method_classes.csv",
        }
        for class_name, cid in CLASS_IDS.items()
    ]
    return G, communities, community_labels


def write_report(G: nx.Graph, communities: dict[int, list[str]], community_labels: dict[int, str], total_files: int) -> None:
    cohesion = score_all(G, communities)
    report = generate(
        G=G,
        communities=communities,
        cohesion_scores=cohesion,
        community_labels=community_labels,
        god_node_list=god_nodes(G, top_n=10),
        surprise_list=surprising_connections(G, communities=communities, top_n=8),
        detection_result={"total_files": total_files, "total_words": 0, "warning": None},
        token_cost={"input": 0, "output": 0},
        root="Whole-Cell Model Paper Collection",
        suggested_questions=suggest_questions(G, communities, community_labels, top_n=7),
    )
    (GRAPHIFY_OUT / "GRAPH_REPORT.md").write_text(report + "\n", encoding="utf-8")


def graphml_safe_copy(G: nx.Graph) -> nx.Graph:
    H = nx.Graph()
    for node_id, data in G.nodes(data=True):
        H.add_node(node_id, **{k: v for k, v in data.items() if v is not None and not isinstance(v, (list, dict))})
    for left, right, data in G.edges(data=True):
        H.add_edge(left, right, **{k: v for k, v in data.items() if v is not None and not isinstance(v, (list, dict))})
    return H


def write_metadata_json(paper_meta: dict[str, dict]) -> None:
    payload = {
        "papers": paper_meta,
        "provenance_note": (
            "Bullet-level provenance uses parsed local PDF page anchors when available, plus the curated note section "
            "and DOI landing page as fallbacks."
        ),
    }
    PAPER_METADATA_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (GRAPHIFY_OUT / "paper_metadata.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_live_inventory(rows: list[dict[str, str]], paper_meta: dict[str, dict]) -> None:
    fieldnames = [
        "paper_id",
        "title",
        "year",
        "journal",
        "doi",
        "method_class",
        "organism",
        "organism_group",
        "pdf_status",
        "pdf_file",
        "landing_page_url",
    ]
    with LIVE_INVENTORY_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item["paper_id"]):
            meta = paper_meta[row["paper_id"]]
            writer.writerow(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "year": row["year"],
                    "journal": row["journal"],
                    "doi": row["doi"],
                    "method_class": meta["method_class"],
                    "organism": meta["organism"],
                    "organism_group": meta["organism_group"],
                    "pdf_status": meta["pdf_status"],
                    "pdf_file": meta["pdf_file"],
                    "landing_page_url": meta["landing_page_url"],
                }
            )


def write_synced_inventory(rows: list[dict[str, str]], paper_meta: dict[str, dict], inventory_urls: dict[str, str]) -> None:
    existing = {}
    if INVENTORY_TABLE.exists():
        for row in load_csv(INVENTORY_TABLE):
            existing[row.get("paper_id", "")] = row
    fieldnames = [
        "paper_id",
        "title",
        "year",
        "journal",
        "doi",
        "pdf_status",
        "pdf_file",
        "pdf_url",
        "landing_page_url",
        "authors",
    ]
    with INVENTORY_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item["paper_id"]):
            meta = paper_meta[row["paper_id"]]
            old = existing.get(row["paper_id"], {})
            writer.writerow(
                {
                    "paper_id": row["paper_id"],
                    "title": row["paper_title"],
                    "year": row["year"],
                    "journal": row["journal"],
                    "doi": row["doi"],
                    "pdf_status": meta["pdf_status"],
                    "pdf_file": meta["pdf_file"],
                    "pdf_url": inventory_urls.get(row["paper_id"], old.get("pdf_url", "")),
                    "landing_page_url": meta["landing_page_url"],
                    "authors": old.get("authors", "; ".join(meta.get("authors", []))),
                }
            )


def rejected_pdf_reason(profile: dict) -> str:
    sample = " ".join(
        part
        for part in [
            profile.get("metadata_title", ""),
            *(profile.get("page_texts", [])[:2]),
        ]
        if part
    ).lower()
    if "patent application publication" in sample or "sheet 1 of" in sample:
        return "Patent or non-article legal document"
    if "supplementary information" in sample or "supplementary fig" in sample:
        return "Supplementary-only PDF"
    if "in format provided by" in sample:
        return "Formatted extract or supporting note, not the full article PDF"
    if "journal pre-proof" in sample:
        return "Pre-proof / publisher intermediary PDF"
    return "PDF failed article validation and needs manual review"


def write_sidecar_outputs(
    rows: list[dict[str, str]],
    paper_meta: dict[str, dict],
    inventory_urls: dict[str, str],
) -> None:
    row_by_id = {row["paper_id"]: row for row in rows}
    review_fields = [
        "paper_id",
        "title",
        "year",
        "journal",
        "doi",
        "method_class",
        "organism",
        "pdf_status",
        "candidate_pdf_url",
        "landing_page_url",
        "next_action",
        "notes",
    ]
    with SIDECAR_REVIEW_QUEUE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_fields)
        writer.writeheader()
        for paper_id in sorted(row_by_id):
            meta = paper_meta[paper_id]
            if meta["pdf_status"] == "downloaded":
                continue
            row = row_by_id[paper_id]
            candidate_pdf_url = inventory_urls.get(paper_id, "")
            notes = "Publisher landing page only."
            if candidate_pdf_url:
                notes = "Known PDF-like URL exists but did not validate from this environment."
            writer.writerow(
                {
                    "paper_id": paper_id,
                    "title": row["paper_title"],
                    "year": row["year"],
                    "journal": row["journal"],
                    "doi": row["doi"],
                    "method_class": meta["method_class"],
                    "organism": meta["organism"],
                    "pdf_status": meta["pdf_status"],
                    "candidate_pdf_url": candidate_pdf_url,
                    "landing_page_url": meta["landing_page_url"],
                    "next_action": "Manually add a validated article PDF into pdfs/ and rerun the graph builder.",
                    "notes": notes,
                }
            )

    rejected_fields = [
        "expected_paper_id",
        "expected_title",
        "rejected_file",
        "reason",
        "recommended_action",
    ]
    known_ids = {row["paper_id"]: row for row in rows}
    with SIDECAR_REJECTED_QUEUE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rejected_fields)
        writer.writeheader()
        for pdf_path in sorted(REJECTED_PDF_DIR.glob("*.pdf")):
            expected_id = pdf_path.name.split("_")[0]
            row = known_ids.get(expected_id)
            try:
                profile = extract_pdf_profile(pdf_path)
                reason = rejected_pdf_reason(profile)
            except Exception:
                reason = "Could not parse rejected PDF; manual review needed."
            writer.writerow(
                {
                    "expected_paper_id": expected_id if row else "",
                    "expected_title": row["paper_title"] if row else "",
                    "rejected_file": pdf_path.name,
                    "reason": reason,
                    "recommended_action": "Replace with the full validated article PDF or keep quarantined.",
                }
            )

    blocked_count = sum(1 for meta in paper_meta.values() if meta["pdf_status"] != "downloaded")
    rejected_count = len(list(REJECTED_PDF_DIR.glob("*.pdf")))
    summary = [
        "# PDF Sidecar Review",
        "",
        f"- Papers still needing validated PDFs: {blocked_count}",
        f"- Rejected/quarantined PDFs: {rejected_count}",
        "",
        "Files:",
        f"- `{SIDECAR_REVIEW_QUEUE.relative_to(ROOT).as_posix()}`",
        f"- `{SIDECAR_REJECTED_QUEUE.relative_to(ROOT).as_posix()}`",
        "",
        "Workflow:",
        "1. Review `pdf_sidecar_review_queue.csv` for papers still blocked by landing-page-only access.",
        "2. Drop a validated article PDF into `pdfs/` using the paper ID prefix when possible, for example `WCM-018_<title>.pdf`.",
        "3. Review `pdf_sidecar_rejected.csv` for files that were quarantined because they looked like supplements, patents, or mismatched PDFs.",
        "4. Rerun `python scripts/build_wcm_graph.py` to rescan and refresh the graph.",
    ]
    SIDECAR_SUMMARY.write_text("\n".join(summary) + "\n", encoding="utf-8")


def write_summary(rows: list[dict[str, str]], classes: dict[str, dict[str, str]], G: nx.Graph) -> None:
    class_counts = Counter(classes[row["paper_id"]]["method_class"] for row in rows)
    downloaded_count = sum(1 for row in rows if row["pdf_status"] == "downloaded")
    color_names = {
        0: "Blue",
        1: "Orange",
        2: "Red",
        3: "Green",
        4: "Teal",
        5: "Purple",
    }
    lines = [
        "# WCM Graph Build",
        "",
        "- Papers: {}".format(len(rows)),
        "- Downloaded PDFs: {}".format(downloaded_count),
        "- Nodes: {}".format(G.number_of_nodes()),
        "- Edges: {}".format(G.number_of_edges()),
        "",
        "Outputs:",
        "- `graphify-out/graph.html` (enhanced WCM viewer)",
        "- `graphify-out/graph_base.html` (raw Graphify HTML export)",
        "- `graphify-out/graph.json`",
        "- `graphify-out/graph.graphml`",
        "- `graphify-out/graph.svg`",
        "- `graphify-out/GRAPH_REPORT.md`",
        "- `graphify-out/paper_metadata.json`",
        "- `metadata/live_paper_inventory.csv`",
        "- `metadata/pdf_processing_status.csv`",
        "- `metadata/pdf_sidecar_review_queue.csv`",
        "- `metadata/pdf_sidecar_rejected.csv`",
        "- `metadata/zotero_sync_state.json`",
        "",
        "Color mapping in the graph:",
        "",
        "Viewer layouts:",
        "- Force layout",
        "- Timeline layout (by year)",
        "- Organism layout (manually curated organism groups)",
        "",
        "PDF sync:",
        "- Rebuilds treat `pdfs/` as the graph source of truth and rescan only top-level PDFs, ignoring `pdfs/_rejected/`",
        "- Unchanged PDFs reuse cached parse results from `metadata/pdf_parse_cache.json` instead of being reparsed",
        "- If `ZOTERO_USER_ID` and `ZOTERO_API_KEY` are set, the builder pulls PDFs from the Zotero `Whole Cell Model` collection into `pdfs/` before graph generation",
        "- If `ZOTERO_UPLOAD_LOCAL=1` is set, local PDFs missing from the Zotero collection are uploaded back to Zotero after local matching",
        "- Manually added PDFs with resolvable DOI/title can be auto-ingested as new paper nodes",
        "- Bullet provenance prefers parsed local PDF page anchors and falls back to the curated note + DOI record",
        "",
        "Regenerate with:",
        "```bash",
        "python scripts/build_wcm_graph.py",
        "```",
    ]
    class_lines = ["- {}: {}".format(class_name, class_counts[class_name]) for class_name, _cid in ordered_class_items()]
    lines[6:6] = class_lines
    color_lines = [
        "- {}: {}".format(color_names.get(cid, f"Color {cid + 1}"), class_name)
        for class_name, cid in ordered_class_items()
    ]
    color_header_index = lines.index("Color mapping in the graph:")
    lines[color_header_index + 1 : color_header_index + 1] = color_lines
    (GRAPHIFY_OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_enhanced_html(graph_data: dict, community_labels: dict[int, str]) -> None:
    template = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WCM Graph Explorer</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: Inter, system-ui, sans-serif; background: #0f172a; color: #e2e8f0; display: grid; grid-template-columns: 1fr 360px; height: 100vh; overflow: hidden; }
  #main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  #toolbar { padding: 12px 16px; border-bottom: 1px solid #1e293b; display: flex; gap: 12px; align-items: center; background: linear-gradient(90deg, #111827, #0f172a); }
  #title-block { min-width: 220px; }
  #title-block h1 { margin: 0; font-size: 16px; }
  #title-block p { margin: 4px 0 0; color: #94a3b8; font-size: 12px; }
  #controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .control-label { font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.04em; }
  .button-group { display: flex; gap: 6px; }
  button, select, input { background: #111827; color: #e2e8f0; border: 1px solid #334155; border-radius: 10px; padding: 8px 10px; font-size: 13px; }
  button { cursor: pointer; }
  button.active { border-color: #38bdf8; box-shadow: 0 0 0 1px #38bdf8 inset; }
  #search { width: 240px; }
  #graph { flex: 1 1 auto; position: relative; min-height: 560px; width: 100%; height: 100%; overflow: hidden; }
  #network-canvas { position: absolute; inset: 0; z-index: 1; }
  #layout-guides { position: absolute; inset: 0; pointer-events: none; z-index: 6; }
  .guide-line { position: absolute; top: 54px; bottom: 10px; width: 0; border-left: 1.5px dashed rgba(148, 163, 184, 0.42); }
  .guide-line-horizontal { left: 14px; right: 14px; height: 0; width: auto; border-left: none; border-top: 1.5px dashed rgba(148, 163, 184, 0.5); }
  .guide-label { position: absolute; top: 10px; transform: translateX(-50%); color: #e2e8f0; font-size: 12px; letter-spacing: 0.05em; text-transform: uppercase; background: rgba(15, 23, 42, 0.92); padding: 4px 8px; border-radius: 999px; border: 1px solid rgba(148, 163, 184, 0.28); white-space: nowrap; }
  .guide-label-horizontal { transform: none; }
  #legend { position: absolute; left: 16px; bottom: 92px; display: flex; gap: 8px; background: rgba(15, 23, 42, 0.82); padding: 10px 12px; border-radius: 14px; border: 1px solid rgba(148, 163, 184, 0.2); backdrop-filter: blur(10px); z-index: 7; flex-wrap: wrap; max-width: min(760px, calc(100% - 32px)); }
  #graph .vis-network { z-index: 1; }
  #graph .vis-navigation { left: 16px !important; bottom: 16px !important; top: auto !important; }
  .legend-item { position: relative; display: flex; align-items: center; gap: 6px; font-size: 12px; color: #cbd5e1; padding: 4px 6px; border-radius: 10px; }
  .legend-item:hover { background: rgba(30, 41, 59, 0.92); }
  .legend-dot { width: 10px; height: 10px; border-radius: 999px; }
  .legend-popover { display: none; position: absolute; left: 0; bottom: calc(100% + 8px); width: 260px; background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 10px 12px; z-index: 30; box-shadow: 0 10px 24px rgba(0, 0, 0, 0.35); }
  .legend-item:hover .legend-popover { display: block; }
  .legend-popover h4 { margin: 0 0 6px; font-size: 11px; color: #93c5fd; text-transform: uppercase; letter-spacing: 0.05em; }
  .legend-popover p { margin: 0; font-size: 12px; line-height: 1.55; color: #cbd5e1; }
  #sidebar { border-left: 1px solid #1e293b; background: #0b1220; overflow-y: auto; padding: 18px; }
  #empty-state { color: #94a3b8; font-size: 14px; line-height: 1.6; }
  .meta-header h2 { margin: 0; font-size: 20px; line-height: 1.25; }
  .meta-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 14px 0 18px; }
  .meta-card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 10px 12px; }
  .meta-card .label { display: block; color: #94a3b8; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .meta-card .value { font-size: 13px; line-height: 1.4; }
  .chip-row { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
  .chip { padding: 5px 8px; border-radius: 999px; font-size: 11px; border: 1px solid rgba(148, 163, 184, 0.25); background: #111827; color: #dbeafe; }
  .section { margin-bottom: 18px; }
  .section h3 { margin: 0 0 8px; font-size: 13px; color: #93c5fd; text-transform: uppercase; letter-spacing: 0.05em; }
  .section p { margin: 0; color: #e5e7eb; font-size: 14px; line-height: 1.7; }
  .bullet-list { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 10px; }
  .bullet-item { position: relative; color: #e5e7eb; font-size: 14px; line-height: 1.6; }
  .ref-trigger { display: inline-flex; align-items: center; justify-content: center; margin-left: 8px; min-width: 18px; height: 18px; border-radius: 999px; font-size: 11px; background: #1d4ed8; color: white; cursor: default; }
  .ref-popover { display: none; position: absolute; left: 0; top: calc(100% + 6px); width: 280px; background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 10px 12px; z-index: 50; box-shadow: 0 10px 24px rgba(0, 0, 0, 0.35); }
  .bullet-item:hover .ref-popover, .ref-trigger:hover + .ref-popover { display: block; }
  .ref-popover h4 { margin: 0 0 8px; font-size: 11px; color: #93c5fd; text-transform: uppercase; letter-spacing: 0.05em; }
  .ref-entry { margin-bottom: 8px; font-size: 12px; line-height: 1.5; color: #cbd5e1; }
  .ref-entry a { color: #7dd3fc; text-decoration: none; }
  .ref-entry a:hover { text-decoration: underline; }
  #neighbors { margin-top: 18px; }
  #neighbors h3 { margin: 0 0 8px; font-size: 13px; color: #93c5fd; text-transform: uppercase; letter-spacing: 0.05em; }
  .neighbor { display: block; width: 100%; text-align: left; padding: 8px 10px; margin-bottom: 6px; border-radius: 10px; background: #111827; border: 1px solid #1f2937; color: #e5e7eb; }
  .neighbor small { display: block; color: #94a3b8; margin-top: 2px; }
  .viewer-note { margin-top: 18px; padding: 10px 12px; border-radius: 12px; background: #111827; border: 1px solid #1f2937; color: #cbd5e1; font-size: 12px; line-height: 1.6; }
  @media (max-width: 1080px) { body { grid-template-columns: 1fr; grid-template-rows: 58vh 42vh; } #main { min-height: 0; } #graph { min-height: 360px; } #sidebar { border-left: none; border-top: 1px solid #1e293b; } }
</style>
</head>
<body>
  <div id="main">
    <div id="toolbar">
      <div id="title-block">
        <h1>Whole-Cell Model Explorer</h1>
        <p>Graphify graph with richer WCM metadata, alternate layouts, and provenance-aware bullets.</p>
      </div>
      <div id="controls">
        <span class="control-label">Layout</span>
        <div class="button-group">
          <button data-layout="force" class="layout-btn active">Force</button>
          <button data-layout="year" class="layout-btn">By Year</button>
          <button data-layout="organism" class="layout-btn">By Organism</button>
          <button data-layout="virtual" class="layout-btn">Virtual Cells</button>
        </div>
        <input id="search" type="search" placeholder="Search papers, authors, organism, journals..." />
      </div>
    </div>
    <div id="graph">
      <div id="network-canvas"></div>
      <div id="layout-guides"></div>
      <div id="legend"></div>
    </div>
  </div>
  <aside id="sidebar">
    <div id="empty-state">
      Select a node to inspect its metadata. The viewer panel is modular: it renders from each node's section payload, so new sections can be added later without changing the panel structure.
    </div>
    <div id="details" style="display:none;"></div>
  </aside>
<script>
const GRAPH_DATA = __GRAPH_DATA__;
const COMMUNITY_LABELS = __COMMUNITY_LABELS__;
const COMMUNITY_COLORS = __COMMUNITY_COLORS__;
const COMMUNITY_DEFINITIONS = __COMMUNITY_DEFINITIONS__;
const SECTION_ORDER = ['overview', 'abstract', 'methods_summary', 'limitations', 'future_work'];
const ORGANISM_GROUP_ORDER = __ORGANISM_GROUP_ORDER__;
const ORGANISM_GROUP_LABELS = __ORGANISM_GROUP_LABELS__;
const ORGANISM_COMPLETENESS_DIVIDER_Y = -10;
const ORGANISM_COMPLETE_START_Y = -300;
const ORGANISM_INCOMPLETE_START_Y = 90;
const ORGANISM_ROW_STEP = 118;
const VIRTUAL_GROUP_ORDER = ['complete', 'partial', 'related'];
const VIRTUAL_GROUP_LABELS = {
  complete: 'Complete WCM',
  partial: 'Partial WCM',
  related: 'Related Model',
};
const VIRTUAL_GROUP_DEFINITIONS = {
  complete: 'A true whole-cell model — covers the cell broadly enough to count as a virtual cell in this collection.',
  partial: 'A substantial but still incomplete WCM (half-WCM, organism-wide model missing major layers).',
  related: 'A related modeling, subsystem, review, or enabling paper that supports virtual-cell construction.',
};
const VIRTUAL_TOTAL_WIDTH = 1500;
const VIRTUAL_ROW_START_Y = -340;
const VIRTUAL_ROW_STEP = 56;
const VIRTUAL_SUBCOL_WIDTH = 110;
const VIRTUAL_SUBCOL_THRESHOLD = 20;

const nodeById = new Map(GRAPH_DATA.nodes.map(node => [node.id, node]));
const edgeByPair = new Map();
for (const edge of GRAPH_DATA.links) {
  const key = [edge.source, edge.target].sort().join('::');
  edgeByPair.set(key, edge);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function layoutColor(node) {
  return COMMUNITY_COLORS[String(node.community)] || '#94a3b8';
}

let currentLayout = 'force';
let idleBase = { position: { x: 0, y: 0 }, scale: 1 };
let idlePausedUntil = Date.now() + 1800;
let hoverPaused = false;
let activeSelection = null;
const nodeStyleCache = new Map();
const edgeStyleCache = new Map();
let visNodes = null;
let visEdges = null;
let network = null;
let currentGuideSpecs = [];
let searchResultIds = null;

GRAPH_DATA.nodes.forEach(node => {
  const style = {
    id: node.id,
    label: node.file_type === 'paper' ? (node.display_label || node.label) : node.label,
    color: {
      background: layoutColor(node),
      border: layoutColor(node),
      highlight: { background: '#ffffff', border: layoutColor(node) },
    },
    font: {
      color: '#ffffff',
      size: node.file_type === 'paper' ? 11 : 16,
      face: 'Inter, system-ui, sans-serif',
      bold: node.file_type !== 'paper',
    },
    shape: node.file_type === 'paper' ? 'dot' : 'box',
    size: node.file_type === 'paper' ? 18 : 24,
    title: node.file_type === 'paper' ? node.label : node.label,
  };
  nodeStyleCache.set(node.id, JSON.parse(JSON.stringify(style)));
});

GRAPH_DATA.links.forEach((edge, index) => {
  const style = {
    id: index,
    from: edge.source,
    to: edge.target,
    width: edge.confidence === 'EXTRACTED' ? 2.1 : 1.2,
    dashes: edge.confidence !== 'EXTRACTED',
    color: { color: '#94a3b8', opacity: edge.confidence === 'EXTRACTED' ? 0.5 : 0.22 },
    title: `${edge.relation} [${edge.confidence}]`,
  };
  edgeStyleCache.set(index, JSON.parse(JSON.stringify(style)));
});

const graphContainer = document.getElementById('graph');
const networkCanvas = document.getElementById('network-canvas');

function ensureGraphContainerSize() {
  if (!graphContainer.style.height || graphContainer.clientHeight < 320) {
    graphContainer.style.height = `${Math.max(window.innerHeight - 90, 420)}px`;
  }
  networkCanvas.style.height = '100%';
}

ensureGraphContainerSize();

function renderLegend() {
  const legend = document.getElementById('legend');
  legend.innerHTML = Object.entries(COMMUNITY_LABELS)
    .map(([cid, label]) => `
      <div class="legend-item">
        <span class="legend-dot" style="background:${COMMUNITY_COLORS[cid]};"></span>
        <span>${escapeHtml(label)}</span>
        <div class="legend-popover">
          <h4>${escapeHtml(label)}</h4>
          <p>${escapeHtml(COMMUNITY_DEFINITIONS[label] || '')}</p>
        </div>
      </div>
    `)
    .join('');
}

function normalizeSearchText(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
}

function searchScore(node, normalizedQuery) {
  const authors = (node.authors || []).map(author => normalizeSearchText(author));
  if (authors.some(author => author === normalizedQuery)) return 500;
  if (authors.some(author => author.startsWith(normalizedQuery))) return 420;
  if (authors.some(author => author.includes(normalizedQuery))) return 360;

  const displayLabel = normalizeSearchText(node.display_label || '');
  const title = normalizeSearchText(node.label || '');
  const journal = normalizeSearchText(node.journal || '');
  const organism = normalizeSearchText(`${node.organism || ''} ${node.organism_group || ''}`);
  const methodClass = normalizeSearchText(node.method_class || '');
  const extra = normalizeSearchText([...(node.modeled_objects || []), ...(node.themes || [])].join(' '));

  if (title === normalizedQuery) return 320;
  if (title.startsWith(normalizedQuery)) return 280;
  if (displayLabel.includes(normalizedQuery)) return 220;
  if (title.includes(normalizedQuery)) return 200;
  if (journal.includes(normalizedQuery)) return 120;
  if (organism.includes(normalizedQuery)) return 110;
  if (methodClass.includes(normalizedQuery)) return 100;
  if (extra.includes(normalizedQuery)) return 80;
  return 0;
}

function yearGuideSpecs(years) {
  const totalWidth = Math.max(1800, years.length * 120);
  const step = years.length > 1 ? totalWidth / (years.length - 1) : 0;
  return years.map((year, index) => ({
    key: `year-${year}`,
    label: year,
    x: -(totalWidth / 2) + index * step,
  }));
}

function virtualGuideSpecs() {
  const totalWidth = VIRTUAL_TOTAL_WIDTH;
  const step = VIRTUAL_GROUP_ORDER.length > 1 ? totalWidth / (VIRTUAL_GROUP_ORDER.length - 1) : 0;
  return VIRTUAL_GROUP_ORDER.map((key, index) => ({
    key: `virtual-${key}`,
    label: VIRTUAL_GROUP_LABELS[key] || key,
    x: -(totalWidth / 2) + index * step,
    orientation: 'vertical',
  }));
}

function positionForVirtualLayout(node, indexWithinGroup, groupSize) {
  const totalWidth = VIRTUAL_TOTAL_WIDTH;
  const step = VIRTUAL_GROUP_ORDER.length > 1 ? totalWidth / (VIRTUAL_GROUP_ORDER.length - 1) : 0;
  if (node.file_type !== 'paper') {
    return { x: -(totalWidth / 2) - 200, y: -560 };
  }
  const key = String(node.wcm_completeness_key || 'related');
  const groupIndex = Math.max(0, VIRTUAL_GROUP_ORDER.indexOf(key));
  const baseX = -(totalWidth / 2) + groupIndex * step;
  // Wrap large groups (e.g. 'related' with 50 papers) into a 2-column block so
  // the column doesn't run off the bottom of the canvas.
  const useSubcols = (groupSize || 0) > VIRTUAL_SUBCOL_THRESHOLD;
  if (!useSubcols) {
    return { x: baseX, y: VIRTUAL_ROW_START_Y + indexWithinGroup * VIRTUAL_ROW_STEP };
  }
  const rowsPerCol = Math.ceil(groupSize / 2);
  const subcol = Math.floor(indexWithinGroup / rowsPerCol);
  const rowInSubcol = indexWithinGroup % rowsPerCol;
  return {
    x: baseX + (subcol === 0 ? -VIRTUAL_SUBCOL_WIDTH / 2 : VIRTUAL_SUBCOL_WIDTH / 2),
    y: VIRTUAL_ROW_START_Y + rowInSubcol * VIRTUAL_ROW_STEP,
  };
}

function organismGuideSpecs(groups) {
  const totalWidth = Math.max(1500, groups.length * 180);
  const step = groups.length > 1 ? totalWidth / (groups.length - 1) : 0;
  const verticals = groups.map((group, index) => ({
    key: `organism-${group.replace(/[^a-z0-9]+/gi, '-')}`,
    label: ORGANISM_GROUP_LABELS[group] || group,
    x: -(totalWidth / 2) + index * step,
    orientation: 'vertical',
  }));
  verticals.push({
    key: 'organism-completeness-divider',
    label: 'Complete WCMs',
    y: ORGANISM_COMPLETENESS_DIVIDER_Y,
    labelX: -(totalWidth / 2) + 24,
    orientation: 'horizontal',
  });
  return verticals;
}

function updateGuidePositions() {
  if (!network || !currentGuideSpecs.length) return;
  currentGuideSpecs.forEach(spec => {
    document.querySelectorAll(`[data-guide-key="${spec.key}"]`).forEach(el => {
      if (spec.orientation === 'horizontal') {
        const point = network.canvasToDOM({ x: 0, y: spec.y || 0 });
        if (el.classList.contains('guide-line-horizontal')) {
          el.style.top = `${point.y}px`;
        } else {
          const labelPoint = network.canvasToDOM({ x: spec.labelX || 0, y: spec.y || 0 });
          el.style.left = `${labelPoint.x}px`;
          el.style.top = `${point.y - 30}px`;
        }
      } else {
        const point = network.canvasToDOM({ x: spec.x, y: 0 });
        el.style.left = `${point.x}px`;
      }
    });
  });
}

function completenessRank(node) {
  const key = String(node.wcm_completeness_key || 'related');
  if (key === 'complete') return 0;
  if (key === 'partial') return 1;
  return 2;
}

function compareOrganismNodes(left, right) {
  return (
    completenessRank(left) - completenessRank(right) ||
    ((left.year || 0) - (right.year || 0)) ||
    String(left.label || '').localeCompare(String(right.label || ''))
  );
}

function setIdleBaseFromCurrentView() {
  if (!network) return;
  idleBase = {
    position: network.getViewPosition(),
    scale: network.getScale(),
  };
}

function markInteractionPause(ms = 6000) {
  idlePausedUntil = Date.now() + ms;
  setIdleBaseFromCurrentView();
}

function nodeStyleForLayout(node, layoutName, targets = null) {
  const style = JSON.parse(JSON.stringify(nodeStyleCache.get(node.id)));
  const structuredView = layoutName !== 'force';
  if (structuredView && node.file_type !== 'paper') {
    style.hidden = true;
    style.physics = false;
    style.fixed = { x: true, y: true };
    return style;
  }
  if (structuredView) {
    style.font.size = 10;
    style.size = 16;
  } else {
    style.font.size = node.file_type === 'paper' ? 11 : 16;
    style.size = node.file_type === 'paper' ? 18 : 24;
  }
  style.hidden = false;
  if (structuredView && targets && targets[node.id]) {
    style.x = targets[node.id].x;
    style.y = targets[node.id].y;
    style.fixed = { x: true, y: true };
    style.physics = false;
  } else {
    style.fixed = false;
    style.physics = true;
  }
  return style;
}

function edgeStyleForLayout(edge, index, layoutName) {
  const style = JSON.parse(JSON.stringify(edgeStyleCache.get(index)));
  const sourceNode = nodeById.get(edge.source);
  const targetNode = nodeById.get(edge.target);
  const structuredView = layoutName !== 'force';
  if (structuredView && (sourceNode?.file_type !== 'paper' || targetNode?.file_type !== 'paper')) {
    style.hidden = true;
    return style;
  }
  style.hidden = false;
  if (structuredView) {
    style.width = edge.confidence === 'EXTRACTED' ? 1.7 : 0.9;
    style.color = { color: '#94a3b8', opacity: edge.confidence === 'EXTRACTED' ? 0.24 : 0.1 };
  }
  return style;
}

function restoreGraphFocus() {
  activeSelection = null;
  if (!visNodes || !visEdges) return;
  const targets = currentLayout === 'force' ? null : buildTargets(currentLayout);
  visNodes.update(GRAPH_DATA.nodes.map(node => nodeStyleForLayout(node, currentLayout, targets)));
  visEdges.update(GRAPH_DATA.links.map((edge, index) => edgeStyleForLayout(edge, index, currentLayout)));
}

function applySearchFilter(matchIds) {
  if (!visNodes || !visEdges || !network) return;
  searchResultIds = new Set(matchIds);
  activeSelection = null;
  const targets = currentLayout === 'force' ? null : buildTargets(currentLayout);
  visNodes.update(GRAPH_DATA.nodes.map(node => {
    const base = nodeStyleForLayout(node, currentLayout, targets);
    if (node.file_type !== 'paper' || !searchResultIds.has(node.id)) {
      base.hidden = true;
    }
    return base;
  }));
  visEdges.update(GRAPH_DATA.links.map((edge, index) => {
    const base = edgeStyleForLayout(edge, index, currentLayout);
    if (!searchResultIds.has(edge.source) || !searchResultIds.has(edge.target)) {
      base.hidden = true;
    }
    return base;
  }));
  network.unselectAll();
  if (matchIds.length) {
    network.fit({ nodes: matchIds, animation: { duration: 350, easingFunction: 'easeInOutQuad' } });
  }
}

function clearSearchFilter() {
  searchResultIds = null;
  restoreGraphFocus();
  if (network) {
    if (currentLayout === 'force') {
      network.fit({ animation: false });
    } else {
      network.fit({ animation: { duration: 350, easingFunction: 'easeInOutQuad' } });
      updateGuidePositions();
    }
  }
}

function relatedNodeIds(nodeId) {
  const related = new Set([nodeId]);
  const direct = network.getConnectedNodes(nodeId);
  direct.forEach(id => {
    if (!searchResultIds || searchResultIds.has(id)) {
      related.add(id);
    }
  });
  const selectedNode = nodeById.get(nodeId);
  if (selectedNode?.file_type === 'paper') {
    GRAPH_DATA.nodes
      .filter(node => node.file_type === 'paper' && node.method_class === selectedNode.method_class)
      .forEach(node => {
        if (!searchResultIds || searchResultIds.has(node.id)) {
          related.add(node.id);
        }
      });
  }
  return related;
}

function applyFocusMode(nodeId) {
  activeSelection = nodeId;
  if (!visNodes || !visEdges || !network) return;
  const related = relatedNodeIds(nodeId);
  const targets = currentLayout === 'force' ? null : buildTargets(currentLayout);
  const nodeUpdates = GRAPH_DATA.nodes.map(node => {
    const base = nodeStyleForLayout(node, currentLayout, targets);
    if (searchResultIds && !searchResultIds.has(node.id)) {
      base.hidden = true;
      return base;
    }
    if (base.hidden) {
      return base;
    }
    const isRelated = related.has(node.id);
    if (!isRelated) {
      base.color.background = 'rgba(71, 85, 105, 0.22)';
      base.color.border = 'rgba(71, 85, 105, 0.28)';
      base.font.color = 'rgba(226, 232, 240, 0.32)';
      base.size = node.file_type === 'paper' ? 13 : 18;
    } else if (node.id === nodeId) {
      base.color.background = '#f8fafc';
      base.color.border = layoutColor(node);
      base.font.color = '#dc2626';
      base.font.multi = 'md';
      base.label = `**${base.label}**`;
      base.size = node.file_type === 'paper' ? 26 : 30;
    } else {
      base.size = node.file_type === 'paper' ? 21 : 26;
    }
    return base;
  });
  const edgeUpdates = GRAPH_DATA.links.map((edge, index) => {
    const base = edgeStyleForLayout(edge, index, currentLayout);
    if (searchResultIds && (!searchResultIds.has(edge.source) || !searchResultIds.has(edge.target))) {
      base.hidden = true;
      return base;
    }
    if (base.hidden) {
      return base;
    }
    const connected = edge.source === nodeId || edge.target === nodeId || (related.has(edge.source) && related.has(edge.target));
    if (connected) {
      base.color = { color: '#cbd5e1', opacity: edge.source === nodeId || edge.target === nodeId ? 0.92 : 0.5 };
      base.width = edge.source === nodeId || edge.target === nodeId ? 3.4 : 2.2;
    } else {
      base.color = { color: '#475569', opacity: 0.08 };
      base.width = 0.8;
    }
    return base;
  });
  visNodes.update(nodeUpdates);
  visEdges.update(edgeUpdates);
}

function baseNetworkOptions(layoutName) {
  return {
    physics: {
      enabled: layoutName === 'force',
      solver: 'forceAtlas2Based',
      stabilization: { iterations: 200, fit: true },
      forceAtlas2Based: {
        gravitationalConstant: -42,
        centralGravity: 0.01,
        springLength: 130,
        springConstant: 0.06,
        damping: 0.45,
      },
    },
    interaction: { hover: true, tooltipDelay: 120, navigationButtons: true },
    nodes: { borderWidth: 1.5 },
    edges: { smooth: { type: 'continuous', roundness: 0.18 } },
  };
}

function requestedLayoutFromHash() {
  const hash = (window.location.hash || '').replace('#', '').toLowerCase();
  if (hash === 'year' || hash === 'organism' || hash === 'force' || hash === 'virtual') {
    return hash;
  }
  return 'force';
}

function syncActiveLayoutButton(layoutName) {
  document.querySelectorAll('.layout-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.layout === layoutName);
  });
}

function buildTargets(layoutName) {
  if (layoutName === 'force') {
    renderGuides([]);
    return {};
  }
  if (layoutName === 'virtual') {
    renderGuides(virtualGuideSpecs());
    const grouped = {};
    for (const node of GRAPH_DATA.nodes.filter(node => node.file_type === 'paper')) {
      const key = String(node.wcm_completeness_key || 'related');
      grouped[key] = grouped[key] || [];
      grouped[key].push(node);
    }
    const targets = {};
    for (const key of VIRTUAL_GROUP_ORDER) {
      const nodes = (grouped[key] || []).sort((a, b) =>
        (a.year || 0) - (b.year || 0) ||
        String(a.label || '').localeCompare(String(b.label || ''))
      );
      nodes.forEach((node, idx) => {
        targets[node.id] = positionForVirtualLayout(node, idx, nodes.length);
      });
    }
    for (const node of GRAPH_DATA.nodes.filter(node => node.file_type !== 'paper')) {
      targets[node.id] = positionForVirtualLayout(node, 0, 0);
    }
    return targets;
  }
  if (layoutName === 'year') {
    const years = [...new Set(GRAPH_DATA.nodes.filter(node => node.file_type === 'paper').map(node => String(node.year)))].sort();
    renderGuides(yearGuideSpecs(years));
    const grouped = {};
    for (const node of GRAPH_DATA.nodes.filter(node => node.file_type === 'paper')) {
      grouped[node.year] = grouped[node.year] || [];
      grouped[node.year].push(node);
    }
    const targets = {};
    for (const [year, nodes] of Object.entries(grouped)) {
      nodes.sort((a, b) => a.label.localeCompare(b.label));
      nodes.forEach((node, idx) => { targets[node.id] = positionForYearLayout(node, years, idx); });
    }
    for (const node of GRAPH_DATA.nodes.filter(node => node.file_type !== 'paper')) {
      targets[node.id] = positionForYearLayout(node, years, 0);
    }
    return targets;
  }
  const grouped = {};
  for (const node of GRAPH_DATA.nodes.filter(node => node.file_type === 'paper')) {
    const key = node.organism_group || 'General bacteria';
    grouped[key] = grouped[key] || [];
    grouped[key].push(node);
  }
  const groupOrder = [
    ...ORGANISM_GROUP_ORDER,
    ...Object.keys(grouped).filter(groupName => !ORGANISM_GROUP_ORDER.includes(groupName)).sort(),
  ];
  renderGuides(organismGuideSpecs(groupOrder));
  const targets = {};
  for (const groupName of groupOrder) {
    const nodes = (grouped[groupName] || []).sort(compareOrganismNodes);
    const completeNodes = nodes.filter(node => String(node.wcm_completeness_key || '') === 'complete');
    const incompleteNodes = nodes.filter(node => String(node.wcm_completeness_key || '') !== 'complete');
    completeNodes.forEach((node, idx) => {
      targets[node.id] = positionForOrganismLayout(node, groupOrder, idx, 'complete');
    });
    incompleteNodes.forEach((node, idx) => {
      targets[node.id] = positionForOrganismLayout(node, groupOrder, idx, 'incomplete');
    });
  }
  for (const node of GRAPH_DATA.nodes.filter(node => node.file_type !== 'paper')) {
    targets[node.id] = positionForOrganismLayout(node, groupOrder, 0, 'incomplete');
  }
  return targets;
}

function buildNodeDataset(layoutName) {
  const targets = buildTargets(layoutName);
  return new vis.DataSet(GRAPH_DATA.nodes.map(node => nodeStyleForLayout(node, layoutName, targets)));
}

function buildEdgeDataset(layoutName) {
  return new vis.DataSet(GRAPH_DATA.links.map((edge, index) => edgeStyleForLayout(edge, index, layoutName)));
}

function attachNetworkEvents() {
  if (!network) return;
  network.on('click', params => {
    markInteractionPause();
    if (params.nodes.length) {
      focusNode(params.nodes[0]);
    } else {
      restoreGraphFocus();
      network.unselectAll();
    }
  });

  ['dragStart', 'dragEnd', 'zoom', 'doubleClick'].forEach(eventName => {
    network.on(eventName, () => markInteractionPause());
  });

  network.on('hoverNode', () => {
    hoverPaused = true;
    markInteractionPause(1500);
  });
  network.on('blurNode', () => {
    hoverPaused = false;
    markInteractionPause(1200);
  });
  network.on('hoverEdge', () => {
    hoverPaused = true;
    markInteractionPause(1500);
  });
  network.on('blurEdge', () => {
    hoverPaused = false;
    markInteractionPause(1200);
  });
  network.on('afterDrawing', () => {
    updateGuidePositions();
  });
}

function mountNetwork(layoutName) {
  currentLayout = layoutName;
  syncActiveLayoutButton(layoutName);
  ensureGraphContainerSize();
  activeSelection = null;
  visNodes = buildNodeDataset(layoutName);
  visEdges = buildEdgeDataset(layoutName);
  if (network) {
    network.destroy();
  }
  network = new vis.Network(networkCanvas, { nodes: visNodes, edges: visEdges }, baseNetworkOptions(layoutName));
  attachNetworkEvents();
  if (layoutName === 'force') {
    network.once('stabilizationIterationsDone', () => {
      network.setOptions({ physics: { enabled: false } });
      network.fit({ animation: false });
      const searchQuery = document.getElementById('search')?.value?.trim();
      if (searchQuery) {
        document.getElementById('search').dispatchEvent(new Event('input'));
      }
      setTimeout(() => setIdleBaseFromCurrentView(), 220);
    });
  } else {
    network.setOptions({ physics: { enabled: false } });
    requestAnimationFrame(() => {
      network.redraw();
      network.fit({ animation: { duration: 550, easingFunction: 'easeInOutQuad' } });
      updateGuidePositions();
      const searchQuery = document.getElementById('search')?.value?.trim();
      if (searchQuery) {
        document.getElementById('search').dispatchEvent(new Event('input'));
      }
      setTimeout(() => setIdleBaseFromCurrentView(), 620);
    });
  }
}

window.addEventListener('load', () => {
  mountNetwork(requestedLayoutFromHash());
});

window.addEventListener('hashchange', () => {
  const requested = requestedLayoutFromHash();
  if (requested !== currentLayout) {
    mountNetwork(requested);
  }
});

window.addEventListener('resize', () => {
  ensureGraphContainerSize();
  if (network) {
    network.redraw();
    network.fit({ animation: false });
  }
});

function refsHtml(refs) {
  if (!refs || !refs.length) return '';
  return `
    <span class="ref-trigger">i</span>
    <div class="ref-popover">
      <h4>Provenance</h4>
      ${refs.map(ref => `
        <div class="ref-entry">
          <div><strong>${escapeHtml(ref.label)}</strong></div>
          <div>${ref.href ? `<a href="${escapeHtml(ref.href)}" target="_blank" rel="noreferrer">${escapeHtml(ref.section || 'Open source')}</a>` : escapeHtml(ref.section || '')}</div>
          ${ref.note ? `<div>${escapeHtml(ref.note)}</div>` : ''}
        </div>
      `).join('')}
    </div>
  `;
}

function renderSection(section) {
  if (!section) return '';
  if (section.kind === 'text') {
    return `
      <section class="section">
        <h3>${escapeHtml(section.title)}</h3>
        <p>${escapeHtml(section.content || 'Not available.')}</p>
        ${section.refs && section.refs.length ? `<div style="margin-top:8px; position:relative; display:inline-block;">${refsHtml(section.refs)}</div>` : ''}
      </section>
    `;
  }
  if (section.kind === 'bullets') {
    return `
      <section class="section">
        <h3>${escapeHtml(section.title)}</h3>
        <ul class="bullet-list">
          ${(section.items || []).map(item => `
            <li class="bullet-item">
              <span>${escapeHtml(item.text)}</span>
              ${item.refs && item.refs.length ? refsHtml(item.refs) : ''}
            </li>
          `).join('')}
        </ul>
      </section>
    `;
  }
  return '';
}

function neighborIds(nodeId) {
  return network.getConnectedNodes(nodeId).filter(id => nodeById.get(id)?.file_type === 'paper');
}

function renderNeighbors(nodeId) {
  const neighbors = neighborIds(nodeId)
    .map(id => nodeById.get(id))
    .sort((a, b) => (b.year || 0) - (a.year || 0));
  if (!neighbors.length) return '';
  return `
    <div id="neighbors">
      <h3>Connected Papers</h3>
          ${neighbors.map(node => `
        <button class="neighbor" onclick="focusNode('${node.id}')">
          ${escapeHtml(node.label)}
          <small>${escapeHtml(node.display_label || '')} · ${escapeHtml(node.journal || '')}</small>
        </button>
      `).join('')}
    </div>
  `;
}

function renderClassNode(node) {
  const members = GRAPH_DATA.nodes
    .filter(candidate => candidate.file_type === 'paper' && candidate.method_class === node.label)
    .sort((a, b) => a.year - b.year);
  return `
    <div class="meta-header"><h2>${escapeHtml(node.label)}</h2></div>
    <div class="viewer-note">${escapeHtml(node.definition || '')}</div>
    <div id="neighbors">
      <h3>Member Papers</h3>
      ${members.map(member => `
        <button class="neighbor" onclick="focusNode('${member.id}')">
          ${escapeHtml(member.label)}
          <small>${escapeHtml(member.display_label || '')} · ${escapeHtml(member.journal || '')}</small>
        </button>
      `).join('')}
    </div>
  `;
}

function renderPaperNode(node) {
  const sections = node.sections || {};
  return `
    <div class="meta-header">
      <h2>${escapeHtml(node.label)}</h2>
    </div>
    <div class="meta-grid">
      <div class="meta-card"><span class="label">Journal</span><span class="value">${escapeHtml(node.journal || '')}</span></div>
      <div class="meta-card"><span class="label">Year</span><span class="value">${escapeHtml(String(node.year || ''))}</span></div>
      <div class="meta-card"><span class="label">Author + Year</span><span class="value">${escapeHtml(node.display_label || '')}</span></div>
      <div class="meta-card"><span class="label">Method Class</span><span class="value">${escapeHtml(node.method_class || '')}</span></div>
      <div class="meta-card"><span class="label">Citations</span><span class="value">${node.cited_by_count != null ? Number(node.cited_by_count).toLocaleString() : '—'}${node.citation_source ? ` <small style="color:#94a3b8">(${escapeHtml(node.citation_source)})</small>` : ''}</span></div>
      <div class="meta-card"><span class="label">WCM Completeness</span><span class="value">${escapeHtml(node.wcm_completeness_label || '')}</span></div>
      <div class="meta-card"><span class="label">Completeness Source</span><span class="value">${escapeHtml(node.wcm_completeness_source || '')}</span></div>
      <div class="meta-card"><span class="label">Organism</span><span class="value">${escapeHtml(node.organism || '')}</span></div>
      <div class="meta-card"><span class="label">Organism Group</span><span class="value">${escapeHtml(node.organism_group || '')}</span></div>
      <div class="meta-card"><span class="label">PDF Status</span><span class="value">${escapeHtml(node.pdf_status || '')}</span></div>
    </div>
    <div class="viewer-note">
      ${escapeHtml(node.wcm_completeness_definition || 'No WCM completeness note is available yet.')}
      ${node.wcm_completeness_confidence ? ` Classification confidence: ${escapeHtml(String(node.wcm_completeness_confidence))}.` : ''}
    </div>
    <div class="chip-row">
      ${(node.modeled_objects || []).map(obj => `<span class="chip">${escapeHtml(obj)}</span>`).join('')}
    </div>
    ${SECTION_ORDER.map(key => renderSection(sections[key])).join('')}
    ${renderNeighbors(node.id)}
    <div class="viewer-note">
      Bullet hover cards now prefer parsed local PDF page anchors when a PDF is available, then fall back to the curated note and DOI record.
    </div>
  `;
}

function renderNode(nodeId) {
  const node = nodeById.get(nodeId);
  if (!node) return;
  document.getElementById('empty-state').style.display = 'none';
  const details = document.getElementById('details');
  details.style.display = 'block';
  details.innerHTML = node.file_type === 'paper' ? renderPaperNode(node) : renderClassNode(node);
}

window.focusNode = function(nodeId) {
  markInteractionPause();
  if (!network) return;
  network.selectNodes([nodeId]);
  applyFocusMode(nodeId);
  network.focus(nodeId, { scale: 1.2, animation: { duration: 450, easingFunction: 'easeInOutQuad' } });
  renderNode(nodeId);
}

document.getElementById('search').addEventListener('input', event => {
  const query = event.target.value.trim().toLowerCase();
  if (!query) {
    clearSearchFilter();
    return;
  }
  const normalizedQuery = normalizeSearchText(query);
  const matches = [];
  GRAPH_DATA.nodes.forEach(node => {
    if (node.file_type !== 'paper') return;
    const score = searchScore(node, normalizedQuery);
    if (score > 0) {
      matches.push({ id: node.id, score });
    }
  });
  matches.sort((left, right) => right.score - left.score || String(left.id).localeCompare(String(right.id)));
  if (matches.length) {
    applySearchFilter(matches.map(match => match.id));
  }
});

function renderGuides(specs) {
  const guideRoot = document.getElementById('layout-guides');
  currentGuideSpecs = specs || [];
  if (!currentGuideSpecs.length) {
    guideRoot.innerHTML = '';
    return;
  }
  guideRoot.innerHTML = currentGuideSpecs.map(spec => {
    const isHorizontal = spec.orientation === 'horizontal';
    return `
      <div class="guide-line${isHorizontal ? ' guide-line-horizontal' : ''}" data-guide-key="${escapeHtml(spec.key)}"></div>
      <div class="guide-label${isHorizontal ? ' guide-label-horizontal' : ''}" data-guide-key="${escapeHtml(spec.key)}">${escapeHtml(spec.label)}</div>
    `;
  }).join('');
  updateGuidePositions();
}

function positionForYearLayout(node, groups, indexWithinGroup) {
  const totalWidth = Math.max(1800, groups.length * 120);
  const step = groups.length > 1 ? totalWidth / (groups.length - 1) : 0;
  if (node.file_type !== 'paper') {
    return { x: -900 + (node.community * 900), y: -560 };
  }
  const groupIndex = Math.max(0, groups.indexOf(String(node.year)));
  return {
    x: -(totalWidth / 2) + groupIndex * step,
    y: -130 + indexWithinGroup * 132,
  };
}

function positionForOrganismLayout(node, groups, orderInGroup, bucket = 'incomplete') {
  const totalWidth = Math.max(1500, groups.length * 180);
  const step = groups.length > 1 ? totalWidth / (groups.length - 1) : 0;
  if (node.file_type !== 'paper') {
    return { x: -960 + (node.community * 960), y: -560 };
  }
  const group = node.organism_group || 'General bacteria';
  const groupIndex = Math.max(0, groups.indexOf(group));
  return {
    x: -(totalWidth / 2) + groupIndex * step,
    y: (bucket === 'complete' ? ORGANISM_COMPLETE_START_Y : ORGANISM_INCOMPLETE_START_Y) + orderInGroup * ORGANISM_ROW_STEP,
  };
}

function applyForceLayout() {
  mountNetwork('force');
}

function applyYearLayout() {
  mountNetwork('year');
}

function applyOrganismLayout() {
  mountNetwork('organism');
}

document.querySelectorAll('.layout-btn').forEach(button => {
  button.addEventListener('click', () => {
    const layout = button.dataset.layout;
    window.location.hash = layout;
  });
});

function startIdleDrift() {
  function frame(now) {
    if (network && currentLayout === 'force' && !activeSelection && Date.now() > idlePausedUntil && !hoverPaused) {
      const x = idleBase.position.x + Math.cos(now / 5200) * 42;
      const y = idleBase.position.y + Math.sin(now / 7200) * 26;
      const scale = idleBase.scale * (1 + Math.sin(now / 6400) * 0.014);
      network.moveTo({ position: { x, y }, scale, animation: false });
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

renderLegend();
setTimeout(() => setIdleBaseFromCurrentView(), 600);
startIdleDrift();
</script>
</body>
</html>
"""
    html = (
        template.replace("__GRAPH_DATA__", json.dumps(graph_data))
        .replace("__COMMUNITY_LABELS__", json.dumps({str(key): value for key, value in community_labels.items()}))
        .replace("__COMMUNITY_COLORS__", json.dumps({str(key): value for key, value in LAYOUT_COLORS.items()}))
        .replace("__COMMUNITY_DEFINITIONS__", json.dumps(CLASS_DEFINITIONS))
        .replace("__ORGANISM_GROUP_ORDER__", json.dumps(ORGANISM_GROUP_ORDER))
        .replace("__ORGANISM_GROUP_LABELS__", json.dumps(ORGANISM_GROUP_LABELS))
    )
    (GRAPHIFY_OUT / "graph.html").write_text(html, encoding="utf-8")


def main() -> int:
    ensure_dirs()
    rows = read_master_table()
    inventory_urls = read_inventory_urls()
    classes = read_classes()
    organisms = read_organisms()
    zotero_state = sync_from_zotero(rows, PDF_DIR, ZOTERO_SYNC_STATE)
    rows, classes, organisms, profiles, parse_cache = sync_rows_with_pdfs(rows, classes, organisms, inventory_urls)
    zotero_state = sync_to_zotero(rows, PDF_DIR, ZOTERO_SYNC_STATE)
    paper_meta, _summaries = build_paper_metadata(rows, classes, organisms, profiles)
    write_paper_notes(paper_meta)
    write_metadata_json(paper_meta)
    write_live_inventory(rows, paper_meta)
    write_synced_inventory(rows, paper_meta, inventory_urls)
    write_sidecar_outputs(rows, paper_meta, inventory_urls)
    write_pdf_processing_status(rows, parse_cache, zotero_state)
    G, communities, community_labels = build_graph(rows, classes, paper_meta)
    to_json(G, communities, str(GRAPHIFY_OUT / "graph.json"))
    to_html(G, communities, str(GRAPHIFY_OUT / "graph_base.html"), community_labels=community_labels)
    graph_base_html = GRAPHIFY_OUT / "graph_base.html"
    graph_base_text = graph_base_html.read_text(encoding="utf-8")
    graph_base_text = graph_base_text.replace(
        f"<title>graphify - {graph_base_html}</title>",
        "<title>Whole-Cell Model Graph Explorer</title>",
    )
    graph_base_html.write_text(graph_base_text, encoding="utf-8")
    to_graphml(graphml_safe_copy(G), communities, str(GRAPHIFY_OUT / "graph.graphml"))
    to_svg(G, communities, str(GRAPHIFY_OUT / "graph.svg"), community_labels=community_labels)
    graph_data = json.loads((GRAPHIFY_OUT / "graph.json").read_text(encoding="utf-8"))
    write_enhanced_html(graph_data, community_labels)
    write_report(G, communities, community_labels, total_files=len(rows))
    write_summary(rows, classes, G)
    print(
        json.dumps(
            {
                "papers": len(rows),
                "nodes": G.number_of_nodes(),
                "edges": G.number_of_edges(),
                "output_dir": str(GRAPHIFY_OUT),
                "metadata_file": str(PAPER_METADATA_JSON),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
