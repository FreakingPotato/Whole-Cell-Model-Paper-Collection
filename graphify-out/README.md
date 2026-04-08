# WCM Graph Build

- Papers: 43
- Downloaded PDFs: 15
- Nodes: 46
- Edges: 327
- Mechanistic models: 36
- Machine Learning Model: 1
- Hybrid architectures: 6

Outputs:
- `graphify-out/graph.html` (enhanced WCM viewer)
- `graphify-out/graph_base.html` (raw Graphify HTML export)
- `graphify-out/graph.json`
- `graphify-out/graph.graphml`
- `graphify-out/graph.svg`
- `graphify-out/GRAPH_REPORT.md`
- `graphify-out/paper_metadata.json`
- `metadata/live_paper_inventory.csv`
- `metadata/pdf_sidecar_review_queue.csv`
- `metadata/pdf_sidecar_rejected.csv`

Color mapping in the graph:
- Blue: Mechanistic models
- Orange: Machine Learning Model
- Red: Hybrid architectures

Viewer layouts:
- Force layout
- Timeline layout (by year)
- Organism layout (manually curated organism groups)

PDF sync:
- Rebuilds rescan `pdfs/`, parse any new local PDFs, and try to match them back to papers by DOI/title
- Manually added PDFs with resolvable DOI/title can be auto-ingested as new paper nodes
- Bullet provenance prefers parsed local PDF page anchors and falls back to the curated note + DOI record

Regenerate with:
```bash
python scripts/build_wcm_graph.py
```
