"""Tests for diagnose_model_coverage.py."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import pytest

from tecan_tools import diagnose_model_coverage as coverage

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIM_DIR = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent"
HOST_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
ZEIA_MANIFEST = SIM_DIR / "manifest.sim.bak.json"

pytestmark = pytest.mark.fluentcontrol_shell


class DiagnoseModelCoverageTests(unittest.TestCase):
    def test_build_coverage_report_shape(self) -> None:
        if not (SIM_DIR / "manifest.json").exists():
            self.skipTest("simulator manifest not available")
        if not (HOST_INSTALL / "SystemSpecific/Worktable/Meshes").exists():
            self.skipTest("host install not available")

        report = coverage.build_coverage_report(
            host_install=HOST_INSTALL,
            sim_dir=SIM_DIR,
            zeia_manifest_path=ZEIA_MANIFEST if ZEIA_MANIFEST.exists() else None,
            zeia_archive_path=None,
            registry_path=SIM_DIR / "local/registry.json" if (SIM_DIR / "local/registry.json").exists() else None,
            manifest_path=SIM_DIR / "manifest.json",
            hardware_manifest_path=None,
            hardware_asset_dirs=[],
        )

        self.assertEqual(report["kind"], coverage.DIAGNOSTIC_KIND)
        self.assertIn("hostOnly", report)
        self.assertIn("zeiaOnly", report)
        self.assertIn("missingGlbs", report)
        self.assertIn("duplicateNames", report)
        self.assertIn("guidConflicts", report)
        self.assertIn("componentsWithoutMesh", report)
        self.assertIn("texturesNotDecoded", report)
        self.assertIn("failedConversions", report)
        summary = report["summary"]
        # Host FluentControl installs can gain meshes across catalog updates.
        # Keep fixture-owned counts useful while allowing newer host content.
        self.assertGreaterEqual(summary["hostMeshCount"], 319)
        if ZEIA_MANIFEST.exists():
            self.assertGreaterEqual(summary["zeiaMeshCount"], 296)
        else:
            self.assertEqual(summary["zeiaMeshCount"], 0)
        self.assertGreaterEqual(summary["glbCount"], 324)
        self.assertGreaterEqual(summary["hostOnlyCount"], 28)
        self.assertGreaterEqual(summary["zeiaOnlyCount"], 0)

    def test_duplicate_name_includes_six_grid_segment(self) -> None:
        if not ZEIA_MANIFEST.exists():
            self.skipTest("zeia manifest backup not available")
        if not (HOST_INSTALL / "SystemSpecific/Worktable/Meshes").exists():
            self.skipTest("host install not available")

        report = coverage.build_coverage_report(
            host_install=HOST_INSTALL,
            sim_dir=SIM_DIR,
            zeia_manifest_path=ZEIA_MANIFEST,
            zeia_archive_path=None,
            registry_path=None,
            manifest_path=SIM_DIR / "manifest.json" if (SIM_DIR / "manifest.json").exists() else None,
            hardware_manifest_path=None,
            hardware_asset_dirs=[],
        )
        six_grid = [item for item in report["duplicateNames"] if item["name"] == "6 grid segment"]
        self.assertEqual(len(six_grid), 1)
        self.assertIn("6bbad266-6a72-4df3-9865-8af59954a7e3", six_grid[0]["guids"])
        self.assertIn("ea5aa04d-d217-429a-a544-e90c452358da", six_grid[0]["guids"])

    def test_write_report_files(self) -> None:
        if not (SIM_DIR / "manifest.json").exists():
            self.skipTest("simulator manifest not available")
        if not (HOST_INSTALL / "SystemSpecific/Worktable/Meshes").exists():
            self.skipTest("host install not available")

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "model_coverage_report.json"
            report = coverage.build_coverage_report(
                host_install=HOST_INSTALL,
                sim_dir=SIM_DIR,
                zeia_manifest_path=ZEIA_MANIFEST if ZEIA_MANIFEST.exists() else None,
                zeia_archive_path=None,
                registry_path=SIM_DIR / "local/registry.json" if (SIM_DIR / "local/registry.json").exists() else None,
                manifest_path=SIM_DIR / "manifest.json",
                hardware_manifest_path=None,
                hardware_asset_dirs=[],
            )
            out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            md = out.with_suffix(".md")
            md.write_text(coverage.render_markdown_report(report), encoding="utf-8")
            self.assertTrue(out.exists())
            self.assertTrue(md.exists())


if __name__ == "__main__":
    unittest.main()
