from __future__ import annotations

from .models import DEFAULT_CLASS_KEY_TO_LABEL


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

