from __future__ import annotations

from .models import DEFAULT_METHOD_CLASSES


def configure_legacy_method_classes(legacy, method_classes: list[dict]) -> None:
    ordered = sorted(method_classes, key=lambda row: (row["sort_order"], row["key"]))
    labels = [row["display_name"] for row in ordered if row.get("active", 1)]
    definitions = {row["display_name"]: row["definition"] for row in ordered}
    colors = {index: row["color"] for index, row in enumerate(ordered)}
    legacy.CLASS_IDS = {label: index for index, label in enumerate(labels)}
    legacy.CLASS_DEFINITIONS = definitions
    legacy.LAYOUT_COLORS = colors

