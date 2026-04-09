from __future__ import annotations

import unittest
from pathlib import Path
import sys
import tempfile

from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wcm.classify import classify_method_class_key
from wcm.db import connect, init_db, utc_now
from wcm.discovery import discover_local_pdfs
from wcm.graph import configure_legacy_method_classes
from wcm.parse import parse_assets


def make_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


class PipelineTests(unittest.TestCase):
    def test_classification_override_beats_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            conn = connect(tmpdir / "test.sqlite")
            init_db(conn)
            now = utc_now()
            conn.execute(
                """
                INSERT INTO papers (
                    paper_id, title, normalized_title, year, journal, doi, landing_page_url,
                    summary, method_summary, contribution, limitation, future_work, openalex_id,
                    organism, organism_group, method_class_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', '', ?, '', '', '', '', '', '', '', ?, ?, ?)
                """,
                (
                    "WCM-001",
                    "How to build the virtual cell with artificial intelligence",
                    "how to build the virtual cell with artificial intelligence",
                    2024,
                    "Cell",
                    "AI roadmap",
                    "mechanistic",
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO classification_events (paper_id, proposed_key, final_key, source, confidence, rationale, created_at)
                VALUES ('WCM-001', 'ml', 'hybrid', 'manual_override', 1.0, 'manual', ?)
                """,
                (now,),
            )
            proposed, _, _ = classify_method_class_key(
                "How to build the virtual cell with artificial intelligence",
                "AI roadmap",
                "Cell",
            )
            self.assertEqual(proposed, "ml")
            row = conn.execute(
                """
                SELECT final_key, source FROM classification_events
                WHERE paper_id = 'WCM-001'
                ORDER BY event_id DESC LIMIT 1
                """
            ).fetchone()
            self.assertEqual(row["final_key"], "hybrid")
            self.assertEqual(row["source"], "manual_override")

    def test_discovery_and_parse_cache_reuse_on_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            pdf_root = tmpdir / "pdfs"
            rejected = pdf_root / "_rejected"
            pdf_root.mkdir()
            rejected.mkdir()
            source = pdf_root / "old_name.pdf"
            make_pdf(source)

            conn = connect(tmpdir / "test.sqlite")
            init_db(conn)
            discover_local_pdfs(conn, pdf_root, rejected)

            import wcm.parse as parse_module

            call_count = {"value": 0}
            original = parse_module.parse_pdf_profile

            def fake_parse(path: Path):
                call_count["value"] += 1
                return {
                    "path": str(path),
                    "file_name": path.name,
                    "page_count": 1,
                    "metadata_title": "Test PDF",
                    "metadata_author": "",
                    "doi": "",
                    "page_texts": ["Test"],
                    "full_text": "Test",
                }

            parse_module.parse_pdf_profile = fake_parse
            try:
                parse_assets(conn, tmpdir, full_rebuild=False)
                self.assertEqual(call_count["value"], 1)

                target = pdf_root / "renamed.pdf"
                source.rename(target)
                discover_local_pdfs(conn, pdf_root, rejected)
                parse_assets(conn, tmpdir, full_rebuild=False)
                self.assertEqual(call_count["value"], 1)
            finally:
                parse_module.parse_pdf_profile = original

    def test_dynamic_method_class_config_uses_display_names(self) -> None:
        class Legacy:
            CLASS_IDS = {}
            CLASS_DEFINITIONS = {}
            LAYOUT_COLORS = {}

        configure_legacy_method_classes(
            Legacy,
            [
                {
                    "key": "mechanistic",
                    "display_name": "Mechanistic Core",
                    "definition": "A",
                    "color": "#111111",
                    "sort_order": 0,
                    "active": 1,
                },
                {
                    "key": "ml",
                    "display_name": "ML Systems",
                    "definition": "B",
                    "color": "#222222",
                    "sort_order": 1,
                    "active": 1,
                },
            ],
        )
        self.assertEqual(Legacy.CLASS_IDS, {"Mechanistic Core": 0, "ML Systems": 1})
        self.assertEqual(Legacy.CLASS_DEFINITIONS["ML Systems"], "B")
        self.assertEqual(Legacy.LAYOUT_COLORS[1], "#222222")


if __name__ == "__main__":
    unittest.main()
