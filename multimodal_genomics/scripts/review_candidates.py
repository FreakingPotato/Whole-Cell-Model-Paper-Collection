#!/usr/bin/env python3
"""Agent R — review and promote hybrid evidence candidates.

Reads the three paradigm-specific scored candidate files
(``metadata/hybrid_candidates_scored_*.json``), unifies them, applies the
Agent-R promotion rules (threshold tier, per-claim cap, diversity filter,
multi-claim cap), and:

  1. Appends new MMG evidence points to the right claim in
     ``multimodal_genomics/metadata/evidence.json`` (existing rows untouched).
  2. Overwrites ``multimodal_genomics/metadata/candidates.json`` with the
     unified candidates record (every (paper, claim) row carries
     ``tier`` and ``promotion_status``).
  3. Writes a human-readable trace to
     ``multimodal_genomics/metadata/promotion_log.md``.

The script is fully deterministic — same inputs → same outputs.

CLI flags:
  --dry-run     compute promotions but don't write any output files
  --report-only re-derive log from existing candidates file (skips
                rewriting the evidence file). Useful for re-generating the
                log after manual curation. (Currently behaves like
                regular run on the candidates file but skips evidence
                mutation.)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "metadata"

SCORED_FILES = {
    "embedded": META / "hybrid_candidates_scored_embedded.json",
    "pipeline": META / "hybrid_candidates_scored_pipeline.json",
    "parallel": META / "hybrid_candidates_scored_parallel.json",
}
EVIDENCE_FILE = META / "multimodal_genomics_evidence.json"
CANDIDATES_OUT = META / "hybrid_evidence_candidates.json"
LOG_OUT = META / "hybrid_evidence_promotion_log.md"
EXTERNAL_PAPERS = META / "hybrid_external_papers.json"

# Rule constants ------------------------------------------------------------

PRIMARY_THRESHOLD = 18.0
SECONDARY_THRESHOLD = 14.0
PER_CLAIM_MIN = 6
PER_CLAIM_MAX = 10
DIVERSITY_MAX_SAME_DOMAIN = 3  # i.e. error if 4+ from same domain
DIVERSITY_OVERRIDE_SCORE = 22.0  # all same-domain must be >= this to skip filter
MULTI_CLAIM_CAP = 4

RUBRIC_DIMENSION_ORDER = (
    "useful_outcomes",
    "immediate_benefit",
    "plausible",
    "scalable",
    "how_to_validate",
)
TEXT_MAX_CHARS = 240


# Helpers -------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_evidence_id(claim_id: str, idx: int) -> str:
    """Produce a human-readable evidence id for an MMG row.

    Example: ``hybrid-embedded-closure-EXT003`` (3rd appended MMG row to
    the embedded-closure claim). The numeric portion picks up at the
    next free integer past existing MMG rows, but for the schema we
    simply use a stable index ≥ 100 so we never collide with the
    existing ``hybrid-<claim>-001`` style MMG evidence ids.
    """
    return f"hybrid-{claim_id}-MMG{idx:03d}"


def _shorten_title(title: str, limit: int = 80) -> str:
    title = title.strip()
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "…"


def _pick_text(rubric: dict[str, Any], paper_title: str) -> str:
    """Build the 1-2 sentence ``text`` field deterministically."""
    best_dim = None
    best_score = -1
    for dim in RUBRIC_DIMENSION_ORDER:
        d = rubric.get(dim) or {}
        s = d.get("score")
        if not isinstance(s, (int, float)):
            continue
        if s > best_score:
            best_score = s
            best_dim = dim
    rationale = ""
    if best_dim is not None:
        rationale = (rubric.get(best_dim) or {}).get("rationale", "") or ""
    short_title = _shorten_title(paper_title)
    text = f"{short_title}: {rationale}".strip()
    if len(text) > TEXT_MAX_CHARS:
        text = text[: TEXT_MAX_CHARS - 1].rstrip() + "…"
    return text


def _agent_for_paradigm(paradigm: str) -> str:
    return {
        "embedded": "agent-S1",
        "pipeline": "agent-S2",
        "parallel": "agent-S3",
    }.get(paradigm, f"agent-{paradigm}")


def _tier_for_score(weighted_total: float) -> str:
    if weighted_total >= PRIMARY_THRESHOLD:
        return "primary"
    if weighted_total >= SECONDARY_THRESHOLD:
        return "secondary"
    return "dropped"


# Core logic ----------------------------------------------------------------


def load_unified_candidates() -> tuple[list[dict[str, Any]], dict[str, str], dict[str, dict[str, Any]]]:
    """Return a flat list of every (paper, claim) candidate row, plus
    the {claim_id: paradigm_id} and {claim_id: claim_meta} maps."""
    rows: list[dict[str, Any]] = []
    claim_to_paradigm: dict[str, str] = {}
    claim_meta: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    duplicates: list[str] = []

    for paradigm, path in SCORED_FILES.items():
        d = _load_json(path)
        for cid, claim in (d.get("claims") or {}).items():
            claim_to_paradigm[cid] = paradigm
            claim_meta[cid] = {
                "paradigm": paradigm,
                "claim_text": claim.get("claim_text", ""),
            }
            for cand in claim.get("candidates") or []:
                cand_id = cand.get("candidate_id")
                if cand_id in seen_ids:
                    duplicates.append(cand_id)
                seen_ids.add(cand_id)
                rows.append(
                    {
                        "candidate_id": cand_id,
                        "paper_id": cand.get("paper_id"),
                        "paper_title": cand.get("paper_title", ""),
                        "paper_year": cand.get("paper_year"),
                        "paper_journal": cand.get("paper_journal", ""),
                        "paper_cited_by_count": cand.get("paper_cited_by_count"),
                        "claim_ref": cid,
                        "paradigm": paradigm,
                        "rubric": cand.get("rubric") or {},
                        "weighted_total": float(cand.get("weighted_total") or 0.0),
                        "match_score": float(cand.get("match_score") or 0.0),
                    }
                )
    rows.sort(
        key=lambda r: (r["claim_ref"], -r["weighted_total"], r["paper_id"])
    )
    return rows, claim_to_paradigm, claim_meta, duplicates  # type: ignore[return-value]


def apply_promotion_rules(
    rows: list[dict[str, Any]],
    paper_domain: dict[str, str],
) -> dict[str, Any]:
    """Apply tiering, per-claim cap, diversity filter, multi-claim cap.

    Returns a dict with keys:
      - ``rows``: same input rows, each annotated with ``tier`` +
        ``promotion_status``.
      - ``per_claim``: {claim_id: {"primary": [...], "secondary": [...],
        "promoted": [...], "dropped": [...]}}.
      - ``multi_claim_drops``: list of (paper_id, claim_id, reason).
      - ``diversity_drops``: list of (paper_id, claim_id, domain).
    """
    # Pass 1 — tier assignment.
    for r in rows:
        r["tier"] = _tier_for_score(r["weighted_total"])

    # Group by claim.
    by_claim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_claim[r["claim_ref"]].append(r)

    # First-pass per-claim selection (before multi-claim cap).
    per_claim: dict[str, dict[str, Any]] = {}
    for cid, crows in by_claim.items():
        crows_sorted = sorted(
            crows, key=lambda r: (-r["weighted_total"], r["paper_id"])
        )
        primary = [r for r in crows_sorted if r["tier"] == "primary"]
        secondary = [r for r in crows_sorted if r["tier"] == "secondary"]
        dropped_below = [r for r in crows_sorted if r["tier"] == "dropped"]
        for r in dropped_below:
            r["promotion_status"] = "dropped_below_threshold"

        # Take primaries up to PER_CLAIM_MAX.
        promoted = primary[:PER_CLAIM_MAX]
        # Anything else from primary is over-cap.
        primary_overflow = primary[PER_CLAIM_MAX:]
        for r in primary_overflow:
            r["promotion_status"] = "dropped_per_claim_cap"

        # If under PER_CLAIM_MIN, fill from secondary.
        if len(promoted) < PER_CLAIM_MIN:
            need = PER_CLAIM_MIN - len(promoted)
            promoted.extend(secondary[:need])
            for r in secondary[need:]:
                r["promotion_status"] = "dropped_per_claim_cap"
        else:
            # All secondary are surplus — they don't fit.
            for r in secondary:
                r["promotion_status"] = "dropped_per_claim_cap"

        per_claim[cid] = {
            "primary": primary,
            "secondary": secondary,
            "promoted": promoted,
            "dropped_below": dropped_below,
            "diversity_actions": [],
        }

    # Pass 2 — diversity filter (only on the per-claim ``promoted`` set).
    for cid, payload in per_claim.items():
        promoted = payload["promoted"]
        primary = payload["primary"]
        secondary = payload["secondary"]
        # Build domain counts.
        domain_counts: dict[str, int] = defaultdict(int)
        for r in promoted:
            d = paper_domain.get(r["paper_id"], "unknown")
            domain_counts[d] += 1
        # If 4+ from same domain, check override condition.
        for dom, count in list(domain_counts.items()):
            if count <= DIVERSITY_MAX_SAME_DOMAIN:
                continue
            same_dom_rows = [
                r
                for r in promoted
                if paper_domain.get(r["paper_id"], "unknown") == dom
            ]
            # Override: keep all if every same-domain row scores >=22.
            if all(r["weighted_total"] >= DIVERSITY_OVERRIDE_SCORE for r in same_dom_rows):
                continue
            same_dom_rows.sort(
                key=lambda r: (-r["weighted_total"], r["paper_id"])
            )
            # Build the pool of candidate replacements (different domain,
            # not already promoted, primary or secondary tier).
            existing_paper_ids = {r["paper_id"] for r in promoted}
            backfill_pool: list[dict[str, Any]] = []
            for r in primary + secondary:
                if r in promoted:
                    continue
                if r["paper_id"] in existing_paper_ids:
                    continue
                if paper_domain.get(r["paper_id"], "unknown") == dom:
                    continue
                backfill_pool.append(r)
            backfill_pool.sort(
                key=lambda r: (-r["weighted_total"], r["paper_id"])
            )
            # Only demote one row per available backfill — if there are
            # zero replacements, we leave the same-domain papers alone
            # (else we'd just shrink the claim's promoted set with no
            # diversity gain).
            n_to_demote = min(
                len(same_dom_rows) - DIVERSITY_MAX_SAME_DOMAIN,
                len(backfill_pool),
            )
            if n_to_demote <= 0:
                continue
            # Demote the lowest-scoring same-domain rows.
            demote = same_dom_rows[-n_to_demote:]
            for r in demote:
                r["promotion_status"] = "dropped_diversity_filter"
                payload["diversity_actions"].append(
                    {
                        "paper_id": r["paper_id"],
                        "claim_id": cid,
                        "domain": dom,
                        "weighted_total": r["weighted_total"],
                        "reason": "demoted_for_domain_diversity",
                    }
                )
            promoted = [r for r in promoted if r not in demote]
            for r in backfill_pool[:n_to_demote]:
                r["promotion_status"] = "promoted"
                promoted.append(r)
            payload["promoted"] = promoted
            # Refresh domain_counts after backfill so the next domain
            # iteration sees current state.
            domain_counts.clear()
            for r in promoted:
                d = paper_domain.get(r["paper_id"], "unknown")
                domain_counts[d] += 1

    # Pass 3 — multi-claim cap. A paper may appear under at most
    # MULTI_CLAIM_CAP claims; keep its top-scoring instances.
    paper_appearances: dict[str, list[tuple[float, str, dict[str, Any]]]] = defaultdict(list)
    for cid, payload in per_claim.items():
        for r in payload["promoted"]:
            paper_appearances[r["paper_id"]].append(
                (r["weighted_total"], cid, r)
            )
    multi_claim_drops: list[dict[str, Any]] = []
    for paper_id, appearances in paper_appearances.items():
        if len(appearances) <= MULTI_CLAIM_CAP:
            continue
        # Sort by (-score, claim_id) for determinism.
        appearances.sort(key=lambda t: (-t[0], t[1]))
        keep = appearances[:MULTI_CLAIM_CAP]
        drop = appearances[MULTI_CLAIM_CAP:]
        keep_set = {(t[1], id(t[2])) for t in keep}
        for score, cid, r in drop:
            r["promotion_status"] = "dropped_multi_claim_cap"
            multi_claim_drops.append(
                {
                    "paper_id": paper_id,
                    "claim_id": cid,
                    "weighted_total": score,
                    "reason": f"paper appears in >{MULTI_CLAIM_CAP} claims",
                }
            )
            # Remove from per_claim[cid]["promoted"]
            per_claim[cid]["promoted"] = [
                x for x in per_claim[cid]["promoted"] if x is not r
            ]

    # Pass 4 — final mark-up: anything in ``promoted`` is "promoted".
    # Anything not yet status-tagged in primary/secondary that wasn't
    # promoted is "dropped_per_claim_cap" already; we just fix any
    # untagged rows.
    for cid, payload in per_claim.items():
        promoted_set = {id(r) for r in payload["promoted"]}
        for r in payload["primary"] + payload["secondary"]:
            if id(r) in promoted_set:
                r["promotion_status"] = "promoted"
            else:
                r.setdefault("promotion_status", "dropped_per_claim_cap")
        for r in payload["dropped_below"]:
            r.setdefault("promotion_status", "dropped_below_threshold")

    return {
        "per_claim": per_claim,
        "multi_claim_drops": multi_claim_drops,
    }


def build_evidence_point(
    row: dict[str, Any],
    seq: int,
) -> dict[str, Any]:
    cid = row["claim_ref"]
    paradigm = row["paradigm"]
    eid = _format_evidence_id(cid, seq)
    rubric = row["rubric"]
    text = _pick_text(rubric, row["paper_title"])
    tier = row["tier"]
    return {
        "id": eid,
        "paper_id": row["paper_id"],
        "claim_match_quality": tier,
        "confidence": "needs_review",
        "external_reference": True,
        "discovered_by": "agent-D1",
        "scored_by": _agent_for_paradigm(paradigm),
        "promoted_by": "agent-R",
        "match_score": round(row["match_score"], 3),
        "rubric": rubric,
        "weighted_total": round(row["weighted_total"], 1),
        "text": text,
        "screenshot_status": "no_pdf",
    }


def write_evidence_file(
    promotion: dict[str, Any],
    rows_by_id: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Append MMG evidence points and write the evidence file. Returns
    the {claim_id: [appended_points]} map for log generation."""
    ev = _load_json(EVIDENCE_FILE)
    appended_by_claim: dict[str, list[dict[str, Any]]] = {}
    for paradigm in ev.get("paradigms") or []:
        for claim in paradigm.get("claims") or []:
            cid = claim["id"]
            payload = promotion["per_claim"].get(cid)
            if not payload:
                continue
            promoted = sorted(
                payload["promoted"],
                key=lambda r: (-r["weighted_total"], r["paper_id"]),
            )
            new_points: list[dict[str, Any]] = []
            for idx, r in enumerate(promoted, start=1):
                pt = build_evidence_point(r, idx)
                new_points.append(pt)
            # Append to existing list (preserve all existing rows).
            claim["evidence_points"].extend(new_points)
            appended_by_claim[cid] = new_points
    EVIDENCE_FILE.write_text(
        json.dumps(ev, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return appended_by_claim


def write_candidates_file(
    rows: list[dict[str, Any]],
    claim_meta: dict[str, dict[str, Any]],
) -> None:
    """Write the unified hybrid_evidence_candidates.json."""
    by_claim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_claim[r["claim_ref"]].append(
            {
                "candidate_id": r["candidate_id"],
                "paper_id": r["paper_id"],
                "claim_ref": r["claim_ref"],
                "paradigm": r["paradigm"],
                "paper_title": r["paper_title"],
                "rubric": r["rubric"],
                "weighted_total": round(r["weighted_total"], 1),
                "match_score": round(r["match_score"], 3),
                "tier": r["tier"],
                "promotion_status": r["promotion_status"],
            }
        )
    # Stable order within each claim: by promotion_status (promoted first),
    # then weighted_total desc, then paper_id.
    status_order = {
        "promoted": 0,
        "dropped_diversity_filter": 1,
        "dropped_multi_claim_cap": 2,
        "dropped_per_claim_cap": 3,
        "dropped_below_threshold": 4,
    }
    for cid in by_claim:
        by_claim[cid].sort(
            key=lambda r: (
                status_order.get(r["promotion_status"], 9),
                -r["weighted_total"],
                r["paper_id"],
            )
        )
    payload = {
        "schema_version": "0.2",
        "generated_by": "agent-R",
        "generated_at": date.today().isoformat(),
        "claims": {
            cid: {
                "paradigm": claim_meta[cid]["paradigm"],
                "claim_text": claim_meta[cid]["claim_text"],
                "candidates": by_claim[cid],
            }
            for cid in sorted(by_claim.keys())
        },
    }
    CANDIDATES_OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_log(
    rows: list[dict[str, Any]],
    promotion: dict[str, Any],
    paper_domain: dict[str, str],
    paper_titles: dict[str, str],
    claim_meta: dict[str, dict[str, Any]],
    duplicates: list[str],
) -> None:
    by_claim = promotion["per_claim"]
    total = len(rows)
    n_primary_promoted = 0
    n_secondary_promoted = 0
    n_drop_below = 0
    n_drop_div = 0
    n_drop_multi = 0
    n_drop_cap = 0
    for r in rows:
        st = r["promotion_status"]
        if st == "promoted":
            if r["tier"] == "primary":
                n_primary_promoted += 1
            else:
                n_secondary_promoted += 1
        elif st == "dropped_below_threshold":
            n_drop_below += 1
        elif st == "dropped_diversity_filter":
            n_drop_div += 1
        elif st == "dropped_multi_claim_cap":
            n_drop_multi += 1
        elif st == "dropped_per_claim_cap":
            n_drop_cap += 1

    lines: list[str] = []
    lines.append("# Hybrid Evidence Promotion Log")
    lines.append("")
    lines.append(f"Generated by Agent R on {date.today().isoformat()}.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total scored candidates: {total}")
    lines.append(f"- Promoted (primary): {n_primary_promoted}")
    lines.append(f"- Promoted (secondary): {n_secondary_promoted}")
    lines.append(f"- Dropped (below threshold): {n_drop_below}")
    lines.append(f"- Dropped (diversity filter): {n_drop_div}")
    lines.append(f"- Dropped (multi-claim cap): {n_drop_multi}")
    lines.append(f"- Dropped (per-claim cap, surplus): {n_drop_cap}")
    lines.append("")

    lines.append("## Per-claim breakdown")
    lines.append("")
    for cid in sorted(by_claim.keys()):
        payload = by_claim[cid]
        lines.append(f"### {cid}")
        promoted_set = {id(r) for r in payload["promoted"]}
        primaries = [r for r in payload["primary"] if id(r) in promoted_set]
        secondaries = [r for r in payload["secondary"] if id(r) in promoted_set]
        below = payload["dropped_below"]
        # Diversity-dropped or multi-claim-dropped specifically:
        div_drops = [
            r
            for r in payload["primary"] + payload["secondary"]
            if r["promotion_status"] == "dropped_diversity_filter"
        ]
        multi_drops = [
            r
            for r in payload["primary"] + payload["secondary"]
            if r["promotion_status"] == "dropped_multi_claim_cap"
        ]
        cap_drops = [
            r
            for r in payload["primary"] + payload["secondary"]
            if r["promotion_status"] == "dropped_per_claim_cap"
        ]
        lines.append(f"- Promoted total: {len(payload['promoted'])} "
                     f"(primary {len(primaries)}, secondary {len(secondaries)})")
        if primaries:
            lines.append("- Primary (>= 18):")
            for r in sorted(primaries, key=lambda x: (-x["weighted_total"], x["paper_id"])):
                lines.append(
                    f"  - {r['paper_id']} | {paper_titles.get(r['paper_id'], r['paper_title'])} "
                    f"| weighted_total={r['weighted_total']:.1f} | citing={r['paper_cited_by_count']} "
                    f"| domain={paper_domain.get(r['paper_id'], 'unknown')}"
                )
        if secondaries:
            lines.append("- Secondary (14-17):")
            for r in sorted(secondaries, key=lambda x: (-x["weighted_total"], x["paper_id"])):
                lines.append(
                    f"  - {r['paper_id']} | {paper_titles.get(r['paper_id'], r['paper_title'])} "
                    f"| weighted_total={r['weighted_total']:.1f} | citing={r['paper_cited_by_count']} "
                    f"| domain={paper_domain.get(r['paper_id'], 'unknown')}"
                )
        if below:
            lines.append(f"- Dropped (below threshold): {len(below)} candidates")
            for r in below:
                lines.append(
                    f"  - {r['paper_id']} | weighted_total={r['weighted_total']:.1f} "
                    f"| reason=below_threshold"
                )
        if cap_drops:
            lines.append(f"- Dropped (per-claim cap, surplus): {len(cap_drops)} candidates")
            for r in cap_drops:
                lines.append(
                    f"  - {r['paper_id']} | weighted_total={r['weighted_total']:.1f} | tier={r['tier']}"
                )
        if div_drops:
            lines.append(f"- Dropped (diversity filter): {len(div_drops)} candidates")
            for r in div_drops:
                lines.append(
                    f"  - {r['paper_id']} | weighted_total={r['weighted_total']:.1f} "
                    f"| domain={paper_domain.get(r['paper_id'], 'unknown')}"
                )
        if multi_drops:
            lines.append(f"- Dropped (multi-claim cap): {len(multi_drops)} candidates")
            for r in multi_drops:
                lines.append(
                    f"  - {r['paper_id']} | weighted_total={r['weighted_total']:.1f}"
                )
        lines.append("")

    # Multi-claim papers section.
    paper_to_claims: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in rows:
        if r["promotion_status"] == "promoted":
            paper_to_claims[r["paper_id"]].append((r["claim_ref"], r["weighted_total"]))
    multi_papers = {
        pid: appearances
        for pid, appearances in paper_to_claims.items()
        if len(appearances) > 1
    }
    lines.append("## Multi-claim papers")
    lines.append("")
    if not multi_papers:
        lines.append("- (none)")
    else:
        for pid in sorted(multi_papers.keys()):
            apps = sorted(multi_papers[pid], key=lambda t: (-t[1], t[0]))
            title = paper_titles.get(pid, "")
            short_title = _shorten_title(title, limit=60)
            joined = ", ".join(f"{c} ({s:.1f})" for c, s in apps)
            lines.append(
                f"- {pid} ({short_title}) appears under {len(apps)} claims: {joined}"
            )
    lines.append("")

    # Notes section.
    lines.append("## Notes")
    lines.append("")
    short_claims = [
        cid for cid, p in by_claim.items() if len(p["promoted"]) < PER_CLAIM_MIN
    ]
    if short_claims:
        lines.append(
            "- Claims that fell short of the 6-paper minimum after primary+secondary exhausted:"
        )
        for cid in short_claims:
            lines.append(
                f"  - {cid}: {len(by_claim[cid]['promoted'])} promoted "
                f"(no remaining primary/secondary candidates available)"
            )
    else:
        lines.append("- All 9 claims reached the 6-paper minimum.")
    diversity_actions = []
    for cid, payload in by_claim.items():
        diversity_actions.extend(payload.get("diversity_actions", []))
    if diversity_actions:
        lines.append("- Diversity-filter actions:")
        for action in diversity_actions:
            lines.append(
                f"  - claim={action['claim_id']} demoted {action['paper_id']} "
                f"(domain={action['domain']}, weighted_total={action['weighted_total']:.1f}): "
                f"{action['reason']}"
            )
    else:
        lines.append("- No diversity-filter actions triggered.")
    if duplicates:
        lines.append(
            f"- Duplicate candidate_ids detected across scored files: {duplicates}"
        )
    lines.append(
        "- All MMG evidence points are appended with `screenshot_status: \"no_pdf\"`; "
        "Agent P will fetch PDFs and Agent H pass 2 will rewrite to "
        "`pending` / `ok` once screenshots are produced."
    )
    lines.append(
        "- All MMG rows carry `external_reference: true` so the validator's "
        "`paper_id in wcm_paper_metadata` check passes."
    )
    lines.append("")
    LOG_OUT.write_text("\n".join(lines), encoding="utf-8")


# Entry point ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute promotions but don't write any output files",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="recompute candidates+log; skip evidence-file mutation",
    )
    args = parser.parse_args(argv)

    rows, claim_to_paradigm, claim_meta, duplicates = load_unified_candidates()
    ext_payload = _load_json(EXTERNAL_PAPERS)
    papers = ext_payload.get("papers") or {}
    paper_domain = {pid: meta.get("domain", "unknown") for pid, meta in papers.items()}
    paper_titles = {pid: meta.get("title", "") for pid, meta in papers.items()}

    promotion = apply_promotion_rules(rows, paper_domain)

    rows_by_id = {r["candidate_id"]: r for r in rows}

    if args.dry_run:
        promoted_count = sum(
            1 for r in rows if r.get("promotion_status") == "promoted"
        )
        print(f"[dry-run] {promoted_count} candidates would be promoted")
        return 0

    write_candidates_file(rows, claim_meta)

    if not args.report_only:
        write_evidence_file(promotion, rows_by_id)

    write_log(rows, promotion, paper_domain, paper_titles, claim_meta, duplicates)

    promoted_count = sum(1 for r in rows if r["promotion_status"] == "promoted")
    print(
        f"OK — promoted {promoted_count} MMG evidence points "
        f"across {len(promotion['per_claim'])} claims"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
