from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tecan_common import native_acceleration
import tecan_reader.diagnostics as diagnostics_module
from tecan_reader.diagnostics import (
    DiagnosticCode,
    IngestionArchiveError,
    diagnostic_message,
    make_diagnostic,
    sort_diagnostics,
)
from tecan_reader.zeia_adapters import ZeiaFormatError, ingest_zeia, probe_zeia


class IngestionDiagnosticTests(unittest.TestCase):
    def test_serialization_and_sorting_are_deterministic(self) -> None:
        later = make_diagnostic(
            DiagnosticCode.PARSER_FAILED,
            "later",
            archive_path="archive.zeia",
            entry_path="b.xscr",
        )
        earlier = make_diagnostic(
            DiagnosticCode.ENCODING_DECODE_FAILED,
            "earlier",
            archive_path="archive.zeia",
            entry_path="a.xscr",
        )
        ordered = sort_diagnostics([later, earlier])
        self.assertEqual([item["entry_path"] for item in ordered], ["a.xscr", "b.xscr"])
        self.assertEqual(json.dumps(ordered, sort_keys=True), json.dumps(sort_diagnostics([later, earlier]), sort_keys=True))
        self.assertEqual(ordered[0]["schema_version"], "tecan.ingestion_diagnostic.v1")

    def test_native_diagnostic_message_preserves_fallback_cases(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "diagnostic_message"
        ):
            self.skipTest("optional Rust diagnostic message helper is not built")
        supported = [
            {"message": "specific", "code": "code"},
            {"message": "", "code": "code"},
            {"message": None, "code": ""},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [diagnostic_message(item) for item in supported]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                diagnostics_module,
                "_native_diagnostic_message",
                wraps=diagnostics_module._native_diagnostic_message,
            ) as native_message:
                actual = [diagnostic_message(item) for item in supported]
                self.assertEqual(native_message.call_count, len(supported))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["specific", "code", "ingestion diagnostic"])

        unsupported = {"message": 0, "code": "fallback"}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                diagnostics_module,
                "_native_diagnostic_message",
                wraps=diagnostics_module._native_diagnostic_message,
            ) as native_message:
                self.assertEqual(diagnostic_message(unsupported), "fallback")
                native_message.assert_called_once_with(0, "fallback")
                self.assertIsNone(native_acceleration.diagnostic_message(0, "fallback"))

    def test_native_sort_is_stable_and_keeps_shallow_copy_semantics(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "diagnostic_sort_indices"
        ):
            self.skipTest("optional Rust diagnostic sorter is not built")
        nested = {"mutable": []}
        later = {
            "archive_path": "z.zeia",
            "entry_path": "last.xscr",
            "code": "PARSER_FAILED",
            "severity": "error",
            "message": "later",
            "nested": nested,
        }
        tied_first = {
            "archive_path": "a.zeia",
            "entry_path": "same.xscr",
            "code": "PARSER_FAILED",
            "severity": "error",
            "message": "same",
            "tag": "first",
            "nested": nested,
        }
        tied_second = {**tied_first, "tag": "second"}
        earlier = {
            "archive_path": None,
            "entry_path": "first.xscr",
            "code": "ENCODING_DECODE_FAILED",
            "severity": "error",
            "message": "earlier",
        }
        items = [later, tied_first, tied_second, earlier]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = sort_diagnostics(items)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                diagnostics_module,
                "_native_diagnostic_sort_indices",
                wraps=diagnostics_module._native_diagnostic_sort_indices,
            ) as native_sort:
                actual = sort_diagnostics(items)
                native_sort.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual([item.get("tag") for item in actual[1:3]], ["first", "second"])
        self.assertIsNot(actual[1], tied_first)
        self.assertIs(actual[1]["nested"], nested)

        class StringSubclass(str):
            pass

        unsupported = [
            {"archive_path": 10, "entry_path": "b", "code": "c", "severity": "e", "message": "m"},
            {"archive_path": 2, "entry_path": "a", "code": "c", "severity": "e", "message": "m"},
            {"archive_path": StringSubclass("a"), "entry_path": "x", "code": "c", "severity": "e", "message": "m"},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = sort_diagnostics(unsupported)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                diagnostics_module,
                "_native_diagnostic_sort_indices",
                wraps=diagnostics_module._native_diagnostic_sort_indices,
            ) as native_sort:
                actual_fallback = sort_diagnostics(unsupported)
                native_sort.assert_not_called()
        self.assertEqual(actual_fallback, expected_fallback)

    def test_sensitive_message_and_exception_details_are_redacted(self) -> None:
        diagnostic = make_diagnostic(
            DiagnosticCode.PARSER_FAILED,
            "provider token=top-secret",
            exception=ValueError("password: hunter2"),
        )

        self.assertEqual(diagnostic["message"], "provider token=<redacted>")
        self.assertEqual(diagnostic["exception_detail"], "password=<redacted>")
        self.assertNotIn("top-secret", json.dumps(diagnostic))
        self.assertNotIn("hunter2", json.dumps(diagnostic))

    def test_malformed_member_keeps_archive_and_member_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "malformed.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("Scripts/bad.xscr", "<broken")
            model = ingest_zeia(archive)

        diagnostic = model.errors[0]
        self.assertEqual(diagnostic["code"], DiagnosticCode.ZEIA_SCHEMA_MALFORMED)
        self.assertEqual(diagnostic["entry_path"], "Scripts/bad.xscr")
        self.assertEqual(diagnostic["archive_path"], str(archive.resolve()))
        self.assertEqual(diagnostic["classification"], "malformed")

    def test_unsupported_ambiguous_and_limits_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsupported = root / "unsupported.zeia"
            with zipfile.ZipFile(unsupported, "w") as zf:
                zf.writestr("README.txt", "not a ZEIA")
            ambiguous = root / "ambiguous.zeia"
            with zipfile.ZipFile(ambiguous, "w") as zf:
                zf.writestr("DataStore/v2.xscr", '<VxData dataStoreVersion="2"><Script /></VxData>')
                zf.writestr("DataStore/v3.xscr", '<VxData dataStoreVersion="3"><Script /></VxData>')
            limited = root / "limited.zeia"
            with zipfile.ZipFile(limited, "w") as zf:
                zf.writestr("Scripts/main.xscr", "<Root><Script /></Root>")

            unsupported_result = probe_zeia(unsupported)
            ambiguous_result = probe_zeia(ambiguous)
            with self.assertRaises(ZeiaFormatError) as ambiguous_error:
                ingest_zeia(ambiguous)
            with self.assertRaises(IngestionArchiveError) as limit_error:
                probe_zeia(limited, max_entry_count=0)

        self.assertEqual(unsupported_result.diagnostic_records[0]["code"], DiagnosticCode.ZEIA_UNKNOWN_FORMAT)
        self.assertEqual(ambiguous_result.diagnostic_records[0]["code"], DiagnosticCode.ZEIA_AMBIGUOUS_FORMAT)
        self.assertEqual(ambiguous_error.exception.result.diagnostic_records[0]["code"], DiagnosticCode.ZEIA_AMBIGUOUS_FORMAT)
        self.assertEqual(limit_error.exception.diagnostic["code"], DiagnosticCode.LIMIT_ENTRY_COUNT)


if __name__ == "__main__":
    unittest.main()
