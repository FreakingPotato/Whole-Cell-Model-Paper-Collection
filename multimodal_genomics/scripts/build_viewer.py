#!/usr/bin/env python3
"""Build a self-contained ``multimodal_genomics/index.html`` viewer.

Reads:
  - ontology.json    (paradigms, claims, rubric, viewer config)
  - evidence.json    (curated evidence points with screenshots[])
  - papers.json      (paper registry: title, authors, year, journal, doi, citations)

Produces a single-file static HTML viewer with the same UX patterns proven
in the WCM Hybrid Model Summary view: paradigm cards → expandable claims →
ranked evidence rows → click-to-open modal with multi-sentence screenshot
stack + rubric breakdown + DOI link, plus a TSV download button for
spreadsheet export.

The HTML mirrors the assets/evidence/*.png paths into multimodal_genomics/
so the viewer is portable: serve the multimodal_genomics/ directory and
nothing breaks.

Usage::

    python multimodal_genomics/scripts/build_viewer.py
    python multimodal_genomics/scripts/build_viewer.py --out custom.html
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOPIC_DIR = ROOT / "multimodal_genomics"
META_DIR = TOPIC_DIR / "metadata"

ONTOLOGY_FILE = META_DIR / "ontology.json"
EVIDENCE_FILE = META_DIR / "evidence.json"
PAPERS_FILE = META_DIR / "papers.json"
DEFAULT_OUT = TOPIC_DIR / "index.html"

# Embedded asset path used inside the HTML, relative to the file itself.
ASSETS_REL = "assets/evidence"


def _load_json(path: Path, default: dict | None = None) -> dict:
    if not path.is_file():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"WARNING: {path} is not valid JSON ({exc})", file=sys.stderr)
        return default if default is not None else {}


def _registry(papers_payload: dict) -> dict[str, dict]:
    p = papers_payload.get("papers")
    if isinstance(p, dict):
        return p
    if isinstance(p, list):
        return {x.get("paper_id"): x for x in p if x.get("paper_id")}
    return {}


def _embedded_evidence(ev: dict, out_dir: Path) -> dict:
    """Rewrite ``screenshots[].href`` paths so they're relative to the
    viewer file (which sits at multimodal_genomics/index.html), and ensure
    the PNG files actually live next to the viewer.

    Source paths in evidence.json look like
    ``multimodal_genomics/assets/evidence/<file>.png``; the viewer needs
    ``assets/evidence/<file>.png``.
    """
    out = json.loads(json.dumps(ev))  # deep copy
    for paradigm in out.get("paradigms", []) or []:
        for claim in paradigm.get("claims", []) or []:
            for point in claim.get("evidence_points", []) or []:
                # multi-sentence stack
                for s in point.get("screenshots", []) or []:
                    href = s.get("href")
                    if href and "/assets/evidence/" in href:
                        s["href"] = href.split("/assets/evidence/", 1)[1]
                        s["href"] = f"{ASSETS_REL}/{s['href']}"
                # legacy single-screenshot fallback
                href = point.get("screenshot_href")
                if href and "/assets/evidence/" in href:
                    point["screenshot_href"] = (
                        f"{ASSETS_REL}/{href.split('/assets/evidence/', 1)[1]}"
                    )
    return out


# Journal-IF tier table shared with rank_evidence.py. Lower number =
# higher impact. Used to colour-pip evidence rows.
JOURNAL_TIER: list[tuple[str, int]] = [
    ("nature methods", 2),
    ("nature biotechnology", 2),
    ("nature computational science", 2),
    ("nature machine intelligence", 2),
    ("nature reviews", 2),
    ("nature genetics", 2),
    ("nature communications", 2),
    ("nature", 1),
    ("science advances", 2),
    ("science", 1),
    ("cell systems", 2),
    ("cell reports", 2),
    ("cell", 1),
    ("pnas", 2),
    ("proceedings of the national academy of sciences", 2),
    ("genome biology", 3),
    ("genome research", 3),
    ("nucleic acids research", 3),
    ("nar genomics", 3),
    ("bioinformatics", 3),
    ("briefings in bioinformatics", 3),
    ("plos computational biology", 3),
    ("molecular systems biology", 3),
    ("elife", 3),
    ("journal of chemical information", 3),
    ("neurips", 3),
    ("advances in neural information", 3),
    ("icml", 3),
    ("international conference on machine learning", 3),
    ("iclr", 3),
    ("international conference on learning representations", 3),
    ("acl", 3),
    ("emnlp", 3),
    ("naacl", 3),
    ("cvpr", 3),
    ("aaai", 3),
    ("journal of machine learning research", 3),
    ("transactions on machine learning research", 3),
    ("arxiv", 5),
    ("biorxiv", 5),
    ("medrxiv", 5),
]


def journal_tier(journal: str | None) -> int:
    if not journal:
        return 4
    j = journal.lower()
    for needle, tier in sorted(JOURNAL_TIER, key=lambda kv: -len(kv[0])):
        if needle in j:
            return tier
    return 4


def _ensure_assets(papers_dir_evidence: Path, out_dir: Path) -> None:
    """No copy required: viewer at multimodal_genomics/index.html shares
    the same parent directory as multimodal_genomics/assets/evidence/.
    But validate that the asset directory exists and warn on missing PNGs."""
    target = out_dir.parent / ASSETS_REL
    if not target.is_dir():
        print(f"WARNING: {target} does not exist — viewer will show empty stacks",
              file=sys.stderr)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__VIEWER_TITLE__</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: Inter, system-ui, -apple-system, sans-serif; background: linear-gradient(180deg, #0a0f1c 0%, #0b1220 100%); color: #e2e8f0; min-height: 100vh; }
  header { padding: 28px 32px 22px; border-bottom: 1px solid rgba(148, 163, 184, 0.12); background: rgba(15, 23, 42, 0.65); backdrop-filter: blur(8px); position: sticky; top: 0; z-index: 5; }
  h1 { margin: 0; font-size: 22px; line-height: 1.25; color: #f1f5f9; letter-spacing: -0.01em; }
  header .subtitle { margin: 6px 0 0; color: #94a3b8; font-size: 13px; max-width: 820px; line-height: 1.55; }
  .topic-pill { display: inline-block; background: rgba(34, 211, 238, 0.12); color: __PRIMARY__; border: 1px solid rgba(34, 211, 238, 0.4); padding: 3px 10px; border-radius: 999px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; }
  main { padding: 24px 32px 96px; max-width: 1180px; margin: 0 auto; }
  .thesis { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 18px 22px; margin: 18px 0 30px; color: #cbd5e1; font-size: 14px; line-height: 1.65; border-left: 3px solid __PRIMARY__; }
  .paradigms { display: flex; flex-direction: column; gap: 22px; }
  .paradigm-card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 20px 22px; }
  .paradigm-header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 6px; }
  .paradigm-header h2 { margin: 0; font-size: 17px; color: #f1f5f9; }
  .paradigm-id { font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: __PRIMARY__; background: rgba(34, 211, 238, 0.12); border: 1px solid rgba(34, 211, 238, 0.4); padding: 2px 8px; border-radius: 999px; }
  .paradigm-summary { margin: 4px 0 14px; color: #cbd5e1; font-size: 13px; line-height: 1.6; }
  .claim-row { border-top: 1px solid #1f2937; padding: 14px 0; }
  .claim-row:first-of-type { border-top: 1px solid #2a3445; }
  .claim-head { display: flex; gap: 14px; align-items: flex-start; cursor: pointer; padding: 4px 6px; border-radius: 8px; }
  .claim-head:hover { background: rgba(56, 189, 248, 0.06); }
  .claim-toggle { color: __PRIMARY__; font-size: 12px; flex: 0 0 14px; user-select: none; }
  .claim-subtype { flex: 0 0 240px; color: __PRIMARY__; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; padding-top: 1px; }
  .claim-text { flex: 1 1 auto; font-size: 14px; color: #e5e7eb; line-height: 1.5; }
  .claim-papercount { flex: 0 0 auto; font-size: 11px; color: #94a3b8; padding: 2px 8px; border: 1px solid #334155; border-radius: 999px; }
  .evidence-list { margin: 12px 0 0 30px; padding: 0; list-style: none; display: none; flex-direction: column; gap: 8px; }
  .claim-row.expanded .evidence-list { display: flex; }
  .claim-row.expanded .claim-toggle::before { content: '▾'; }
  .claim-row:not(.expanded) .claim-toggle::before { content: '▸'; }
  .evidence-item { background: #0f172a; border: 1px solid #1f2937; border-radius: 10px; padding: 10px 14px; cursor: pointer; transition: border-color 120ms; }
  .evidence-item:hover { border-color: __PRIMARY__; }
  .evidence-text { margin: 0 0 6px; font-size: 13px; color: #e5e7eb; line-height: 1.55; }
  .evidence-paper { font-size: 12px; color: #94a3b8; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  .evidence-paper .citation-pill { color: #7dd3fc; font-weight: 500; }
  .evidence-paper .journal-pill { color: __SECONDARY__; font-style: italic; }
  .evidence-paper .citation-count-pill { color: #f0abfc; padding: 1px 6px; border: 1px solid rgba(240, 171, 252, 0.3); border-radius: 999px; font-size: 10px; }
  .evidence-paper .pdf-pill { color: #34d399; }
  .evidence-paper .nopdf-pill { color: #fbbf24; }
  .evidence-paper .confidence-pill { color: #cbd5e1; padding: 1px 6px; border: 1px solid #334155; border-radius: 999px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
  .evidence-paper .quality-primary { color: __PRIMARY__; border: 1px solid rgba(34, 211, 238, 0.4); padding: 1px 6px; border-radius: 999px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
  .evidence-paper .quality-secondary { color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.35); padding: 1px 6px; border-radius: 999px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
  .evidence-paper .multi-claim-pill { color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.35); padding: 1px 6px; border-radius: 999px; font-size: 10px; }
  .evidence-rank-pill { display: inline-block; min-width: 22px; text-align: center; color: #64748b; border: 1px solid #334155; padding: 1px 6px; border-radius: 999px; font-size: 10px; margin-right: 8px; vertical-align: middle; }
  .evidence-rubric { font-family: ui-monospace, monospace; color: #cbd5e1; background: #111827; border: 1px solid #1f2937; padding: 3px 8px; border-radius: 6px; font-size: 10px; margin-top: 4px; display: inline-block; }
  /* Modal */
  #modal { display: none; position: fixed; inset: 0; z-index: 100; background: rgba(2, 6, 23, 0.78); backdrop-filter: blur(6px); align-items: center; justify-content: center; padding: 24px; }
  #modal[data-open="true"] { display: flex; }
  #modal .card { background: #0b1220; border: 1px solid #334155; border-radius: 14px; width: min(1180px, 100%); height: min(86vh, 920px); display: grid; grid-template-columns: minmax(0, 360px) minmax(0, 1fr); overflow: hidden; box-shadow: 0 30px 80px rgba(0, 0, 0, 0.55); }
  #modal .info { padding: 20px 22px; overflow-y: auto; border-right: 1px solid #1e293b; }
  #modal .pdf { background: #020617; position: relative; overflow-y: auto; padding: 14px; gap: 14px; flex-direction: column; }
  #modal .pdf.stack-mode { display: flex; }
  #modal .pdf-fallback { color: #cbd5e1; padding: 24px; font-size: 13px; line-height: 1.65; }
  #modal h3 { margin: 0 0 4px; font-size: 16px; color: #f1f5f9; }
  #modal .citation { color: #7dd3fc; font-size: 13px; margin-bottom: 14px; }
  #modal .callout { background: #111827; border: 1px solid #1f2937; border-left: 3px solid __PRIMARY__; padding: 10px 12px; border-radius: 8px; font-size: 13px; line-height: 1.6; color: #e5e7eb; margin-bottom: 12px; }
  #modal .meta { font-size: 12px; color: #94a3b8; line-height: 1.7; margin-bottom: 14px; }
  #modal .meta strong { color: #cbd5e1; }
  #modal .actions { display: flex; flex-direction: column; gap: 8px; }
  #modal .actions a { display: inline-flex; align-items: center; gap: 8px; background: #111827; border: 1px solid #334155; color: #e2e8f0; padding: 8px 12px; border-radius: 10px; font-size: 12px; text-decoration: none; }
  #modal .actions a:hover { border-color: __PRIMARY__; color: #f1f5f9; }
  #modal .close { position: absolute; top: 12px; right: 14px; background: rgba(15, 23, 42, 0.8); color: #e2e8f0; border: 1px solid #334155; border-radius: 999px; width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 14px; z-index: 5; }
  #modal .rubric-table { display: grid; grid-template-columns: 130px 30px 1fr; gap: 4px 10px; font-size: 11px; color: #cbd5e1; margin: 12px 0 6px; }
  #modal .rubric-table .label { color: __PRIMARY__; text-transform: uppercase; letter-spacing: 0.05em; font-size: 10px; padding-top: 1px; }
  #modal .rubric-table .score { font-family: ui-monospace, monospace; text-align: center; color: #f1f5f9; font-weight: 600; }
  #modal .rubric-table .rationale { color: #cbd5e1; line-height: 1.5; }
  #modal .rubric-total { font-family: ui-monospace, monospace; color: #f1f5f9; padding: 4px 10px; background: #111827; border: 1px solid #1f2937; border-radius: 6px; display: inline-block; font-size: 11px; margin: 6px 0 14px; }
  .screenshot-card { background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 10px; }
  .screenshot-caption { display: inline-block; background: rgba(15, 23, 42, 0.85); border: 1px solid #334155; color: #cbd5e1; font-size: 11px; padding: 3px 8px; border-radius: 999px; margin-bottom: 8px; }
  .screenshot-card img { width: 100%; height: auto; display: block; border-radius: 6px; background: #020617; }
  .screenshot-sentence { color: #cbd5e1; font-size: 12px; line-height: 1.55; margin: 8px 4px 0; padding: 0 0 0 8px; border-left: 2px solid rgba(34, 211, 238, 0.4); font-style: italic; }
  /* TSV download button */
  #download-tsv { position: fixed; right: 28px; bottom: 28px; z-index: 9; background: linear-gradient(180deg, #1e293b, #0b1220); color: #e2e8f0; border: 1px solid __PRIMARY__; border-radius: 999px; padding: 11px 18px; font-size: 13px; cursor: pointer; box-shadow: 0 4px 14px rgba(2, 6, 23, 0.45), 0 0 0 1px rgba(34, 211, 238, 0.18); display: inline-flex; align-items: center; gap: 8px; font-family: inherit; }
  #download-tsv:hover { color: #fff; box-shadow: 0 6px 20px rgba(2, 6, 23, 0.55), 0 0 0 1px rgba(34, 211, 238, 0.4); }
  /* Architecture footer */
  footer { padding: 16px 32px; border-top: 1px solid rgba(148, 163, 184, 0.1); color: #64748b; font-size: 11px; text-align: center; }
  footer a { color: #7dd3fc; text-decoration: none; }
  @media (max-width: 980px) {
    .claim-head { flex-direction: column; gap: 6px; }
    .claim-subtype { flex: 0 0 auto; }
    #modal .card { grid-template-columns: 1fr; grid-template-rows: auto 1fr; height: 92vh; }
    #modal .info { border-right: 0; border-bottom: 1px solid #1e293b; max-height: 35vh; }
  }
</style>
</head>
<body>
<header>
  <span class="topic-pill">__TOPIC_LABEL__</span>
  <h1>__VIEWER_TITLE__</h1>
  <p class="subtitle">__SUBTITLE__</p>
</header>
<main>
  <div class="thesis">__THESIS__</div>
  <div id="root" class="paradigms"></div>
</main>
<button id="download-tsv" type="button" title="Download evidence metadata as TSV (Google Sheets / Excel)">
  <span aria-hidden="true">⬇</span> Download metadata (TSV)
</button>
<div id="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
  <div class="card">
    <div class="info"></div>
    <div class="pdf"><div class="pdf-fallback">Loading…</div></div>
    <button class="close" type="button" aria-label="Close">×</button>
  </div>
</div>
<footer>
  Built with the agentic knowledge-graph pipeline: Architect → Discoverer → Scorers → Reviewer → Ranker → Fetcher → Linker → QC → Auditor.
  Generated __GENERATED_AT__ ·
  __N_PAPERS__ papers, __N_EVIDENCE__ evidence points, __N_PNGS__ paragraph screenshots.
</footer>
<script>
const ONTOLOGY = __ONTOLOGY__;
const EVIDENCE = __EVIDENCE__;
const PAPERS = __PAPERS__;

const RUBRIC_LABELS = {
  useful_outcomes: "Useful outcomes",
  immediate_benefit: "Immediate benefit",
  plausible: "Plausible",
  scalable: "Scalable",
  how_to_validate: "How to validate"
};

function escapeHtml(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function lookupPaper(pid) {
  if (!pid || !PAPERS || !PAPERS.papers) return null;
  const rec = PAPERS.papers[pid];
  if (!rec) return null;
  const authors = Array.isArray(rec.authors) ? rec.authors : [];
  const last = authors.length ? (authors[0].split(/\s+/).slice(-1)[0]) : "";
  const displayLabel = last && rec.year ? `${last} ${rec.year}` : (last || pid);
  return {
    paper_id: pid,
    title: rec.title || "",
    authors: authors,
    year: rec.year || "",
    journal: rec.journal || "",
    doi: rec.doi || "",
    landing_page_url: rec.doi ? `https://doi.org/${rec.doi}` : "",
    cited_by_count: rec.cited_by_count || 0,
    display_label: displayLabel,
    abstract: rec.abstract || ""
  };
}

function formatCitations(n) {
  if (typeof n !== "number" || !Number.isFinite(n) || n <= 0) return "";
  return n.toLocaleString("en-US");
}

const PAPER_CLAIMS = (() => {
  const m = {};
  if (!EVIDENCE || !Array.isArray(EVIDENCE.paradigms)) return m;
  EVIDENCE.paradigms.forEach(p => {
    (p.claims || []).forEach(c => {
      (c.evidence_points || []).forEach(pt => {
        if (!pt.paper_id) return;
        if (!m[pt.paper_id]) m[pt.paper_id] = new Set();
        m[pt.paper_id].add(c.id);
      });
    });
  });
  return m;
})();

const EVIDENCE_BY_ID = (() => {
  const m = {};
  if (!EVIDENCE || !Array.isArray(EVIDENCE.paradigms)) return m;
  EVIDENCE.paradigms.forEach(p => {
    (p.claims || []).forEach(c => {
      (c.evidence_points || []).forEach(pt => {
        m[pt.id] = { paradigm: p, claim: c, point: pt };
      });
    });
  });
  return m;
})();

function rubricSummaryPill(point) {
  const r = point.rubric;
  if (!r) return "";
  const u = r.useful_outcomes && r.useful_outcomes.score;
  const i = r.immediate_benefit && r.immediate_benefit.score;
  const p = r.plausible && r.plausible.score;
  const s = r.scalable && r.scalable.score;
  const v = r.how_to_validate && r.how_to_validate.score;
  const wt = (typeof point.weighted_total === "number") ? point.weighted_total.toFixed(1) : "";
  return `<span class="evidence-rubric" title="Rubric breakdown — click for details">U:${u||0} I:${i||0} P:${p||0} S:${s||0} V:${v||0} → ${wt}</span>`;
}

function renderEvidenceItem(point, claim, paradigm) {
  const paper = lookupPaper(point.paper_id) || {};
  const citation = paper.display_label || point.paper_id || "Unknown paper";
  const journal = paper.journal || "";
  const year = paper.year || "";
  const cited = paper.cited_by_count || 0;
  const citePill = (cited > 0)
    ? `<span class="citation-count-pill">${formatCitations(cited)} citations</span>` : "";
  const conf = point.confidence || "metadata_only";
  const rank = (typeof point.rank_within_claim === "number")
    ? `<span class="evidence-rank-pill">#${point.rank_within_claim}</span>` : "";
  let qualityPill = "";
  if (point.claim_match_quality === "primary") qualityPill = '<span class="quality-primary">primary</span>';
  else if (point.claim_match_quality === "secondary") qualityPill = '<span class="quality-secondary">secondary</span>';
  const claimSet = PAPER_CLAIMS[point.paper_id];
  const multiClaim = (claimSet && claimSet.size >= 2)
    ? `<span class="multi-claim-pill" title="Paper supports multiple claims">Matches ${claimSet.size} claims</span>` : "";
  const hasScreens = Array.isArray(point.screenshots) && point.screenshots.length > 0;
  const pdfChip = hasScreens
    ? `<span class="pdf-pill">📷 ${point.screenshots.length} highlights</span>`
    : `<span class="nopdf-pill">DOI / landing page only</span>`;
  return `
    <li class="evidence-item" data-evidence-id="${escapeHtml(point.id)}">
      <p class="evidence-text">${rank}${escapeHtml(point.text || "")}</p>
      <div class="evidence-paper">
        <span class="citation-pill">${escapeHtml(citation)}</span>
        ${journal ? `<span class="journal-pill">${escapeHtml(journal)}</span>` : ""}
        ${year ? `<span>${escapeHtml(String(year))}</span>` : ""}
        ${citePill}
        ${pdfChip}
        <span class="confidence-pill">${escapeHtml(conf)}</span>
        ${qualityPill}
        ${multiClaim}
      </div>
      ${rubricSummaryPill(point)}
    </li>`;
}

function renderClaim(claim, paradigm) {
  const points = claim.evidence_points || [];
  const sorted = [...points].sort((a, b) => (a.rank_within_claim || 99) - (b.rank_within_claim || 99));
  const items = sorted.map(p => renderEvidenceItem(p, claim, paradigm)).join("");
  return `
    <div class="claim-row" data-claim="${escapeHtml(claim.id)}">
      <div class="claim-head" data-action="toggle-claim">
        <span class="claim-toggle"></span>
        <span class="claim-subtype">${escapeHtml(claim.subtype || "")}</span>
        <span class="claim-text">${escapeHtml(claim.claim || "")}</span>
        <span class="claim-papercount">${points.length} ${points.length === 1 ? "paper" : "papers"}</span>
      </div>
      <ul class="evidence-list">${items}</ul>
    </div>`;
}

function renderParadigm(paradigm) {
  const claims = (paradigm.claims || []).map(c => renderClaim(c, paradigm)).join("");
  return `
    <section class="paradigm-card">
      <div class="paradigm-header">
        <h2>${escapeHtml(paradigm.label || paradigm.id)}</h2>
        <span class="paradigm-id">${escapeHtml(paradigm.id || "")}</span>
      </div>
      <p class="paradigm-summary">${escapeHtml(paradigm.summary || "")}</p>
      ${claims}
    </section>`;
}

function mount() {
  const root = document.getElementById("root");
  if (!EVIDENCE || !Array.isArray(EVIDENCE.paradigms)) {
    root.innerHTML = `<div class="thesis">No evidence yet — populate <code>multimodal_genomics/metadata/evidence.json</code> and rebuild.</div>`;
    return;
  }
  root.innerHTML = EVIDENCE.paradigms.map(renderParadigm).join("");
  // Auto-expand first claim of each paradigm.
  document.querySelectorAll(".paradigm-card").forEach(c => {
    const first = c.querySelector(".claim-row");
    if (first) first.classList.add("expanded");
  });
  root.addEventListener("click", ev => {
    const head = ev.target.closest('[data-action="toggle-claim"]');
    if (head) {
      head.parentElement.classList.toggle("expanded");
      return;
    }
    const item = ev.target.closest(".evidence-item");
    if (item) openModal(item.dataset.evidenceId);
  });
}

const modal = document.getElementById("modal");

function openModal(evidenceId) {
  const r = EVIDENCE_BY_ID[evidenceId];
  if (!r) return;
  const { paradigm, claim, point } = r;
  const paper = lookupPaper(point.paper_id) || {};
  const url = paper.landing_page_url || (paper.doi ? `https://doi.org/${paper.doi}` : "");

  const info = modal.querySelector(".info");
  const r2 = point.rubric || {};
  const rubricRows = ["useful_outcomes","immediate_benefit","plausible","scalable","how_to_validate"]
    .filter(k => r2[k]).map(k => {
      const d = r2[k];
      return `<div class="label">${escapeHtml(RUBRIC_LABELS[k] || k)}</div>
        <div class="score">${escapeHtml(String(d.score || 0))}</div>
        <div class="rationale">${escapeHtml(d.rationale || "")}</div>`;
    }).join("");
  const rubricBlock = rubricRows ? `<div class="rubric-table">${rubricRows}</div>
    <div class="rubric-total">weighted_total = ${typeof point.weighted_total === "number" ? point.weighted_total.toFixed(1) : "?"} / 28</div>` : "";

  const claimSet = PAPER_CLAIMS[point.paper_id];
  let alsoMatches = "";
  if (claimSet && claimSet.size >= 2) {
    const others = [...claimSet].filter(c => c !== claim.id);
    if (others.length) alsoMatches = `<details class="modal-also-claims" style="margin-top:14px;font-size:11px;color:#cbd5e1;">
      <summary style="cursor:pointer;color:#fbbf24;">Also matches ${others.length} other claim${others.length > 1 ? "s" : ""}</summary>
      <ul style="margin:6px 0 0 20px;padding:0;color:#94a3b8;">${others.map(c => `<li>${escapeHtml(c)}</li>`).join("")}</ul>
    </details>`;
  }

  info.innerHTML = `
    <h3 id="modal-title">${escapeHtml(paper.title || "Evidence")}</h3>
    <div class="citation">${escapeHtml(paper.display_label || "")}${paper.journal ? " · " + escapeHtml(paper.journal) : ""}${paper.year ? " · " + escapeHtml(String(paper.year)) : ""}</div>
    <div class="callout"><strong style="color:#7dd3fc">${escapeHtml(paradigm.label || "")} → ${escapeHtml(claim.subtype || "")}</strong><br>${escapeHtml(point.text || "")}</div>
    ${rubricBlock}
    <div class="meta">
      ${paper.cited_by_count ? `<div><strong>Cited by:</strong> ${formatCitations(paper.cited_by_count)}</div>` : ""}
      ${paper.doi ? `<div><strong>DOI:</strong> ${escapeHtml(paper.doi)}</div>` : ""}
      <div><strong>Provenance:</strong> ${escapeHtml(point.confidence || "metadata_only")}</div>
    </div>
    <div class="actions">
      ${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">Open DOI / landing page ↗</a>` : ""}
    </div>
    ${alsoMatches}
  `;

  const pdfPanel = modal.querySelector(".pdf");
  if (Array.isArray(point.screenshots) && point.screenshots.length > 0) {
    pdfPanel.classList.add("stack-mode");
    const cards = point.screenshots.map((s, idx) => {
      const numCircle = idx < 9 ? `&#${10112 + idx};` : `${idx + 1}.`;
      const caption = s.page
        ? `<span class="screenshot-caption">${numCircle} Page ${s.page}${s.section_hint ? " · " + escapeHtml(s.section_hint) : ""}</span>`
        : `<span class="screenshot-caption">${numCircle}</span>`;
      const sentences = (s.sentences || []).map(sn => `<p class="screenshot-sentence">"${escapeHtml(sn.text || "")}"</p>`).join("");
      return `<div class="screenshot-card">${caption}<img src="${escapeHtml(s.href || "")}" alt="Supporting paragraph for ${escapeHtml(paper.title || "")}" loading="${idx === 0 ? "eager" : "lazy"}">${sentences}</div>`;
    }).join("");
    pdfPanel.innerHTML = cards;
  } else {
    pdfPanel.classList.remove("stack-mode");
    pdfPanel.innerHTML = `<div class="pdf-fallback">
      <p><strong>Local PDF unavailable.</strong></p>
      <p>${escapeHtml(point.text || "")}</p>
      <p style="margin-top:12px;">Use the DOI / landing-page link to read the full source.</p>
    </div>`;
  }
  modal.setAttribute("data-open", "true");
}

function closeModal() {
  modal.removeAttribute("data-open");
  const pdfPanel = modal.querySelector(".pdf");
  pdfPanel.classList.remove("stack-mode");
  pdfPanel.innerHTML = '<div class="pdf-fallback">Loading…</div>';
}

modal.addEventListener("click", e => {
  if (e.target === modal || e.target.classList.contains("close")) closeModal();
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && modal.getAttribute("data-open") === "true") closeModal();
});

// ---- TSV download (same columns as the WCM Hybrid view) ----
function _csvField(s) { return String(s == null ? "" : s).replace(/[\t\r\n]+/g, " ").trim(); }
function _topRationales(point, n) {
  if (!point || !point.rubric) return "";
  const order = ["useful_outcomes","immediate_benefit","plausible","scalable","how_to_validate"];
  const entries = order.map(d => {
    const r = point.rubric[d];
    if (!r) return null;
    return { dim: d, score: r.score || 0, rationale: r.rationale || "" };
  }).filter(Boolean);
  entries.sort((a, b) => b.score - a.score);
  return entries.slice(0, n).map(e => `[${RUBRIC_LABELS[e.dim] || e.dim} ${e.score}] ${e.rationale}`).join(" | ");
}
function buildTSV() {
  const cols = ["Title","Authors","Group","Year","URL","Reviewed by","Model paradigm","Key methods/results","Core contribution to topic",
                "Paper ID","Claim ID","Subtype","Journal","Cited by count","Match quality","Weighted total","Rank within claim","Confidence"];
  const rows = [cols];
  if (!EVIDENCE || !Array.isArray(EVIDENCE.paradigms)) return rows.map(r => r.join("\t")).join("\n");
  for (const paradigm of EVIDENCE.paradigms) {
    for (const claim of (paradigm.claims || [])) {
      for (const point of (claim.evidence_points || [])) {
        const paper = lookupPaper(point.paper_id) || {};
        const authors = Array.isArray(paper.authors) ? paper.authors.join(", ") : "";
        const url = paper.landing_page_url || (paper.doi ? `https://doi.org/${paper.doi}` : "");
        const reviewed = point.scored_by || point.promoted_by || "agent pipeline";
        rows.push([
          _csvField(paper.title || ""),
          _csvField(authors),
          _csvField(paradigm.id || ""),
          _csvField(paper.year || ""),
          _csvField(url),
          _csvField(reviewed),
          _csvField(paradigm.label || ""),
          _csvField(_topRationales(point, 2)),
          _csvField(point.text || ""),
          _csvField(point.paper_id || ""),
          _csvField(claim.id || ""),
          _csvField(claim.subtype || ""),
          _csvField(paper.journal || ""),
          _csvField(typeof paper.cited_by_count === "number" ? paper.cited_by_count : ""),
          _csvField(point.claim_match_quality || ""),
          _csvField(typeof point.weighted_total === "number" ? point.weighted_total : ""),
          _csvField(typeof point.rank_within_claim === "number" ? point.rank_within_claim : ""),
          _csvField(point.confidence || "")
        ]);
      }
    }
  }
  return rows.map(r => r.join("\t")).join("\n");
}
function downloadTSV() {
  const tsv = buildTSV();
  const blob = new Blob(["﻿" + tsv], { type: "text/tab-separated-values;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const ts = new Date().toISOString().slice(0, 10);
  a.href = url; a.download = `multimodal_genomics_evidence_${ts}.tsv`;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 0);
}
document.getElementById("download-tsv").addEventListener("click", downloadTSV);

mount();
</script>
</body>
</html>
"""


def build(out_path: Path) -> int:
    ontology = _load_json(ONTOLOGY_FILE)
    evidence = _load_json(EVIDENCE_FILE, default={"paradigms": []})
    papers = _load_json(PAPERS_FILE, default={"papers": {}})

    # Prep counts.
    n_papers = len((papers.get("papers") or {}))
    n_evidence = sum(
        len(c.get("evidence_points") or [])
        for p in evidence.get("paradigms") or []
        for c in p.get("claims") or []
    )
    n_pngs = sum(
        len(pt.get("screenshots") or [])
        for p in evidence.get("paradigms") or []
        for c in p.get("claims") or []
        for pt in c.get("evidence_points") or []
    )

    embedded_evidence = _embedded_evidence(evidence, out_path)
    _ensure_assets(TOPIC_DIR / "assets" / "evidence", out_path)

    viewer = ontology.get("viewer", {}) or {}
    primary = viewer.get("primary_color", "#22d3ee")
    secondary = viewer.get("secondary_color", "#a78bfa")
    title = viewer.get("title") or ontology.get("topic_label") or "Knowledge Graph"
    subtitle = viewer.get("subtitle", "")
    topic_label = ontology.get("topic_label", "")
    thesis = ontology.get("thesis", "")

    html = HTML_TEMPLATE
    html = html.replace("__VIEWER_TITLE__", title)
    html = html.replace("__SUBTITLE__", subtitle)
    html = html.replace("__TOPIC_LABEL__", topic_label)
    html = html.replace("__THESIS__", thesis)
    html = html.replace("__PRIMARY__", primary)
    html = html.replace("__SECONDARY__", secondary)
    html = html.replace("__GENERATED_AT__", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    html = html.replace("__N_PAPERS__", str(n_papers))
    html = html.replace("__N_EVIDENCE__", str(n_evidence))
    html = html.replace("__N_PNGS__", str(n_pngs))
    html = html.replace("__ONTOLOGY__", json.dumps(ontology))
    html = html.replace("__EVIDENCE__", json.dumps(embedded_evidence))
    html = html.replace("__PAPERS__", json.dumps(papers))

    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path}  papers={n_papers} evidence={n_evidence} pngs={n_pngs}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output HTML path (default: {DEFAULT_OUT})")
    args = parser.parse_args(argv)
    return build(args.out)


if __name__ == "__main__":
    sys.exit(main())
