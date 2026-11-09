from __future__ import annotations

from contextlib import redirect_stderr
import json
import os
from io import StringIO
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from tecan_common.performance import performance_marker


class PerformanceMarkerTests(unittest.TestCase):
    def _records(self, output: str) -> list[dict[str, object]]:
        return [
            json.loads(line.removeprefix("PERF "))
            for line in output.splitlines()
            if line.startswith("PERF ")
        ]

    def test_markers_are_silent_when_not_opted_in(self) -> None:
        stderr = StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
            with performance_marker("test.logic_stage") as metrics:
                metrics["item_count"] = 2
        self.assertEqual(stderr.getvalue(), "")

    def test_marker_emits_structured_stage_timing_only_when_enabled(self) -> None:
        stderr = StringIO()
        with patch.dict(os.environ, {"TECAN_PERFORMANCE_MARKERS": "1"}, clear=False):
            with redirect_stderr(stderr):
                with performance_marker("test.logic_stage", input_count=3) as metrics:
                    metrics["output_count"] = 2

        records = self._records(stderr.getvalue())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["type"], "performance_marker")
        self.assertEqual(records[0]["name"], "test.logic_stage")
        self.assertEqual(records[0]["outcome"], "ok")
        self.assertGreaterEqual(records[0]["duration_ms"], 0)
        self.assertEqual(
            records[0]["metrics"],
            {"input_count": 3, "output_count": 2},
        )

    def test_marker_does_not_emit_text_or_exception_details(self) -> None:
        stderr = StringIO()
        with patch.dict(os.environ, {"TECAN_PERFORMANCE_MARKERS": "1"}, clear=False):
            try:
                with redirect_stderr(stderr):
                    with performance_marker("test.failure_stage", description="secret") as metrics:
                        metrics["value"] = "private input"
                        raise RuntimeError("private exception detail")
            except RuntimeError:
                pass

        output = stderr.getvalue()
        records = self._records(output)
        self.assertNotIn("secret", output)
        self.assertNotIn("private", output)
        self.assertEqual(records[0]["outcome"], "error")
        self.assertEqual(records[0]["metrics"], {})

    def test_xscr_parse_reports_logic_stages_without_source_paths(self) -> None:
        from fluentcoder.decompiler.xscr_parser import parse_xscr

        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "acceptance"
            / "synthetic_transfer.xscr"
        )
        stderr = StringIO()
        with patch.dict(os.environ, {"TECAN_PERFORMANCE_MARKERS": "yes"}, clear=False):
            with redirect_stderr(stderr):
                parse_xscr(fixture)

        records = self._records(stderr.getvalue())
        self.assertEqual(
            [record["name"] for record in records],
            [
                "xscr.xml_tree_parse",
                "xscr.step_field_index",
                "xscr.ir_model_construction",
            ],
        )
        self.assertNotIn(str(fixture), stderr.getvalue())

    def test_zeia_index_and_pattern_mining_report_logic_stages(self) -> None:
        from tecan_reader.pattern_library import mine_script_patterns
        from tecan_reader.project_index import build_project_index

        xscr = '''<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="3">
          <Payload><ObjectName>MarkerScript</ObjectName><PayloadData><Script version="2.0">
            <Commands><ScriptGroup><Objects><Object Type="Tecan.Core.Scripting.CommentStatement">
              <CommentStatement><Comment>synthetic</Comment><LineNumber>1</LineNumber></CommentStatement>
            </Object></Objects></ScriptGroup></Commands>
          </Script></PayloadData></Payload>
        </sd:VxData>'''
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as raw:
            archive = Path(raw) / "synthetic.zeia"
            database = Path(raw) / "index.sqlite"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("DataStore/UserSpecific/marker.xscr", xscr)
                bundle.writestr("DataStore/nodedescription.xml", "<root />")
            with patch.dict(
                os.environ, {"TECAN_PERFORMANCE_MARKERS": "1"}, clear=False
            ):
                with redirect_stderr(stderr):
                    build_project_index([archive], database, force=True)
                    mine_script_patterns(database)

        names = {record["name"] for record in self._records(stderr.getvalue())}
        self.assertTrue(
            {
                "project_index.input_discovery",
                "project_index.archive_ingestion",
                "zeia.archive_inventory_and_format_detection",
                "zeia.member_inspection_and_normalization",
                "zeia.completeness_and_limit_accounting",
                "zeia.canonical_model_assembly",
                "project_index.sqlite_archive_write",
                "project_index.sqlite_commit",
                "project_index.summary_queries",
                "patterns.load_indexed_scripts",
                "patterns.classify_commands",
                "patterns.window_discovery_and_persistence",
                "patterns.sqlite_commit",
                "patterns.summary_query",
            }.issubset(names)
        )
        self.assertNotIn(str(archive), stderr.getvalue())

    def test_catalog_index_reports_file_processing_stages(self) -> None:
        from fluentcoder.catalog.indexer import build_index

        stderr = StringIO()
        with tempfile.TemporaryDirectory() as raw:
            install = Path(raw) / "install"
            components = install / "SystemSpecific" / "Worktable" / "Components"
            components.mkdir(parents=True)
            (components / "synthetic.xcmp").write_text("<Component />", encoding="utf-8")
            workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
            workspaces.mkdir(parents=True)
            (workspaces / "synthetic.xwsp").write_text("<Workspace />", encoding="utf-8")
            database = Path(raw) / "catalog.sqlite"
            with patch.dict(
                os.environ, {"TECAN_PERFORMANCE_MARKERS": "1"}, clear=False
            ):
                with redirect_stderr(stderr):
                    build_index(install_path=install, db_path=database)

        names = {record["name"] for record in self._records(stderr.getvalue())}
        self.assertTrue(
            {
                "catalog.install_stat_fingerprint",
                "catalog.incremental_cache_check",
                "catalog.source_content_fingerprints",
                "catalog.component_parse_inference_and_upsert",
                "catalog.workspace_parse_and_upsert",
                "catalog.site_path_indexing",
                "catalog.connector_reference_resolution",
                "catalog.liquid_class_parse_and_upsert",
                "catalog.stale_source_cleanup_and_commit",
                "catalog.final_count_queries",
            }.issubset(names)
        )
        self.assertNotIn(str(install), stderr.getvalue())

    def test_geometry_reports_separate_parse_and_derivation_stages(self) -> None:
        from fluent_pipeline.worktable_geometry import build_worktable_geometry

        stderr = StringIO()
        with patch.dict(os.environ, {"TECAN_PERFORMANCE_MARKERS": "on"}, clear=False):
            with redirect_stderr(stderr):
                result = build_worktable_geometry({"objects": []})

        self.assertEqual(result["component_count"], 0)
        self.assertEqual(
            [record["name"] for record in self._records(stderr.getvalue())],
            [
                "worktable.manifest_object_selection",
                "worktable.component_site_connector_parse",
                "worktable.workspace_resolution",
                "worktable.derived_geometry_and_output",
            ],
        )
