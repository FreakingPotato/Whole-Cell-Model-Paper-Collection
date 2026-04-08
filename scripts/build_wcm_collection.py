#!/usr/bin/env python3
"""Build the curated whole-cell-model paper collection."""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METADATA_DIR = ROOT / "metadata"
PDF_DIR = ROOT / "pdfs"
MAILTO = "stark@example.com"


SEED_DOI = "10.1016/j.cell.2026.02.009"
CURATED_TITLES = [
    "A Whole-Cell Computational Model Predicts Phenotype from Genotype",
    "Fundamental behaviors emerge from simulations of a living minimal cell",
    "Metabolism, cell growth and the bacterial cell cycle",
    "Integrating cellular and molecular structures and dynamics into whole-cell models",
    "How to build the virtual cell with artificial intelligence: Priorities and opportunities",
    "Simultaneous cross-evaluation of heterogeneous E. coli datasets via mechanistic simulation",
    "Cross-evaluation of E. coli’s operon structures via a whole-cell model suggests alternative cellular benefits for low- versus high-expressing operons",
    "Toward a Whole-Cell Model of Ribosome Biogenesis: Kinetic Modeling of SSU Assembly",
    "Ribosome biogenesis in replicating cells: Integration of experiment and theory",
    "Creation of a Bacterial Cell Controlled by a Chemically Synthesized Genome",
    "Design and synthesis of a minimal bacterial genome",
    "Essential metabolism for a minimal cell",
    "Kinetic Modeling of the Genetic Information Processes in a Minimal Cell",
    "Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps",
    "Dynamics of chromosome organization in a minimal bacterial cell",
    "Toward the Complete Functional Characterization of a Minimal Bacterial Proteome",
    "Genetic requirements for cell division in a genomically minimal cell",
    "Metabolite Damage and Damage Control in a Minimal Genome",
    "Adaptive evolution of a minimal organism with a synthetic genome",
    "Evolution of a minimal cell",
    "A tuneable minimal cell membrane reveals that two lipid species suffice for life",
    "Integrative modeling of JCVI-Syn3A nucleoids with a modular approach",
    "Integrative characterization of the near‐minimal bacterium Mesoplasma florum",
    "Lattice microbes: High‐performance stochastic simulation method for the reaction‐diffusion master equation",
    "Simulation of reaction diffusion processes over biologically relevant size and time scales using multi-GPU workstations",
    "Molecular dynamics simulation of an entire cell",
    "Building Structural Models of a Whole Mycoplasma Cell",
    "In-cell architecture of an actively transcribing-translating expressome",
    "Visualizing translation dynamics at atomic detail inside a bacterial cell",
    "Entropy as the driver of chromosome segregation",
    "Bacterial chromosome segregation by the ParABS system",
    "Mechanisms for Chromosome Segregation in Bacteria",
    "Real-time imaging of DNA loop extrusion by condensin",
    "Chromosome organization by one-sided and two-sided loop extrusion",
    "DNA-loop-extruding SMC complexes can traverse one another in vivo",
    "Loop-extruders alter bacterial chromosome topology to direct entropic forces for segregation",
    "RNA polymerases as moving barriers to condensin loop extrusion",
    "Defined chromosome structure in the genome-reduced bacterium Mycoplasma pneumoniae",
    "Spatial organization of the flow of genetic information in bacteria",
    "Superresolution imaging of ribosomes and RNA polymerase in live <i>Escherichia coli</i> cells",
    "Modulation of Chemical Composition and Other Parameters of the Cell at Different Exponential Growth Rates",
    "Cell Reproduction and Morphological Changes in <i>Mycoplasma capricolum</i>",
]


def norm(text: str) -> str:
    text = text.replace("‐", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def openalex_json(url: str) -> dict:
    delim = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{delim}mailto={urllib.parse.quote(MAILTO)}",
        headers={"User-Agent": f"WCMCollector/1.0 ({MAILTO})"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def fetch_seed() -> dict:
    quoted = urllib.parse.quote(f"https://doi.org/{SEED_DOI}", safe="")
    return openalex_json(f"https://api.openalex.org/works/{quoted}")


def load_candidates() -> list[dict]:
    with (METADATA_DIR / "candidate_papers.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def select_curated_rows(seed: dict, candidates: list[dict]) -> list[dict]:
    by_title = {norm(row["title"]): row for row in candidates}
    curated = [
        {
            "openalex_id": seed.get("id", ""),
            "doi": (seed.get("doi") or "").removeprefix("https://doi.org/"),
            "year": str(seed.get("publication_year", "")),
            "title": seed.get("display_name", ""),
            "venue": ((seed.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
            "authors": "; ".join(
                (auth.get("author") or {}).get("display_name", "")
                for auth in seed.get("authorships") or []
                if (auth.get("author") or {}).get("display_name")
            ),
            "cited_by_count": str(seed.get("cited_by_count", 0)),
            "pdf_url": "",
            "landing_page_url": ((seed.get("primary_location") or {}).get("landing_page_url") or seed.get("doi") or ""),
            "abstract": abstract_text(seed),
        }
    ]
    missing = []
    for title in CURATED_TITLES:
        row = by_title.get(norm(title))
        if row is None:
            missing.append(title)
            continue
        curated.append(row)
    if missing:
        raise SystemExit(f"Missing curated titles: {missing}")
    return curated


def abstract_text(work: dict) -> str:
    inverted = work.get("abstract_inverted_index")
    if not inverted:
        return ""
    words = {}
    for token, positions in inverted.items():
        for position in positions:
            words[position] = token
    return " ".join(words[i] for i in sorted(words))


def filename_stub(paper_id: str, row: dict) -> str:
    authors = row.get("authors", "").split(";")[0].strip() or "Unknown"
    first_author = authors.split()[-1]
    parts = [paper_id, first_author, row.get("year", ""), row.get("title", "")[:60]]
    stub = "_".join(p for p in parts if p)
    stub = re.sub(r"<[^>]+>", "", stub)
    stub = re.sub(r"[^A-Za-z0-9._-]+", "_", stub).strip("_")
    return stub + ".pdf"


def fetch_pdf(url: str, target: Path) -> bool:
    if not url:
        return False
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"WCMCollector/1.0 ({MAILTO})",
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > 50_000_000:
                return False
            data = response.read()
        if "pdf" not in content_type and not data.startswith(b"%PDF"):
            return False
        target.write_bytes(data)
        return True
    except Exception:  # noqa: BLE001
        return False


def make_annotation(row: dict, idx: int) -> dict:
    title = re.sub(r"<[^>]+>", "", row["title"])
    abstract = row.get("abstract", "").strip()
    venue = row.get("venue", "")
    lower = f"{title} {abstract}".lower()
    if any(word in lower for word in ["review", "priorities", "opportunities", "mechanisms"]):
        method = "Narrative synthesis of prior literature to organize the field and compare modeling strategies."
        limitation = "Review-oriented framing; it does not itself resolve missing parameters or validate a new mechanistic model."
        future = "Use the review framework to prioritize model coupling, better data standards, and cross-scale validation."
    elif any(word in lower for word in ["simulation", "model", "dynamics", "modeling"]):
        method = "Mechanistic modeling and simulation, calibrated against experiments where available."
        limitation = "Model behavior depends on assumptions, parameter availability, and the scope of experimental calibration."
        future = "Expand the model with richer spatial detail, broader validation, and tighter integration with new measurements."
    elif any(word in lower for word in ["imaging", "tomogram", "tomograms", "structure", "visualizing"]):
        method = "Imaging- or structure-driven analysis used to constrain or interpret cell-scale organization."
        limitation = "Structural snapshots can under-sample dynamic behavior and often need complementary functional measurements."
        future = "Combine structural measurements with live-cell dynamics to connect geometry to cell-state transitions."
    else:
        method = "Experimental characterization of a process or system that later supports whole-cell-scale modeling."
        limitation = "Focused scope; the study isolates one subsystem rather than integrating the entire cell."
        future = "Reuse the measurements as constraints in broader whole-cell or minimal-cell simulations."

    summary = abstract.split(". ")[0].strip() if abstract else f"{title} is a key paper connected to the whole-cell-model literature."
    if summary and not summary.endswith("."):
        summary += "."
    contribution = f"Provides a reusable result for whole-cell modeling by clarifying {title.lower()[0:80].rstrip()}."
    if "minimal" in lower or "jcvi" in lower or "synthetic genome" in lower:
        contribution = "Establishes or extends the minimal-cell experimental and conceptual foundation used by later whole-cell models."
    elif "whole-cell" in lower or "virtual cell" in lower:
        contribution = "Advances whole-cell-model scope, validation, or organization at the system level."
    elif "chromosome" in lower or "condensin" in lower or "smc" in lower or "parab" in lower:
        contribution = "Explains chromosome organization or segregation mechanisms that the 4D cell-cycle models must encode."
    elif "ribosome" in lower or "translation" in lower or "expressome" in lower:
        contribution = "Constrains gene-expression modules by quantifying transcription, translation, or ribosome assembly behavior."
    elif "metabolism" in lower:
        contribution = "Supplies metabolic constraints and physiological logic needed to couple growth with information-processing modules."

    return {
        "paper_id": f"WCM-{idx:03d}",
        "paper_title": title,
        "year": row.get("year", ""),
        "journal": venue,
        "doi": row.get("doi", ""),
        "summary": summary,
        "method_summary": method,
        "contribution": contribution,
        "limitation": limitation,
        "future_work": future,
    }


def main() -> int:
    seed = fetch_seed()
    candidates = load_candidates()
    curated_rows = select_curated_rows(seed, candidates)
    annotations = []
    inventory_rows = []

    for idx, row in enumerate(curated_rows, start=1):
        paper_id = f"WCM-{idx:03d}"
        pdf_name = filename_stub(paper_id, row)
        pdf_target = PDF_DIR / pdf_name
        pdf_ok = fetch_pdf(row.get("pdf_url", ""), pdf_target)
        pdf_status = "downloaded" if pdf_ok else "landing_page_only"
        annotations.append(make_annotation(row, idx))
        inventory_rows.append(
            {
                "paper_id": paper_id,
                "title": re.sub(r"<[^>]+>", "", row["title"]),
                "year": row.get("year", ""),
                "journal": row.get("venue", ""),
                "doi": row.get("doi", ""),
                "pdf_status": pdf_status,
                "pdf_file": pdf_name if pdf_ok else "",
                "pdf_url": row.get("pdf_url", ""),
                "landing_page_url": row.get("landing_page_url", ""),
                "authors": row.get("authors", ""),
            }
        )

    with (METADATA_DIR / "curated_papers_inventory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory_rows[0].keys()))
        writer.writeheader()
        writer.writerows(inventory_rows)

    with (METADATA_DIR / "curated_papers_annotations.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(annotations[0].keys()))
        writer.writeheader()
        writer.writerows(annotations)

    print(f"curated papers: {len(curated_rows)}")
    print(f"pdfs downloaded: {sum(1 for row in inventory_rows if row['pdf_status'] == 'downloaded')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
