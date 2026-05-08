#!/usr/bin/env python3
"""Generate multi-sentence evidence screenshots for hybrid_model_evidence.json.

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

Schema written into ``metadata/hybrid_model_evidence.json`` (per evidence
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
  python scripts/generate_evidence_screenshots.py
  python scripts/generate_evidence_screenshots.py --force
  python scripts/generate_evidence_screenshots.py --id <evidence_id>
  python scripts/generate_evidence_screenshots.py --limit N
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


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FILE = ROOT / "metadata" / "hybrid_model_evidence.json"
LOG_FILE = ROOT / "metadata" / "hybrid_evidence_screenshot_log.json"
PDF_DIR = ROOT / "pdfs"
ASSETS_DIR = ROOT / "docs" / "assets" / "evidence"

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


def _build_sentence_corpus(doc: fitz.Document) -> tuple[list[Sentence], int]:
    """Return ``(sentences, total_block_text_len)``."""
    sentences: list[Sentence] = []
    total_text_len = 0

    for page_idx in range(len(doc)):
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


def _score_sentence(sent_text: str, claim_lower: str, keywords: list[str]) -> float:
    st = sent_text.lower()
    if not st or not claim_lower:
        return 0.0
    score = float(fuzz.partial_ratio(claim_lower, st))
    if any(kw in st for kw in keywords):
        score += KEYWORD_BOOST
    if _looks_like_reference_line(sent_text):
        score -= REFLINE_PENALTY
    return score


def _detect_repeated_headers(sentences: list[Sentence]) -> set[str]:
    """A 'running header' = a short normalized sentence that appears verbatim on
    >=2 pages, typically near the top. Return the set of normalized strings to
    penalise.
    """
    from collections import defaultdict
    by_norm: dict[str, set[int]] = defaultdict(set)
    for s in sentences:
        norm = _collapse_ws(s.text).lower()
        if len(norm) < 30 or len(norm) > 220:
            continue
        by_norm[norm].add(s.page)
    return {norm for norm, pages in by_norm.items() if len(pages) >= 2}


def _select_top_sentences(
    sentences: list[Sentence],
    claim_text: str,
    quote_text: str | None,
) -> list[tuple[Sentence, float]]:
    keywords = _rare_keywords((claim_text or "") + " " + (quote_text or ""))
    eq_text = (quote_text or claim_text or "").strip()
    if not eq_text:
        return []
    eq_lower = _collapse_ws(eq_text).lower()

    repeated_headers = _detect_repeated_headers(sentences)

    scored: list[tuple[Sentence, float]] = []
    for s in sentences:
        sc = _score_sentence(s.text, eq_lower, keywords)
        if _collapse_ws(s.text).lower() in repeated_headers:
            sc -= HEADER_PENALTY
        if sc < SENTENCE_SCORE_THRESHOLD:
            continue
        scored.append((s, sc))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:TOP_N_SENTENCES]


def _max_score_anywhere(sentences: list[Sentence], claim_text: str, quote_text: str | None) -> float:
    keywords = _rare_keywords((claim_text or "") + " " + (quote_text or ""))
    eq_text = (quote_text or claim_text or "").strip()
    if not eq_text:
        return 0.0
    eq_lower = _collapse_ws(eq_text).lower()
    best = 0.0
    for s in sentences:
        sc = _score_sentence(s.text, eq_lower, keywords)
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
    for paradigm in data.get("paradigms", []):
        for claim in paradigm.get("claims", []) or []:
            for ep in claim.get("evidence_points", []) or []:
                yield ep


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

        top_scored = _select_top_sentences(sentences, claim_text, quote_text)

        # If nothing meets the sentence threshold, decide between
        # block-fallback and not_found by looking at the global max score.
        if not top_scored:
            global_best = _max_score_anywhere(sentences, claim_text, quote_text)
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
            href = f"docs/assets/evidence/{eid}__01.png"
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
            href = f"docs/assets/evidence/{eid}__{i:02d}.png"
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
