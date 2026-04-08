# Whole-Cell Model Paper Collection

This folder now contains a first-pass curated literature collection for classic and closely related whole-cell-model papers.

The collection is anchored on the 2026 Cell paper *Bringing the genetically minimal cell to life on a computer in 4D* and expands outward to landmark whole-cell-model, minimal-cell, chromosome-organization, and spatial/stochastic modeling papers that support the same modeling stack.

## Current Status

- Curated papers: 43
- Validated article PDFs on disk: 15
- Remaining papers tracked with DOI and landing page metadata for follow-up retrieval
- Non-article or mismatched downloads are quarantined under `pdfs/_rejected/` and excluded from graph provenance

## Key Files

- Master table with the requested annotation columns:
  - `metadata/whole_cell_model_papers_master_table.csv`
- Inventory with PDF status and file names:
  - `metadata/curated_papers_inventory.csv`
- Live graph inventory with current method class, organism, and validated PDF bindings:
  - `metadata/live_paper_inventory.csv`
- Sidecar review queue for blocked or rejected PDFs:
  - `metadata/pdf_sidecar_review_queue.csv`
  - `metadata/pdf_sidecar_rejected.csv`
  - `metadata/pdf_sidecar_summary.md`
- Annotation-only table:
  - `metadata/curated_papers_annotations.csv`
- Raw seed-paper reference harvest:
  - `metadata/seed_references_raw.json`
- Candidate pool before final pruning:
  - `metadata/candidate_papers.csv`

## Folder Layout

- `pdfs/`: downloaded PDFs with normalized file names
- `metadata/`: CSV and JSON outputs
- `scripts/`: reproducible harvesting/build scripts

## Notes

- The `summary`, `method summary`, `contribution`, `limitation`, and `future work` fields are concise synthesis notes for triage and reading prioritization.
- For papers without directly accessible open PDFs, the inventories still record metadata and landing pages so the set remains organized.
- Some publisher or PMC-style URLs currently return bot challenges or 403 responses from this environment; those papers remain in `landing_page_only` state until a valid local PDF is dropped into `pdfs/`.
- Local PDFs under `pdfs/` are intentionally gitignored for the GitHub repository; the metadata tables and graph still preserve their paper-level provenance.
- Re-running `python scripts/build_wcm_graph.py` rescans `pdfs/`, parses any newly added local PDFs, tries to match them back to known papers by `paper_id` prefix or DOI, and auto-ingests new papers when a dropped PDF has enough metadata to identify it.

## Graphify Knowledge Graph

- Hosted GitHub Pages view: [Whole-Cell Model Explorer](https://freakingpotato.github.io/Whole-Cell-Model-Paper-Collection/)
- Graph outputs are in `graphify-out/`
- Main interactive view: `graphify-out/graph.html`
- Raw Graphify export: `graphify-out/graph_base.html`
- Graph JSON: `graphify-out/graph.json`
- GraphML for Gephi/yEd: `graphify-out/graph.graphml`
- Static SVG: `graphify-out/graph.svg`
- Human-readable audit report: `graphify-out/GRAPH_REPORT.md`
- Rich per-paper metadata: `metadata/wcm_paper_metadata.json`

Method classes used for color grouping in the graph:

- Blue: `Mechanistic models`
- Orange: `Machine Learning Model`
- Red: `Hybrid architectures`

The enhanced viewer now supports:

- cleaner paper labels: `Author Year` on the graph, full title on hover and in the side panel
- richer node details: title, journal, year, abstract, methods summary, limitations, and future work
- alternate layouts: force, by year, and by organism
- dashed guide columns and headings in structured layouts
- subtle idle camera drift when the graph is not being manipulated
- provenance-aware limitation and future-work bullets via hover cards

GitHub Pages note:

- No file-structure reformat is required for Pages; the site can be published directly from the repository root.
- `index.html` redirects the site root to `graphify-out/graph.html`.
- `.nojekyll` is included so the viewer's relative links into `graphify_corpus/` keep working as plain static files.

Current provenance behavior:

- hover cards prefer parsed local PDF page anchors when a validated article PDF is available
- they also point to the curated note section for that paper
- they always include the DOI / landing-page link
- exact in-document section anchors are still approximate page-level anchors rather than perfect section bookmarks for every paper

Editable class assignments live in:

- `metadata/wcm_method_classes.csv`

Regenerate the graph with:

```bash
python scripts/build_wcm_graph.py
```

## Inventory

| Paper ID | Year | PDF | Title |
| --- | --- | --- | --- |
| WCM-001 | 2026 | no | Bringing the genetically minimal cell to life on a computer in 4D |
| WCM-002 | 2012 | no | A Whole-Cell Computational Model Predicts Phenotype from Genotype |
| WCM-003 | 2022 | no | Fundamental behaviors emerge from simulations of a living minimal cell |
| WCM-004 | 2009 | no | Metabolism, cell growth and the bacterial cell cycle |
| WCM-005 | 2022 | no | Integrating cellular and molecular structures and dynamics into whole-cell models |
| WCM-006 | 2024 | yes | How to build the virtual cell with artificial intelligence: Priorities and opportunities |
| WCM-007 | 2020 | no | Simultaneous cross-evaluation of heterogeneous E. coli datasets via mechanistic simulation |
| WCM-008 | 2024 | no | Cross-evaluation of E. coli’s operon structures via a whole-cell model suggests alternative cellular benefits for low- versus high-expressing operons |
| WCM-009 | 2015 | no | Toward a Whole-Cell Model of Ribosome Biogenesis: Kinetic Modeling of SSU Assembly |
| WCM-010 | 2016 | no | Ribosome biogenesis in replicating cells: Integration of experiment and theory |
| WCM-011 | 2010 | no | Creation of a Bacterial Cell Controlled by a Chemically Synthesized Genome |
| WCM-012 | 2016 | no | Design and synthesis of a minimal bacterial genome |
| WCM-013 | 2019 | no | Essential metabolism for a minimal cell |
| WCM-014 | 2019 | yes | Kinetic Modeling of the Genetic Information Processes in a Minimal Cell |
| WCM-015 | 2021 | yes | Generating Chromosome Geometries in a Minimal Cell From Cryo-Electron Tomograms and Chromosome Conformation Capture Maps |
| WCM-016 | 2023 | yes | Dynamics of chromosome organization in a minimal bacterial cell |
| WCM-017 | 2022 | no | Toward the Complete Functional Characterization of a Minimal Bacterial Proteome |
| WCM-018 | 2021 | no | Genetic requirements for cell division in a genomically minimal cell |
| WCM-019 | 2022 | yes | Metabolite Damage and Damage Control in a Minimal Genome |
| WCM-020 | 2023 | no | Adaptive evolution of a minimal organism with a synthetic genome |
| WCM-021 | 2023 | yes | Evolution of a minimal cell |
| WCM-022 | 2024 | yes | A tuneable minimal cell membrane reveals that two lipid species suffice for life |
| WCM-023 | 2023 | no | Integrative modeling of JCVI-Syn3A nucleoids with a modular approach |
| WCM-024 | 2020 | no | Integrative characterization of the near-minimal bacterium Mesoplasma florum |
| WCM-025 | 2012 | no | Lattice microbes: High-performance stochastic simulation method for the reaction-diffusion master equation |
| WCM-026 | 2014 | no | Simulation of reaction diffusion processes over biologically relevant size and time scales using multi-GPU workstations |
| WCM-027 | 2023 | yes | Molecular dynamics simulation of an entire cell |
| WCM-028 | 2021 | no | Building Structural Models of a Whole Mycoplasma Cell |
| WCM-029 | 2020 | no | In-cell architecture of an actively transcribing-translating expressome |
| WCM-030 | 2022 | no | Visualizing translation dynamics at atomic detail inside a bacterial cell |
| WCM-031 | 2010 | no | Entropy as the driver of chromosome segregation |
| WCM-032 | 2020 | no | Bacterial chromosome segregation by the ParABS system |
| WCM-033 | 2021 | yes | Mechanisms for Chromosome Segregation in Bacteria |
| WCM-034 | 2018 | no | Real-time imaging of DNA loop extrusion by condensin |
| WCM-035 | 2020 | no | Chromosome organization by one-sided and two-sided loop extrusion |
| WCM-036 | 2021 | no | DNA-loop-extruding SMC complexes can traverse one another in vivo |
| WCM-037 | 2024 | yes | Loop-extruders alter bacterial chromosome topology to direct entropic forces for segregation |
| WCM-038 | 2019 | no | RNA polymerases as moving barriers to condensin loop extrusion |
| WCM-039 | 2017 | yes | Defined chromosome structure in the genome-reduced bacterium Mycoplasma pneumoniae |
| WCM-040 | 2010 | no | Spatial organization of the flow of genetic information in bacteria |
| WCM-041 | 2012 | no | Superresolution imaging of ribosomes and RNA polymerase in live Escherichia coli cells |
| WCM-042 | 2008 | no | Modulation of Chemical Composition and Other Parameters of the Cell at Different Exponential Growth Rates |
| WCM-043 | 1998 | no | Cell Reproduction and Morphological Changes in Mycoplasma capricolum |
