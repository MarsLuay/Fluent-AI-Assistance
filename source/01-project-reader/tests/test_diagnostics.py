from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tecan_reader.diagnostics import (
    DiagnosticCode,
    IngestionArchiveError,
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
