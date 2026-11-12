from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
import tecan_reader.full_export_readiness as readiness_module
from tecan_reader.full_export_readiness import (
    ReadinessStatus,
    UnresolvedReference,
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

    def test_unresolved_diagnostic_uses_native_substring_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native,
            "ascii_case_insensitive_substring_match_values",
        ):
            self.skipTest("optional Rust substring matcher is not built")
        items = [
            UnresolvedReference(
                "synthetic.zeia",
                "entry",
                "entity",
                "target",
                "unknown",
                diagnostic_code=code,
            )
            for code in (
                "DEPENDENCY_AMBIGUOUS",
                "DEPENDENCY_AMBIGUOUſ",
                "DEPENDENCY_UNRESOLVED",
            )
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [readiness_module._unresolved_diagnostic(item) for item in items]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_case_insensitive_substring_match_values",
                wraps=native_acceleration.ascii_case_insensitive_substring_match_values,
            ) as native_call:
                actual = [readiness_module._unresolved_diagnostic(item) for item in items]
                self.assertEqual(native_call.call_count, 3)
        self.assertEqual(actual, expected)
        self.assertEqual(
            [item["code"] for item in actual],
            [
                DiagnosticCode.DEPENDENCY_AMBIGUOUS,
                DiagnosticCode.DEPENDENCY_AMBIGUOUS,
                DiagnosticCode.DEPENDENCY_UNRESOLVED,
            ],
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

    def test_native_record_fingerprint_matches_python_casefold_and_sorting(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "project_record_fingerprint"
        ):
            self.skipTest("optional Rust readiness fingerprint is not built")
        records = [
            {},
            {
                "kind": "WorkSpace",
                "object_name": "Straße",
                "type_id": "WorkTable",
                "guids": ["Z", "ä", "A", "ß", "a"],
                "names": ("Ωmega", "omega", "e\\u0301", "é"),
                "functional_group": "İstanbul",
                "provenance": {"source_archive": "ignored"},
            },
            {
                "kind": "",
                "object_name": None,
                "type_id": "Typeİ",
                "guids": [],
                "names": None,
                "functional_group": "Σςσ",
            },
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                readiness_module.native_acceleration,
                "project_record_fingerprint_required",
                return_value=None,
            ):
                expected = [
                    readiness_module._record_fingerprint(record) for record in records
                ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "project_record_fingerprint",
                wraps=native_acceleration._native.project_record_fingerprint,
            ) as rust_call:
                actual = [
                    readiness_module._record_fingerprint(record) for record in records
                ]
                self.assertEqual(rust_call.call_count, len(records))
        self.assertEqual(actual, expected)
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(
                readiness_module._record_fingerprint(records[1]), expected[1]
            )
        self.assertEqual(actual[1][0:4], ("workspace", "strasse", "worktable", "a,a,ss,z,ä"))
        self.assertEqual(actual[1][4], "e\\u0301,omega,é,ωmega")
        self.assertEqual(actual[1][5], "i̇stanbul")

    def test_native_record_fingerprint_falls_back_for_non_string_values(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "project_record_fingerprint"
        ):
            self.skipTest("optional Rust readiness fingerprint is not built")
        class StringSubclass(str):
            pass

        records = [
            {"kind": 0, "object_name": "Object"},
            {"kind": False, "guids": 0},
            {"guids": ["known", 42]},
            {"names": [StringSubclass("subclass")]},
            {"functional_group": {"custom": "value"}},
            {"object_name": "\ud800"},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                readiness_module.native_acceleration,
                "project_record_fingerprint_required",
                return_value=None,
            ):
                expected = [
                    readiness_module._record_fingerprint(record) for record in records
                ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            for record in records:
                self.assertIsNone(native_acceleration.project_record_fingerprint(record))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "project_record_fingerprint",
                wraps=native_acceleration._native.project_record_fingerprint,
            ) as rust_call:
                actual = [
                    readiness_module._record_fingerprint(record) for record in records
                ]
                self.assertEqual(rust_call.call_count, len(records))
        self.assertEqual(actual, expected)

    def test_casefold_text_uses_rust_with_exact_python_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_casefold_trim"
        ):
            self.skipTest("optional Rust ASCII casefold helper is not built")

        class StringSubclass(str):
            pass

        values = ["Workspace", "Straße", " Name ", StringSubclass("Label")]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [readiness_module._casefold_text(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [readiness_module._casefold_text(value) for value in values]
                self.assertEqual(native_call.call_count, 2)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["workspace", "strasse", " name ", "label"])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration, "ascii_casefold_trim", return_value=None
            ):
                fallback = [readiness_module._casefold_text(value) for value in values]
        self.assertEqual(fallback, expected)

    def test_duplicate_identifier_is_ambiguous(self) -> None:
        result = resolve_full_export_readiness(
            [
                _model("one.zeia", objects=[_object("workspace", "Same", "one")]),
                _model("two.zeia", objects=[_object("workspace", "Same", "two")]),
            ]
        )
        self.assertEqual(result.status, ReadinessStatus.AMBIGUOUS)
        self.assertEqual(result.conflicts[0].identifier, "same")

        unicode_result = resolve_full_export_readiness(
            [
                _model(
                    "one.zeia",
                    objects=[_object("workspace", "Straße", "one")],
                ),
                _model(
                    "two.zeia",
                    objects=[_object("workspace", "STRASSE", "two")],
                ),
            ]
        )
        self.assertEqual(unicode_result.status, ReadinessStatus.AMBIGUOUS)
        self.assertEqual(unicode_result.conflicts[0].identifier, "strasse")

    def test_unicode_casefold_reference_lookup_matches_python(self) -> None:
        models = [
            _model(
                "source.zeia",
                scripts=[
                    _script(
                        "Source",
                        reference={"object_name": "STRASSE", "type_id": "reference"},
                    )
                ],
            ),
            _model(
                "target.zeia",
                objects=[_object("workspace", "Straße", "workspace-guid")],
            ),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = resolve_full_export_readiness(models).to_dict()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = resolve_full_export_readiness(models).to_dict()
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["unresolved_references"], [])

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

    def test_native_readiness_kind_token_matches_ascii_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "readiness_kind_token"
        ):
            self.skipTest("optional Rust readiness token normalizer is not built")
        ascii_values = [
            None,
            "",
            "WorktableWorkspace",
            "Worktable_Workspace",
            "A-1._x",
            "  spaces / and punctuation!",
            "".join(chr(value) for value in range(128)),
            *(chr(value) for value in range(128)),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                readiness_module.native_acceleration,
                "readiness_kind_token_required",
                return_value=None,
            ):
                expected = [
                    readiness_module._kind_token(value) for value in ascii_values
                ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "readiness_kind_token",
                wraps=native_acceleration._native.readiness_kind_token,
            ) as rust_call:
                actual = [readiness_module._kind_token(value) for value in ascii_values]
                self.assertEqual(rust_call.call_count, len(ascii_values))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[2], "worktableworkspace")
        self.assertEqual(actual[3], "worktableworkspace")
        self.assertEqual(actual[4], "a1x")
        self.assertEqual(
            actual[6],
            "0123456789abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
        )

        class StringSubclass(str):
            pass

        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(readiness_module._kind_token("A-B"), "ab")

        fallback_values = [
            "Straße", "é", "İ", "中", 0, False, StringSubclass("A-B"), "\ud800"
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                readiness_module.native_acceleration,
                "readiness_kind_token_required",
                return_value=None,
            ):
                expected_fallback = [
                    readiness_module._kind_token(value) for value in fallback_values
                ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertTrue(
                all(
                    native_acceleration.readiness_kind_token(value) is None
                    for value in fallback_values
                )
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "readiness_kind_token",
                wraps=native_acceleration._native.readiness_kind_token,
            ) as rust_call:
                actual_fallback = [
                    readiness_module._kind_token(value) for value in fallback_values
                ]
                rust_call.assert_not_called()
        self.assertEqual(actual_fallback, expected_fallback)
        self.assertEqual(actual_fallback[:4], ["strasse", "é", "i", "中"])
        self.assertEqual(actual_fallback[4:], ["", "", "ab", ""])

    def test_native_readiness_candidate_projection_matches_python_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "readiness_candidate"
        ):
            self.skipTest("optional Rust readiness candidate projector is not built")
        records = [
            {},
            {
                "provenance": {
                    "source_archive": "source.zeia",
                    "entry_path": "Objects/Worktable.xml",
                },
                "entry": "ignored.xml",
                "kind": "workspace",
                "object_name": "Worktable",
            },
            {
                "provenance": {"source_archive": "", "entry_path": ""},
                "entry": "Objects/fallback.xml",
                "kind": "",
                "object_name": "",
            },
            {
                "provenance": None,
                "entry": "Scripts/β.xscr",
                "kind": None,
                "object_name": "Straße",
            },
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [readiness_module._candidate(record) for record in records]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                readiness_module.native_acceleration,
                "readiness_candidate",
                wraps=native_acceleration.readiness_candidate,
            ) as native_call:
                actual = [readiness_module._candidate(record) for record in records]
                self.assertEqual(native_call.call_count, len(records))
        self.assertEqual(actual, expected)
        self.assertEqual(
            list(actual[0]),
            ["source_archive", "source_entry", "kind", "object_name"],
        )
        self.assertEqual(
            actual[0],
            {
                "source_archive": "",
                "source_entry": "",
                "kind": "object",
                "object_name": "",
            },
        )
        self.assertEqual(actual[1]["source_entry"], "Objects/Worktable.xml")
        self.assertEqual(actual[2]["source_entry"], "Objects/fallback.xml")
        self.assertEqual(actual[3]["source_entry"], "Scripts/β.xscr")

        class DictSubclass(dict):
            pass

        unsupported = [
            {"kind": 42, "object_name": "Object"},
            {"provenance": {"source_archive": 42}},
            {"entry": ["preserve", "list"]},
            DictSubclass({"kind": "subclass"}),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = [
                readiness_module._candidate(record) for record in unsupported
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertTrue(
                all(
                    native_acceleration.readiness_candidate(record) is None
                    for record in unsupported
                )
            )
            actual_fallback = [
                readiness_module._candidate(record) for record in unsupported
            ]
        self.assertEqual(actual_fallback, expected_fallback)
        self.assertEqual(actual_fallback[0]["kind"], 42)
        self.assertEqual(actual_fallback[1]["source_archive"], 42)
        self.assertEqual(actual_fallback[2]["source_entry"], ["preserve", "list"])

    def test_readiness_label_casefolding_uses_rust_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_casefold_equals"
        ):
            self.skipTest("optional Rust ASCII comparison is not built")
        model = _model(
            "source.zeia",
            scripts=[_script("Source")],
            objects=[
                _object("WORKSPACE", "Worktable", "workspace"),
                _object("LIQUID_CLASS", "Water", "water"),
            ],
            errors=[
                {"severity": "WARNING", "entry": "metadata.xml", "error": "notice"}
            ],
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = resolve_full_export_readiness(model).to_dict()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_call:
                actual = resolve_full_export_readiness(model).to_dict()
                self.assertGreaterEqual(native_call.call_count, 4)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["status"], ReadinessStatus.COMPLETE_WITH_WARNINGS.value)
        self.assertIsNone(
            native_acceleration.ascii_casefold_equals("warníng", "warning")
        )

    def test_legacy_reference_id_uses_native_ascii_comparison_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_casefold_equals"
        ):
            self.skipTest("optional Rust ASCII comparison is not built")
        items = [
            UnresolvedReference("synthetic.zeia", "entry", "entity", "target", target)
            for target in (
                "WORKTABLEWORKSPACE",
                "LIQUID_CLASSES",
                "WorktableWorkſpace",
                "other",
            )
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [readiness_module._legacy_reference_id(item) for item in items]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_call:
                actual = [readiness_module._legacy_reference_id(item) for item in items]
                self.assertEqual(native_call.call_count, 6)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                "missing_referenced_worktables",
                "missing_liquid_class_objects",
                "missing_referenced_worktables",
                "unresolved_script_references",
            ],
        )
        self.assertIsNone(
            native_acceleration.ascii_casefold_equals(
                "WorktableWorkſpace", "worktableworkspace"
            )
        )

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
