# WCM Graph Build

- Papers: 56
- Downloaded PDFs: 26
- Nodes: 59
- Edges: 388
- Mechanistic models: 41
- Machine Learning Model: 4
- Hybrid architectures: 11

Outputs:
- `graphify-out/graph.html` (enhanced WCM viewer)
- `graphify-out/graph_base.html` (raw Graphify HTML export)
- `graphify-out/graph.json`
- `graphify-out/graph.graphml`
- `graphify-out/graph.svg`
- `graphify-out/GRAPH_REPORT.md`
- `graphify-out/paper_metadata.json`
- `metadata/live_paper_inventory.csv`
- `metadata/pdf_processing_status.csv`
- `metadata/pdf_sidecar_review_queue.csv`
- `metadata/pdf_sidecar_rejected.csv`
- `metadata/zotero_sync_state.json`

Color mapping in the graph:
- Blue: Mechanistic models
- Orange: Machine Learning Model
- Red: Hybrid architectures

Viewer layouts:
- Force layout
- Timeline layout (by year)
- Organism layout (manually curated organism groups)

PDF sync:
- Rebuilds treat `pdfs/` as the graph source of truth and rescan only top-level PDFs, ignoring `pdfs/_rejected/`
- Unchanged PDFs reuse cached parse results from `metadata/pdf_parse_cache.json` instead of being reparsed
- If `ZOTERO_USER_ID` and `ZOTERO_API_KEY` are set, the builder pulls PDFs from the Zotero `Whole Cell Model` collection into `pdfs/` before graph generation
- If `ZOTERO_UPLOAD_LOCAL=1` is set, local PDFs missing from the Zotero collection are uploaded back to Zotero after local matching
- Manually added PDFs with resolvable DOI/title can be auto-ingested as new paper nodes
- Bullet provenance prefers parsed local PDF page anchors and falls back to the curated note + DOI record

Regenerate with:
```bash
python scripts/build_wcm_graph.py
```
