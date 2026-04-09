# Whole-Cell Model Paper Collection

This folder now contains a first-pass curated literature collection for classic and closely related whole-cell-model papers.

The collection is anchored on the 2026 Cell paper *Bringing the genetically minimal cell to life on a computer in 4D* and expands outward to landmark whole-cell-model, minimal-cell, chromosome-organization, and spatial/stochastic modeling papers that support the same modeling stack.

## Current Status

- Canonical project state now lives in `metadata/wcm_state.sqlite`
- Live derived inventory currently tracks 56 papers
- Parsed local PDFs are tracked incrementally and reused by content hash
- Non-article or mismatched downloads are quarantined under `pdfs/_rejected/` and excluded from graph provenance

## Key Files

- Canonical SQLite state store:
  - `metadata/wcm_state.sqlite`
- Master table with the requested annotation columns:
  - `metadata/whole_cell_model_papers_master_table.csv`
- Method-class catalog with stable keys, display labels, definitions, and colors:
  - `metadata/wcm_method_class_catalog.csv`
- Inventory with PDF status and file names:
  - `metadata/curated_papers_inventory.csv`
- Live graph inventory with current method class, organism, and validated PDF bindings:
  - `metadata/live_paper_inventory.csv`
- Local PDF processing status and parse cache:
  - `metadata/pdf_processing_status.csv`
  - `metadata/pdf_parse_cache.json`
- Local Zotero sync state:
  - `metadata/zotero_sync_state.json`
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
- `metadata/`: canonical SQLite state plus derived CSV and JSON exports
- `scripts/`: reproducible harvesting/build scripts and the modular `wcm/` pipeline package

## Notes

- The `summary`, `method summary`, `contribution`, `limitation`, and `future work` fields are concise synthesis notes for triage and reading prioritization.
- For papers without directly accessible open PDFs, the inventories still record metadata and landing pages so the set remains organized.
- `pdfs/` is the source of truth for graph generation. The builder only scans top-level PDFs there and ignores `pdfs/_rejected/`.
- Local PDFs under `pdfs/` are intentionally gitignored for the GitHub repository; the metadata tables and graph still preserve their paper-level provenance.
- Re-running `python scripts/build_wcm_graph.py` now runs an incremental SQLite-backed pipeline: discover -> sync -> parse -> match -> normalize -> enrich -> classify -> export.
- Unchanged PDFs are not reparsed. The parser cache is keyed by file hash plus parser version, so canonical renames reuse existing parse state.
- New PDFs in `pdfs/` are auto-detected, matched to existing papers by `paper_id` prefix, DOI, and title evidence, then normalized to the canonical filename scheme.
- Auto-ingested papers receive a stable method-class key (`mechanistic`, `ml`, `hybrid`) plus editable display metadata for the graph.
- If `ZOTERO_USER_ID` and `ZOTERO_API_KEY` are set, the builder also checks the Zotero `Whole Cell Model` collection before graph generation and tries to mirror downloadable PDF attachments into `pdfs/`.
- If `ZOTERO_UPLOAD_LOCAL=1` is set, local PDFs that are missing from the Zotero collection are prepared for upload back to Zotero after local matching. `ZOTERO_DRY_RUN=1` shows what would happen without changing the remote library.
- Some Zotero attachments may exist as metadata records without a downloadable server-side file yet. In that case the builder records the Zotero item keys in `metadata/zotero_sync_state.json` and leaves the paper in `landing_page_only` until the binary becomes available or a local PDF is present.
- `scripts/build_wcm_collection.py` should now be treated as a bootstrap/import helper rather than the ongoing operational pipeline.

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

Method classes used for color grouping in the graph now come from the exported class catalog:

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

Editable per-paper class assignments live in:

- `metadata/wcm_method_classes.csv`

Class display labels, definitions, and colors live in:

- `metadata/wcm_method_class_catalog.csv`

Regenerate the graph or inspect state with:

```bash
python scripts/build_wcm_graph.py
python scripts/build_wcm_graph.py --status
python scripts/build_wcm_graph.py --full-rebuild
```

## Inventory

The live inventory is now generated automatically from the SQLite state store. Use:

- `metadata/live_paper_inventory.csv` for the current graph-facing inventory
- `metadata/pdf_processing_status.csv` for per-paper parse and remote-sync status
