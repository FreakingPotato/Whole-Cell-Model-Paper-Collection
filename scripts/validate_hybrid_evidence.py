#!/usr/bin/env python3
"""Validate metadata/hybrid_model_evidence.json against the seeded schema.

Checks:
1. JSON is parseable.
2. Top level has schema_version, title, paradigms (list).
3. Every paradigm has id, label, summary, claims (list).
4. Every claim has id, subtype, claim, evidence_points (list).
5. Every evidence point has id, paper_id (or external_reference=true), text.
6. Every evidence id is globally unique.
7. Every paper_id resolves in metadata/wcm_paper_metadata.json
   (unless external_reference is set on the point).
8. confidence values are in {manual_verified, parsed_pdf, metadata_only,
   needs_review}.
9. Items without page or quote are flagged (warning, not error) so curators
   know where to deepen evidence.

Run: python scripts/validate_hybrid_evidence.py
Returns nonzero on any error; warnings are printed but do not fail the script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FILE = ROOT / "metadata" / "hybrid_model_evidence.json"
PAPER_META_FILE = ROOT / "metadata" / "wcm_paper_metadata.json"

VALID_CONFIDENCE = {"manual_verified", "parsed_pdf", "metadata_only", "needs_review"}
VALID_SCREENSHOT_STATUS = {"ok", "not_found", "manual_review", "no_pdf"}
QUOTE_MAX_LEN = 1200


def _load_papers() -> set[str]:
    if not PAPER_META_FILE.is_file():
        return set()
    payload = json.loads(PAPER_META_FILE.read_text(encoding="utf-8"))
    papers = payload.get("papers")
    if isinstance(papers, dict):
        return set(papers.keys())
    if isinstance(papers, list):
        return {p.get("paper_id") for p in papers if p.get("paper_id")}
    return set()


def main() -> int:
    if not EVIDENCE_FILE.is_file():
        print(f"ERROR: {EVIDENCE_FILE} not found", file=sys.stderr)
        return 2

    try:
        data = json.loads(EVIDENCE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in {EVIDENCE_FILE}: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    warnings: list[str] = []

    for required in ("schema_version", "title", "paradigms"):
        if required not in data:
            errors.append(f"missing top-level field: {required!r}")

    paradigms = data.get("paradigms")
    if not isinstance(paradigms, list):
        errors.append("paradigms must be a list")
        paradigms = []

    known_papers = _load_papers()
    if not known_papers:
        warnings.append(
            f"could not load paper metadata from {PAPER_META_FILE}; paper_id checks skipped"
        )

    seen_evidence_ids: set[str] = set()
    seen_claim_ids: set[str] = set()
    seen_paradigm_ids: set[str] = set()
    n_evidence = 0

    for p_idx, paradigm in enumerate(paradigms):
        prefix = f"paradigms[{p_idx}]"
        for required in ("id", "label", "summary", "claims"):
            if required not in paradigm:
                errors.append(f"{prefix}: missing field {required!r}")
        pid = paradigm.get("id")
        if pid:
            if pid in seen_paradigm_ids:
                errors.append(f"{prefix}: duplicate paradigm id {pid!r}")
            seen_paradigm_ids.add(pid)
        for c_idx, claim in enumerate(paradigm.get("claims") or []):
            cprefix = f"{prefix}.claims[{c_idx}]"
            for required in ("id", "subtype", "claim", "evidence_points"):
                if required not in claim:
                    errors.append(f"{cprefix}: missing field {required!r}")
            cid = claim.get("id")
            if cid:
                if cid in seen_claim_ids:
                    errors.append(f"{cprefix}: duplicate claim id {cid!r}")
                seen_claim_ids.add(cid)
            for e_idx, point in enumerate(claim.get("evidence_points") or []):
                eprefix = f"{cprefix}.evidence_points[{e_idx}]"
                n_evidence += 1
                for required in ("id", "text"):
                    if required not in point:
                        errors.append(f"{eprefix}: missing field {required!r}")
                eid = point.get("id")
                if eid:
                    if eid in seen_evidence_ids:
                        errors.append(f"{eprefix}: duplicate evidence id {eid!r}")
                    seen_evidence_ids.add(eid)
                paper_id = point.get("paper_id")
                external = bool(point.get("external_reference"))
                if not paper_id and not external:
                    errors.append(
                        f"{eprefix}: must set paper_id or external_reference=true"
                    )
                if paper_id and known_papers and paper_id not in known_papers and not external:
                    errors.append(
                        f"{eprefix}: paper_id {paper_id!r} not found in {PAPER_META_FILE.name}"
                    )
                conf = point.get("confidence")
                if conf is not None and conf not in VALID_CONFIDENCE:
                    errors.append(
                        f"{eprefix}: confidence {conf!r} not in {sorted(VALID_CONFIDENCE)}"
                    )
                pdf_href = point.get("pdf_href")
                if pdf_href and not (pdf_href.startswith("../") or pdf_href.startswith("./") or pdf_href.startswith("pdfs/") or pdf_href.startswith("http")):
                    errors.append(f"{eprefix}: pdf_href {pdf_href!r} looks malformed")
                screenshot_href = point.get("screenshot_href")
                if screenshot_href is not None:
                    if not isinstance(screenshot_href, str):
                        errors.append(f"{eprefix}: screenshot_href must be a string")
                    else:
                        if not screenshot_href.endswith(".png"):
                            errors.append(
                                f"{eprefix}: screenshot_href {screenshot_href!r} must end in .png"
                            )
                        else:
                            candidate = (EVIDENCE_FILE.parent / screenshot_href).resolve()
                            if not candidate.is_file():
                                alt = (ROOT / screenshot_href).resolve()
                                if not alt.is_file():
                                    warnings.append(
                                        f"{eprefix}: screenshot_href {screenshot_href!r} not found on disk"
                                    )
                screenshot_status = point.get("screenshot_status")
                if screenshot_status is not None and screenshot_status not in VALID_SCREENSHOT_STATUS:
                    errors.append(
                        f"{eprefix}: screenshot_status {screenshot_status!r} not in {sorted(VALID_SCREENSHOT_STATUS)}"
                    )
                page_val = point.get("page")
                if page_val is not None:
                    if not isinstance(page_val, int) or isinstance(page_val, bool) or page_val <= 0:
                        errors.append(
                            f"{eprefix}: page {page_val!r} must be a positive integer"
                        )
                quote_val = point.get("quote")
                if quote_val is not None:
                    if not isinstance(quote_val, str):
                        errors.append(f"{eprefix}: quote must be a string")
                    elif len(quote_val) > QUOTE_MAX_LEN:
                        warnings.append(
                            f"{eprefix}: quote length {len(quote_val)} exceeds {QUOTE_MAX_LEN} chars"
                        )
                if not point.get("quote") and not point.get("page") and screenshot_status != "ok":
                    if conf in (None, "metadata_only"):
                        warnings.append(
                            f"{eprefix}: no quote / page anchor — current confidence={conf or 'metadata_only'}; consider deepening to parsed_pdf"
                        )

    if errors:
        print(f"FAILED: {len(errors)} error(s) in {EVIDENCE_FILE.name}", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print(f"OK: {EVIDENCE_FILE.name} — "
              f"{len(paradigms)} paradigms, {len(seen_claim_ids)} claims, {n_evidence} evidence points")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
