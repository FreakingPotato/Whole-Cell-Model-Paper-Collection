#!/usr/bin/env python3
"""Generate evidence screenshots for hybrid_model_evidence.json.

For each evidence point with a local PDF in pdfs/, find the best matching
text block via PyMuPDF + rapidfuzz partial_ratio and render a cropped page
PNG with a translucent yellow highlight over the matched block.

Updates metadata/hybrid_model_evidence.json in place with:
  - screenshot_href, page, quote, screenshot_status
  - confidence bumped from metadata_only -> parsed_pdf

Idempotent: if screenshot_status == "ok" and the PNG exists, skip
(unless --force).

Usage:
    python scripts/generate_evidence_screenshots.py [--force] [--id <evidence_id>]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from PIL import Image
from rapidfuzz import fuzz


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FILE = ROOT / "metadata" / "hybrid_model_evidence.json"
LOG_FILE = ROOT / "metadata" / "hybrid_evidence_screenshot_log.json"
PDF_DIR = ROOT / "pdfs"
ASSETS_DIR = ROOT / "docs" / "assets" / "evidence"

MATCH_THRESHOLD = 60
MARGIN_PX = 120  # vertical margin around the matched block in rendered pixels
DPI = 200
ZOOM = DPI / 72.0
PNG_BYTES_CAP = 350 * 1024  # 350 KB
MAX_DOWNSCALE_WIDTH = 1200
MAX_QUOTE_LEN = 600
MIN_DOC_TEXT_LEN = 200  # heuristic for image-only / garbled scans


_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{5,}")  # words length >= 6


def _collapse_ws(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _rare_keywords(text: str) -> list[str]:
    """Lowercased word tokens of length >= 6 from the evidence text."""
    return list({m.group(0).lower() for m in _WORD_RE.finditer(text)})


def _find_pdf(paper_id: str) -> Path | None:
    if not PDF_DIR.is_dir():
        return None
    for p in PDF_DIR.iterdir():
        if p.is_file() and p.name.startswith(f"{paper_id}_") and p.suffix.lower() == ".pdf":
            return p
    return None


def _score_block(block_text: str, eq_text: str, keywords: list[str]) -> float:
    bt = _collapse_ws(block_text).lower()
    et = _collapse_ws(eq_text).lower()
    if not bt or not et:
        return 0.0
    score = float(fuzz.partial_ratio(et, bt))
    if any(kw in bt for kw in keywords):
        score += 20.0
    return score


def _best_match(doc: fitz.Document, evidence_text: str, quote_text: str | None) -> dict | None:
    """Return best-match dict {page_index, bbox, block_text, score} or None."""
    keywords = _rare_keywords(evidence_text + " " + (quote_text or ""))
    # Prefer the explicit quote if present, else the evidence text.
    eq_text = quote_text if quote_text else evidence_text

    best = None
    total_text_len = 0
    for page_idx in range(len(doc)):
        page = doc.load_page(page_idx)
        try:
            blocks = page.get_text("blocks")
        except Exception:
            blocks = []
        for b in blocks:
            # b: (x0, y0, x1, y1, text, block_no, block_type)
            if len(b) < 7:
                continue
            x0, y0, x1, y1, btext, _bno, btype = b[:7]
            if btype != 0 or not btext or not btext.strip():
                continue
            total_text_len += len(btext)
            sc = _score_block(btext, eq_text, keywords)
            # Short blocks (<80 chars) are usually figure labels, headings, or
            # page numbers — they often partial-match on a single shared word
            # and yield misleading "perfect" scores. Drop them entirely; we
            # want a paragraph, not a header.
            if len(_collapse_ws(btext)) < 80:
                continue
            if best is None or sc > best["score"] or (
                sc == best["score"] and len(btext) > len(best["block_text"])
            ):
                best = {
                    "page_index": page_idx,
                    "bbox": (float(x0), float(y0), float(x1), float(y1)),
                    "block_text": btext,
                    "score": sc,
                }

    return best, total_text_len


def _render_with_highlight(
    doc: fitz.Document, page_idx: int, bbox: tuple[float, float, float, float]
) -> bytes:
    """Render page as PNG bytes with a yellow translucent rectangle drawn on
    the matched block, then crop to the block's vertical extent +/- MARGIN_PX
    in rendered pixel space."""
    page = doc.load_page(page_idx)
    rect = fitz.Rect(*bbox)

    # Draw a yellow translucent fill over the bbox before rasterizing. We use
    # an annotation so we don't permanently modify the document on disk
    # (PyMuPDF won't save unless we ask, but be defensive anyway).
    annot = page.add_rect_annot(rect)
    annot.set_colors(stroke=(0.85, 0.7, 0.0), fill=(1.0, 0.95, 0.2))
    annot.set_opacity(0.30)
    annot.set_border(width=1.5)
    annot.update()

    pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), alpha=False)

    # Remove the annotation (so the doc isn't dirty for downstream pages).
    page.delete_annot(annot)

    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    # Crop vertically around the bbox; keep full page width to preserve column
    # context. Convert PDF-points bbox to pixel coords.
    y0_px = max(0, int(bbox[1] * ZOOM) - MARGIN_PX)
    y1_px = min(img.height, int(bbox[3] * ZOOM) + MARGIN_PX)
    if y1_px - y0_px < 200:  # avoid absurdly tiny crops
        y0_px = max(0, y0_px - 60)
        y1_px = min(img.height, y1_px + 60)
    crop = img.crop((0, y0_px, img.width, y1_px))

    buf = io.BytesIO()
    crop.save(buf, format="PNG", optimize=True)
    out_bytes = buf.getvalue()

    # Enforce 350 KB cap by iterative downscale. First pass caps width at
    # MAX_DOWNSCALE_WIDTH (1200 px); subsequent passes shrink width by 20%
    # until we fit. Hard floor at 500 px so text remains legible.
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

    # If still over (very tall crops with text-heavy content), reduce the
    # color palette. PNG quantize with 256 colors typically halves size for
    # text-on-white.
    if len(out_bytes) > PNG_BYTES_CAP:
        try:
            quant = cur_img.quantize(colors=128, method=Image.MEDIANCUT)
            buf3 = io.BytesIO()
            quant.save(buf3, format="PNG", optimize=True)
            if len(buf3.getvalue()) < len(out_bytes):
                out_bytes = buf3.getvalue()
        except Exception:
            pass

    return out_bytes


def _iter_evidence(data: dict[str, Any]):
    for paradigm in data.get("paradigms", []):
        for claim in paradigm.get("claims", []) or []:
            for ep in claim.get("evidence_points", []) or []:
                yield ep


def _process_one(ep: dict, force: bool, log: dict) -> str:
    """Process a single evidence point. Returns one of:
    ok | not_found | manual_review | no_pdf | skipped | locked
    """
    eid = ep.get("id")
    paper_id = ep.get("paper_id")
    if not eid:
        return "skipped"

    # Don't touch human-curated entries.
    if ep.get("confidence") == "manual_verified":
        log[eid] = {"status": "locked", "notes": "confidence=manual_verified, untouched"}
        return "locked"

    if not paper_id:
        log[eid] = {"status": "no_pdf", "notes": "no paper_id"}
        return "no_pdf"

    pdf_path = _find_pdf(paper_id)
    if pdf_path is None:
        log[eid] = {"status": "no_pdf", "notes": f"no PDF for {paper_id}"}
        return "no_pdf"

    out_path = ASSETS_DIR / f"{eid}.png"
    if not force and ep.get("screenshot_status") == "ok" and out_path.is_file():
        log[eid] = {
            "status": "ok",
            "notes": "skipped (idempotent)",
            "png_bytes": out_path.stat().st_size,
            "page": ep.get("page"),
        }
        return "skipped"

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        log[eid] = {"status": "manual_review", "notes": f"could not open PDF: {exc}"}
        ep["screenshot_status"] = "manual_review"
        return "manual_review"

    try:
        best, total_text_len = _best_match(doc, ep.get("text", ""), ep.get("quote"))
    finally:
        pass  # close after rendering

    if total_text_len < MIN_DOC_TEXT_LEN:
        log[eid] = {
            "status": "manual_review",
            "notes": f"PDF text under {MIN_DOC_TEXT_LEN} chars (likely image-only/scan)",
            "png_bytes": 0,
        }
        ep["screenshot_status"] = "manual_review"
        doc.close()
        return "manual_review"

    if best is None or best["score"] < MATCH_THRESHOLD:
        log[eid] = {
            "status": "not_found",
            "score": (best["score"] if best else 0),
            "notes": f"best score below threshold {MATCH_THRESHOLD}",
        }
        ep["screenshot_status"] = "not_found"
        doc.close()
        return "not_found"

    try:
        png_bytes = _render_with_highlight(doc, best["page_index"], best["bbox"])
    except Exception as exc:
        log[eid] = {"status": "manual_review", "notes": f"render failed: {exc}"}
        ep["screenshot_status"] = "manual_review"
        doc.close()
        return "manual_review"
    finally:
        doc.close()

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png_bytes)

    page_one_indexed = best["page_index"] + 1
    quote = _collapse_ws(best["block_text"])
    if len(quote) > MAX_QUOTE_LEN:
        quote = quote[: MAX_QUOTE_LEN - 1].rstrip() + "…"

    ep["page"] = page_one_indexed
    ep["quote"] = quote
    ep["screenshot_href"] = f"docs/assets/evidence/{eid}.png"
    ep["screenshot_status"] = "ok"
    if ep.get("confidence") == "metadata_only" or "confidence" not in ep:
        ep["confidence"] = "parsed_pdf"

    log[eid] = {
        "status": "ok",
        "score": round(best["score"], 1),
        "page": page_one_indexed,
        "block_bbox": [round(v, 2) for v in best["bbox"]],
        "png_bytes": len(png_bytes),
        "notes": f"matched on page {page_one_indexed}",
    }
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-render even if PNG exists")
    parser.add_argument("--id", dest="only_id", default=None, help="process a single evidence id")
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
    total_bytes = 0

    targets = []
    for ep in _iter_evidence(data):
        if args.only_id and ep.get("id") != args.only_id:
            continue
        targets.append(ep)

    if args.only_id and not targets:
        print(f"ERROR: evidence id {args.only_id!r} not found", file=sys.stderr)
        return 2

    for ep in targets:
        eid = ep.get("id", "?")
        status = _process_one(ep, force=args.force, log=log)
        counts[status] = counts.get(status, 0) + 1
        entry = log.get(eid, {})
        score = entry.get("score", "-")
        page = entry.get("page", "-")
        png_bytes = entry.get("png_bytes")
        if status == "ok" and png_bytes is not None:
            total_bytes += png_bytes
        print(
            f"  [{status:>13}] {eid}  paper={ep.get('paper_id') or '-':<8}  "
            f"page={page}  score={score}  bytes={png_bytes if png_bytes is not None else '-'}"
        )

    # Persist evidence JSON (only if we processed at least one and aren't using --id-only on a locked).
    EVIDENCE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    # Persist log.
    LOG_FILE.write_text(
        json.dumps(log, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    print("\nTally:")
    for k in ("ok", "not_found", "manual_review", "no_pdf", "skipped", "locked"):
        print(f"  {k}: {counts[k]}")
    print(f"  total PNG bytes (this run, ok only): {total_bytes}")
    print(f"  evidence JSON: {EVIDENCE_FILE.relative_to(ROOT)}")
    print(f"  log: {LOG_FILE.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
