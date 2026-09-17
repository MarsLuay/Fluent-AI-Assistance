from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from compatibility_matrix_support import materialize_recipe, recipe_sha256
from tecan_reader.project_index import build_project_index
from tecan_reader.zeia_adapters import ADAPTERS, ZeiaFormatError, ingest_zeia, probe_zeia


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "zeia_compatibility"
MANIFEST_PATH = FIXTURE_DIR / "compatibility_manifest.json"


class ZeiaCompatibilityMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.rows = {row["variant_id"]: row for row in cls.manifest["variants"]}

    def test_manifest_is_complete_and_checksums_are_current(self) -> None:
        self.assertEqual(
            set(self.manifest["supported_adapters"]),
            {adapter.adapter_id for adapter in ADAPTERS},
        )
        self.assertEqual(
            set(self.manifest["fixture_checksums"]),
            {
                path.relative_to(FIXTURE_DIR).as_posix()
                for path in FIXTURE_DIR.rglob("*.zeia.json")
            },
        )
        for relative_path, expected_sha256 in self.manifest["fixture_checksums"].items():
            path = FIXTURE_DIR / relative_path
            self.assertTrue(path.is_file(), relative_path)
            self.assertEqual(recipe_sha256(path), expected_sha256, relative_path)

        for row in self.manifest["variants"]:
            self.assertIn("tests", row)
            self.assertIn("expected_detection", row)
            self.assertIn("known_limitations", row)
            self.assertIn("fluentcontrol_version", row)
            self.assertIn("export_version", row)
            minimal = FIXTURE_DIR / row["minimal_fixture"]
            self.assertTrue(minimal.is_file(), row["variant_id"])
            if row["supported"]:
                self.assertIsNotNone(row["representative_fixture"])
                self.assertIsNotNone(row["expected_golden"])
                self.assertIsNotNone(row["negative_neighbor"])
                self.assertTrue((FIXTURE_DIR / row["representative_fixture"]).is_file())
                self.assertTrue((FIXTURE_DIR / row["expected_golden"]).is_file())
            else:
                self.assertIsNone(row["representative_fixture"])
                self.assertIsNone(row["expected_golden"])

    def test_manifest_supported_variant_matrix(self) -> None:
        for row in self.manifest["variants"]:
            if not row["supported"]:
                continue
            with self.subTest(variant=row["variant_id"]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                minimal = materialize_recipe(FIXTURE_DIR / row["minimal_fixture"], root / "minimal")
                representative = materialize_recipe(
                    FIXTURE_DIR / row["representative_fixture"], root / "representative"
                )
                minimal_detection = probe_zeia(minimal)
                representative_detection = probe_zeia(representative)
                expected_detection = row["expected_detection"]
                for detection in (minimal_detection, representative_detection):
                    self.assertEqual(detection.status, expected_detection["status"])
                    self.assertEqual(
                        detection.selected.adapter_id,
                        expected_detection["selected_adapter"],
                    )
                    self.assertEqual(
                        detection.selected.format_family,
                        expected_detection["format_family"],
                    )

                model = ingest_zeia(representative)
                self.assertEqual(self._golden_view(model), self._read_golden(row), row["variant_id"])
                self.assertTrue(model.completeness["complete"])
                self.assertEqual(
                    model.scripts[0]["references"][0]["object_name"],
                    "WT_Demo",
                )
                custom = next(
                    obj for obj in model.objects if obj.get("object_name") == "Unicode Ω Component"
                )
                self.assertEqual(
                    custom["source_metadata"]["unknown_fields"]["FutureField"],
                    ["additive-value"],
                )
                self._assert_representative_feature_shapes(representative)

                summary = build_project_index(
                    [representative], root / "project-index.sqlite", force=True
                )
                expected = self._read_golden(row)
                self.assertEqual(summary["script_count"], expected["counts"]["scripts"])
                self.assertEqual(summary["catalog_object_count"], expected["counts"]["objects"])
                self.assertEqual(summary["worklist_count"], expected["counts"]["worklists"])

    def test_manifest_negative_neighbors_and_failure_fixtures(self) -> None:
        for row in self.manifest["variants"]:
            if not row["supported"]:
                continue
            with self.subTest(variant=row["variant_id"]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                negative = materialize_recipe(
                    FIXTURE_DIR / row["negative_neighbor"]["fixture"], root / "negative"
                )
                detection = probe_zeia(negative)
                self.assertEqual(detection.status, "supported")
                self.assertEqual(
                    detection.selected.adapter_id,
                    row["negative_neighbor"]["expected_adapter_id"],
                )

                for case in row["negative_cases"]:
                    fixture = materialize_recipe(FIXTURE_DIR / case["fixture"], root / case["fixture"])
                    case_detection = probe_zeia(fixture)
                    self.assertEqual(case_detection.status, case["expected_status"])
                    model = ingest_zeia(fixture)
                    self.assertEqual(model.completeness["complete"], case["expected_complete"])
                    if case.get("expected_reference_object_name"):
                        names = {
                            reference.get("object_name")
                            for script in model.scripts
                            for reference in script.get("references", [])
                        }
                        self.assertIn(case["expected_reference_object_name"], names)

    def test_unknown_variant_fails_safely(self) -> None:
        row = self.rows["unknown-datastore-version"]
        with tempfile.TemporaryDirectory() as tmp:
            fixture = materialize_recipe(FIXTURE_DIR / row["minimal_fixture"], tmp)
            detection = probe_zeia(fixture)
            expected_detection = row["expected_detection"]
            self.assertEqual(detection.status, expected_detection["status"])
            self.assertIsNone(detection.selected)
            self.assertIsNone(expected_detection["selected_adapter"])
            self.assertTrue(any("unsupported" in message for message in detection.diagnostics))
            with self.assertRaises(ZeiaFormatError):
                ingest_zeia(fixture)

    def test_multi_archive_full_export_folder(self) -> None:
        scenario = self.manifest["multi_archive_folder"]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "full-export-folder"
            archives = [
                materialize_recipe(FIXTURE_DIR / relative_path, folder / str(index))
                for index, relative_path in enumerate(scenario["fixtures"])
            ]
            summary = build_project_index([folder], Path(tmp) / "folder-index.sqlite", force=True)
            self.assertEqual(summary["zeia_file_count"], scenario["expected_archive_count"])
            self.assertEqual(summary["script_count"], scenario["expected_script_count"])
            self.assertEqual(
                {Path(path).name for path in summary["indexed_files"]},
                {archive.name for archive in archives},
            )

    @staticmethod
    def _golden_view(model) -> dict:
        return {
            "adapter_id": model.adapter_id,
            "detection": {
                "format_family": model.detection["format_family"],
                "selected_adapter": model.detection["selected_adapter"],
                "status": model.detection["status"],
            },
            "counts": {
                "objects": len(model.objects),
                "scripts": len(model.scripts),
                "worklists": len(model.worklists),
            },
            "names": {
                "objects": sorted(
                    str(obj.get("object_name") or obj.get("source") or "")
                    for obj in model.objects
                ),
                "scripts": sorted(str(script.get("object_name") or "") for script in model.scripts),
                "worklists": sorted(str(worklist.get("source") or "") for worklist in model.worklists),
            },
            "schema_version": model.schema_version,
        }

    @staticmethod
    def _read_golden(row: dict) -> dict:
        return json.loads((FIXTURE_DIR / row["expected_golden"]).read_text(encoding="utf-8"))

    @staticmethod
    def _assert_representative_feature_shapes(archive: Path) -> None:
        with zipfile.ZipFile(archive) as zf:
            entries = {name.casefold() for name in zf.namelist()}
        assert any(name.endswith(".xscr") for name in entries)
        assert any(name.endswith(".xwsp") for name in entries)
        assert any(name.endswith(".xlqc") for name in entries)
        assert any(name.endswith(".xsit") for name in entries)
        assert any(name.endswith(".gwl") for name in entries)
        assert any(name.endswith(".png") for name in entries)
        assert any("unicode" in name for name in entries)


if __name__ == "__main__":
    unittest.main()
