from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.dump_error_scan import scan_fluent_dump_errors


class DumpErrorScanTests(unittest.TestCase):
    def test_scans_ascii_and_utf16_script_editor_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "SystemSW.exe.1.dmp"
            dump.write_bytes(
                b"header Mismatching If-Else branches footer\x00"
                + "Command \"ResolvexA200_Run\" is unknown".encode("utf-16-le")
            )

            report = scan_fluent_dump_errors(Path(tmp), since_days=1)

        self.assertEqual(
            {finding["id"] for finding in report["findings"]},
            {
                "fluent_log.if_else_branches_mismatched",
                "fluent_log.resolvex_a200_command_unknown",
            },
        )

    def test_ignores_old_dump_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "SystemSW.exe.old.dmp"
            dump.write_text("Select a valid labware", encoding="utf-8")
            old_time = 0
            os.utime(dump, (old_time, old_time))

            report = scan_fluent_dump_errors(Path(tmp), since_days=1)

        self.assertEqual(report["findings"], [])


if __name__ == "__main__":
    unittest.main()
