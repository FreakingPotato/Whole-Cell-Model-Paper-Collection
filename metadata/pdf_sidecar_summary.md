# PDF Sidecar Review

- Papers still needing validated PDFs: 56
- Rejected/quarantined PDFs: 0

Files:
- `metadata/pdf_sidecar_review_queue.csv`
- `metadata/pdf_sidecar_rejected.csv`

Workflow:
1. Review `pdf_sidecar_review_queue.csv` for papers still blocked by landing-page-only access.
2. Drop a validated article PDF into `pdfs/` using the paper ID prefix when possible, for example `WCM-018_<title>.pdf`.
3. Review `pdf_sidecar_rejected.csv` for files that were quarantined because they looked like supplements, patents, or mismatched PDFs.
4. Rerun `python scripts/build_wcm_graph.py` to rescan and refresh the graph.
