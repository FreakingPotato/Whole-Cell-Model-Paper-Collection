#!/usr/bin/env python3
"""Rank evidence points within each claim of metadata/hybrid_model_evidence.json.

For every claim, the evidence_points are sorted by:
  1. Primary:    cited_by_count (descending) — pulled from the right registry
                 based on paper_id prefix (WCM → wcm_paper_metadata,
                 EXT → hybrid_external_papers).
  2. Tiebreak:   year (descending — newer wins).
  3. Final:      paper_id (lexicographic, ascending — for determinism).
                 If two evidence points share the same paper_id under the same
                 claim, the evidence point id is used as the absolute final
                 tiebreak, also ascending.

A 1-indexed ``rank_within_claim`` field is then written on every evidence
point. All other fields are preserved as-is.

Usage::

    python scripts/rank_hybrid_evidence.py            # writes in place
    python scripts/rank_hybrid_evidence.py --dry-run  # prints a per-claim
                                                       # table without writing
    python scripts/rank_hybrid_evidence.py --log PATH # write a markdown log

The script is deterministic: same input → same output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FILE = ROOT / "metadata" / "hybrid_model_evidence.json"
WCM_FILE = ROOT / "metadata" / "wcm_paper_metadata.json"
EXT_FILE = ROOT / "metadata" / "hybrid_external_papers.json"
DEFAULT_LOG = ROOT / "metadata" / "hybrid_rank_log.md"


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
) -> Tuple[int, int, str]:
    """Return (cited_by_count, year, title) for a paper_id, defaulting safely.

    cited_by_count defaults to 0 if missing.
    year defaults to 0 if missing (so older/unknown sorts last).
    title defaults to '' if missing.
    """
    record: Dict[str, Any] = {}
    if isinstance(paper_id, str):
        if paper_id.startswith("WCM"):
            record = wcm.get(paper_id, {})
        elif paper_id.startswith("EXT"):
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
    return cited, year, title


def _sort_key(
    point: Dict[str, Any],
    wcm: Dict[str, Dict[str, Any]],
    ext: Dict[str, Dict[str, Any]],
) -> Tuple[int, int, str, str]:
    paper_id = point.get("paper_id") or ""
    eid = point.get("id") or ""
    cited, year, _ = _lookup(paper_id, wcm, ext)
    # Negate cited and year so that a plain ascending sort gives:
    #   higher cited first, then higher year first,
    #   then ascending paper_id, then ascending evidence id.
    return (-cited, -year, paper_id, eid)


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
                cited, year, title = _lookup(paper_id, wcm, ext)
                log_rows.append({
                    "paradigm_id": paradigm.get("id"),
                    "claim_id": claim.get("id"),
                    "rank": rank,
                    "evidence_id": pt.get("id"),
                    "paper_id": paper_id,
                    "title": title,
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
        print(f"{'rank':>4}  {'paper_id':<10}  {'cited':>6}  {'year':>5}  title")
        print("-" * 100)
        for r in by_claim[cid]:
            print(
                f"{r['rank']:>4}  {r['paper_id']:<10}  "
                f"{r['cited_by_count']:>6}  {r['year']:>5}  "
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
        "Generated by `scripts/rank_hybrid_evidence.py`. "
        "Sort key: cited_by_count desc, year desc, paper_id asc, evidence id asc."
    )
    lines.append("")
    for cid in order:
        lines.append(f"## {cid}")
        lines.append("")
        lines.append("| rank | paper_id | cited_by_count | year | title |")
        lines.append("| ---: | :--- | ---: | ---: | :--- |")
        for r in by_claim[cid]:
            title = _truncate(r["title"], 80).replace("|", "\\|")
            lines.append(
                f"| {r['rank']} | {r['paper_id']} | "
                f"{r['cited_by_count']} | {r['year']} | {title} |"
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
        help=f"Path to WCM paper metadata (default: {WCM_FILE}).",
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
