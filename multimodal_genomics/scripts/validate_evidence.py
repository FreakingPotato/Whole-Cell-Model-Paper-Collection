#!/usr/bin/env python3
"""Validate multimodal_genomics/metadata/evidence.json against the seeded schema.

Checks:
1. JSON is parseable.
2. Top level has schema_version, title, paradigms (list).
3. Every paradigm has id, label, summary, claims (list).
4. Every claim has id, subtype, claim, evidence_points (list).
5. Every evidence point has id, paper_id (or external_reference=true), text.
6. Every evidence id is globally unique.
7. Every paper_id resolves in multimodal_genomics/metadata/papers.json
   (unless external_reference is set on the point).
8. confidence values are in {manual_verified, parsed_pdf, metadata_only,
   needs_review}.
9. Evidence points may carry either:
     - new multi-screenshot schema: ``screenshots`` list +
       ``screenshot_count`` + ``screenshot_strategy``,
     - or legacy single-screenshot fields: ``screenshot_href`` / ``page`` /
       ``quote`` (still accepted for backward-compat).
   Items with neither still raise the "missing anchor" warning unless they
   are external references or otherwise opted out.

Run: python scripts/validate_evidence.py
Returns nonzero on any error; warnings are printed but do not fail the script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_FILE = ROOT / "multimodal_genomics" / "metadata" / "evidence.json"
PAPER_META_FILE = ROOT / "multimodal_genomics" / "metadata" / "papers.json"

VALID_CONFIDENCE = {"manual_verified", "parsed_pdf", "metadata_only", "needs_review"}
VALID_SCREENSHOT_STATUS = {"ok", "not_found", "manual_review", "no_pdf"}
VALID_HIGHLIGHT_GRANULARITY = {"sentence", "sentence-group", "block"}
VALID_SCREENSHOT_STRATEGY = {"multi-sentence-multi-page", "single-sentence", "block-fallback"}
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


def _validate_screenshots_array(
    screenshots: object,
    eprefix: str,
    errors: list[str],
    warnings: list[str],
) -> int:
    """Validate the ``screenshots`` array. Returns the count of valid entries
    (or -1 if not a list)."""
    if not isinstance(screenshots, list):
        errors.append(f"{eprefix}: screenshots must be a list")
        return -1
    for i, s in enumerate(screenshots):
        sprefix = f"{eprefix}.screenshots[{i}]"
        if not isinstance(s, dict):
            errors.append(f"{sprefix}: must be an object")
            continue
        href = s.get("href")
        if not isinstance(href, str) or not href:
            errors.append(f"{sprefix}: href must be a non-empty string")
        else:
            if not href.endswith(".png"):
                errors.append(f"{sprefix}: href {href!r} must end in .png")
            else:
                candidate = (EVIDENCE_FILE.parent / href).resolve()
                if not candidate.is_file():
                    alt = (ROOT / href).resolve()
                    if not alt.is_file():
                        warnings.append(
                            f"{sprefix}: href {href!r} not found on disk"
                        )
        page_val = s.get("page")
        if not isinstance(page_val, int) or isinstance(page_val, bool) or page_val <= 0:
            errors.append(f"{sprefix}: page {page_val!r} must be a positive integer")
        gran = s.get("highlight_granularity")
        if gran not in VALID_HIGHLIGHT_GRANULARITY:
            errors.append(
                f"{sprefix}: highlight_granularity {gran!r} not in {sorted(VALID_HIGHLIGHT_GRANULARITY)}"
            )
        sentences = s.get("sentences")
        if sentences is not None:
            if not isinstance(sentences, list):
                errors.append(f"{sprefix}: sentences must be a list")
            else:
                for j, snt in enumerate(sentences):
                    sjprefix = f"{sprefix}.sentences[{j}]"
                    if not isinstance(snt, dict):
                        errors.append(f"{sjprefix}: must be an object")
                        continue
                    if not isinstance(snt.get("text", ""), str):
                        errors.append(f"{sjprefix}: text must be a string")
                    score = snt.get("match_score")
                    if score is not None and not isinstance(score, (int, float)):
                        errors.append(f"{sjprefix}: match_score must be numeric")
    return len(screenshots)


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
                if pdf_href and not (
                    pdf_href.startswith("../")
                    or pdf_href.startswith("./")
                    or pdf_href.startswith("pdfs/")
                    or pdf_href.startswith("http")
                ):
                    errors.append(f"{eprefix}: pdf_href {pdf_href!r} looks malformed")

                # Legacy single-screenshot fields (still accepted) ---------
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
                if (
                    screenshot_status is not None
                    and screenshot_status not in VALID_SCREENSHOT_STATUS
                ):
                    errors.append(
                        f"{eprefix}: screenshot_status {screenshot_status!r} not in {sorted(VALID_SCREENSHOT_STATUS)}"
                    )
                page_val = point.get("page")
                if page_val is not None:
                    if (
                        not isinstance(page_val, int)
                        or isinstance(page_val, bool)
                        or page_val <= 0
                    ):
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

                # New multi-screenshot fields -------------------------------
                screenshots_field = point.get("screenshots")
                screenshot_count = point.get("screenshot_count")
                screenshot_strategy = point.get("screenshot_strategy")

                screenshots_present = screenshots_field is not None
                screenshots_len = -1
                if screenshots_present:
                    screenshots_len = _validate_screenshots_array(
                        screenshots_field, eprefix, errors, warnings
                    )

                if screenshot_count is not None:
                    if not isinstance(screenshot_count, int) or isinstance(
                        screenshot_count, bool
                    ):
                        errors.append(
                            f"{eprefix}: screenshot_count must be an integer"
                        )
                    elif screenshots_present and screenshots_len >= 0 and screenshot_count != screenshots_len:
                        errors.append(
                            f"{eprefix}: screenshot_count={screenshot_count} != len(screenshots)={screenshots_len}"
                        )

                if screenshot_strategy is not None:
                    if screenshot_strategy not in VALID_SCREENSHOT_STRATEGY:
                        errors.append(
                            f"{eprefix}: screenshot_strategy {screenshot_strategy!r} not in {sorted(VALID_SCREENSHOT_STRATEGY)}"
                        )

                # Anchor-warning suppression --------------------------------
                # Only warn about missing anchors when neither legacy nor new
                # screenshot info is present and the status isn't ok.
                has_new_anchor = bool(screenshots_field)
                has_legacy_anchor = bool(point.get("quote") or point.get("page"))
                if (
                    not has_new_anchor
                    and not has_legacy_anchor
                    and screenshot_status != "ok"
                ):
                    if conf in (None, "metadata_only"):
                        warnings.append(
                            f"{eprefix}: no quote / page anchor — current confidence={conf or 'metadata_only'}; consider deepening to parsed_pdf"
                        )

    if errors:
        print(f"FAILED: {len(errors)} error(s) in {EVIDENCE_FILE.name}", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print(
            f"OK: {EVIDENCE_FILE.name} — "
            f"{len(paradigms)} paradigms, {len(seen_claim_ids)} claims, {n_evidence} evidence points"
        )
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
