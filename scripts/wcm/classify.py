from __future__ import annotations

from .models import DEFAULT_CLASS_KEY_TO_LABEL, DEFAULT_COMPLETENESS_KEY_TO_LABEL


def classify_method_class_key(title: str, abstract: str, journal: str = "") -> tuple[str, float, str]:
    text = f"{title} {abstract} {journal}".lower()
    ml_tokens = [
        "artificial intelligence",
        "machine learning",
        "deep learning",
        "foundation model",
        "single-cell model",
        "generative ai",
        "transformer",
        "scgpt",
        "state",
    ]
    hybrid_tokens = [
        "integrative",
        "hybrid",
        "cross-evaluation",
        "multi-omics",
        "multi omics",
        "multi-modal",
        "multi modal",
        "structural models",
        "pan-cancer",
        "pan cancer",
        "interface and engine",
        "workflow",
    ]
    if any(token in text for token in ml_tokens):
        return ("ml", 0.88, "Matched machine-learning keywords in title/abstract/journal.")
    if any(token in text for token in hybrid_tokens):
        return ("hybrid", 0.78, "Matched hybrid/integrative modeling keywords in title/abstract/journal.")
    return ("mechanistic", 0.72, "Defaulted to mechanistic modeling based on absence of ML/hybrid keywords.")


def label_for_method_class_key(key: str) -> str:
    return DEFAULT_CLASS_KEY_TO_LABEL.get(key, key)


def classify_completeness_key(title: str, abstract: str, journal: str = "") -> tuple[str, float, str]:
    text = f"{title} {abstract} {journal}".lower()
    if any(
        token in text
        for token in [
            "predicts phenotype from genotype",
            "simulations of a living minimal cell",
            "genetically minimal cell to life on a computer in 4d",
            "expanded whole-cell model of e. coli",
        ]
    ):
        return ("complete", 0.96, "Matched known complete whole-cell-model title patterns.")
    if any(
        token in text
        for token in [
            "whole-cell model wm_s288c",
            "whole-cell modeling in yeast",
            "compartment-specific proteome constraints",
        ]
    ):
        return ("partial", 0.82, "Matched yeast whole-cell-model phrases treated as partial WCM coverage.")
    return ("related", 0.84, "Defaulted to related model because only the curated full WCMs are treated as complete, with yeast tracked separately as partial.")


def label_for_completeness_key(key: str) -> str:
    return DEFAULT_COMPLETENESS_KEY_TO_LABEL.get(key, key)
