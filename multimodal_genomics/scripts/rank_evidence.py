#!/usr/bin/env python3
"""Rank evidence points within each claim of multimodal_genomics/metadata/evidence.json.

For every claim, the evidence_points are sorted by:
  1. Primary:    journal_tier (ascending — Tier 1 = Nature/Science/Cell first;
                 5 = preprints last). Maps the paper's journal name to a tier
                 via the JOURNAL_TIER table below.
  2. Secondary:  cited_by_count (descending) — pulled from the right registry
                 based on paper_id prefix (MMG → wcm_paper_metadata,
                 MMG → hybrid_external_papers).
  3. Tiebreak:   year (descending — newer wins).
  4. Final:      paper_id (lexicographic, ascending — for determinism).
                 If two evidence points share the same paper_id under the same
                 claim, the evidence point id is used as the absolute final
                 tiebreak, also ascending.

A 1-indexed ``rank_within_claim`` field is then written on every evidence
point. All other fields are preserved as-is.

Usage::

    python scripts/rank_evidence.py            # writes in place
    python scripts/rank_evidence.py --dry-run  # prints a per-claim
                                                       # table without writing
    python scripts/rank_evidence.py --log PATH # write a markdown log

The script is deterministic: same input → same output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_FILE = ROOT / "multimodal_genomics" / "metadata" / "evidence.json"
WCM_FILE = ROOT / "multimodal_genomics" / "metadata" / "papers.json"
EXT_FILE = ROOT / "multimodal_genomics" / "metadata" / "papers.json"
DEFAULT_LOG = ROOT / "multimodal_genomics" / "metadata" / "rank_log.md"


# Journal-IF tier table. Lower number = higher impact. Used as the PRIMARY
# sort key (so Nature / Science / Cell papers always lead a claim, regardless
# of citation count, and preprints sink to the bottom even when popular).
# Match is by lowercased substring against the journal name, longest match
# wins (so "Nature Methods" picks up tier 2, not tier 1).
JOURNAL_TIER: list[tuple[str, int]] = [
    # Tier 1 — generalist top-3
    ("nature", 1),
    ("science", 1),
    ("cell", 1),
    # Tier 2 — top family flagships and adjacent very-high-IF venues
    ("nature methods", 2),
    ("nature computational science", 2),
    ("nature machine intelligence", 2),
    ("nature reviews physics", 2),
    ("nature chemistry", 2),
    ("nature communications", 2),
    ("cell systems", 2),
    ("cell reports", 2),
    ("science advances", 2),
    ("science translational medicine", 2),
    ("proceedings of the national academy of sciences", 2),
    ("pnas", 2),
    # Tier 3 — high-impact specialist journals + top ML proceedings
    ("journal of computational physics", 3),
    ("computer methods in applied mechanics and engineering", 3),
    ("siam journal on scientific computing", 3),
    ("physical review letters", 3),
    ("physical review x", 3),
    ("journal of chemical theory and computation", 3),
    ("jacs", 3),
    ("journal of the american chemical society", 3),
    ("npj computational materials", 3),
    ("elife", 3),
    ("molecular systems biology", 3),
    ("genome research", 3),
    ("nucleic acids research", 3),
    ("bioinformatics", 3),
    ("neurips", 3),
    ("advances in neural information processing systems", 3),
    ("icml", 3),
    ("international conference on machine learning", 3),
    ("iclr", 3),
    ("international conference on learning representations", 3),
    ("journal of machine learning research", 3),
    ("transactions on machine learning research", 3),
    # Tier 4 — peer-reviewed but lower-IF or community-specific
    ("plos computational biology", 4),
    ("plos one", 4),
    ("current opinion in structural biology", 4),
    ("frontiers in", 4),
    ("mbio", 4),
    ("journal of physical chemistry b", 4),
    ("biopolymers", 4),
    ("biophysical journal", 4),
    ("iscience", 4),
    ("crystallography reports", 4),
    # Tier 5 — preprint servers
    ("arxiv", 5),
    ("biorxiv", 5),
    ("medrxiv", 5),
    ("cold spring harbor", 5),
    ("zenodo", 5),
]
# Build a lookup ordered by length-desc so "nature methods" wins over "nature".
_JOURNAL_TIER_ORDERED = sorted(JOURNAL_TIER, key=lambda kv: -len(kv[0]))
DEFAULT_JOURNAL_TIER = 4  # unknown peer-reviewed venue


def journal_tier(journal: str | None) -> int:
    if not journal:
        return DEFAULT_JOURNAL_TIER
    j = journal.lower()
    for needle, tier in _JOURNAL_TIER_ORDERED:
        if needle in j:
            return tier
    return DEFAULT_JOURNAL_TIER


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_registry(path: Path) -> Dict[str, Dict[str, Any]]:
    """Return a {paper_id: paper_record} dict from a registry file."""
    if not path.is_file():
        return {}
    payload = _load_json(path)
    papers = payload.get("papers")
    if isinstance(papers, dict):
        return papers
    if isinstance(papers, list):
        return {p["paper_id"]: p for p in papers if p.get("paper_id")}
    return {}


def _lookup(
    paper_id: str,
    wcm: Dict[str, Dict[str, Any]],
    ext: Dict[str, Dict[str, Any]],
) -> Tuple[int, int, str, str]:
    """Return (cited_by_count, year, title, journal) for a paper_id.

    cited_by_count defaults to 0 if missing.
    year defaults to 0 if missing (so older/unknown sorts last).
    title and journal default to '' if missing.
    """
    record: Dict[str, Any] = {}
    if isinstance(paper_id, str) and paper_id.startswith("MMG"):
        record = ext.get(paper_id, {})
    cited = record.get("cited_by_count")
    if not isinstance(cited, int) or isinstance(cited, bool):
        cited = 0
    year = record.get("year")
    if not isinstance(year, int) or isinstance(year, bool):
        year = 0
    title = record.get("title") or ""
    if not isinstance(title, str):
        title = str(title)
    journal = record.get("journal") or ""
    if not isinstance(journal, str):
        journal = str(journal)
    return cited, year, title, journal


def _sort_key(
    point: Dict[str, Any],
    wcm: Dict[str, Dict[str, Any]],
    ext: Dict[str, Dict[str, Any]],
) -> Tuple[int, int, int, str, str]:
    paper_id = point.get("paper_id") or ""
    eid = point.get("id") or ""
    cited, year, _, journal = _lookup(paper_id, wcm, ext)
    tier = journal_tier(journal)
    # Plain ascending sort gives: lower tier first (Nature/Science/Cell win),
    # then higher cited (negated), then higher year (negated), then
    # ascending paper_id and evidence id.
    return (tier, -cited, -year, paper_id, eid)


def rank_evidence(
    data: Dict[str, Any],
    wcm: Dict[str, Dict[str, Any]],
    ext: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Mutate ``data`` in place: reorder evidence_points by sort key and stamp
    ``rank_within_claim``. Returns a list of per-row dicts for logging."""
    log_rows: List[Dict[str, Any]] = []
    for paradigm in data.get("paradigms", []):
        for claim in paradigm.get("claims", []):
            points: List[Dict[str, Any]] = claim.get("evidence_points") or []
            ordered = sorted(points, key=lambda pt: _sort_key(pt, wcm, ext))
            for rank, pt in enumerate(ordered, start=1):
                pt["rank_within_claim"] = rank
                paper_id = pt.get("paper_id") or ""
                cited, year, title, journal = _lookup(paper_id, wcm, ext)
                tier = journal_tier(journal)
                # Stamp journal_tier on the evidence point so the viewer can
                # surface it without re-running the lookup.
                pt["journal_tier"] = tier
                log_rows.append({
                    "paradigm_id": paradigm.get("id"),
                    "claim_id": claim.get("id"),
                    "rank": rank,
                    "evidence_id": pt.get("id"),
                    "paper_id": paper_id,
                    "title": title,
                    "journal": journal,
                    "journal_tier": tier,
                    "cited_by_count": cited,
                    "year": year,
                })
            claim["evidence_points"] = ordered
    return log_rows


def _truncate(s: str, n: int = 70) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _print_table(log_rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(log_rows)
    by_claim: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for r in rows:
        cid = r["claim_id"]
        if cid not in by_claim:
            by_claim[cid] = []
            order.append(cid)
        by_claim[cid].append(r)
    for cid in order:
        print(f"\n## {cid}")
        print(f"{'rank':>4}  {'paper_id':<10}  {'tier':>4}  "
              f"{'cited':>6}  {'year':>5}  journal · title")
        print("-" * 110)
        for r in by_claim[cid]:
            print(
                f"{r['rank']:>4}  {r['paper_id']:<10}  "
                f"{r.get('journal_tier','-'):>4}  "
                f"{r['cited_by_count']:>6}  {r['year']:>5}  "
                f"{_truncate((r.get('journal','') or '?'), 28)} · "
                f"{_truncate(r['title'])}"
            )


def _write_log(log_rows: Iterable[Dict[str, Any]], path: Path) -> None:
    rows = list(log_rows)
    by_claim: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for r in rows:
        cid = r["claim_id"]
        if cid not in by_claim:
            by_claim[cid] = []
            order.append(cid)
        by_claim[cid].append(r)
    lines: List[str] = []
    lines.append("# Hybrid evidence rank log")
    lines.append("")
    lines.append(
        "Generated by `scripts/rank_evidence.py`. "
        "Sort key: journal_tier asc (Nature/Science/Cell first), "
        "cited_by_count desc, year desc, paper_id asc, evidence id asc."
    )
    lines.append("")
    for cid in order:
        lines.append(f"## {cid}")
        lines.append("")
        lines.append("| rank | paper_id | tier | cited | year | journal | title |")
        lines.append("| ---: | :--- | ---: | ---: | ---: | :--- | :--- |")
        for r in by_claim[cid]:
            title = _truncate(r["title"], 80).replace("|", "\\|")
            journal = _truncate(r.get("journal", "") or "—", 32).replace("|", "\\|")
            lines.append(
                f"| {r['rank']} | {r['paper_id']} | {r.get('journal_tier','')} | "
                f"{r['cited_by_count']} | {r['year']} | {journal} | {title} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print per-claim table without modifying any file.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=EVIDENCE_FILE,
        help=f"Path to evidence JSON (default: {EVIDENCE_FILE}).",
    )
    parser.add_argument(
        "--wcm",
        type=Path,
        default=WCM_FILE,
        help=f"Path to MMG paper metadata (default: {WCM_FILE}).",
    )
    parser.add_argument(
        "--ext",
        type=Path,
        default=EXT_FILE,
        help=f"Path to external paper metadata (default: {EXT_FILE}).",
    )
    parser.add_argument(
        "--log",
        type=Path,
        nargs="?",
        const=DEFAULT_LOG,
        default=None,
        help=(
            "Write a markdown log file. With no value, writes to "
            f"{DEFAULT_LOG}."
        ),
    )
    args = parser.parse_args(argv)

    if not args.evidence.is_file():
        print(f"ERROR: {args.evidence} not found", file=sys.stderr)
        return 2

    data = _load_json(args.evidence)
    wcm = _load_registry(args.wcm)
    ext = _load_registry(args.ext)
    log_rows = rank_evidence(data, wcm, ext)

    if args.dry_run:
        _print_table(log_rows)
        print(
            f"\n[dry-run] {len(log_rows)} evidence points across "
            f"{len({r['claim_id'] for r in log_rows})} claims; "
            f"no files modified."
        )
        return 0

    args.evidence.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {args.evidence} — ranked {len(log_rows)} evidence points "
        f"across {len({r['claim_id'] for r in log_rows})} claims"
    )

    if args.log is not None:
        _write_log(log_rows, args.log)
        print(f"wrote log {args.log}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
