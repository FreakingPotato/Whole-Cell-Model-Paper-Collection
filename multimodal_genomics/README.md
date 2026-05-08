# Multimodal NLP × Genomic Foundation Models — Knowledge Graph

A self-contained subtree applying the agentic knowledge-graph pipeline (originally built for whole-cell-model literature) to a new topic: **multimodal natural-language models combined with DNA / RNA foundation models**.

## Open the viewer

[**▶ open `index.html`**](./index.html) — a self-contained static page. Three paradigm cards, expandable claims, ranked evidence rows, click any row to see paragraph-screenshot stacks plus a 5-dimension rubric breakdown. **TSV download** button at the bottom-right exports straight to Google Sheets / Excel.

The same view works from a `file://` URL or any static-file host (no backend required).

## Topic ontology (`metadata/ontology.json`)

The knowledge graph is built around a fixed schema:

- **3 paradigms** × **3 claims each = 9 claims**:

| Paradigm | Claim ID | Subtype |
|---|---|---|
| Genomic Foundation Models (DNA / RNA) | `gfm-tokenization` | Tokenization |
| | `gfm-long-context` | Long-context architecture |
| | `gfm-pretraining-objective` | Pretraining objective |
| Text–Sequence Multimodal Integration | `multimodal-shared-embeddings` | Shared embedding spaces |
| | `multimodal-instruction-tuning` | Instruction tuning |
| | `multimodal-text-conditioned-generation` | Text-conditioned sequence generation |
| Downstream Multimodal Applications | `applications-variant-effects` | Variant-effect prediction |
| | `applications-regulatory-annotation` | Regulatory-element annotation |
| | `applications-drug-discovery` | Sequence-aware drug discovery |

- **5-dimension rubric** for scoring each candidate paper against each claim (weights: useful_outcomes 1.5, immediate_benefit 1.3, plausible 1.1, scalable 0.9, how_to_validate 0.7 → max 28).
- **High-impact venue allowlist**: Nature/Science/Cell families, Nature Methods/Biotech/Mach Intell/Genetics, Cell Systems, Genome Biology, Nucleic Acids Research, Bioinformatics, NeurIPS/ICML/ICLR/ACL, arXiv/bioRxiv (preprints allowed).

## Agent pipeline

```
Architect → ontology.json
          ↓
Discoverer (D)        → papers.json + candidates.json
          ↓
Scorers ×3 (S₁ S₂ S₃) → scored_<paradigm>.json (parallel)
          ↓
Reviewer (R)          → evidence.json (primary/secondary tiers)
          ↓
   ┌─────────────┬──────────────┐
   ▼             ▼              ▼
Fetcher (P)   Ranker (K)   Viewer (V)
   ↓
Linker (H)            → multi-sentence screenshot stacks
          ↓
QC checker            → qc_report.{json,md}
          ↓
Auditor               → final HTML
```

## Files

| Path | Owner | Purpose |
|---|---|---|
| `metadata/ontology.json` | Architect | Topic schema (paradigms, claims, rubric, allowlist) |
| `metadata/papers.json` | D, P | `MMG-NNN` paper registry (title, authors, journal, citations, DOI, arXiv) |
| `metadata/candidates.json` | D | All discovered (paper, claim) pairs before scoring |
| `metadata/scored_<paradigm>.json` | S₁ S₂ S₃ | Per-paradigm rubric scoring slices |
| `metadata/evidence.json` | R | Curated, ranked, tier-tagged evidence with screenshot stacks |
| `metadata/promotion_log.md` | R | Audit trail of which candidates got promoted/dropped and why |
| `metadata/rank_log.md` | K | Per-claim ranked tables (journal tier → citations → year) |
| `metadata/screenshot_log.json` | H | Per-evidence screenshot generation outcome |
| `metadata/qc_report.{json,md}` | QC | Per-screenshot quality verdicts |
| `assets/evidence/*.png` | H | Multi-sentence highlighted paragraph screenshots |
| `index.html` | V | Standalone static viewer |
| `scripts/*.py` | reproducible | Each agent's logic is also a re-runnable CLI |

## Reproducing

```bash
# 1. Discover candidates (D agent or CLI):
python multimodal_genomics/scripts/discover_candidates.py

# 2. Score (S₁/S₂/S₃ agents — one per paradigm):
# (each writes to its own metadata/scored_<paradigm>.json)

# 3. Promote candidates to evidence (R agent or CLI):
python multimodal_genomics/scripts/review_candidates.py

# 4. Rank within each claim by journal tier + citations + year:
python multimodal_genomics/scripts/rank_evidence.py --log

# 5. Render paragraph screenshots from local PDFs:
python multimodal_genomics/scripts/generate_screenshots.py --force

# 6. Validate the evidence file:
python multimodal_genomics/scripts/validate_evidence.py

# 7. Build the standalone viewer:
python multimodal_genomics/scripts/build_viewer.py
```

PDFs (gitignored under `pdfs/MMG-*.pdf`) are fetched via OpenAlex / Unpaywall / Europe PMC / arXiv / bioRxiv — open-access only, no Sci-Hub.

## Generalising to another topic

1. Author a new `ontology.json` (paradigms + claims + rubric + allowlist).
2. Re-run the pipeline. The scripts are topic-agnostic; only the ontology and the agent prompts need to change.
3. Drop the rendered viewer anywhere static; it contains all data inline (~1 MB HTML for ~80 papers).
