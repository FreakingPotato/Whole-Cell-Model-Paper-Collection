#!/usr/bin/env python3
"""Harvest candidate whole-cell-model papers and available PDF links."""

from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METADATA_DIR = ROOT / "metadata"
SEED_DOI = "10.1016/j.cell.2026.02.009"
MAILTO = "stark@example.com"


KEYWORDS = [
    "whole-cell",
    "whole cell",
    "minimal cell",
    "syn3",
    "jcvi",
    "mycoplasma",
    "cell cycle",
    "gene expression",
    "metabolic",
    "metabolism",
    "spatial",
    "stochastic",
    "replication",
    "segregation",
    "division",
    "lattice microbes",
    "brownian dynamics",
]


MANUAL_SEED_DOIS = [
    "10.1016/j.cell.2012.05.044",
    "10.1126/science.1190719",
    "10.1126/science.1260419",
    "10.1016/j.cels.2019.05.001",
    "10.1016/j.cels.2021.05.008",
    "10.1016/j.cels.2020.03.010",
    "10.1038/s41586-021-04185-w",
    "10.1038/s41467-024-54674-3",
    "10.1016/j.molcel.2017.12.025",
    "10.1016/j.celrep.2018.10.040",
    "10.1016/j.cell.2021.01.013",
]


def openalex_json(url: str) -> dict:
    delim = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{delim}mailto={urllib.parse.quote(MAILTO)}",
        headers={"User-Agent": f"WCMCollector/1.0 ({MAILTO})"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def openalex_work_from_doi(doi: str) -> dict:
    doi = doi.lower().removeprefix("https://doi.org/").removeprefix("doi:")
    quoted = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
    return openalex_json(f"https://api.openalex.org/works/{quoted}")


def openalex_work_from_id(work_id: str) -> dict:
    short_id = work_id.rsplit("/", 1)[-1]
    return openalex_json(f"https://api.openalex.org/works/{short_id}")


def clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def landing_url(work: dict) -> str:
    primary = work.get("primary_location") or {}
    if primary.get("landing_page_url"):
        return primary["landing_page_url"]
    locations = work.get("locations") or []
    for location in locations:
        if location.get("landing_page_url"):
            return location["landing_page_url"]
    return work.get("doi") or ""


def pdf_url(work: dict) -> str:
    primary = work.get("primary_location") or {}
    if primary.get("pdf_url"):
        return primary["pdf_url"]
    best_oa = work.get("best_oa_location") or {}
    if best_oa.get("pdf_url"):
        return best_oa["pdf_url"]
    locations = work.get("locations") or []
    for location in locations:
        if location.get("pdf_url"):
            return location["pdf_url"]
    return ""


def author_string(work: dict) -> str:
    names = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        if author.get("display_name"):
            names.append(author["display_name"])
    return "; ".join(names[:8])


def venue(work: dict) -> str:
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    return source.get("display_name") or ""


def score_candidate(work: dict) -> int:
    text = " ".join(
        [
            clean(work.get("display_name")),
            clean(work.get("abstract_inverted_index") and " ".join(work["abstract_inverted_index"].keys())),
            clean(venue(work)),
            " ".join(con.get("display_name", "") for con in work.get("concepts") or []),
        ]
    ).lower()
    score = 0
    for kw in KEYWORDS:
        if kw in text:
            score += 2
    if work.get("cited_by_count", 0) >= 100:
        score += 2
    if work.get("publication_year", 9999) <= 2015:
        score += 1
    return score


def abstract_text(work: dict) -> str:
    inverted = work.get("abstract_inverted_index")
    if not inverted:
        return ""
    words = {}
    for token, positions in inverted.items():
        for position in positions:
            words[position] = token
    return " ".join(words[i] for i in sorted(words))


def collect_seed_references() -> list[dict]:
    seed = openalex_work_from_doi(SEED_DOI)
    rows = []
    for ref_id in seed.get("referenced_works") or []:
        try:
            work = openalex_work_from_id(ref_id)
        except Exception as exc:  # noqa: BLE001
            print(f"failed {ref_id}: {exc}", file=sys.stderr)
            continue
        rows.append(work)
        time.sleep(0.08)
    return rows


def collect_manual_seeds() -> list[dict]:
    rows = []
    for doi in MANUAL_SEED_DOIS:
        try:
            work = openalex_work_from_doi(doi)
        except Exception as exc:  # noqa: BLE001
            print(f"failed {doi}: {exc}", file=sys.stderr)
            continue
        rows.append(work)
        time.sleep(0.08)
    return rows


def dedupe(works: list[dict]) -> list[dict]:
    seen = {}
    for work in works:
        seen[work["id"]] = work
    return list(seen.values())


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_csv(path: Path, works: list[dict]) -> None:
    fields = [
        "openalex_id",
        "doi",
        "year",
        "title",
        "venue",
        "authors",
        "cited_by_count",
        "candidate_score",
        "pdf_url",
        "landing_page_url",
        "abstract",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for work in sorted(works, key=lambda w: (-score_candidate(w), w.get("publication_year") or 9999, clean(w.get("display_name")))):
            writer.writerow(
                {
                    "openalex_id": work.get("id", ""),
                    "doi": (work.get("doi") or "").removeprefix("https://doi.org/"),
                    "year": work.get("publication_year", ""),
                    "title": clean(work.get("display_name")),
                    "venue": venue(work),
                    "authors": author_string(work),
                    "cited_by_count": work.get("cited_by_count", 0),
                    "candidate_score": score_candidate(work),
                    "pdf_url": pdf_url(work),
                    "landing_page_url": landing_url(work),
                    "abstract": abstract_text(work),
                }
            )


def main() -> int:
    seed_refs = collect_seed_references()
    manual = collect_manual_seeds()
    combined = dedupe(seed_refs + manual)
    write_json(METADATA_DIR / "seed_references_raw.json", seed_refs)
    write_json(METADATA_DIR / "manual_seed_raw.json", manual)
    write_csv(METADATA_DIR / "candidate_papers.csv", combined)
    print(f"seed references: {len(seed_refs)}")
    print(f"manual seeds: {len(manual)}")
    print(f"combined candidates: {len(combined)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
