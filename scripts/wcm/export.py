from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import fetch_method_classes, fetch_papers, fetch_pdf_assets, fetch_pdf_parse_cache, primary_pdf_asset_for_paper
from .graph import configure_legacy_method_classes
from .models import (
    CLASS_CATALOG_TABLE,
    CLASS_TABLE,
    CORPUS_DIR,
    INVENTORY_TABLE,
    LIVE_INVENTORY_TABLE,
    MASTER_TABLE,
    ORGANISM_TABLE,
    PAPER_METADATA_JSON,
    PDF_PARSE_CACHE_JSON,
)
from .parse import parsed_profiles_by_path
from . import legacy_graph_builder as legacy


def organism_group_for_label(label: str) -> str:
    return legacy.organism_group_for_label(label)


def paper_status_for(asset: dict[str, Any] | None) -> str:
    if asset is None:
        return "metadata_only"
    if asset["status"] == "parsed":
        return "parsed"
    if asset["status"] == "failed_parse":
        return "parse_failed"
    if asset["status"] in {"discovered", "matched", "renamed"}:
        return "pdf_present"
    return "review_needed"


def remote_status_for(paper: dict[str, Any], asset: dict[str, Any] | None) -> str:
    remote = paper.get("remote_link")
    if remote and remote.get("status") == "zotero_present" and asset:
        return "zotero_synced"
    if remote and remote.get("status") == "zotero_present":
        return "zotero_present"
    if remote and remote.get("status") == "zotero_missing_pdf":
        return "zotero_missing_pdf"
    if asset:
        return "zotero_upload_pending"
    return ""


def export_tables(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], dict[str, dict[str, str]], list[dict[str, Any]]]:
    method_classes = fetch_method_classes(conn)
    papers = fetch_papers(conn)
    profiles_by_path = parsed_profiles_by_path(conn)
    pdf_cache = fetch_pdf_parse_cache(conn)
    assets = fetch_pdf_assets(conn)
    asset_by_paper: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        if asset["paper_id"]:
            asset_by_paper.setdefault(asset["paper_id"], []).append(asset)

    with CLASS_CATALOG_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["key", "display_name", "definition", "color", "sort_order", "active", "updated_at"])
        writer.writeheader()
        writer.writerows(method_classes)

    master_rows = []
    class_rows = []
    organism_rows = []
    inventory_rows = []
    paper_meta_input_rows = []
    classes_for_legacy: dict[str, dict[str, str]] = {}
    organisms_for_legacy: dict[str, dict[str, str]] = {}

    for paper in papers:
        asset = primary_pdf_asset_for_paper(asset_by_paper.get(paper["paper_id"], []))
        pdf_file = (asset["relative_path"].split("/")[-1] if asset else "") if asset else ""
        pdf_status = "downloaded" if asset and asset["status"] in {"parsed", "matched", "renamed", "discovered"} else "landing_page_only"
        paper_status = paper_status_for(asset)
        remote_status = remote_status_for(paper, asset)

        master_row = {
            "paper id": paper["paper_id"],
            "paper title": paper["title"],
            "summary": paper["summary"] or "",
            "method summary": paper["method_summary"] or "",
            "contribution": paper["contribution"] or "",
            "limitation": paper["limitation"] or "",
            "future work": paper["future_work"] or "",
            "year": paper["year"] or "",
            "journal": paper["journal"] or "",
            "doi": paper["doi"] or "",
            "pdf status": pdf_status,
            "pdf file": pdf_file,
            "landing page url": paper["landing_page_url"] or "",
        }
        master_rows.append(master_row)
        paper_meta_input_rows.append(
            {
                **master_row,
                "paper_id": paper["paper_id"],
                "paper_title": paper["title"],
                "method_summary": paper["method_summary"] or "",
                "future_work": paper["future_work"] or "",
                "pdf_status": pdf_status,
                "pdf_file": pdf_file,
                "landing_page_url": paper["landing_page_url"] or "",
            }
        )

        class_row = {
            "paper_id": paper["paper_id"],
            "method_class_key": paper["method_class_key"],
            "method_class_label": paper["method_class_label"],
            "method_class": paper["method_class_label"],
            "classification_source": paper["classification_source"] or "",
            "classification_confidence": paper["classification_confidence"] or "",
            "rationale": paper["classification_rationale"] or "",
        }
        class_rows.append(class_row)
        classes_for_legacy[paper["paper_id"]] = {
            "paper_id": paper["paper_id"],
            "method_class": paper["method_class_label"],
            "rationale": paper["classification_rationale"] or "",
            "method_class_key": paper["method_class_key"],
        }

        organism_rows.append(
            {
                "paper_id": paper["paper_id"],
                "organism": paper["organism"] or "",
                "organism_group": paper.get("organism_group") or organism_group_for_label(paper["organism"] or ""),
            }
        )
        organisms_for_legacy[paper["paper_id"]] = {"paper_id": paper["paper_id"], "organism": paper["organism"] or ""}

        inventory_rows.append(
            {
                "paper_id": paper["paper_id"],
                "title": paper["title"],
                "year": paper["year"] or "",
                "journal": paper["journal"] or "",
                "doi": paper["doi"] or "",
                "method_class_key": paper["method_class_key"],
                "method_class_label": paper["method_class_label"],
                "method_class": paper["method_class_label"],
                "classification_source": paper["classification_source"] or "",
                "classification_confidence": paper["classification_confidence"] or "",
                "organism": paper["organism"] or "",
                "organism_group": paper.get("organism_group") or organism_group_for_label(paper["organism"] or ""),
                "paper_status": paper_status,
                "pdf_status": pdf_status,
                "pdf_file": pdf_file,
                "landing_page_url": paper["landing_page_url"] or "",
                "remote_status": remote_status,
            }
        )

    with MASTER_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(master_rows[0].keys()))
        writer.writeheader()
        writer.writerows(master_rows)

    with CLASS_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(class_rows[0].keys()))
        writer.writeheader()
        writer.writerows(class_rows)

    with ORGANISM_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(organism_rows[0].keys()))
        writer.writeheader()
        writer.writerows(organism_rows)

    with LIVE_INVENTORY_TABLE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory_rows[0].keys()))
        writer.writeheader()
        writer.writerows(inventory_rows)

    with INVENTORY_TABLE.open("w", encoding="utf-8", newline="") as handle:
        fields = ["paper_id", "title", "year", "journal", "doi", "pdf_status", "pdf_file", "pdf_url", "landing_page_url", "authors"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for paper in papers:
            asset = paper.get("primary_pdf_asset")
            writer.writerow(
                {
                    "paper_id": paper["paper_id"],
                    "title": paper["title"],
                    "year": paper["year"] or "",
                    "journal": paper["journal"] or "",
                    "doi": paper["doi"] or "",
                    "pdf_status": "downloaded" if asset else "landing_page_only",
                    "pdf_file": (asset["relative_path"].split("/")[-1] if asset else "") if asset else "",
                    "pdf_url": "",
                    "landing_page_url": paper["landing_page_url"] or "",
                    "authors": "",
                }
            )

    parse_cache_payload = {"version": 2, "files": {}}
    for asset in assets:
        cache = pdf_cache.get((asset["file_hash"], asset.get("parser_version") or ""))
        if not cache:
            continue
        parse_cache_payload["files"][Path(asset["relative_path"]).name] = {
            "signature": {"size": asset["size_bytes"], "mtime_ns": asset["mtime_ns"]},
            "parsed": bool(cache["parsed"]),
            "profile": cache.get("profile"),
            "error": cache.get("error") or "",
            "updated_at": cache.get("updated_at") or "",
        }
    PDF_PARSE_CACHE_JSON.write_text(json.dumps(parse_cache_payload, indent=2) + "\n", encoding="utf-8")
    return paper_meta_input_rows, classes_for_legacy, organisms_for_legacy, list(profiles_by_path.values())


def export_graph_and_metadata(conn: sqlite3.Connection) -> dict[str, Any]:
    method_classes = fetch_method_classes(conn)
    configure_legacy_method_classes(legacy, method_classes)
    rows, classes_for_legacy, organisms_for_legacy, profiles = export_tables(conn)
    paper_meta, _ = legacy.build_paper_metadata(rows, classes_for_legacy, organisms_for_legacy, profiles)
    papers = fetch_papers(conn)
    papers_by_id = {paper["paper_id"]: paper for paper in papers}
    for paper_id, meta in paper_meta.items():
        paper = papers_by_id[paper_id]
        asset = paper.get("primary_pdf_asset")
        meta["method_class_key"] = paper["method_class_key"]
        meta["method_class_label"] = paper["method_class_label"]
        meta["classification_source"] = paper["classification_source"] or ""
        meta["classification_confidence"] = paper["classification_confidence"] or ""
        meta["paper_status"] = paper_status_for(asset)
        meta["remote_status"] = remote_status_for(paper, asset)

    legacy.write_paper_notes(paper_meta)
    payload = {
        "papers": paper_meta,
        "provenance_note": (
            "Bullet-level provenance uses parsed local PDF page anchors when available, plus the curated note section "
            "and DOI landing page as fallbacks."
        ),
    }
    PAPER_METADATA_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (legacy.GRAPHIFY_OUT / "paper_metadata.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    legacy.write_sidecar_outputs(rows, paper_meta, {})
    assets = fetch_pdf_assets(conn)
    remote_links = {paper["paper_id"]: paper.get("remote_link") for paper in papers}
    with legacy.PDF_PROCESSING_STATUS.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "paper_id",
            "title",
            "doi",
            "pdf_file",
            "pdf_exists",
            "pdf_status",
            "paper_status",
            "parsed",
            "page_count",
            "parse_error",
            "cache_updated_at",
            "method_class_key",
            "method_class_label",
            "classification_source",
            "classification_confidence",
            "remote_status",
            "zotero_has_pdf",
            "zotero_parent_key",
            "zotero_attachment_keys",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        assets_by_paper: dict[str, list[dict[str, Any]]] = {}
        for asset in assets:
            if asset["paper_id"]:
                assets_by_paper.setdefault(asset["paper_id"], []).append(asset)
        pdf_cache = fetch_pdf_parse_cache(conn)
        for paper in papers:
            asset = primary_pdf_asset_for_paper(assets_by_paper.get(paper["paper_id"], []))
            cache = pdf_cache.get((asset["file_hash"], asset.get("parser_version") or "")) if asset else None
            remote = remote_links.get(paper["paper_id"]) or {}
            attachment_keys = remote.get("attachment_keys_json") or []
            writer.writerow(
                {
                    "paper_id": paper["paper_id"],
                    "title": paper["title"],
                    "doi": paper["doi"] or "",
                    "pdf_file": (asset["relative_path"].split("/")[-1] if asset else "") if asset else "",
                    "pdf_exists": "yes" if asset and asset["status"] != "missing" else "no",
                    "pdf_status": "downloaded" if asset else "landing_page_only",
                    "paper_status": paper_status_for(asset),
                    "parsed": "yes" if asset and asset["status"] == "parsed" else "no",
                    "page_count": asset["page_count"] if asset else "",
                    "parse_error": asset["parse_error"] if asset else "",
                    "cache_updated_at": cache.get("updated_at") if cache else "",
                    "method_class_key": paper["method_class_key"],
                    "method_class_label": paper["method_class_label"],
                    "classification_source": paper["classification_source"] or "",
                    "classification_confidence": paper["classification_confidence"] or "",
                    "remote_status": remote_status_for(paper, asset),
                    "zotero_has_pdf": "yes" if remote.get("status") == "zotero_present" else "no",
                    "zotero_parent_key": remote.get("remote_key", ""),
                    "zotero_attachment_keys": ";".join(attachment_keys),
                }
            )

    G, communities, community_labels = legacy.build_graph(rows, classes_for_legacy, paper_meta)
    legacy.to_json(G, communities, str(legacy.GRAPHIFY_OUT / "graph.json"))
    legacy.to_html(G, communities, str(legacy.GRAPHIFY_OUT / "graph_base.html"), community_labels=community_labels)
    graph_base_html = legacy.GRAPHIFY_OUT / "graph_base.html"
    graph_base_text = graph_base_html.read_text(encoding="utf-8")
    graph_base_text = graph_base_text.replace(
        f"<title>graphify - {graph_base_html}</title>",
        "<title>Whole-Cell Model Graph Explorer</title>",
    )
    graph_base_html.write_text(graph_base_text, encoding="utf-8")
    legacy.to_graphml(legacy.graphml_safe_copy(G), communities, str(legacy.GRAPHIFY_OUT / "graph.graphml"))
    legacy.to_svg(G, communities, str(legacy.GRAPHIFY_OUT / "graph.svg"), community_labels=community_labels)
    graph_data = json.loads((legacy.GRAPHIFY_OUT / "graph.json").read_text(encoding="utf-8"))
    legacy.write_enhanced_html(graph_data, community_labels)
    legacy.write_report(G, communities, community_labels, total_files=len(rows))
    legacy.write_summary(rows, classes_for_legacy, G)
    return {
        "papers": len(rows),
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "output_dir": str(legacy.GRAPHIFY_OUT),
        "metadata_file": str(PAPER_METADATA_JSON),
    }

