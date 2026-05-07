# Whole-Cell Model Paper Collection

<p align="center">
  <a href="https://freakingpotato.github.io/Whole-Cell-Model-Paper-Collection/">
    <img src="docs/assets/live_explorer.png" alt="Whole-Cell Model Explorer — interactive graph of curated whole-cell-model papers" width="900">
  </a>
</p>

<p align="center">
  <a href="https://freakingpotato.github.io/Whole-Cell-Model-Paper-Collection/">
    <img src="https://img.shields.io/badge/%E2%96%B6%20Open%20Live%20Explorer-0f172a?style=for-the-badge&logo=githubpages&logoColor=white" alt="Open Live Explorer">
  </a>
</p>

<p align="center"><b>👉 <a href="https://freakingpotato.github.io/Whole-Cell-Model-Paper-Collection/">Click here to open the interactive Whole-Cell Model Explorer</a></b></p>

A curated literature collection for classic and closely related whole-cell-model papers, anchored on the 2026 *Cell* paper *Bringing the genetically minimal cell to life on a computer in 4D* and expanded outward to landmark whole-cell-model, minimal-cell, chromosome-organization, and spatial/stochastic modeling papers that support the same modeling stack.

The **Live Explorer** lets you browse the corpus as an interactive knowledge graph: switch between force, by-year, and by-organism layouts; hover any paper for title, abstract, methods summary, limitations, and future-work bullets; and jump straight to the DOI or the parsed PDF page anchor.

---

## ⭐ Related project — Knowledge Graph Agent

A more **systematic, agentic build** of the same idea — a general-purpose knowledge-graph builder, not limited to whole-cell-model papers — lives in a separate repo:

> **🔗 [FreakingPotato/Knowledge_Graph_Agent](https://github.com/FreakingPotato/Knowledge_Graph_Agent)**
>
> If you like what this corpus shows, that repo is where the methodology is being generalised: an agent-driven pipeline for harvesting, parsing, classifying, and graphing scientific literature across arbitrary domains. The Whole-Cell Model collection here was the first concrete dataset that motivated it.

---

## Table of Contents

- [Live Explorer](#live-explorer)
- [Current Status](#current-status)
- [Repository Layout](#repository-layout)
- [Knowledge Graph Outputs](#knowledge-graph-outputs)
- [Pipeline & Build](#pipeline--build)
- [Metadata Files](#metadata-files)
- [Notes & Conventions](#notes--conventions)

---

## Live Explorer

[**▶ Open the Live Explorer →**](https://freakingpotato.github.io/Whole-Cell-Model-Paper-Collection/)

What you can do in the explorer:

- **Four graph layouts + one evidence view** — force-directed, by year, by organism, **Virtual Cells** (groups papers by WCM completeness: Complete · Partial · Related), and **Hybrid Model Summary** (a non-graph evidence-table view, see below).
- **Cleaner labels** — `Author Year` on the graph, full title on hover and in the side panel.
- **Rich node details** — title, journal, year, citation count, abstract, methods summary, limitations, and future work.
- **Provenance-aware hovers** — limitation and future-work bullets link to parsed-PDF page anchors when an article PDF is available, plus the curated note section and the DOI / landing page.
- **Color = method class** — 🔵 Mechanistic models · 🟠 Machine Learning models · 🔴 Hybrid architectures.
- Subtle idle camera drift when the graph is not being manipulated.

### Hybrid Model Summary view

Argues — with seeded literature evidence — that progress toward predictive whole-cell models will come from coordinating three complementary ML–mechanistic strategies, not from a single best paradigm:

1. **Embedded ML–MM hybridisation** — closure, constraint, emulation/surrogate.
2. **Pipeline ML→MM systems** — curation, inference, structural learning.
3. **Parallel ML–MM comparison** — matched predictions, agreement vs disagreement, foundation-model baselines.

Each paradigm card lists claims; each claim expands into evidence bullets that trace back to specific papers in this corpus. **Click an evidence bullet** to open a modal that shows the paper title, citation, the supporting excerpt, and — when a local PDF is present — embeds the PDF with page-level navigation (`#page=N`); falls back gracefully to the DOI / landing page.

Evidence lives in the editable `metadata/hybrid_model_evidence.json` (mirrored to `graphify-out/hybrid_model_evidence.json` at build time). To extend:

```bash
# 1. Edit the JSON (see schema + adding-evidence note below).
# 2. Validate.
python scripts/validate_hybrid_evidence.py

# 3. (Re)generate the paragraph-screenshot PNGs from local PDFs.
python scripts/generate_evidence_screenshots.py            # idempotent; only renders what's new
python scripts/generate_evidence_screenshots.py --force    # re-render everything
python scripts/generate_evidence_screenshots.py --id <evidence_id>  # single point

# 4. Re-export the viewer.
python scripts/build_wcm_graph.py --stage export
```

#### How modal previews work

When a user clicks an evidence bullet, the modal renders, in priority order:

1. **Paragraph screenshot** — when `screenshot_href` points at a PNG generated by `generate_evidence_screenshots.py`. The PNG is a cropped, yellow-highlighted rendering of the supporting paragraph in the actual PDF, captured at 200 DPI. Page number and citation appear in a caption strip above the image. This is the default modern path.
2. **Embedded PDF iframe** — when only a local PDF + page anchor exist (`pdf_href` + `point.page`). Loads the PDF inline and jumps to `#page=N`.
3. **DOI / landing-page fallback** — when the paper is paywalled and we have neither a screenshot nor a local PDF. Shows the excerpt + DOI button.

Screenshot renderer dependencies: `pymupdf` (PyMuPDF / `fitz`) and `rapidfuzz` — both already pinned via `pyproject.toml`. The renderer:

- Walks every text block in the PDF, scoring each against the evidence's `text` (and `quote` if curated) using `rapidfuzz.fuzz.partial_ratio` plus a rare-keyword bonus.
- Picks the best block above threshold 60 (skipping headings/short blocks under 80 chars to avoid spurious matches).
- Renders that page at 200 DPI, draws a translucent yellow rectangle over the matched bbox, crops to the paragraph plus ~120 px context, and saves under `docs/assets/evidence/<evidence_id>.png` (≤ 350 KB; iterative downscale + 128-color palette fallback if needed).
- Updates the canonical `hybrid_model_evidence.json` in place: `screenshot_href`, `page`, `quote` (≤ 600 chars), `screenshot_status: ok`, `confidence: parsed_pdf`.
- Is idempotent — never overwrites a `confidence: manual_verified` row, never re-renders an existing `ok` row unless `--force`.
- Logs per-row outcome to `metadata/hybrid_evidence_screenshot_log.json`.

The build step (`scripts/build_wcm_graph.py --stage export`) mirrors all referenced PNGs into `graphify-out/evidence/` and rewrites the *embedded* `screenshot_href` paths so the modal images resolve under both local file:// and GitHub Pages serving.

#### Adding new evidence

Each evidence point lives under a paradigm → claim and looks like:

```json
{
  "id": "hybrid-embedded-closure-001",
  "paper_id": "WCM-046",
  "text": "Concise plain-English summary of the support claim.",
  "section": "Results",
  "page": 3,
  "quote": "Optional verbatim excerpt (≤ 1 sentence is fine).",
  "confidence": "manual_verified | parsed_pdf | metadata_only | needs_review"
}
```

- `paper_id` must match an entry in `metadata/wcm_paper_metadata.json`. To cite a paper not yet in the corpus, set `"external_reference": true` and provide a `citation_label` + `doi`.
- `page` is consumed both by the iframe fallback (`#page=N`) and by the screenshot renderer.
- `confidence` defaults to `metadata_only`. The screenshot renderer auto-bumps to `parsed_pdf` when it succeeds. Use `manual_verified` to lock a curator-edited row from being overwritten.
- `screenshot_status` (set automatically): `ok` (PNG rendered), `not_found` (text didn't match anything in the PDF), `manual_review` (PDF was image-only / OCR'd), or `no_pdf` (paper is paywalled — fall back to DOI).
- The validation script flags missing `quote`/`page` anchors as warnings (non-fatal) so curators can deepen weaker rows over time. It also warns if a `screenshot_href` points at a missing file.

GitHub Pages is published from the repository root; `index.html` redirects to `graphify-out/graph.html`, and `.nojekyll` keeps the viewer's relative links into `graphify_corpus/` working as plain static files.

---

## Current Status

- Canonical project state lives in `metadata/wcm_state.sqlite`.
- Live derived inventory currently tracks **56 papers**.
- Parsed local PDFs are tracked incrementally and reused by content hash.
- Non-article or mismatched downloads are quarantined under `pdfs/_rejected/` and excluded from graph provenance.

---

## Repository Layout

```
.
├── pdfs/                # Downloaded PDFs with normalized file names (gitignored on GitHub)
├── metadata/            # Canonical SQLite state plus derived CSV / JSON exports
├── scripts/             # Reproducible harvesting/build scripts and the modular `wcm/` pipeline package
├── graphify-out/        # Generated interactive graph + static exports
├── graphify_corpus/     # Per-paper assets the explorer links into
└── index.html           # Redirect to graphify-out/graph.html for GitHub Pages
```

---

## Knowledge Graph Outputs

All outputs land in `graphify-out/`:

| File | Purpose |
| --- | --- |
| `graph.html` | Main interactive viewer (the Live Explorer) |
| `graph_base.html` | Raw Graphify export (unenhanced) |
| `graph.json` | Graph data (nodes + edges) |
| `graph.graphml` | GraphML for Gephi / yEd |
| `graph.svg` | Static SVG snapshot |
| `GRAPH_REPORT.md` | Human-readable audit report |
| `metadata/wcm_paper_metadata.json` | Rich per-paper metadata used by the viewer |

Method-class colors come from the exported class catalog (`metadata/wcm_method_class_catalog.csv`):

- 🔵 **Blue** — Mechanistic models
- 🟠 **Orange** — Machine Learning models
- 🔴 **Red** — Hybrid architectures

Editable per-paper class assignments live in `metadata/wcm_method_classes.csv`.

---

## Pipeline & Build

Regenerate the graph or inspect state:

```bash
python scripts/build_wcm_graph.py                 # incremental rebuild
python scripts/build_wcm_graph.py --status        # show pipeline state
python scripts/build_wcm_graph.py --full-rebuild  # rebuild from scratch
```

`build_wcm_graph.py` runs an incremental SQLite-backed pipeline:

```
discover → sync → parse → match → normalize → enrich → classify → export
```

- Unchanged PDFs are not reparsed. The parser cache is keyed by file hash plus parser version, so canonical renames reuse existing parse state.
- New PDFs in `pdfs/` are auto-detected, matched to existing papers by `paper_id` prefix, DOI, and title evidence, then normalized to the canonical filename scheme.
- Auto-ingested papers receive a stable method-class key (`mechanistic`, `ml`, `hybrid`) plus editable display metadata.
- `scripts/build_wcm_collection.py` is now a bootstrap/import helper rather than the ongoing operational pipeline.

### Optional: refresh citation counts

Citations come from OpenAlex during the normal `enrich` stage. For papers OpenAlex hasn't indexed yet (preprints, brand-new journals), you can run an explicit refresh that falls back to Semantic Scholar:

```bash
python scripts/build_wcm_graph.py --refresh-citations                  # all papers, persistent cache
python scripts/build_wcm_graph.py --refresh-citations --citations-only-missing
python scripts/build_wcm_graph.py --refresh-citations --citations-rate 3.0
```

Results are persisted in `metadata/citation_counts.json` and layered in at the next graph export, so the side panel shows the count and the source (`openalex` / `semantic_scholar`). Adapted from [`Knowledge_Graph_Agent`'s citations module](https://github.com/FreakingPotato/Knowledge_Graph_Agent/blob/main/scripts/kgbuild/citations.py).

### PDF parsing — content over metadata

`extract_pdf_profile` now extracts heuristic excerpts of the **abstract**, **introduction**, **methods**, and **references** directly from each PDF (cached by file hash + parser version). When OpenAlex has no abstract for a paper, the PDF excerpt fills the side panel automatically. Reference lists land in the parse cache for future cross-paper linking.

### Optional: Zotero sync

Set environment variables to enable Zotero round-tripping:

| Variable | Effect |
| --- | --- |
| `ZOTERO_USER_ID` + `ZOTERO_API_KEY` | Builder checks the `Whole Cell Model` collection and mirrors downloadable PDF attachments into `pdfs/` |
| `ZOTERO_UPLOAD_LOCAL=1` | Local PDFs missing from the Zotero collection are prepared for upload after local matching |
| `ZOTERO_DRY_RUN=1` | Show what would happen without changing the remote library |

Zotero items without a downloadable server-side file are recorded in `metadata/zotero_sync_state.json` and stay `landing_page_only` until a binary becomes available locally or remotely.

---

## Metadata Files

| File | Contents |
| --- | --- |
| `metadata/wcm_state.sqlite` | Canonical SQLite state store |
| `metadata/whole_cell_model_papers_master_table.csv` | Master table with the requested annotation columns |
| `metadata/wcm_method_class_catalog.csv` | Method-class catalog (stable keys, display labels, definitions, colors) |
| `metadata/wcm_method_classes.csv` | Editable per-paper class assignments |
| `metadata/curated_papers_inventory.csv` | Inventory with PDF status and file names |
| `metadata/live_paper_inventory.csv` | Live graph inventory with current method class, organism, and validated PDF bindings |
| `metadata/pdf_processing_status.csv` | Local PDF processing status |
| `metadata/pdf_parse_cache.json` | Parse cache (keyed by file hash + parser version) |
| `metadata/zotero_sync_state.json` | Local Zotero sync state |
| `metadata/pdf_sidecar_review_queue.csv` | Sidecar review queue |
| `metadata/pdf_sidecar_rejected.csv` | Rejected sidecars |
| `metadata/pdf_sidecar_summary.md` | Sidecar summary |
| `metadata/curated_papers_annotations.csv` | Annotation-only table |
| `metadata/seed_references_raw.json` | Raw seed-paper reference harvest |
| `metadata/candidate_papers.csv` | Candidate pool before final pruning |

The inventory is regenerated automatically from the SQLite state store on every build.

---

## Notes & Conventions

- The `summary`, `method summary`, `contribution`, `limitation`, and `future work` fields are concise synthesis notes for triage and reading prioritization — not extracted abstracts.
- For papers without directly accessible open PDFs, the inventories still record metadata and landing pages so the set remains organized.
- `pdfs/` is the source of truth for graph generation. The builder only scans top-level PDFs there and ignores `pdfs/_rejected/`.
- Local PDFs under `pdfs/` are intentionally gitignored for the GitHub repository; the metadata tables and graph still preserve their paper-level provenance.
- Hover-card section anchors are page-level approximations rather than perfect section bookmarks for every paper.
