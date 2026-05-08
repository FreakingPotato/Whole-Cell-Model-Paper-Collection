#!/usr/bin/env python3
"""Score and render planning-evidence screenshots.

For each next-step claim in ``planning_ontology.json``, walk every paper
that has a local PDF in ``pdfs/MMG-*.pdf``, locate that paper's
**Discussion / Limitations / Future Work / Outlook** section, score the
sentences in those sections against the next-step claim, render
multi-sentence screenshots of the strongest matches, and write the
per-(next-step, paper) results into
``multimodal_genomics/metadata/planning_evidence.json``.

This is a SEPARATE pipeline from the regular screenshot renderer
(``generate_screenshots.py``):

  - regular renderer: scores against every body sentence, excluding
    references / back-matter — used for the 9 ontology claims.
  - planning renderer: ONLY considers sentences in discussion-style
    sections — used for the 5 next-step claims.

Reuses the helper machinery from ``generate_screenshots.py`` (PyMuPDF
text/dict extraction, sentence scoring, fragment / caption / refline
penalties, multi-page sentence grouping, PNG rendering).

Usage::

    python multimodal_genomics/scripts/render_planning.py
    python multimodal_genomics/scripts/render_planning.py --force
    python multimodal_genomics/scripts/render_planning.py --next-step <id>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

# Reuse all helpers from the regular renderer — same scoring + rendering
# pipeline, just targeted at a different sub-section of each PDF.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import generate_screenshots as gs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
META_DIR = ROOT / "multimodal_genomics" / "metadata"
ASSETS_DIR = ROOT / "multimodal_genomics" / "assets" / "evidence"
PDF_DIR = ROOT / "pdfs"

PLANNING_ONTOLOGY = META_DIR / "planning_ontology.json"
PAPERS_FILE = META_DIR / "papers.json"
EVIDENCE_OUT = META_DIR / "planning_evidence.json"
LOG_FILE = META_DIR / "planning_log.json"

# ---------------------------------------------------------------------------
# Section detection — find the Discussion / Limitations / Future Work range
# inside each PDF. We need a *positive* range (start, end) here, not just
# the back-matter cutoff that the regular renderer uses.
# ---------------------------------------------------------------------------

# Section *opens*. Once we hit any of these we start collecting sentences.
_SECTION_OPEN_RE = re.compile(
    r"^\s*(?:\d+\.?\s*)?"
    r"(?:discussion|limitations(?:\s+and\s+future\s+work)?|"
    r"future\s+work|future\s+directions?|"
    r"outlook|conclusions?(?:\s+and\s+future\s+work)?|"
    r"open\s+(?:problems|questions|challenges)|"
    r"challenges?(?:\s+and\s+future\s+work)?|"
    r"perspective[s]?|"
    r"summary\s+and\s+future|broader\s+impacts?)\b\s*\.?\s*$",
    re.IGNORECASE,
)
# The renderer's existing back-matter regex covers everything that closes
# the body section (References, Acknowledgements, etc.) — reuse it.
_SECTION_CLOSE_RE = gs._BACK_MATTER_HEADING_RE


def _find_planning_ranges(doc) -> list[tuple[int, float, int, float]]:
    """Walk every page; return a list of ``(start_page, start_y, end_page, end_y)``
    tuples giving the bbox-y ranges of every Discussion / Limitations /
    Future Work / Conclusion section in the document.

    A section ENDS when we hit either another _SECTION_OPEN heading
    (treat it as a separate section), a _SECTION_CLOSE_RE heading, or
    end-of-document.
    """
    n_pages = len(doc)
    if n_pages == 0:
        return []
    # First pass: collect (page_idx, y_top, kind) for every heading-like line.
    # kind ∈ {"open", "close"}.
    headings: list[tuple[int, float, str]] = []
    for page_idx in range(n_pages):
        try:
            page_dict = doc.load_page(page_idx).get_text("dict")
        except Exception:
            continue
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in (block.get("lines") or []):
                spans = line.get("spans") or []
                line_text = "".join(span.get("text", "") for span in spans).strip()
                if not line_text or len(line_text) > 80:
                    continue
                stripped = gs._HEADING_PREFIX_RE.sub("", line_text)
                bbox = line.get("bbox") or (0, 0, 0, 0)
                if _SECTION_OPEN_RE.match(stripped):
                    headings.append((page_idx, float(bbox[1]), "open"))
                elif _SECTION_CLOSE_RE.match(stripped):
                    headings.append((page_idx, float(bbox[1]), "close"))
    # Sort by document position.
    headings.sort(key=lambda h: (h[0], h[1]))
    # Pair each "open" with the next heading (open or close).
    ranges: list[tuple[int, float, int, float]] = []
    for i, (p, y, kind) in enumerate(headings):
        if kind != "open":
            continue
        # Find next heading.
        if i + 1 < len(headings):
            ep, ey, _ek = headings[i + 1]
        else:
            ep, ey = n_pages - 1, 1e9
        ranges.append((p, y, ep, ey))
    return ranges


def _sentence_in_planning_ranges(s, ranges) -> bool:
    """A sentence is in-range iff its top-y bbox lies inside *any* of the
    Discussion / Limitations / Future Work spans we found."""
    if not s.line_bboxes:
        return False
    s_top = min(b[1] for b in s.line_bboxes)
    for (sp, sy, ep, ey) in ranges:
        if s.page < sp or s.page > ep:
            continue
        if s.page == sp and s_top < sy:
            continue
        if s.page == ep and s_top >= ey:
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Per-paper × per-next-step processing
# ---------------------------------------------------------------------------

def _process_paper_for_next_step(
    paper_id: str,
    paper_meta: dict,
    next_step: dict,
    *,
    force: bool,
) -> dict | None:
    """Render multi-sentence planning screenshots for one (paper, next_step).
    Returns the evidence-point payload if at least one screenshot was rendered;
    None when the PDF is missing or no sentences crossed threshold.
    """
    pdf_path = gs._find_pdf(paper_id)
    if not pdf_path:
        return {"paper_id": paper_id, "screenshot_status": "no_pdf", "screenshots": [], "screenshot_count": 0}

    try:
        doc = gs.fitz.open(pdf_path)
    except Exception as exc:
        return {"paper_id": paper_id, "screenshot_status": "manual_review", "error": str(exc), "screenshots": [], "screenshot_count": 0}

    # Phase 1: find Discussion / Limitations / Future Work ranges.
    ranges = _find_planning_ranges(doc)
    if not ranges:
        # Some short papers (preprints, conference brief) have no Discussion
        # heading. Fallback: use the LAST 30 % of the document as the
        # "back end" we'll draw from. If even that is empty, mark not_found.
        n_pages = len(doc)
        if n_pages == 0:
            return None
        ranges = [(int(n_pages * 0.7), 0.0, n_pages - 1, 1e9)]

    # Phase 2: build the full sentence corpus, then keep only those that fall
    # inside the planning ranges.
    sentences, total_len = gs._build_sentence_corpus(doc)
    in_range = [s for s in sentences if _sentence_in_planning_ranges(s, ranges)]
    if not in_range:
        return {"paper_id": paper_id, "screenshot_status": "not_found", "screenshots": [], "screenshot_count": 0}

    # Phase 3: build query pool + score sentences. The next-step's title +
    # summary + why_now + search_terms together give a rich signal.
    queries: list[str] = []
    for k in ("title", "subtitle", "summary", "why_now", "search_terms"):
        v = next_step.get(k)
        if isinstance(v, str) and v:
            queries.append(v)
    claim_text = (next_step.get("title") or "") + ". " + (next_step.get("summary") or "")
    paper_title = paper_meta.get("title") or ""

    page_heights = gs._build_page_heights(doc)
    top_scored = gs._select_top_sentences(
        in_range,
        claim_text=claim_text,
        quote_text=None,
        extra_queries=queries,
        paper_title=paper_title,
        page_heights=page_heights,
    )
    if not top_scored:
        return {"paper_id": paper_id, "screenshot_status": "not_found", "screenshots": [], "screenshot_count": 0}

    # Phase 4: group, render, write PNGs.
    groups = gs._group_sentences(top_scored)
    eid_safe = f"planning-{next_step['id']}-{paper_id}"
    if force:
        gs._delete_old_screenshots(eid_safe)
    rendered: list[dict] = []
    for idx, group in enumerate(groups, start=1):
        page_idx = group[0][0].page
        overall = gs._group_bbox(group)
        line_bboxes: list[tuple[float, float, float, float]] = []
        for s, _sc in group:
            line_bboxes.extend(s.line_bboxes)
        try:
            png_bytes = gs._render_group_png(doc, page_idx, line_bboxes, overall)
        except Exception as exc:
            print(f"  [render-fail] {eid_safe} #{idx}: {exc}", file=sys.stderr)
            continue
        png_name = f"{eid_safe}__{idx:02d}.png"
        png_path = ASSETS_DIR / png_name
        png_path.write_bytes(png_bytes)
        first_sent = group[0][0]
        rendered.append({
            "href": f"multimodal_genomics/assets/evidence/{png_name}",
            "page": page_idx + 1,
            "section_hint": "Discussion / Future Work",
            "highlight_granularity": "sentence" if len(group) == 1 else "sentence-group",
            "sentences": [
                {"text": s.text, "match_score": round(sc, 1)}
                for (s, sc) in sorted(group, key=lambda t: t[0].union_bbox()[1])
            ],
        })
    doc.close()
    if not rendered:
        return {"paper_id": paper_id, "screenshot_status": "not_found", "screenshots": [], "screenshot_count": 0}
    return {
        "paper_id": paper_id,
        "screenshot_status": "ok",
        "screenshots": rendered,
        "screenshot_count": len(rendered),
        "screenshot_strategy": "planning-future-work",
    }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true",
                        help="re-render every (next-step, paper) pair even if PNGs already exist")
    parser.add_argument("--next-step", type=str, default=None,
                        help="render only this next-step id (for debugging)")
    parser.add_argument("--max-papers-per-step", type=int, default=8,
                        help="cap papers processed per next-step (default: 8)")
    args = parser.parse_args(argv)

    ontology = json.loads(PLANNING_ONTOLOGY.read_text(encoding="utf-8"))
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8")).get("papers", {})

    # Pick the candidate paper pool: papers with a local PDF (filename-prefix
    # match against pdfs/MMG-*.pdf). The expansion-discovery agent might have
    # added more without PDFs — those will fall through to no_pdf and remain
    # in the registry but won't get screenshots.
    pdf_papers = []
    for pid in sorted(papers.keys()):
        if gs._find_pdf(pid):
            pdf_papers.append(pid)
    print(f"papers with local PDFs: {len(pdf_papers)} / {len(papers)}")

    out_paradigms = []
    log = {}
    n_per_step = []

    next_steps = ontology.get("next_steps", [])
    if args.next_step:
        next_steps = [ns for ns in next_steps if ns["id"] == args.next_step]

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    for ns in next_steps:
        ns_id = ns["id"]
        print(f"\n=== {ns_id} — {ns.get('title','')} ===")
        ns_evidence = []
        ok = nope = nopdf = 0
        for paper_id in pdf_papers:
            paper_meta = papers.get(paper_id) or {}
            res = _process_paper_for_next_step(
                paper_id, paper_meta, ns, force=args.force,
            )
            if not res:
                continue
            status = res.get("screenshot_status")
            if status == "ok":
                ok += 1
                eid = f"planning-{ns_id}-{paper_id}"
                # Score the paper for ranking — use the highest match_score
                # across all sentences.
                top = 0.0
                for s in res.get("screenshots", []):
                    for sn in s.get("sentences", []):
                        if sn.get("match_score", 0) > top:
                            top = sn["match_score"]
                ns_evidence.append({
                    "id": eid,
                    "paper_id": paper_id,
                    "next_step_id": ns_id,
                    "best_score": top,
                    "screenshots": res["screenshots"],
                    "screenshot_count": res["screenshot_count"],
                    "screenshot_status": "ok",
                    "screenshot_strategy": res.get("screenshot_strategy"),
                    "confidence": "parsed_pdf",
                })
            elif status == "no_pdf":
                nopdf += 1
            else:
                nope += 1
        # Sort by best_score desc, cap at max-per-step.
        ns_evidence.sort(key=lambda e: -e["best_score"])
        ns_evidence = ns_evidence[: args.max_papers_per_step]
        for rank, ev in enumerate(ns_evidence, start=1):
            ev["rank_within_next_step"] = rank
        out_paradigms.append({
            "id": ns_id,
            "title": ns["title"],
            "subtitle": ns.get("subtitle", ""),
            "summary": ns.get("summary", ""),
            "why_now": ns.get("why_now", ""),
            "evidence_points": ns_evidence,
        })
        log[ns_id] = {
            "ok": ok, "not_found": nope, "no_pdf": nopdf,
            "kept": len(ns_evidence),
        }
        n_per_step.append(len(ns_evidence))
        print(f"  ok={ok} not_found={nope} no_pdf={nopdf} kept={len(ns_evidence)}")

    payload = {
        "schema_version": "0.1",
        "topic": "multimodal_genomics_planning",
        "header": ontology.get("header"),
        "description": ontology.get("description"),
        "rendered_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "next_steps": out_paradigms,
    }
    EVIDENCE_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    LOG_FILE.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {EVIDENCE_OUT}  evidence={sum(n_per_step)} per-step={n_per_step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
