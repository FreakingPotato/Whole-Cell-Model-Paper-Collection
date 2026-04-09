from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
METADATA_DIR = ROOT / "metadata"
GRAPHIFY_OUT = ROOT / "graphify-out"
CORPUS_DIR = ROOT / "graphify_corpus"
PAPER_NOTES_DIR = CORPUS_DIR / "papers"
PDF_DIR = ROOT / "pdfs"
REJECTED_PDF_DIR = PDF_DIR / "_rejected"

MASTER_TABLE = METADATA_DIR / "whole_cell_model_papers_master_table.csv"
CLASS_TABLE = METADATA_DIR / "wcm_method_classes.csv"
CLASS_CATALOG_TABLE = METADATA_DIR / "wcm_method_class_catalog.csv"
COMPLETENESS_TABLE = METADATA_DIR / "wcm_completeness.csv"
COMPLETENESS_CATALOG_TABLE = METADATA_DIR / "wcm_completeness_catalog.csv"
ORGANISM_TABLE = METADATA_DIR / "wcm_organisms.csv"
LIVE_INVENTORY_TABLE = METADATA_DIR / "live_paper_inventory.csv"
INVENTORY_TABLE = METADATA_DIR / "curated_papers_inventory.csv"
PAPER_METADATA_JSON = METADATA_DIR / "wcm_paper_metadata.json"
PDF_PROCESSING_STATUS = METADATA_DIR / "pdf_processing_status.csv"
PDF_PARSE_CACHE_JSON = METADATA_DIR / "pdf_parse_cache.json"
PDF_SIDECAR_REVIEW_QUEUE = METADATA_DIR / "pdf_sidecar_review_queue.csv"
PDF_SIDECAR_REJECTED_QUEUE = METADATA_DIR / "pdf_sidecar_rejected.csv"
PDF_SIDECAR_SUMMARY = METADATA_DIR / "pdf_sidecar_summary.md"
AUTO_INGEST_JSON = METADATA_DIR / "auto_ingested_papers.json"
OPENALEX_CACHE_DIR = METADATA_DIR / "openalex_cache"
ZOTERO_SYNC_STATE = METADATA_DIR / "zotero_sync_state.json"
DB_PATH = METADATA_DIR / "wcm_state.sqlite"

PARSER_VERSION = "2"
OPENALEX_CACHE_TTL_DAYS = 30


@dataclass(frozen=True)
class MethodClassSeed:
    key: str
    display_name: str
    definition: str
    color: str
    sort_order: int


DEFAULT_METHOD_CLASSES: tuple[MethodClassSeed, ...] = (
    MethodClassSeed(
        key="mechanistic",
        display_name="Mechanistic models",
        definition=(
            "Rule-based, kinetic, stochastic, physical, or simulation-first studies "
            "that represent cellular processes explicitly."
        ),
        color="#4E79A7",
        sort_order=0,
    ),
    MethodClassSeed(
        key="ml",
        display_name="Machine Learning Model",
        definition="Data-driven or AI-first work where predictive learning is the central modeling strategy.",
        color="#F28E2B",
        sort_order=1,
    ),
    MethodClassSeed(
        key="hybrid",
        display_name="Hybrid architectures",
        definition=(
            "Workflows that combine mechanistic simulation with data integration, structural constraints, "
            "or AI-assisted components."
        ),
        color="#E15759",
        sort_order=2,
    ),
)

DEFAULT_CLASS_LABEL_TO_KEY = {seed.display_name: seed.key for seed in DEFAULT_METHOD_CLASSES}
DEFAULT_CLASS_KEY_TO_LABEL = {seed.key: seed.display_name for seed in DEFAULT_METHOD_CLASSES}

DEFAULT_COMPLETENESS_LEVELS: tuple[MethodClassSeed, ...] = (
    MethodClassSeed(
        key="complete",
        display_name="Complete WCM",
        definition="A paper that presents a true whole-cell model covering the cell broadly enough to count as a complete WCM in this collection.",
        color="#22c55e",
        sort_order=0,
    ),
    MethodClassSeed(
        key="partial",
        display_name="Partial WCM",
        definition="A paper that presents a substantial but still incomplete whole-cell model, such as a half-WCM or an organism-wide model missing major layers.",
        color="#eab308",
        sort_order=1,
    ),
    MethodClassSeed(
        key="related",
        display_name="Related Model",
        definition="A related modeling, subsystem, review, or enabling paper that is relevant to WCMs but is not itself a complete WCM.",
        color="#64748b",
        sort_order=2,
    ),
)

DEFAULT_COMPLETENESS_LABEL_TO_KEY = {seed.display_name: seed.key for seed in DEFAULT_COMPLETENESS_LEVELS}
DEFAULT_COMPLETENESS_KEY_TO_LABEL = {seed.key: seed.display_name for seed in DEFAULT_COMPLETENESS_LEVELS}

CURATED_COMPLETENESS_OVERRIDES = {
    "WCM-001": "complete",
    "WCM-002": "complete",
    "WCM-003": "complete",
    "WCM-054": "partial",
    "WCM-055": "complete",
    "WCM-056": "partial",
}
