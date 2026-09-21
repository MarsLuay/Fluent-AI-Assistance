from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tecan_reader.full_export_readiness import (
    ReadinessStatus,
    resolve_full_export_readiness,
)
from tecan_reader.diagnostics import DiagnosticCode
from tecan_reader.project_model import CanonicalProjectModel


def _model(
    archive: str,
    *,
    scripts: list[dict] | None = None,
    objects: list[dict] | None = None,
    errors: list[dict] | None = None,
) -> CanonicalProjectModel:
    for record in [*(scripts or []), *(objects or [])]:
        record.setdefault("provenance", {})["source_archive"] = archive
    return CanonicalProjectModel(
        source_archive=archive,
        adapter_id="test",
        detection={"status": "supported"},
        scripts=scripts or [],
        objects=objects or [],
        worklists=[],
        errors=errors or [],
        source_metadata={},
        completeness={"complete": True, "mode": "complete"},
    )


def _script(name: str, *, reference: dict | None = None) -> dict:
    return {
        "kind": "script",
        "object_name": name,
        "entry": f"Scripts/{name}.xscr",
        "references": [reference] if reference else [],
        "dependencies": {"liquid_classes": []},
        "provenance": {
            "source_archive": "",
            "entry_path": f"Scripts/{name}.xscr",
        },
    }


def _object(kind: str, name: str, guid: str) -> dict:
    return {
        "kind": kind,
        "object_name": name,
        "guids": [guid],
        "names": [],
        "entry": f"Objects/{name}.xml",
        "provenance": {"source_archive": "", "entry_path": f"Objects/{name}.xml"},
    }


class FullExportReadinessTests(unittest.TestCase):
    def test_cross_archive_reference_resolution_is_deterministic(self) -> None:
        first = _model(
            "first.zeia",
            scripts=[
                _script(
                    "Source",
                    reference={
                        "object_name": "Shared Worktable",
                        "guid": "workspace-guid",
                        "type_id": "WorktableWorkspace",
                    },
                )
            ],
        )
        second = _model(
            "second.zeia",
            objects=[
                _object("workspace", "Shared Worktable", "workspace-guid"),
                _object("liquid_class", "Water", "water-guid"),
            ],
        )
        result = resolve_full_export_readiness([first, second])
        self.assertEqual(result.status, ReadinessStatus.COMPLETE)
        self.assertEqual(result.to_dict(), resolve_full_export_readiness([first, second]).to_dict())

    def test_missing_dependency_is_reported_with_source_provenance(self) -> None:
        result = resolve_full_export_readiness(
            _model(
                "source.zeia",
                scripts=[
                    _script(
                        "Source",
                        reference={
                            "object_name": "Missing Worktable",
                            "guid": "missing-guid",
                            "type_id": "WorktableWorkspace",
                        },
                    )
                ],
            )
        )
        self.assertEqual(result.status, ReadinessStatus.PARTIAL)
        self.assertEqual(result.unresolved_references[0].source_archive, "source.zeia")
        self.assertEqual(result.unresolved_references[0].source_entry, "Scripts/Source.xscr")
        self.assertIn(
            DiagnosticCode.DEPENDENCY_UNRESOLVED,
            {item["code"] for item in result.to_dict()["diagnostics"]},
        )

    def test_required_dependency_stays_partial_in_dependency_rich_export(self) -> None:
        result = resolve_full_export_readiness(
            _model(
                "source.zeia",
                scripts=[
                    _script(
                        "Source",
                        reference={
                            "object_name": "Missing Worktable",
                            "guid": "missing-worktable-guid",
                            "type_id": "WorktableWorkspace",
                        },
                    )
                ],
                objects=[
                    _object("workspace", "Base Worktable", "workspace-guid"),
                    _object("liquid_class", "Water", "water-guid"),
                    _object("labware", "Source Plate", "plate-guid"),
                    _object("system", "Instrument", "system-guid"),
                    _object("custom_asset", "Asset", "asset-guid"),
                ],
            )
        )
        self.assertEqual(result.status, ReadinessStatus.PARTIAL)
        self.assertFalse(result.accepted)
        self.assertEqual(result.unresolved_references[0].diagnostic_code, "unresolved_reference")

    def test_reference_type_ids_match_canonical_kinds(self) -> None:
        result = resolve_full_export_readiness(
            _model(
                "source.zeia",
                scripts=[
                    _script(
                        "Source",
                        reference={
                            "object_name": "Water",
                            "guid": "water-guid",
                            "type_id": "LiquidClass",
                        },
                    )
                ],
                objects=[
                    _object("workspace", "Worktable", "workspace-guid"),
                    _object("liquid_class", "Water", "water-guid"),
                ],
            )
        )
        self.assertEqual(result.status, ReadinessStatus.COMPLETE)
        self.assertEqual(result.unresolved_references, ())

    def test_duplicate_identifier_is_ambiguous(self) -> None:
        result = resolve_full_export_readiness(
            [
                _model("one.zeia", objects=[_object("workspace", "Same", "one")]),
                _model("two.zeia", objects=[_object("workspace", "Same", "two")]),
            ]
        )
        self.assertEqual(result.status, ReadinessStatus.AMBIGUOUS)
        self.assertEqual(result.conflicts[0].identifier, "same")

    def test_parse_warning_is_complete_with_warnings(self) -> None:
        result = resolve_full_export_readiness(
            _model(
                "source.zeia",
                scripts=[_script("Source")],
                objects=[
                    _object("workspace", "Worktable", "workspace"),
                    _object("liquid_class", "Water", "water"),
                ],
                errors=[{"severity": "warning", "entry": "metadata.xml", "error": "unknown field"}],
            )
        )
        self.assertEqual(result.status, ReadinessStatus.COMPLETE_WITH_WARNINGS)

    def test_partial_approval_does_not_change_factual_status(self) -> None:
        result = resolve_full_export_readiness(
            _model("source.zeia", scripts=[_script("Source")]),
            approve_partial_zeia=True,
        )
        self.assertEqual(result.status, ReadinessStatus.PARTIAL)
        self.assertTrue(result.accepted)
        self.assertTrue(result.to_dict()["approved_partial_zeia"])

    def test_directory_discovery_uses_all_explicit_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "one.zeia"
            second = root / "two.zeia"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            models = [_model(str(first)), _model(str(second))]
            with patch(
                "tecan_reader.full_export_readiness.ingest_zeia",
                side_effect=models,
            ):
                result = resolve_full_export_readiness(root)
            self.assertEqual(result.signals["archive_count"], 2)


if __name__ == "__main__":
    unittest.main()
