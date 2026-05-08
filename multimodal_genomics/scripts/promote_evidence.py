#!/usr/bin/env python3
"""Agent R - promote multimodal_genomics evidence candidates.

Reads:
  - multimodal_genomics/metadata/ontology.json
  - multimodal_genomics/metadata/scored_gfm.json
  - multimodal_genomics/metadata/scored_multimodal.json
  - multimodal_genomics/metadata/scored_applications.json
  - multimodal_genomics/metadata/papers.json
  - multimodal_genomics/metadata/candidates.json

Writes:
  - multimodal_genomics/metadata/evidence.json (curated, mirrors ontology)
  - multimodal_genomics/metadata/candidates.json (unified, scored, tiered)
  - multimodal_genomics/metadata/promotion_log.md (human-readable trace)

Promotion rules per the Agent R spec.

CLI flags:
  --dry-run     compute promotions but don't write any output files.
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

ONTOLOGY_FILE = META / "ontology.json"
PAPERS_FILE = META / "papers.json"
CANDIDATES_FILE = META / "candidates.json"
EVIDENCE_FILE = META / "evidence.json"
LOG_FILE = META / "promotion_log.md"

SCORED_FILES = {
    "gfm": META / "scored_gfm.json",
    "multimodal": META / "scored_multimodal.json",
    "applications": META / "scored_applications.json",
}

PARADIGM_AGENT = {
    "gfm": "agent-S1",
    "multimodal": "agent-S2",
    "applications": "agent-S3",
}

PRIMARY_THRESHOLD = 18.0
SECONDARY_THRESHOLD = 14.0
PER_CLAIM_MIN = 6
PER_CLAIM_MAX = 10
DIVERSITY_MAX_SAME_DOMAIN = 3
DIVERSITY_OVERRIDE_SCORE = 22.0
MULTI_CLAIM_CAP = 4

RUBRIC_DIMENSION_ORDER = (
    "useful_outcomes",
    "immediate_benefit",
    "plausible",
    "scalable",
    "how_to_validate",
)
TEXT_MAX_CHARS = 240


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _shorten_title(title: str, limit: int = 50) -> str:
    title = (title or "").strip()
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "…"


def _pick_text(rubric: dict[str, Any], paper_title: str) -> str:
    """Build the deterministic text field."""
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
    short_title = _shorten_title(paper_title, limit=50)
    text = f"{short_title}: {rationale}".strip()
    if len(text) > TEXT_MAX_CHARS:
        text = text[: TEXT_MAX_CHARS - 1].rstrip() + "…"
    return text


def _tier_for_score(weighted_total: float) -> str:
    if weighted_total >= PRIMARY_THRESHOLD:
        return "primary"
    if weighted_total >= SECONDARY_THRESHOLD:
        return "secondary"
    return "dropped"


def load_unified_rows() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    """Return list of (paper, claim) rows, claim metadata map, and duplicates."""
    rows: list[dict[str, Any]] = []
    claim_meta: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    duplicates: list[str] = []

    for paradigm, path in SCORED_FILES.items():
        d = _load_json(path)
        for cid, claim in (d.get("claims") or {}).items():
            claim_meta[cid] = {
                "paradigm": paradigm,
                "claim_text": claim.get("claim_text", ""),
            }
            for cand in claim.get("candidates") or []:
                # Some scored files (applications) lack candidate_id; synthesize.
                cand_id = cand.get("candidate_id") or (
                    f"candidate-{cid}-{cand.get('paper_id', 'UNKNOWN')}"
                )
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
    rows.sort(key=lambda r: (r["claim_ref"], -r["weighted_total"], r["paper_id"]))
    return rows, claim_meta, duplicates


def apply_promotion_rules(
    rows: list[dict[str, Any]],
    paper_domain: dict[str, str],
) -> dict[str, Any]:
    """Apply tiering + per-claim cap + diversity filter + multi-claim cap."""
    # Pass 1 - tier assignment.
    for r in rows:
        r["tier"] = _tier_for_score(r["weighted_total"])
        r["promotion_status"] = None  # placeholder; filled below

    # Group by claim.
    by_claim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_claim[r["claim_ref"]].append(r)

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

        promoted = primary[:PER_CLAIM_MAX]
        primary_overflow = primary[PER_CLAIM_MAX:]
        for r in primary_overflow:
            r["promotion_status"] = "dropped_per_claim_cap"

        if len(promoted) < PER_CLAIM_MIN:
            need = PER_CLAIM_MIN - len(promoted)
            fill = secondary[:need]
            promoted.extend(fill)
            for r in secondary[need:]:
                r["promotion_status"] = "dropped_per_claim_cap"
        else:
            for r in secondary:
                r["promotion_status"] = "dropped_per_claim_cap"

        per_claim[cid] = {
            "primary": primary,
            "secondary": secondary,
            "promoted": promoted,
            "dropped_below": dropped_below,
            "diversity_actions": [],
        }

    # Pass 2 - diversity filter.
    for cid, payload in per_claim.items():
        promoted = payload["promoted"]
        primary = payload["primary"]
        secondary = payload["secondary"]
        domain_counts: dict[str, int] = defaultdict(int)
        for r in promoted:
            d = paper_domain.get(r["paper_id"], "unknown")
            domain_counts[d] += 1
        for dom in sorted(domain_counts.keys()):
            count = domain_counts[dom]
            if count <= DIVERSITY_MAX_SAME_DOMAIN:
                continue
            same_dom_rows = [
                r
                for r in promoted
                if paper_domain.get(r["paper_id"], "unknown") == dom
            ]
            if all(r["weighted_total"] >= DIVERSITY_OVERRIDE_SCORE for r in same_dom_rows):
                continue
            same_dom_rows.sort(
                key=lambda r: (-r["weighted_total"], r["paper_id"])
            )
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
            n_to_demote = min(
                len(same_dom_rows) - DIVERSITY_MAX_SAME_DOMAIN,
                len(backfill_pool),
            )
            if n_to_demote <= 0:
                continue
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
            domain_counts.clear()
            for r in promoted:
                d = paper_domain.get(r["paper_id"], "unknown")
                domain_counts[d] += 1

    # Pass 3 - multi-claim cap.
    paper_appearances: dict[str, list[tuple[float, str, dict[str, Any]]]] = defaultdict(list)
    for cid, payload in per_claim.items():
        for r in payload["promoted"]:
            paper_appearances[r["paper_id"]].append(
                (r["weighted_total"], cid, r)
            )
    for paper_id in sorted(paper_appearances.keys()):
        appearances = paper_appearances[paper_id]
        if len(appearances) <= MULTI_CLAIM_CAP:
            continue
        appearances.sort(key=lambda t: (-t[0], t[1]))
        drop = appearances[MULTI_CLAIM_CAP:]
        for score, cid, r in drop:
            r["promotion_status"] = "dropped_multi_claim_cap"
            per_claim[cid]["promoted"] = [
                x for x in per_claim[cid]["promoted"] if x is not r
            ]

    # Pass 4 - finalize promotion_status on remaining promoted rows.
    for cid, payload in per_claim.items():
        promoted_set = {id(r) for r in payload["promoted"]}
        for r in payload["primary"] + payload["secondary"]:
            if id(r) in promoted_set:
                r["promotion_status"] = "promoted"
            elif r.get("promotion_status") in (None,):
                r["promotion_status"] = "dropped_per_claim_cap"
        for r in payload["dropped_below"]:
            if r.get("promotion_status") is None:
                r["promotion_status"] = "dropped_below_threshold"

    return per_claim


def build_evidence_point(row: dict[str, Any], seq: int) -> dict[str, Any]:
    cid = row["claim_ref"]
    paradigm = row["paradigm"]
    eid = f"evidence-{cid}-MMG{seq:03d}"
    rubric = row["rubric"]
    text = _pick_text(rubric, row["paper_title"])
    tier = row["tier"]
    return {
        "id": eid,
        "paper_id": row["paper_id"],
        "claim_match_quality": tier,
        "confidence": "candidate",
        "external_reference": True,
        "discovered_by": "agent-D",
        "scored_by": PARADIGM_AGENT.get(paradigm, f"agent-{paradigm}"),
        "promoted_by": "agent-R",
        "match_score": round(row["match_score"], 3),
        "rubric": rubric,
        "weighted_total": round(row["weighted_total"], 1),
        "text": text,
        "screenshot_status": "pending",
    }


def build_evidence_doc(
    ontology: dict[str, Any],
    per_claim: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Mirror the ontology paradigm/claim arrangement, with promoted points."""
    paradigms_out: list[dict[str, Any]] = []
    for paradigm in ontology.get("paradigms") or []:
        claims_out: list[dict[str, Any]] = []
        for claim in paradigm.get("claims") or []:
            cid = claim["id"]
            payload = per_claim.get(cid, {})
            promoted = sorted(
                payload.get("promoted") or [],
                key=lambda r: (
                    0 if r["tier"] == "primary" else 1,
                    -r["weighted_total"],
                    r["paper_id"],
                ),
            )
            evidence_points = [
                build_evidence_point(r, idx + 1)
                for idx, r in enumerate(promoted)
            ]
            claims_out.append(
                {
                    "id": cid,
                    "subtype": claim.get("subtype", ""),
                    "claim": claim.get("claim", ""),
                    "evidence_points": evidence_points,
                }
            )
        paradigms_out.append(
            {
                "id": paradigm.get("id"),
                "label": paradigm.get("label", ""),
                "summary": paradigm.get("summary", ""),
                "claims": claims_out,
            }
        )
    return {
        "schema_version": "0.1",
        "topic_id": ontology.get("topic_id", "multimodal_genomics"),
        "title": ontology.get("viewer", {}).get(
            "title", ontology.get("topic_label", "")
        ),
        "thesis": ontology.get("thesis", ""),
        "paradigms": paradigms_out,
    }


def build_candidates_doc(
    rows: list[dict[str, Any]],
    claim_meta: dict[str, dict[str, Any]],
) -> dict[str, Any]:
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
    return {
        "schema_version": "0.2",
        "topic": "multimodal_genomics",
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


def build_log(
    rows: list[dict[str, Any]],
    per_claim: dict[str, dict[str, Any]],
    paper_domain: dict[str, str],
    paper_titles: dict[str, str],
    duplicates: list[str],
) -> str:
    n_total = len(rows)
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
    lines.append("# Multimodal Genomics Evidence Promotion Log")
    lines.append("")
    lines.append(f"Generated by Agent R on {date.today().isoformat()}.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total scored candidates: {n_total}")
    lines.append(f"- Promoted (primary): {n_primary_promoted}")
    lines.append(f"- Promoted (secondary): {n_secondary_promoted}")
    lines.append(f"- Dropped (below threshold): {n_drop_below}")
    lines.append(f"- Dropped (diversity filter): {n_drop_div}")
    lines.append(f"- Dropped (multi-claim cap): {n_drop_multi}")
    lines.append(f"- Dropped (per-claim cap): {n_drop_cap}")
    lines.append("")

    lines.append("## Per-claim breakdown")
    lines.append("")
    for cid in sorted(per_claim.keys()):
        payload = per_claim[cid]
        lines.append(f"### {cid}")
        promoted_set = {id(r) for r in payload["promoted"]}
        primaries = [
            r for r in payload["primary"] if id(r) in promoted_set
        ]
        secondaries = [
            r for r in payload["secondary"] if id(r) in promoted_set
        ]
        below = payload["dropped_below"]
        div_drops = [
            r for r in payload["primary"] + payload["secondary"]
            if r["promotion_status"] == "dropped_diversity_filter"
        ]
        multi_drops = [
            r for r in payload["primary"] + payload["secondary"]
            if r["promotion_status"] == "dropped_multi_claim_cap"
        ]
        cap_drops = [
            r for r in payload["primary"] + payload["secondary"]
            if r["promotion_status"] == "dropped_per_claim_cap"
        ]
        lines.append(
            f"- Promoted total: {len(payload['promoted'])} "
            f"(primary {len(primaries)}, secondary {len(secondaries)})"
        )
        if primaries:
            lines.append("- Primary (>= 18):")
            for r in sorted(
                primaries, key=lambda x: (-x["weighted_total"], x["paper_id"])
            ):
                lines.append(
                    f"  - {r['paper_id']} | "
                    f"{paper_titles.get(r['paper_id'], r['paper_title'])} | "
                    f"weighted_total={r['weighted_total']:.1f} | "
                    f"citing={r['paper_cited_by_count']} | "
                    f"domain={paper_domain.get(r['paper_id'], 'unknown')}"
                )
        if secondaries:
            lines.append("- Secondary (14-17):")
            for r in sorted(
                secondaries, key=lambda x: (-x["weighted_total"], x["paper_id"])
            ):
                lines.append(
                    f"  - {r['paper_id']} | "
                    f"{paper_titles.get(r['paper_id'], r['paper_title'])} | "
                    f"weighted_total={r['weighted_total']:.1f} | "
                    f"citing={r['paper_cited_by_count']} | "
                    f"domain={paper_domain.get(r['paper_id'], 'unknown')}"
                )
        if below:
            lines.append(
                f"- Dropped (below threshold): {len(below)} candidates"
            )
            for r in below:
                lines.append(
                    f"  - {r['paper_id']} | "
                    f"weighted_total={r['weighted_total']:.1f} | "
                    f"reason=below_threshold"
                )
        if cap_drops:
            lines.append(
                f"- Dropped (per-claim cap, surplus): {len(cap_drops)} candidates"
            )
            for r in cap_drops:
                lines.append(
                    f"  - {r['paper_id']} | "
                    f"weighted_total={r['weighted_total']:.1f} | "
                    f"tier={r['tier']}"
                )
        if div_drops:
            lines.append(
                f"- Dropped (diversity filter): {len(div_drops)} candidates"
            )
            for r in div_drops:
                lines.append(
                    f"  - {r['paper_id']} | "
                    f"weighted_total={r['weighted_total']:.1f} | "
                    f"domain={paper_domain.get(r['paper_id'], 'unknown')}"
                )
        if multi_drops:
            lines.append(
                f"- Dropped (multi-claim cap): {len(multi_drops)} candidates"
            )
            for r in multi_drops:
                lines.append(
                    f"  - {r['paper_id']} | "
                    f"weighted_total={r['weighted_total']:.1f}"
                )
        lines.append("")

    paper_to_claims: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in rows:
        if r["promotion_status"] == "promoted":
            paper_to_claims[r["paper_id"]].append(
                (r["claim_ref"], r["weighted_total"])
            )
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

    lines.append("## Diversity filter actions")
    lines.append("")
    diversity_actions = []
    for cid in sorted(per_claim.keys()):
        diversity_actions.extend(per_claim[cid].get("diversity_actions", []))
    if not diversity_actions:
        lines.append("- No diversity-filter actions triggered.")
    else:
        for action in diversity_actions:
            lines.append(
                f"- claim={action['claim_id']} demoted {action['paper_id']} "
                f"(domain={action['domain']}, "
                f"weighted_total={action['weighted_total']:.1f}): "
                f"{action['reason']}"
            )
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    short_claims = [
        cid for cid, p in per_claim.items() if len(p["promoted"]) < PER_CLAIM_MIN
    ]
    if short_claims:
        lines.append(
            "- Claims that fell short of the 6-paper minimum after primary+secondary exhausted:"
        )
        for cid in sorted(short_claims):
            lines.append(
                f"  - {cid}: {len(per_claim[cid]['promoted'])} promoted "
                f"(no remaining primary/secondary candidates available)"
            )
    else:
        lines.append("- All 9 claims reached the 6-paper minimum.")
    if duplicates:
        lines.append(
            f"- Duplicate candidate_ids detected across scored files: {duplicates}"
        )
    lines.append(
        "- All evidence points are written with `confidence: candidate` and "
        "`screenshot_status: pending`; downstream agents will deepen and "
        "produce screenshots."
    )
    lines.append(
        "- `external_reference: true` on every point so MMG paper_ids resolve "
        "via the multimodal_genomics paper registry rather than a primary WCM list."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute promotions but do not write any output files",
    )
    args = parser.parse_args(argv)

    ontology = _load_json(ONTOLOGY_FILE)
    rows, claim_meta, duplicates = load_unified_rows()
    papers_payload = _load_json(PAPERS_FILE)
    papers = papers_payload.get("papers") or {}
    paper_domain = {
        pid: meta.get("domain", "unknown") for pid, meta in papers.items()
    }
    paper_titles = {
        pid: meta.get("title", "") for pid, meta in papers.items()
    }

    per_claim = apply_promotion_rules(rows, paper_domain)

    if args.dry_run:
        promoted_count = sum(
            1 for r in rows if r.get("promotion_status") == "promoted"
        )
        print(f"[dry-run] would promote {promoted_count} candidates")
        for cid in sorted(per_claim.keys()):
            n = len(per_claim[cid]["promoted"])
            print(f"  {cid}: {n}")
        return 0

    # Preserve manual_verified rows in evidence.json if present.
    preserved_by_claim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if EVIDENCE_FILE.is_file():
        try:
            existing = _load_json(EVIDENCE_FILE)
            for paradigm in existing.get("paradigms") or []:
                for claim in paradigm.get("claims") or []:
                    for pt in claim.get("evidence_points") or []:
                        if pt.get("confidence") == "manual_verified":
                            preserved_by_claim[claim.get("id", "")].append(pt)
        except Exception:
            pass

    evidence_doc = build_evidence_doc(ontology, per_claim)
    if preserved_by_claim:
        for paradigm in evidence_doc["paradigms"]:
            for claim in paradigm["claims"]:
                preserved = preserved_by_claim.get(claim["id"]) or []
                if preserved:
                    new_ids = {pt["id"] for pt in claim["evidence_points"]}
                    for pt in preserved:
                        if pt.get("id") not in new_ids:
                            claim["evidence_points"].insert(0, pt)

    candidates_doc = build_candidates_doc(rows, claim_meta)
    log_text = build_log(rows, per_claim, paper_domain, paper_titles, duplicates)

    EVIDENCE_FILE.write_text(
        json.dumps(evidence_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    CANDIDATES_FILE.write_text(
        json.dumps(candidates_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    LOG_FILE.write_text(log_text, encoding="utf-8")

    promoted_count = sum(
        1 for r in rows if r["promotion_status"] == "promoted"
    )
    print(
        f"OK -- promoted {promoted_count} evidence points across "
        f"{len(per_claim)} claims"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
