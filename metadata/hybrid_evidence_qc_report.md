# Hybrid Evidence Screenshot QC Report

_Checked at: 2026-05-08T06:27:17.495608+00:00_

## Summary

| Metric | Count |
| --- | ---: |
| total_evidence_points | 82 |
| total_evidence_points_all | 82 |
| total_screenshots | 356 |
| ok | 343 |
| reference | 0 |
| header | 1 |
| fragment | 12 |
| off_topic | 0 |
| borderline | 0 |
| missing_png_files | 0 |

## Verdict definitions

- **ok** — body sentence(s); supports the parent claim.
- **reference** — bibliography / reference-list entry.
- **header** — paper-title page, journal+title running header, all-caps title, author-contributions banner, or section sub-heading without trailing period.
- **fragment** — incomplete sentence (no verb, ends mid-clause / mid-equation, lowercase opening, ≤6 words), or a figure/table caption opener ('Fig. 4 | Title', '(A) ...', 'Table N: ...').
- **off_topic** — complete body sentence that does not relate to the claim. (Reserved; current pass uses structural cues only.)
- **borderline** — text-only judgement is ambiguous; visual check needed.

## Evidence points needing remediation

Total: 12 evidence points across 11 papers.

### `hybrid-embedded-closure-001` (WCM-046) — Closure

- **#1 (page 4)** — `fragment`
  - Reason: Figure/table caption opener ('Fig. 4 | Title', '(A) ...', or 'Table N: ...').
  - Text: "2 | Benchmarking AMNs with different training sets and mechanistic layers."

- **#3 (page 6)** — `fragment`
  - Reason: Figure/table caption opener ('Fig. 4 | Title', '(A) ...', or 'Table N: ...').
  - Text: "4 | AMNs growth rate predictions for E. coli gene KOs mutants."

### `hybrid-embedded-constraint-001` (WCM-046) — Constraint

- **#2 (page 6)** — `fragment`
  - Reason: Figure/table caption opener ('Fig. 4 | Title', '(A) ...', or 'Table N: ...').
  - Text: "4 | AMNs growth rate predictions for E. coli gene KOs mutants."

### `hybrid-embedded-constraint-EXT003` (EXT-020) — Constraint

- **#4 (page 9)** — `fragment`
  - Reason: Incomplete sentence (no verb, ends mid-clause, equation lead-in, lowercase opening, or <=6 words).
  - Text: "(8) is called physics-constrained surrogate (PCS)."

### `hybrid-embedded-constraint-EXT007` (EXT-015) — Constraint

- **#1 (page 1)** — `header`
  - Reason: Paper title, running-header banner, or author-contributions style line.
  - Text: "NSFnets (Navier-Stokes Flow nets): Physics-informed neural networks for the incompressible Navier-Stokes equations"

### `hybrid-parallel-agreement-disagreement-EXT002` (EXT-073) — Agreement

- **#2 (page 4)** — `fragment`
  - Reason: Figure/table caption opener ('Fig. 4 | Title', '(A) ...', or 'Table N: ...').
  - Text: "2 | Uncertainty thresholding yields improved accuracy for high-conﬁdence predictions."

### `hybrid-parallel-foundation-models-EXT002` (EXT-089) — Foundation

- **#2 (page 7)** — `fragment`
  - Reason: Figure/table caption opener ('Fig. 4 | Title', '(A) ...', or 'Table N: ...').
  - Text: "6 | Showcase of the application of TabPFN as tabular foundation model."

### `hybrid-parallel-foundation-models-EXT003` (EXT-081) — Foundation

- **#3 (page 2)** — `fragment`
  - Reason: Figure/table caption opener ('Fig. 4 | Title', '(A) ...', or 'Table N: ...').
  - Text: "1 | MedSAM is trained on a large-scale dataset that can handle diverse segmentation tasks."

### `hybrid-parallel-foundation-models-EXT004` (EXT-083) — Foundation

- **#5 (page 5)** — `fragment`
  - Reason: Figure/table caption opener ('Fig. 4 | Title', '(A) ...', or 'Table N: ...').
  - Text: "3 | Scaling learned interatomic potentials."

### `hybrid-pipeline-inference-EXT007` (EXT-048) — Inference

- **#3 (page 4)** — `fragment`
  - Reason: Incomplete sentence (no verb, ends mid-clause, equation lead-in, lowercase opening, or <=6 words).
  - Text: "C. Simulation-Based Inference with Normalizing Flows."

### `hybrid-pipeline-structural-learning-EXT001` (EXT-057) — Structural

- **#2 (page 13)** — `fragment`
  - Reason: Incomplete sentence (no verb, ends mid-clause, equation lead-in, lowercase opening, or <=6 words).
  - Text: "3.2.2 PINNs for Ultradian Endocrine model."

### `hybrid-pipeline-structural-learning-EXT005` (EXT-052) — Structural

- **#3 (page 5)** — `fragment`
  - Reason: Figure/table caption opener ('Fig. 4 | Title', '(A) ...', or 'Table N: ...').
  - Text: "4 | Learning the mechanism of polymer folding."

### `hybrid-pipeline-structural-learning-EXT007` (EXT-054) — Structural

- **#3 (page 5)** — `fragment`
  - Reason: Incomplete sentence (no verb, ends mid-clause, equation lead-in, lowercase opening, or <=6 words).
  - Text: "(2) with four different reaction models."
