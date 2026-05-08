#!/usr/bin/env python3
"""Generate multi-sentence evidence screenshots for multimodal_genomics_evidence.json.

For each evidence point with a local PDF in ``pdfs/``:
  1. Build a sentence corpus across the entire document, mapping each
     sentence back to the line bboxes it occupies.
  2. Score each sentence against the claim text (and curated quote if any)
     using rapidfuzz.partial_ratio plus a +20 boost for rare keyword overlap.
  3. Keep the top-5 sentences with score >= 60.
  4. Group adjacent sentences (same page, touching line bboxes) into
     "sentence-groups". Render one cropped + highlighted PNG per group.
  5. If no sentence reaches score 50 anywhere, fall back to the legacy
     block-level matcher and emit a single PNG with strategy
     ``block-fallback``.

Schema written into ``multimodal_genomics/metadata/evidence.json`` (per evidence
point):

  {
    "screenshots": [ {href, page, section_hint, sentences:[{text, match_score}],
                      highlight_granularity}, ... ],
    "screenshot_count": N,
    "screenshot_strategy": "multi-sentence-multi-page" |
                           "single-sentence" |
                           "block-fallback",
    "screenshot_status": "ok" | "not_found" | "manual_review" | "no_pdf",
    "confidence": "parsed_pdf"  (set only on success)
  }

Legacy fields (``screenshot_href``, ``page``, ``quote``) are NOT written for
new entries; the validator still tolerates them on incoming candidates.

Idempotent. CLI:
  python scripts/generate_screenshots.py
  python scripts/generate_screenshots.py --force
  python scripts/generate_screenshots.py --id <evidence_id>
  python scripts/generate_screenshots.py --limit N
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from PIL import Image
from rapidfuzz import fuzz

# nltk: prefer the local data path the project already populated.
import nltk

_NLTK_LOCAL = Path.home() / "nltk_data"
if _NLTK_LOCAL.is_dir() and str(_NLTK_LOCAL) not in nltk.data.path:
    nltk.data.path.insert(0, str(_NLTK_LOCAL))

try:
    from nltk.tokenize import sent_tokenize  # punkt_tab tokenizer
except Exception as _exc:  # pragma: no cover - best-effort import
    raise SystemExit(f"nltk import failed: {_exc}") from _exc


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_FILE = ROOT / "multimodal_genomics" / "metadata" / "evidence.json"
LOG_FILE = ROOT / "multimodal_genomics" / "metadata" / "screenshot_log.json"
PDF_DIR = ROOT / "pdfs"
ASSETS_DIR = ROOT / "multimodal_genomics" / "assets" / "evidence"

# Scoring / filtering ---------------------------------------------------------
SENTENCE_SCORE_THRESHOLD = 60.0   # keep sentences with score >= this
BLOCK_FALLBACK_FLOOR = 50.0       # if no sentence >= 50 anywhere, fall back
MIN_BLOCK_CHARS = 80              # blocks shorter than this are skipped
MIN_SENTENCE_CHARS = 40           # short "sentences" are usually fragments / refs
MIN_SENTENCE_WORDS = 6            # require at least N words
TOP_N_SENTENCES = 5               # cap on retained sentences per evidence point
KEYWORD_BOOST = 20.0
MIN_KEYWORD_LEN = 6
MIN_DOC_TEXT_LEN = 200            # heuristic for image-only / garbled scans

# Rendering ------------------------------------------------------------------
DPI = 200
ZOOM = DPI / 72.0
MARGIN_PX = 80                    # vertical margin around group bbox in pixels
PNG_BYTES_CAP = 200 * 1024        # 200 KB tighter cap (more PNGs per evidence)
MAX_DOWNSCALE_WIDTH = 1100
QUANTIZE_COLORS = 128

# Schema helpers -------------------------------------------------------------
VALID_STRATEGIES = {"multi-sentence-multi-page", "single-sentence", "block-fallback"}

_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{%d,}" % (MIN_KEYWORD_LEN - 1))

# Tiny stopword list for the rare-keyword boost. Anything not here counts as
# "rare" (we already require length >= 6, which removes most stopwords).
_STOPWORDS = {
    "however", "between", "without", "because", "through", "rather",
    "almost", "another", "always", "around", "across", "within",
    "really", "should", "though", "before", "during", "either",
    "neither", "either", "yet", "while", "where", "which", "their",
    "there", "these", "those", "would", "could", "might", "every",
    "models", "model", "method", "methods", "results", "result",
    "figure", "figures", "table", "tables", "based", "using",
    "approach", "approaches", "different", "various", "several",
    "potential", "possible", "important", "significant",
}


def _collapse_ws(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _rare_keywords(text: str) -> list[str]:
    """Return lowercased length>=MIN_KEYWORD_LEN tokens that aren't in _STOPWORDS."""
    out = set()
    for m in _WORD_RE.finditer(text):
        w = m.group(0).lower()
        if w in _STOPWORDS:
            continue
        out.add(w)
    return list(out)


_PAPER_REGISTRY_CACHE: dict[str, dict] | None = None


def _paper_registry() -> dict[str, dict]:
    """Lazy-load and merge the MMG and MMG paper registries (paper_id -> record)."""
    global _PAPER_REGISTRY_CACHE
    if _PAPER_REGISTRY_CACHE is not None:
        return _PAPER_REGISTRY_CACHE
    out: dict[str, dict] = {}
    for path, key in (
        (ROOT / "multimodal_genomics" / "metadata" / "papers.json", "papers"),
    ):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        papers = data.get(key)
        if isinstance(papers, dict):
            for pid, rec in papers.items():
                if isinstance(rec, dict) and pid:
                    out[pid] = rec
        elif isinstance(papers, list):
            for rec in papers:
                if isinstance(rec, dict) and rec.get("paper_id"):
                    out[rec["paper_id"]] = rec
    _PAPER_REGISTRY_CACHE = out
    return out


def _paper_title_for(paper_id: str | None) -> str | None:
    if not paper_id:
        return None
    rec = _paper_registry().get(paper_id) or {}
    title = rec.get("title")
    return title if isinstance(title, str) and title.strip() else None


def _find_pdf(paper_id: str) -> Path | None:
    if not PDF_DIR.is_dir():
        return None
    for p in PDF_DIR.iterdir():
        if p.is_file() and p.name.startswith(f"{paper_id}_") and p.suffix.lower() == ".pdf":
            return p
    return None


# ---------------------------------------------------------------------------
# Sentence corpus extraction
# ---------------------------------------------------------------------------

class Sentence:
    __slots__ = ("page", "block_idx", "line_bboxes", "text", "char_count")

    def __init__(
        self,
        page: int,
        block_idx: int,
        line_bboxes: list[tuple[float, float, float, float]],
        text: str,
    ) -> None:
        self.page = page
        self.block_idx = block_idx
        self.line_bboxes = line_bboxes
        self.text = text
        self.char_count = len(text)

    def line_height(self) -> float:
        if not self.line_bboxes:
            return 12.0
        heights = [(y1 - y0) for (_, y0, _, y1) in self.line_bboxes]
        # Use median-ish average; small page headings can produce zero-height bboxes.
        heights = [h for h in heights if h > 0]
        return sum(heights) / len(heights) if heights else 12.0

    def union_bbox(self) -> tuple[float, float, float, float]:
        xs0 = min(b[0] for b in self.line_bboxes)
        ys0 = min(b[1] for b in self.line_bboxes)
        xs1 = max(b[2] for b in self.line_bboxes)
        ys1 = max(b[3] for b in self.line_bboxes)
        return (xs0, ys0, xs1, ys1)


def _section_hint_for_page(page_idx: int) -> str:
    """Very rough page-based section hint. Refined later if needed."""
    if page_idx == 0:
        return "Abstract / first page"
    return f"page {page_idx + 1}"


_REFERENCES_HEADING_RE = re.compile(
    r"^\s*(?:references|bibliography|literature\s+cited|works\s+cited|"
    r"reference\s+list|cited\s+literature|references\s+and\s+notes|"
    r"reference\s+list\s+and\s+notes)\s*$",
    re.IGNORECASE,
)
# Cutoff sections: any heading matched here truncates the document for
# scoring purposes. Acknowledgements / Author Contributions / Funding /
# Data Availability typically follow Methods/Results, and pure metadata
# like "Conflicts of interest" never makes claim-supporting evidence.
_BACK_MATTER_HEADING_RE = re.compile(
    r"^\s*(?:references|bibliography|literature\s+cited|works\s+cited|"
    r"reference\s+list|cited\s+literature|references\s+and\s+notes|"
    r"acknowledg(?:e?ment[s]?|ements?)|"
    r"authors?'?s?\s+contribution[s]?|"
    r"contributor\s+roles?|credit\s+author\s+statement|"
    r"author\s+roles?|author(?:s'?)?\s+disclosure[s]?|"
    r"competing\s+interest[s]?|conflicts?\s+of\s+interest[s]?|"
    r"declarations?\s+of\s+interest[s]?|disclosures?(?:\s+statement)?|"
    r"funding(?:\s+(?:information|sources?|statement))?|"
    r"financial\s+(?:support|disclosure[s]?)|"
    r"grant\s+(?:information|support)|"
    r"data\s+availability(?:\s+statement)?|data\s+access\s+statement|"
    r"code\s+availability(?:\s+statement)?|"
    r"materials?\s+availability(?:\s+statement)?|"
    r"supplementary\s+(?:materials?|information|figures?|notes?|methods?|data)|"
    r"extended\s+data(?:\s+(?:figures?|tables?))?|"
    r"reporting\s+summary|"
    r"about\s+the\s+authors?|"
    r"author\s+information|author\s+affiliations?|"
    r"corresponding\s+author|"
    r"reviewers?(?:'\s*comments)?|"
    r"editor(?:'s|s')?\s+evaluation|"
    r"peer\s+review(?:\s+(?:file|history))?|"
    r"review\s+history|"
    r"ethic[s]?\s+(?:approval|statement)|"
    r"informed\s+consent|"
    r"open\s+access(?:\s+statement)?|"
    r"licensing|copyright|rights\s+and\s+permissions|"
    r"keywords?|abbreviations?|nomenclature|glossary|notation|"
    r"orcid\s+i?ds?|orcid)"
    r"\s*\.?\s*$",
    re.IGNORECASE,
)
# Optional digit prefix like "5.", "[5]", "5)"
_HEADING_PREFIX_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.|\d+\))\s*")


def _find_references_cutoff(
    doc: fitz.Document,
) -> tuple[int, float] | None:
    """Return ``(page_idx, y_top)`` of the FIRST back-matter heading we trust.

    A "back-matter heading" is References / Bibliography / Acknowledgements /
    Author Contributions / Funding / Data Availability / Supplementary etc.
    Once we hit any of these, everything after is excluded.

    Heuristic, robust against three common typesetting variants:

      1. Standalone-line heading. The original detector. Walk every line
         after the 30 % mark, normalize (strip leading numbers, lowercase),
         and check against ``_BACK_MATTER_HEADING_RE``.
      2. Heading-as-block. Some PDFs render "REFERENCES" as a block with no
         body underneath because the references all live in their own blocks
         below. Same regex still matches because we strip whitespace.
      3. Span-level largest-font heading. When the heading is part of a
         bigger block (rare but happens), look at *the largest font size on
         the page* and only treat that line as a candidate.

    To minimize false positives, the heading is only accepted if its line
    bbox is ≤ 60 chars long. Past 60 chars it's almost certainly body text.
    """
    n_pages = len(doc)
    if n_pages == 0:
        return None
    earliest_eligible_page = max(0, int(n_pages * 0.30))
    for page_idx in range(earliest_eligible_page, n_pages):
        page = doc.load_page(page_idx)
        try:
            page_dict = page.get_text("dict")
        except Exception:
            continue
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            lines = block.get("lines") or []
            for line in lines:
                spans = line.get("spans") or []
                line_text = "".join(span.get("text", "") for span in spans).strip()
                if not line_text or len(line_text) > 60:
                    continue
                # Strip numbering prefix like "5. References" / "[5] References".
                stripped = _HEADING_PREFIX_RE.sub("", line_text)
                if _BACK_MATTER_HEADING_RE.match(stripped):
                    bbox = line.get("bbox") or (0, 0, 0, 0)
                    return page_idx, float(bbox[1])
    return None


# ---------------------------------------------------------------------------
# Block-level reference detection. Catches papers (e.g. MMG-061 Med-PaLM)
# whose bibliography format is "Title-only" lines with no author/year markers
# — these slip past _looks_like_reference_line because individual lines look
# innocent. We work at the block level: if a block has high "reference
# density" — many short title-like fragments, lots of years, multi-author
# patterns — we drop the entire block before scoring.
# ---------------------------------------------------------------------------

# Shared helpers — reuse the existing ref-line patterns.
_REFBLOCK_AUTHOR_TOKEN = re.compile(r"\b[A-Z][A-Za-zÀ-ſ\-]+,?\s[A-Z]\.[A-Z]?\.?")
_REFBLOCK_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_REFBLOCK_DOI = re.compile(r"\b10\.\d{4,}/[^\s,;]+", re.IGNORECASE)
_REFBLOCK_VOL_ISSUE = re.compile(r"\b\d{1,3}\s*\(\s*\d{1,4}\s*\)")
_REFBLOCK_PAGE_RANGE = re.compile(r"\b\d{1,4}\s*[-–]\s*\d{1,4}\b")
_REFBLOCK_ET_AL = re.compile(r"\bet\s+al\.?\b", re.IGNORECASE)
_REFBLOCK_ARXIV = re.compile(r"\barxiv\s*[:.]?\s*\d{4}\.\d{4,5}\b", re.IGNORECASE)
_REFBLOCK_PMID = re.compile(r"\b(?:PMID|PMC)\s*:?\s*\d+", re.IGNORECASE)
# "Title-only" reference: a short line ending with a period, ≤ 12 words,
# title-cased (most words start uppercase), no verb-like internal structure.
_TITLE_LINE_RE = re.compile(
    r"^\s*[A-Z][A-Za-z0-9\-:,\s]*[a-z][A-Za-z0-9\-:,\s]*\s*\.\s*$"
)


def _looks_like_title_only_line(text: str) -> bool:
    """A 'title-only' reference fragment: short, declarative-titlecase, no verbs."""
    s = text.strip()
    if not s.endswith("."):
        return False
    if len(s) > 110:
        return False
    words = s[:-1].split()
    if not (3 <= len(words) <= 14):
        return False
    # Must start with capital letter.
    if not s[:1].isupper():
        return False
    # Heuristic: title-only lines mostly avoid verbs like is/are/was/can/will
    # in the middle. Body sentences nearly always have at least one of them.
    body_verbs = {
        "is", "are", "was", "were", "can", "could", "will", "would", "may",
        "might", "must", "shall", "should", "do", "does", "did", "has",
        "have", "had",
    }
    middle = [w.lower().rstrip(",") for w in words[1:-1]]
    if any(v in body_verbs for v in middle):
        return False
    return True


def _block_looks_like_reference_block(block_text: str, sentences: list[str] | None = None) -> bool:
    """Decide whether a *whole text block* is part of the bibliography.

    Returns True when the block has the structural fingerprints of a
    references section: many years, many author tokens, DOIs, page ranges,
    or title-only fragments. We are deliberately strict to avoid dropping
    legitimate body text that just happens to mention years.
    """
    if not block_text:
        return False
    n_authors = len(_REFBLOCK_AUTHOR_TOKEN.findall(block_text))
    n_years = len(_REFBLOCK_YEAR.findall(block_text))
    n_dois = len(_REFBLOCK_DOI.findall(block_text))
    n_vols = len(_REFBLOCK_VOL_ISSUE.findall(block_text))
    n_pages = len(_REFBLOCK_PAGE_RANGE.findall(block_text))
    n_etal = len(_REFBLOCK_ET_AL.findall(block_text))
    n_arxiv = len(_REFBLOCK_ARXIV.findall(block_text))
    n_pmids = len(_REFBLOCK_PMID.findall(block_text))

    # Strong direct signals — any one of these screams "references":
    if n_dois >= 2 or n_arxiv >= 2 or n_pmids >= 2:
        return True
    if n_etal >= 2 and n_years >= 2:
        return True
    # 3+ author tokens AND year/vol/page evidence.
    if n_authors >= 3 and (n_years >= 2 or n_vols >= 1 or n_pages >= 2):
        return True

    # Title-only-line cluster (the MMG-061 / Med-PaLM bug).
    if sentences is not None and len(sentences) >= 3:
        title_only = sum(1 for s in sentences if _looks_like_title_only_line(s))
        # If a clear majority of sentences look like titles, it's a ref block.
        if title_only >= 3 and title_only >= max(2, len(sentences) // 2):
            return True

    return False


def _build_page_heights(doc: fitz.Document) -> dict[int, float]:
    """Map page_idx → page height (used by the top-band running-header detector)."""
    out: dict[int, float] = {}
    for i in range(len(doc)):
        try:
            out[i] = float(doc.load_page(i).rect.height)
        except Exception:
            continue
    return out


def _build_sentence_corpus(doc: fitz.Document) -> tuple[list[Sentence], int]:
    """Return ``(sentences, total_block_text_len)``.

    Sentences that fall at or after the References / Bibliography heading
    are excluded so bibliography entries can never become evidence.
    """
    sentences: list[Sentence] = []
    total_text_len = 0
    refs_cutoff = _find_references_cutoff(doc)

    for page_idx in range(len(doc)):
        # Hard cutoff at the references heading: skip the entire rest of
        # the document so we never touch the bibliography.
        if refs_cutoff is not None and page_idx > refs_cutoff[0]:
            break
        page = doc.load_page(page_idx)
        try:
            page_dict = page.get_text("dict")
        except Exception:
            continue
        for block_idx, block in enumerate(page_dict.get("blocks", [])):
            if block.get("type") != 0:  # 0 = text, 1 = image
                continue
            lines = block.get("lines") or []
            # Build (line_text, line_bbox, char_offset_start, char_offset_end)
            running_text_parts: list[str] = []
            line_records: list[tuple[int, int, tuple[float, float, float, float]]] = []
            offset = 0
            for line in lines:
                spans = line.get("spans") or []
                line_text = "".join(span.get("text", "") for span in spans)
                if not line_text:
                    continue
                bbox = tuple(line.get("bbox") or (0, 0, 0, 0))
                # Treat a line as taking up positions [offset, offset+len(line_text)).
                running_text_parts.append(line_text)
                start = offset
                # Add the line, then a separating space (joining via " ").
                end = offset + len(line_text)
                line_records.append((start, end, bbox))
                offset = end + 1  # account for the joining space below
            if not line_records:
                continue
            block_text = " ".join(running_text_parts)
            total_text_len += len(block_text)

            collapsed = _collapse_ws(block_text)
            if len(collapsed) < MIN_BLOCK_CHARS:
                continue

            # Tokenize sentences off the joined block text.
            try:
                sents = sent_tokenize(block_text)
            except Exception:
                # Tokenizer corrupted? skip the block.
                continue

            # Drop the ENTIRE block when it has bibliographical fingerprints.
            # Catches title-only Nature-style numbered references that would
            # otherwise pass _looks_like_reference_line one at a time.
            if _block_looks_like_reference_block(collapsed, sents):
                continue

            # Map each sentence back to its character span in block_text and
            # then to the lines whose [start,end) overlap that span.
            cursor = 0
            for sent in sents:
                sent_clean = sent.strip()
                if not sent_clean:
                    continue
                # Find this sentence in block_text starting from `cursor` so we
                # are robust to repeated phrases.
                idx = block_text.find(sent_clean, cursor)
                if idx < 0:
                    # Fall back to a fuzzy locate by first 25 chars.
                    needle = sent_clean[:25]
                    idx = block_text.find(needle, cursor) if needle else -1
                    if idx < 0:
                        continue
                start = idx
                end = idx + len(sent_clean)
                cursor = end
                # Map char span -> lines.
                hits: list[tuple[float, float, float, float]] = []
                for ls, le, lb in line_records:
                    if le <= start or ls >= end:
                        continue
                    hits.append(lb)
                if not hits:
                    continue
                # Same-page cutoff: if we're on the references-heading page
                # itself, drop any sentence whose top-line bbox is below the
                # heading.
                if refs_cutoff is not None and page_idx == refs_cutoff[0]:
                    sentence_top = min(b[1] for b in hits)
                    if sentence_top >= refs_cutoff[1]:
                        continue
                clean = _collapse_ws(sent_clean)
                # Filter out reference-list fragments and ultra-short
                # sentences. partial_ratio gives "." or "Struct." a perfect
                # score against any longer claim, so we must drop these
                # before scoring.
                if len(clean) < MIN_SENTENCE_CHARS:
                    continue
                if len(clean.split()) < MIN_SENTENCE_WORDS:
                    continue
                sentences.append(
                    Sentence(
                        page=page_idx,
                        block_idx=block_idx,
                        line_bboxes=hits,
                        text=clean,
                    )
                )

    return sentences, total_text_len


# ---------------------------------------------------------------------------
# Sentence scoring + grouping
# ---------------------------------------------------------------------------

_REFLINE_AUTHORS = re.compile(r"\b[A-Z][A-Za-zÀ-ſ\-]+,?\s[A-Z]\.[A-Z]?\.?")
_REFLINE_DOI = re.compile(r"\b(?:doi:?|DOI:?)\s*10\.\d{4,}/", re.IGNORECASE)
_REFLINE_YEAR_PARENS = re.compile(r"\(\s*(?:19|20)\d{2}\s*[a-z]?\s*\)")
_REFLINE_PAGE_RANGE = re.compile(r"\b\d{1,4}\s*[-–]\s*\d{1,4}\b")
_REFLINE_VOL_ISSUE = re.compile(r"\b\d{1,3}\s*\(\s*\d{1,4}\s*\)")


def _looks_like_reference_line(text: str) -> bool:
    """Cheap heuristic. A 'reference list line' typically has BOTH:
    multiple author tokens AND one of (doi, year-in-parens, vol(issue), page range).
    """
    n_authors = len(_REFLINE_AUTHORS.findall(text))
    has_year = bool(_REFLINE_YEAR_PARENS.search(text))
    has_doi = bool(_REFLINE_DOI.search(text))
    has_vol = bool(_REFLINE_VOL_ISSUE.search(text))
    has_pages = bool(_REFLINE_PAGE_RANGE.search(text))
    if n_authors >= 2 and (has_year or has_doi or has_vol or has_pages):
        return True
    if has_doi and (has_year or has_vol):
        return True
    return False


REFLINE_PENALTY = 30.0
HEADER_PENALTY = 25.0
TITLE_LINE_PENALTY = 35.0
CITATION_PREFIX_PENALTY = 40.0
PAPER_TITLE_PENALTY = 50.0       # bumped — page-1 title pages were leaking
FRAGMENT_PENALTY = 50.0
CAPTION_PENALTY = 35.0
RUNNING_HEADER_PENALTY = 45.0
PAGE_ONE_TITLE_PENALTY = 60.0    # extra penalty for page-1 cover-page titles


# Sentences that look like figure/table caption opens, not body argument.
# Examples: "Fig. 4 | Schematic of …", "Table 3: Mean …", "(B) Box plots …",
# "b, Geographic context for …", "Figure 2 shows …" (last one is borderline OK
# but commonly mid-block continuation we'd rather de-prioritise).
_CAPTION_OPENER_RE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?\s*\d+|figs?\.\s*\d+|"
    r"tab(?:le)?\.?\s*\d+|tabs?\.\s*\d+|"
    r"scheme\s+\d+|chart\s+\d+|panel\s+\d+|"
    r"extended\s+data\s+(?:fig|table)|"
    r"supp(?:lementary)?\s+(?:fig|figure|table|tab)\s*\d*|"
    r"\([a-z0-9]\)|"          # "(b) Box plots…", "(8) is called PCS."
    r"[a-z],\s|"              # "b, Geographic context for…"
    r"\d+\s*\|\s*[A-Z]|"      # "2 | Benchmarking AMNs…"  (Nature panel labels)
    r"[a-z]\s*\|\s*[A-Z])",   # "b | Receptor distribution…"
    re.IGNORECASE,
)
# Subsection-number / lettered openers tokenized as sentences:
# "C. Simulation-Based Inference…", "3.2.2 PINNs for…", "II. Background…"
_SUBSECTION_OPENER_RE = re.compile(
    r"^\s*(?:[A-Z]\.\s+[A-Z]|"            # "C. Foo …"
    r"\d+(?:\.\d+){1,3}\s+[A-Z]|"          # "3.2.2 PINNs"
    r"[IVX]{1,4}\.\s+[A-Z])",              # "II. Background"
)


def _looks_like_section_opener(text: str) -> bool:
    return bool(_SUBSECTION_OPENER_RE.match(text.strip()))


# Survey papers contribute a lot of "we survey..." / "this review covers..."
# scope-statement sentences and dataset-bullet enumerations that match no
# specific claim. Filter those out at sentence level.
_SURVEY_SCOPE_OPENER_RE = re.compile(
    r"^\s*(?:this\s+(?:survey|review|paper)\s+(?:covers|discusses|reviews|"
    r"surveys|presents|provides|examines|focuses\s+on|aims\s+to|is\s+"
    r"organized|introduces|is\s+confined|is\s+structured|will\s+|investigates)|"
    r"the\s+primary\s+goal\s+of\s+this\s+(?:survey|review|paper)|"
    r"the\s+main\s+(?:goal|aim|objective)\s+of\s+this\s+(?:survey|review|paper)|"
    r"in\s+this\s+(?:survey|review|paper|section|work)|"
    r"we\s+(?:survey|review|provide\s+a\s+(?:survey|review|comprehensive))\s|"
    r"the\s+remainder\s+of\s+this|"
    r"the\s+rest\s+of\s+this|"
    r"this\s+section\s+(?:covers|discusses|presents|introduces)|"
    r"section\s+\d+\s+(?:covers|discusses|presents|introduces|describes)|"
    r"there\s+is\s+currently\s+a\s+lack\s+of|"
    r"to\s+the\s+best\s+of\s+our\s+knowledge|"
    r"there\s+(?:are\s+some\s+recent|exists?\s+a)\s+)",
    re.IGNORECASE,
)
# Dataset-row bullet entries common in survey "Datasets / Benchmarks" tables:
# "SciInstruct: 1,500 examples ...", "ProteinKG25: 25k proteins ...",
# "• SciInstruct [395] is a comprehensive scientific instruction tuning dataset.",
# "Xiezhi is a comprehensive evaluation suite for LLMs.",
# "BioMed-RoBERTa [117] is a language model based on the RoBERTa-base architecture."
_DATASET_BULLET_RE = re.compile(
    r"^\s*(?:[•●▪–—◦∙·]\s*)?"                                     # optional bullet glyph
    r"[A-Z][A-Za-z0-9][A-Za-z0-9\-_/\.]+\d*\s*"                   # dataset/system name
    r"(?:\[\d+\]|\([^\)]{1,60}\)|\[\d+\]\s*\([^\)]{1,60}\)|\([^\)]{1,60}\)\s*\[\d+\])?\s*"
    r"(?:[:\(]\s*(?:[\d,\.]+\s*(?:k|m|million|examples|samples|proteins|sequences|reads|cells|genomes|tasks|pairs|tokens))"
    r"|is\s+(?:a|an|the)\s+(?:comprehensive|popular|large(?:-scale)?|scientific|"
    r"benchmark|evaluation|dataset|collection|model|metric|measure|method|"
    r"framework|library|toolkit|database|resource|"
    r"language\s+model|protein\s+language|foundation))",
    re.IGNORECASE,
)
_SECTION_REF_OPENER_RE = re.compile(
    r"^\s*(?:section\s+)?\d+(?:\.\d+)*\s+(?:describes|discusses|presents|introduces|covers|reviews|surveys)",
    re.IGNORECASE,
)


def _looks_like_section_reference(text: str) -> bool:
    return bool(_SECTION_REF_OPENER_RE.match(text.strip()))


def _looks_like_survey_scope(text: str) -> bool:
    return bool(_SURVEY_SCOPE_OPENER_RE.match(text.strip()))


def _looks_like_dataset_bullet(text: str) -> bool:
    return bool(_DATASET_BULLET_RE.match(text.strip()))


_TERMINAL_PUNCT_RE = re.compile(r"[\.\?!][\"\)\]]?\s*$")
# Words a body sentence rarely ENDS on. If a "sentence" ends with one of
# these, the segmenter probably ran out of mid-clause text.
_DANGLING_TAIL_RE = re.compile(
    r"\b(?:of|in|on|at|by|for|from|with|as|to|the|a|an|and|or|but|that|"
    r"which|when|where|while|so|because|via|using|under|over|between|"
    r"among|than|then|via|where|whereas|whether|whose|who|whom|"
    r"where|where|is|are|was|were|be|been|being|has|have|had|having|"
    r"can|could|will|would|may|might|must|should|shall|do|does|did)\s*$",
    re.IGNORECASE,
)
# "Mid-equation" style sentences often have lots of single Greek letters,
# subscripts, or compact math glyphs.
_MATH_DENSE_RE = re.compile(r"[∑∏∇∂∫θφλμσΣΦΛΩαβγδ←→⇒≈≤≥]")


def _looks_like_fragment(text: str) -> bool:
    """Heuristic: catches sentence-tokenizer fragments that are not real
    sentences. The renderer should never highlight these.

    Triggers:
      * starts with lowercase (continuation of prior line)
      * doesn't end with terminal punctuation
      * ends with a dangling preposition / conjunction / aux verb
      * has high math-glyph density and is short (likely an equation slice)
    """
    s = text.strip()
    if not s:
        return True
    if len(s) < 30:
        # Too short to be a self-contained body sentence.
        return True
    first = s[:1]
    if first.isalpha() and first.islower():
        # Lowercase opener → almost always a continuation, not a sentence.
        return True
    if not _TERMINAL_PUNCT_RE.search(s):
        return True
    if _DANGLING_TAIL_RE.search(s):
        return True
    if _MATH_DENSE_RE.search(s) and len(s) < 90:
        return True
    return False


def _looks_like_caption(text: str) -> bool:
    s = text.strip()
    return bool(_CAPTION_OPENER_RE.match(s))

# Front-matter / running-header artefacts that are not real body sentences.
_FRONT_MATTER_PREFIX_RE = re.compile(
    r"^\s*(?:research\s+article|article|review|brief\s+communication|"
    r"perspective|letter|news\s+(?:and|&)\s+views|news\s+feature|comment|"
    r"correspondence|matters\s+arising|technical\s+note|method|protocol|"
    r"editorial|news|original\s+research|short\s+communication|"
    r"insight|primer|review\s+article|primer:?)\b",
    re.IGNORECASE,
)
_CITATION_PREFIX_RE = re.compile(
    r"^\s*(?:citation\s*[:\-–]|cite\s+as\s*[:\-–]|to\s+cite\s+this\s+article\s*[:\-–]|"
    r"received\s*[:\-–]|published\s*[:\-–]|accepted\s*[:\-–]|"
    r"corresponding\s+author|please\s+cite\s+this\s+article)",
    re.IGNORECASE,
)
_DOI_URL_HEADER_RE = re.compile(r"\bhttps?://(?:doi\.org|dx\.doi\.org)/", re.IGNORECASE)


def _looks_like_front_matter_header(text: str) -> bool:
    s = text.strip()
    if _FRONT_MATTER_PREFIX_RE.match(s):
        return True
    if _CITATION_PREFIX_RE.match(s):
        return True
    # "Article https://doi.org/..." running-header style.
    if s.startswith("Article ") and _DOI_URL_HEADER_RE.search(s):
        return True
    return False


def _score_sentence(sent_text: str, claim_lower: str, keywords: list[str]) -> float:
    st = sent_text.lower()
    if not st or not claim_lower:
        return 0.0
    score = float(fuzz.partial_ratio(claim_lower, st))
    if any(kw in st for kw in keywords):
        score += KEYWORD_BOOST
    if _looks_like_reference_line(sent_text):
        score -= REFLINE_PENALTY
    if _looks_like_front_matter_header(sent_text):
        score -= CITATION_PREFIX_PENALTY
    if _looks_like_title_only_line(sent_text):
        score -= TITLE_LINE_PENALTY
    if _looks_like_fragment(sent_text):
        score -= FRAGMENT_PENALTY
    if _looks_like_caption(sent_text):
        score -= CAPTION_PENALTY
    if _looks_like_section_opener(sent_text):
        score -= CAPTION_PENALTY
    if _looks_like_survey_scope(sent_text):
        score -= FRAGMENT_PENALTY
    if _looks_like_dataset_bullet(sent_text):
        score -= CAPTION_PENALTY
    if _looks_like_section_reference(sent_text):
        score -= CAPTION_PENALTY
    return score


def _detect_repeated_headers(sentences: list[Sentence]) -> set[str]:
    """Find verbatim sentences appearing on ≥ 2 pages — running headers /
    journal banners / paper-title repeated at the top or bottom of each page.
    """
    from collections import defaultdict
    by_norm: dict[str, set[int]] = defaultdict(set)
    for s in sentences:
        norm = _collapse_ws(s.text).lower()
        # Looser bounds than before: short banners (≥ 15 chars) and longer
        # title-and-journal banners (≤ 280 chars) both qualify.
        if len(norm) < 15 or len(norm) > 280:
            continue
        by_norm[norm].add(s.page)
    return {norm for norm, pages in by_norm.items() if len(pages) >= 2}


def _detect_top_band_repeats(sentences: list[Sentence], page_heights: dict[int, float]) -> set[str]:
    """A second running-header detector: any normalized text whose top-y on
    each occurrence sits in the **top 12 % of the page**. Catches journal
    banners that span 2+ lines (so the verbatim-equality detector misses them
    because each line has slightly different content).
    """
    from collections import defaultdict
    candidates: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for s in sentences:
        ph = page_heights.get(s.page)
        if not ph:
            continue
        top = min(b[1] for b in s.line_bboxes) if s.line_bboxes else 0.0
        if top > 0.12 * ph:
            continue
        norm = _collapse_ws(s.text).lower()
        if len(norm) < 12:
            continue
        candidates[norm].append((s.page, top))
    return {norm for norm, hits in candidates.items() if len({p for p, _ in hits}) >= 2}


def _build_query_pool(
    claim_text: str,
    quote_text: str | None,
    extra_queries: list[str] | None = None,
) -> list[str]:
    """Build the pool of query strings to score sentences against.

    Sentences are scored against the MAX similarity to any non-empty query.
    The pool always contains the claim text. When the curator supplied a
    ``quote``, that's added too — verbatim signals are the strongest match
    we can hope for. Rubric rationales (per-dimension explanations of *why*
    this paper supports this claim) bring much richer claim-specific
    keywords than the synthesized one-line ``text`` field, so they're
    layered in here.
    """
    pool: list[str] = []
    seen: set[str] = set()

    def _add(s: str | None) -> None:
        if not s:
            return
        cleaned = _collapse_ws(s).strip()
        if not cleaned:
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        pool.append(cleaned)

    _add(quote_text)
    _add(claim_text)
    for q in (extra_queries or []):
        _add(q)
    return pool


def _select_top_sentences(
    sentences: list[Sentence],
    claim_text: str,
    quote_text: str | None,
    extra_queries: list[str] | None = None,
    paper_title: str | None = None,
    page_heights: dict[int, float] | None = None,
) -> list[tuple[Sentence, float]]:
    queries = _build_query_pool(claim_text, quote_text, extra_queries)
    if not queries:
        return []
    # Build per-query keyword sets up-front so we don't recompute per
    # sentence. The aggregate keyword set is the union — gives any sentence
    # that mentions a rare term in *any* query a bonus.
    keywords = _rare_keywords(" ".join(queries))
    queries_lower = [q.lower() for q in queries]
    paper_title_norm = (
        _collapse_ws(paper_title or "").lower().rstrip(" .") or None
    )

    repeated_headers = _detect_repeated_headers(sentences)
    top_band_repeats = (
        _detect_top_band_repeats(sentences, page_heights)
        if page_heights else set()
    )

    scored: list[tuple[Sentence, float]] = []
    for s in sentences:
        # MAX score across all queries — most permissive, most precise.
        best = 0.0
        for ql in queries_lower:
            sc = _score_sentence(s.text, ql, keywords)
            if sc > best:
                best = sc
        sn = _collapse_ws(s.text).lower().rstrip(" .")
        if sn in repeated_headers:
            best -= HEADER_PENALTY
        if sn in top_band_repeats:
            best -= RUNNING_HEADER_PENALTY
        # Penalise the paper's own title (front-page header / running banner)
        # — it's metadata noise, not body argument.
        if paper_title_norm:
            # Loose match: longest-common-substring or fuzz partial_ratio,
            # because the rendered text may have stylistic differences
            # (capitalisation, hyphenation, line wrapping) vs the registry.
            ratio = fuzz.partial_ratio(paper_title_norm, sn)
            if sn == paper_title_norm or ratio >= 90:
                best -= PAPER_TITLE_PENALTY
                if s.page == 0:
                    # Page 1 cover-page title — extra suppression.
                    best -= PAGE_ONE_TITLE_PENALTY
        if best < SENTENCE_SCORE_THRESHOLD:
            continue
        scored.append((s, best))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:TOP_N_SENTENCES]


def _max_score_anywhere(
    sentences: list[Sentence],
    claim_text: str,
    quote_text: str | None,
    extra_queries: list[str] | None = None,
) -> float:
    queries = _build_query_pool(claim_text, quote_text, extra_queries)
    if not queries:
        return 0.0
    keywords = _rare_keywords(" ".join(queries))
    queries_lower = [q.lower() for q in queries]
    best = 0.0
    for s in sentences:
        for ql in queries_lower:
            sc = _score_sentence(s.text, ql, keywords)
            if sc > best:
                best = sc
    return best


def _group_sentences(scored: list[tuple[Sentence, float]]) -> list[list[tuple[Sentence, float]]]:
    """Group sentences by (page, vertical adjacency).

    Two sentences are adjacent iff:
      - same page, AND
      - same block_idx, OR vertical gap between their bboxes < 1.5 * line_height.
    Returns groups sorted by (page, top-y).
    """
    if not scored:
        return []
    # Sort by (page, top-y) for deterministic grouping.
    items = sorted(scored, key=lambda t: (t[0].page, t[0].union_bbox()[1]))

    groups: list[list[tuple[Sentence, float]]] = []
    for item in items:
        s, _sc = item
        sb = s.union_bbox()
        slh = s.line_height()
        placed = False
        for g in groups:
            g_pages = {x[0].page for x in g}
            if s.page not in g_pages:
                continue
            # Check adjacency with any sentence already in the group.
            for (other, _osc) in g:
                if other.page != s.page:
                    continue
                if other.block_idx == s.block_idx:
                    g.append(item)
                    placed = True
                    break
                ob = other.union_bbox()
                lh = max(slh, other.line_height())
                vgap = max(0.0, max(sb[1], ob[1]) - min(sb[3], ob[3]))
                # If bboxes vertically touch / overlap, treat as adjacent.
                # vgap is 0 when they overlap; positive when they don't.
                # We compute the actual gap from one's bottom to the other's top.
                top = min(sb[1], ob[1])
                bot_of_top = sb[3] if sb[1] == top else ob[3]
                top_of_bot = ob[1] if sb[1] == top else sb[1]
                actual_gap = max(0.0, top_of_bot - bot_of_top)
                if actual_gap < 1.5 * lh:
                    g.append(item)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            groups.append([item])

    # Re-sort groups by (page, top-y of group).
    def group_sort_key(g: list[tuple[Sentence, float]]):
        page = min(s.page for s, _ in g)
        top_y = min(s.union_bbox()[1] for s, _ in g)
        return (page, top_y)

    groups.sort(key=group_sort_key)
    return groups


def _group_bbox(group: list[tuple[Sentence, float]]) -> tuple[float, float, float, float]:
    xs0 = min(s.union_bbox()[0] for s, _ in group)
    ys0 = min(s.union_bbox()[1] for s, _ in group)
    xs1 = max(s.union_bbox()[2] for s, _ in group)
    ys1 = max(s.union_bbox()[3] for s, _ in group)
    return (xs0, ys0, xs1, ys1)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_group_png(
    doc: fitz.Document,
    page_idx: int,
    line_bboxes: list[tuple[float, float, float, float]],
    overall_bbox: tuple[float, float, float, float],
) -> bytes:
    """Render one group as a cropped PNG with a translucent yellow highlight.

    We highlight each line bbox individually (tighter than the union box) but
    crop using the union bbox so the rendered PNG covers the whole group.
    """
    page = doc.load_page(page_idx)

    annots = []
    for lb in line_bboxes:
        rect = fitz.Rect(*lb)
        if rect.is_empty:
            continue
        annot = page.add_rect_annot(rect)
        annot.set_colors(stroke=(0.85, 0.7, 0.0), fill=(1.0, 0.95, 0.2))
        annot.set_opacity(0.35)
        annot.set_border(width=0.8)
        annot.update()
        annots.append(annot)

    pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), alpha=False)
    for annot in annots:
        try:
            page.delete_annot(annot)
        except Exception:
            pass

    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    y0_px = max(0, int(overall_bbox[1] * ZOOM) - MARGIN_PX)
    y1_px = min(img.height, int(overall_bbox[3] * ZOOM) + MARGIN_PX)
    if y1_px - y0_px < 200:
        y0_px = max(0, y0_px - 60)
        y1_px = min(img.height, y1_px + 60)
    crop = img.crop((0, y0_px, img.width, y1_px))

    buf = io.BytesIO()
    crop.save(buf, format="PNG", optimize=True)
    out_bytes = buf.getvalue()

    cur_img = crop
    while len(out_bytes) > PNG_BYTES_CAP and cur_img.width > 500:
        if cur_img.width > MAX_DOWNSCALE_WIDTH:
            target_w = MAX_DOWNSCALE_WIDTH
        else:
            target_w = max(500, int(cur_img.width * 0.80))
        ratio = target_w / cur_img.width
        new_size = (target_w, max(1, int(cur_img.height * ratio)))
        cur_img = cur_img.resize(new_size, Image.LANCZOS)
        buf2 = io.BytesIO()
        cur_img.save(buf2, format="PNG", optimize=True)
        out_bytes = buf2.getvalue()

    # Quantize-and-shrink loop: try 128/64/32 colours and progressively
    # smaller widths until under cap or we hit the floor.
    for ncolors in (QUANTIZE_COLORS, 64, 32):
        if len(out_bytes) <= PNG_BYTES_CAP:
            break
        try:
            quant = cur_img.quantize(colors=ncolors, method=Image.MEDIANCUT)
            buf_q = io.BytesIO()
            quant.save(buf_q, format="PNG", optimize=True)
            if len(buf_q.getvalue()) < len(out_bytes):
                out_bytes = buf_q.getvalue()
        except Exception:
            pass

    # If still oversized, downscale further with quantization.
    while len(out_bytes) > PNG_BYTES_CAP and cur_img.width > 450:
        target_w = max(450, int(cur_img.width * 0.85))
        ratio = target_w / cur_img.width
        new_size = (target_w, max(1, int(cur_img.height * ratio)))
        cur_img = cur_img.resize(new_size, Image.LANCZOS)
        try:
            quant = cur_img.quantize(colors=64, method=Image.MEDIANCUT)
            buf_q = io.BytesIO()
            quant.save(buf_q, format="PNG", optimize=True)
            out_bytes = buf_q.getvalue()
        except Exception:
            buf_x = io.BytesIO()
            cur_img.save(buf_x, format="PNG", optimize=True)
            out_bytes = buf_x.getvalue()

    return out_bytes


# ---------------------------------------------------------------------------
# Block-level fallback (legacy single-block matcher)
# ---------------------------------------------------------------------------

def _block_fallback(
    doc: fitz.Document, evidence_text: str, quote_text: str | None
) -> dict | None:
    """Return {page_index, bbox, block_text, score} or None.

    Identical-spirit to the original block-level matcher: scan all blocks,
    score with partial_ratio, prefer longer blocks on tie.
    """
    keywords = _rare_keywords(evidence_text + " " + (quote_text or ""))
    eq_text = quote_text if quote_text else evidence_text
    eq_lower = _collapse_ws(eq_text).lower()

    best = None
    for page_idx in range(len(doc)):
        page = doc.load_page(page_idx)
        try:
            blocks = page.get_text("blocks")
        except Exception:
            continue
        for b in blocks:
            if len(b) < 7:
                continue
            x0, y0, x1, y1, btext, _bno, btype = b[:7]
            if btype != 0 or not btext or not btext.strip():
                continue
            if len(_collapse_ws(btext)) < MIN_BLOCK_CHARS:
                continue
            bt = _collapse_ws(btext).lower()
            sc = float(fuzz.partial_ratio(eq_lower, bt))
            if any(kw in bt for kw in keywords):
                sc += KEYWORD_BOOST
            if best is None or sc > best["score"] or (
                sc == best["score"] and len(btext) > len(best["block_text"])
            ):
                best = {
                    "page_index": page_idx,
                    "bbox": (float(x0), float(y0), float(x1), float(y1)),
                    "block_text": btext,
                    "score": sc,
                }
    return best


# ---------------------------------------------------------------------------
# Driver per evidence point
# ---------------------------------------------------------------------------

def _iter_evidence(data: dict[str, Any]):
    """Yield each evidence point with its parent claim's claim-text stamped on
    a transient ``_parent_claim_text`` key. The renderer reads this to use
    the claim text as one of the matching queries — much closer to the
    real argument than the synthesised one-liner ``text`` field. The
    transient key is stripped before serialisation in ``_strip_transient_fields``.
    """
    for paradigm in data.get("paradigms", []):
        for claim in paradigm.get("claims", []) or []:
            parent_claim_text = claim.get("claim") or ""
            for ep in claim.get("evidence_points", []) or []:
                if parent_claim_text:
                    ep["_parent_claim_text"] = parent_claim_text
                yield ep


def _strip_transient_fields(data: dict[str, Any]) -> None:
    """Remove any ``_parent_claim_text`` keys before saving back to disk."""
    for paradigm in data.get("paradigms", []):
        for claim in paradigm.get("claims", []) or []:
            for ep in claim.get("evidence_points", []) or []:
                ep.pop("_parent_claim_text", None)


def _strip_legacy_screenshot_fields(ep: dict) -> None:
    for k in ("screenshot_href", "page", "quote"):
        if k in ep:
            ep.pop(k, None)


def _delete_old_screenshots(eid: str) -> None:
    """Remove any old PNGs (legacy single OR previous-stack) belonging to this id."""
    if not ASSETS_DIR.is_dir():
        return
    for f in ASSETS_DIR.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() != ".png":
            continue
        name = f.name
        if name == f"{eid}.png":
            f.unlink()
        elif name.startswith(f"{eid}__") and name.endswith(".png"):
            f.unlink()


def _process_one(ep: dict, force: bool, log: dict) -> str:
    """Returns one of: ok | not_found | manual_review | no_pdf | skipped | locked."""
    eid = ep.get("id")
    paper_id = ep.get("paper_id")
    if not eid:
        return "skipped"

    if ep.get("confidence") == "manual_verified":
        log[eid] = {"status": "locked", "notes": "confidence=manual_verified, untouched"}
        return "locked"

    if not paper_id:
        log[eid] = {"status": "no_pdf", "notes": "no paper_id"}
        # Don't overwrite existing screenshot_status for purely-textual evidence.
        ep.setdefault("screenshot_status", "no_pdf")
        return "no_pdf"

    pdf_path = _find_pdf(paper_id)
    if pdf_path is None:
        log[eid] = {"status": "no_pdf", "notes": f"no PDF for {paper_id}"}
        ep["screenshot_status"] = "no_pdf"
        # Make sure we don't leave stale screenshots/screenshot_href around.
        ep.pop("screenshots", None)
        ep["screenshot_count"] = 0
        _strip_legacy_screenshot_fields(ep)
        return "no_pdf"

    # Idempotency: skip if already ok and PNGs are present.
    if not force and ep.get("screenshot_status") == "ok":
        existing = ep.get("screenshots") or []
        if existing and all(
            (ROOT / s.get("href", "")).is_file() for s in existing if s.get("href")
        ):
            log[eid] = {
                "status": "ok",
                "notes": "skipped (idempotent)",
                "screenshots": len(existing),
            }
            return "skipped"

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        log[eid] = {"status": "manual_review", "notes": f"could not open PDF: {exc}"}
        ep["screenshot_status"] = "manual_review"
        return "manual_review"

    try:
        sentences, total_text_len = _build_sentence_corpus(doc)

        if total_text_len < MIN_DOC_TEXT_LEN:
            log[eid] = {
                "status": "manual_review",
                "notes": f"PDF text under {MIN_DOC_TEXT_LEN} chars (likely scan)",
            }
            ep["screenshot_status"] = "manual_review"
            return "manual_review"

        claim_text = ep.get("text") or ""
        quote_text = ep.get("quote") or None
        # Layer in the parent claim's actual claim text + every rubric
        # rationale as additional scoring queries. The rationales are the
        # richest claim-specific signal we have (they were written by the
        # paradigm scorer agents pointing AT the supporting evidence) and
        # consistently produce more on-topic sentence selections than the
        # one-line synthesised ``text`` field alone.
        extra_queries: list[str] = []
        parent_claim = (
            ep.get("_parent_claim_text")
            if isinstance(ep, dict) and ep.get("_parent_claim_text")
            else None
        )
        if parent_claim:
            extra_queries.append(parent_claim)
        rubric = ep.get("rubric") or {}
        if isinstance(rubric, dict):
            for dim_name in (
                "useful_outcomes", "immediate_benefit", "plausible",
                "scalable", "how_to_validate",
            ):
                d = rubric.get(dim_name)
                if isinstance(d, dict) and d.get("rationale"):
                    extra_queries.append(str(d["rationale"]))

        top_scored = _select_top_sentences(
            sentences, claim_text, quote_text, extra_queries=extra_queries,
            paper_title=_paper_title_for(paper_id),
            page_heights=_build_page_heights(doc),
        )

        # If nothing meets the sentence threshold, decide between
        # block-fallback and not_found by looking at the global max score.
        if not top_scored:
            global_best = _max_score_anywhere(sentences, claim_text, quote_text, extra_queries=extra_queries)
            if global_best < BLOCK_FALLBACK_FLOOR:
                log[eid] = {
                    "status": "not_found",
                    "score": round(global_best, 1),
                    "notes": f"no sentence >= {BLOCK_FALLBACK_FLOOR}, no block fallback warranted",
                }
                ep["screenshot_status"] = "not_found"
                ep.pop("screenshots", None)
                ep["screenshot_count"] = 0
                _strip_legacy_screenshot_fields(ep)
                return "not_found"
            # Forced block fallback.
            best_block = _block_fallback(doc, claim_text, quote_text)
            if best_block is None:
                log[eid] = {
                    "status": "not_found",
                    "notes": "block fallback found nothing",
                }
                ep["screenshot_status"] = "not_found"
                ep.pop("screenshots", None)
                ep["screenshot_count"] = 0
                _strip_legacy_screenshot_fields(ep)
                return "not_found"

            _delete_old_screenshots(eid)
            ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            png_bytes = _render_group_png(
                doc,
                best_block["page_index"],
                [best_block["bbox"]],
                best_block["bbox"],
            )
            href = f"multimodal_genomics/assets/evidence/{eid}__01.png"
            (ROOT / href).write_bytes(png_bytes)

            quote = _collapse_ws(best_block["block_text"])
            if len(quote) > 600:
                quote = quote[:599].rstrip() + "…"

            ep["screenshots"] = [
                {
                    "href": href,
                    "page": best_block["page_index"] + 1,
                    "section_hint": _section_hint_for_page(best_block["page_index"]),
                    "sentences": [
                        {"text": quote, "match_score": round(best_block["score"], 1)}
                    ],
                    "highlight_granularity": "block",
                }
            ]
            ep["screenshot_count"] = 1
            ep["screenshot_strategy"] = "block-fallback"
            ep["screenshot_status"] = "ok"
            ep["confidence"] = "parsed_pdf"
            _strip_legacy_screenshot_fields(ep)

            log[eid] = {
                "status": "ok",
                "strategy": "block-fallback",
                "score": round(best_block["score"], 1),
                "screenshots": 1,
                "png_bytes": len(png_bytes),
                "notes": f"block-fallback page={best_block['page_index'] + 1}",
            }
            return "ok"

        # Multi-sentence path -------------------------------------------------
        groups = _group_sentences(top_scored)
        if not groups:
            ep["screenshot_status"] = "not_found"
            ep.pop("screenshots", None)
            ep["screenshot_count"] = 0
            _strip_legacy_screenshot_fields(ep)
            log[eid] = {"status": "not_found", "notes": "no groups after grouping"}
            return "not_found"

        _delete_old_screenshots(eid)
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)

        screenshots: list[dict] = []
        log_entries: list[dict] = []
        for i, g in enumerate(groups, start=1):
            page_idx = g[0][0].page
            overall = _group_bbox(g)
            line_bboxes: list[tuple[float, float, float, float]] = []
            for s, _sc in g:
                line_bboxes.extend(s.line_bboxes)
            try:
                png_bytes = _render_group_png(doc, page_idx, line_bboxes, overall)
            except Exception as exc:
                log_entries.append({"group": i, "error": str(exc)})
                continue
            href = f"multimodal_genomics/assets/evidence/{eid}__{i:02d}.png"
            (ROOT / href).write_bytes(png_bytes)

            granularity = "sentence" if len(g) == 1 else "sentence-group"
            screenshots.append(
                {
                    "href": href,
                    "page": page_idx + 1,
                    "section_hint": _section_hint_for_page(page_idx),
                    "sentences": [
                        {"text": s.text, "match_score": round(sc, 1)}
                        for (s, sc) in sorted(g, key=lambda t: t[0].union_bbox()[1])
                    ],
                    "highlight_granularity": granularity,
                }
            )
            log_entries.append(
                {
                    "group": i,
                    "page": page_idx + 1,
                    "n_sentences": len(g),
                    "png_bytes": len(png_bytes),
                    "scores": [round(sc, 1) for _, sc in g],
                }
            )

        if not screenshots:
            ep["screenshot_status"] = "manual_review"
            log[eid] = {"status": "manual_review", "notes": "render failed for all groups", "groups": log_entries}
            return "manual_review"

        # Strategy classification.
        pages = {s["page"] for s in screenshots}
        if len(screenshots) == 1:
            single = screenshots[0]
            strategy = (
                "single-sentence"
                if single["highlight_granularity"] == "sentence"
                else "multi-sentence-multi-page"  # 1 group with multiple sentences -> still considered multi-sentence
            )
            # Per the spec, the only single-sentence strategy is 1 group with 1 sentence
            # AND that group has `highlight_granularity == "sentence"`. Otherwise
            # we treat it as "multi-sentence-multi-page" (semantically: the
            # multi-sentence-aware path).
            if single["highlight_granularity"] != "sentence":
                strategy = "multi-sentence-multi-page"
        else:
            strategy = "multi-sentence-multi-page"

        ep["screenshots"] = screenshots
        ep["screenshot_count"] = len(screenshots)
        ep["screenshot_strategy"] = strategy
        ep["screenshot_status"] = "ok"
        ep["confidence"] = "parsed_pdf"
        _strip_legacy_screenshot_fields(ep)

        log[eid] = {
            "status": "ok",
            "strategy": strategy,
            "screenshots": len(screenshots),
            "pages": sorted(pages),
            "groups": log_entries,
        }
        return "ok"
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-render even if PNGs exist")
    parser.add_argument("--id", dest="only_id", default=None, help="process a single evidence id")
    parser.add_argument("--limit", type=int, default=None, help="process at most N items")
    args = parser.parse_args()

    if not EVIDENCE_FILE.is_file():
        print(f"ERROR: {EVIDENCE_FILE} not found", file=sys.stderr)
        return 2

    data = json.loads(EVIDENCE_FILE.read_text(encoding="utf-8"))

    log: dict[str, dict] = {}
    if LOG_FILE.is_file():
        try:
            log = json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log = {}

    counts = {"ok": 0, "not_found": 0, "manual_review": 0, "no_pdf": 0, "skipped": 0, "locked": 0}

    targets = []
    for ep in _iter_evidence(data):
        if args.only_id and ep.get("id") != args.only_id:
            continue
        targets.append(ep)
        if args.limit is not None and len(targets) >= args.limit:
            break

    if args.only_id and not targets:
        print(f"ERROR: evidence id {args.only_id!r} not found", file=sys.stderr)
        return 2

    total_pngs = 0
    total_bytes = 0
    stack_size_dist: dict[int, int] = {}
    strategy_dist: dict[str, int] = {}
    per_evidence_summary: list[str] = []

    for ep in targets:
        eid = ep.get("id", "?")
        status = _process_one(ep, force=args.force, log=log)
        counts[status] = counts.get(status, 0) + 1
        ep_screens = ep.get("screenshots") or []
        n = len(ep_screens)
        if status == "ok":
            stack_size_dist[n] = stack_size_dist.get(n, 0) + 1
            strat = ep.get("screenshot_strategy", "unknown")
            strategy_dist[strat] = strategy_dist.get(strat, 0) + 1
            for s in ep_screens:
                f = ROOT / s.get("href", "")
                if f.is_file():
                    total_bytes += f.stat().st_size
                    total_pngs += 1

        per_evidence_summary.append(
            f"  [{status:>13}] {eid}  paper={ep.get('paper_id') or '-':<8}"
            f"  screenshots={n}  strategy={ep.get('screenshot_strategy','-')}"
        )
        print(per_evidence_summary[-1])

    _strip_transient_fields(data)
    EVIDENCE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    LOG_FILE.write_text(
        json.dumps(log, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    print("\nTally:")
    for k in ("ok", "not_found", "manual_review", "no_pdf", "skipped", "locked"):
        print(f"  {k}: {counts[k]}")
    print(f"  total PNGs generated: {total_pngs}")
    print(f"  total PNG bytes: {total_bytes} ({total_bytes / 1024:.1f} KB)")
    if stack_size_dist:
        print("  stack-size distribution (size -> evidence-point count):")
        for sz in sorted(stack_size_dist.keys()):
            print(f"    {sz}: {stack_size_dist[sz]}")
    if strategy_dist:
        print("  strategy distribution:")
        for k in sorted(strategy_dist.keys()):
            print(f"    {k}: {strategy_dist[k]}")
    print(f"  evidence JSON: {EVIDENCE_FILE.relative_to(ROOT)}")
    print(f"  log: {LOG_FILE.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
