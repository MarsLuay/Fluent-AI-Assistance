from __future__ import annotations

from collections import Counter
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_reader import project_model
from tecan_reader.project_model import (
    build_completeness_metadata,
    CANONICAL_PROJECT_MODEL_SCHEMA_VERSION,
    CanonicalProjectModel,
    SourceProvenance,
    normalize_record,
)


class ProjectModelTests(unittest.TestCase):
    def test_completeness_count_keys_use_native_sort_for_unicode(self) -> None:
        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in ("sort_string_keys", "sorted_unique_ascii_strings")
        ):
            self.skipTest("optional Rust mapping sorter is not built")
        arguments = {
            "script_limit": None,
            "object_limit": None,
            "eligible_member_counts": {"scripts": 4, "objects": 3},
            "summarized_counts": {"scripts": 4, "objects": 3},
            "oversized_members": ("zeta", "alpha", "zeta"),
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = build_completeness_metadata(**arguments)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                project_model,
                "_native_sort_string_keys",
                wraps=native_acceleration.sort_string_keys,
            ) as native_call:
                actual = build_completeness_metadata(**arguments)
                self.assertEqual(native_call.call_count, 2)
            with patch.object(
                project_model,
                "_native_sorted_unique_ascii_strings",
                wraps=native_acceleration.sorted_unique_ascii_strings,
            ) as unique_call:
                actual = build_completeness_metadata(**arguments)
                unique_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(actual["oversized_members"], ["alpha", "zeta"])
        self.assertEqual(
            list(actual["eligible_member_counts"]), ["objects", "scripts"]
        )

        unicode_arguments = {
            **arguments,
            "eligible_member_counts": {"é": 1, "a": 2},
            "summarized_counts": {"é": 1, "a": 2},
            "oversized_members": ("é", "a", "é"),
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_sorted = native_acceleration.sort_string_keys(
                unicode_arguments["eligible_member_counts"]
            )
            self.assertIsNotNone(native_sorted)
            self.assertEqual(list(native_sorted), ["a", "é"])
            self.assertIsNone(
                native_acceleration.sorted_unique_ascii_strings([["é", "a", "é"]])
            )
            unicode_result = build_completeness_metadata(**unicode_arguments)
            self.assertEqual(
                unicode_result["eligible_member_counts"], {"a": 2, "é": 1}
            )
            self.assertEqual(unicode_result["oversized_members"], ["a", "é"])

    def test_native_string_key_sort_matches_python_unicode_order(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "sort_string_keys"
        ):
            self.skipTest("optional Rust mapping sorter is not built")
        marker = object()
        mapping = {
            "𝄞": marker,
            "é": "accented",
            "a": "ascii",
            "Ω": "greek",
            "e\u0301": "combining",
            "😀": "emoji",
            "Å": "ring",
            "ß": "sharp-s",
        }
        expected = dict(sorted(mapping.items()))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual = native_acceleration.sort_string_keys(mapping)
        self.assertIsNotNone(actual)
        self.assertEqual(list(actual), list(expected))
        self.assertEqual(actual, expected)
        self.assertIs(actual["𝄞"], marker)

        surrogate_mapping = {chr(0xD800): "surrogate", "a": "ascii"}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.sort_string_keys(surrogate_mapping))

    def test_inspection_counter_aggregation_uses_native_stable_order(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "most_common_ascii_counts"
        ):
            self.skipTest("optional Rust counter ordering is not built")
        commands = {f"Command{index:02d}": 1 for index in range(42)}
        scripts = [
            {
                "object_name": "First",
                "family_counts": {"Zeta": 2, "Alpha": 2, "Beta": 1},
                "command_counts": commands,
                "warnings": ["WarnA", "WarnB", "WarnA"],
            },
            {
                "object_name": "Second",
                "family_counts": {"Beta": 1},
                "command_counts": {"Command00": 1},
                "warnings": ["WarnB"],
            },
        ]
        model = CanonicalProjectModel(
            source_archive="input.zeia",
            adapter_id="test",
            detection={"status": "supported"},
            scripts=scripts,
            objects=[],
            worklists=[],
            errors=[],
            source_metadata={},
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = model.inspection_report()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                project_model,
                "_native_most_common_ascii_counts",
                wraps=native_acceleration.most_common_ascii_counts,
            ) as native_call:
                actual = model.inspection_report()
                self.assertEqual(native_call.call_count, 3)
        self.assertEqual(actual, expected)
        self.assertEqual(
            list(actual["family_counts"].items()),
            [("Zeta", 2), ("Alpha", 2), ("Beta", 2)],
        )
        self.assertEqual(
            list(actual["command_counts"]),
            [f"Command{index:02d}" for index in range(40)],
        )
        self.assertEqual(list(actual["warning_counts"].items()), [("WarnA", 2), ("WarnB", 2)])

        unicode_counts = Counter({"é": 2, "a": 1})
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.most_common_ascii_counts(dict(unicode_counts))
            )
            self.assertEqual(
                project_model._most_common_counts(unicode_counts),
                dict(unicode_counts.most_common()),
            )

    def test_model_json_uses_native_compact_serializer_with_python_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "canonical_json_compact"
        ):
            self.skipTest("optional Rust compact JSON serializer is not built")
        model = CanonicalProjectModel(
            source_archive="input.zeia",
            adapter_id="test",
            detection={"name": "café", "flags": [True, None]},
            scripts=[],
            objects=[],
            worklists=[],
            errors=[],
            source_metadata={"nested": {"z": 1, "a": "μ"}},
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = json.dumps(
                model.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                project_model,
                "_native_canonical_json_compact",
                wraps=native_acceleration.canonical_json_compact,
            ) as native_call:
                actual = model.to_json()
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertNotIn(": ", actual)

        nonfinite_model = CanonicalProjectModel(
            source_archive="input.zeia",
            adapter_id="test",
            detection={"measurement": float("nan")},
            scripts=[],
            objects=[],
            worklists=[],
            errors=[],
            source_metadata={},
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            nonfinite_expected = json.dumps(
                nonfinite_model.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.canonical_json_compact(
                    nonfinite_model.to_dict()
                )
            )
            self.assertEqual(nonfinite_model.to_json(), nonfinite_expected)

    def test_provenance_is_json_serializable_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record = normalize_record(
                {
                    "source": "Scripts/main.xscr",
                    "object_name": "Main",
                    "checksum": "abc",
                    "source_metadata": {"unknown_fields": {"NewField": ["value"]}},
                },
                kind="script",
                source_archive=Path(tmp) / "input.zeia",
            )
            model = CanonicalProjectModel(
                source_archive=str((Path(tmp) / "input.zeia").resolve()),
                adapter_id="test",
                detection={"status": "supported"},
                scripts=[record],
                objects=[],
                worklists=[],
                errors=[],
                source_metadata={"entry_count": 1, "extension_counts": {".xscr": 1}},
            )

            first = model.to_json()
            second = model.to_json()
            payload = json.loads(first)

        self.assertEqual(first, second)
        self.assertEqual(payload["schema_version"], CANONICAL_PROJECT_MODEL_SCHEMA_VERSION)
        self.assertEqual(payload["scripts"][0]["provenance"]["entry_path"], "Scripts/main.xscr")
        self.assertEqual(
            payload["scripts"][0]["provenance"]["original_identifiers"]["object_name"],
            "Main",
        )
        self.assertEqual(
            payload["scripts"][0]["source_metadata"]["unknown_fields"]["NewField"],
            ["value"],
        )

    def test_source_identifier_sorting_uses_native_unicode_sort(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "sort_string_keys"
        ):
            self.skipTest("optional Rust mapping sorter is not built")
        provenance = SourceProvenance(
            source_archive="input.zeia",
            entry_path="Scripts/main.xscr",
            original_identifiers={"guid": "abc", "object_name": "Main"},
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = provenance.to_dict()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                project_model,
                "_native_sort_string_keys",
                wraps=native_acceleration.sort_string_keys,
            ) as native_call:
                actual = provenance.to_dict()
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual["original_identifiers"]), ["guid", "object_name"])

        unicode_provenance = SourceProvenance(
            source_archive="input.zeia",
            entry_path="Scripts/main.xscr",
            original_identifiers={"é": "value", "a": "first"},
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_sorted = native_acceleration.sort_string_keys(
                unicode_provenance.original_identifiers
            )
            self.assertIsNotNone(native_sorted)
            self.assertEqual(list(native_sorted), ["a", "é"])
            self.assertEqual(
                list(unicode_provenance.to_dict()["original_identifiers"]),
                ["a", "é"],
            )

    def test_source_provenance_shape(self) -> None:
        provenance = SourceProvenance(
            source_archive="input.zeia",
            entry_path="Scripts/main.xscr",
            original_identifiers={"guid": "abc", "object_name": "Main"},
        )
        self.assertEqual(
            provenance.to_dict(),
            {
                "source_archive": "input.zeia",
                "entry_path": "Scripts/main.xscr",
                "original_identifiers": {"guid": "abc", "object_name": "Main"},
            },
        )


if __name__ == "__main__":
    unittest.main()
