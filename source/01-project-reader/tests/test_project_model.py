from __future__ import annotations

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
    def test_completeness_count_keys_use_native_sort_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in ("sort_ascii_string_keys", "sorted_unique_ascii_strings")
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
                "_native_sort_ascii_string_keys",
                wraps=native_acceleration.sort_ascii_string_keys,
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
            self.assertIsNone(
                native_acceleration.sort_ascii_string_keys(
                    unicode_arguments["eligible_member_counts"]
                )
            )
            self.assertIsNone(
                native_acceleration.sorted_unique_ascii_strings([["é", "a", "é"]])
            )
            unicode_result = build_completeness_metadata(**unicode_arguments)
            self.assertEqual(
                unicode_result["eligible_member_counts"], {"a": 2, "é": 1}
            )
            self.assertEqual(unicode_result["oversized_members"], ["a", "é"])

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

    def test_source_identifier_sorting_uses_native_ascii_and_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "sort_ascii_string_keys"
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
                "_native_sort_ascii_string_keys",
                wraps=native_acceleration.sort_ascii_string_keys,
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
            self.assertIsNone(
                native_acceleration.sort_ascii_string_keys(
                    unicode_provenance.original_identifiers
                )
            )
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
