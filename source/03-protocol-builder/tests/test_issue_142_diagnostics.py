from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

from fluent_pipeline.application_services import (
    FullExportValidationRequest,
    InputInspectionRequest,
    inspect_input,
    validate_full_export,
)
from fluent_pipeline.cli.parser import _build_parser
from fluent_pipeline.cli.runtime import main
from tecan_reader.diagnostics import DiagnosticCode


class Issue142DiagnosticSurfaceTests(unittest.TestCase):
    def _complete_archive(self, root: Path) -> Path:
        archive = root / "complete.zeia"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Scripts/main.xscr", "<Root><ObjectName>Main</ObjectName><Script /></Root>")
            zf.writestr("Objects/worktable.xwsp", "<Root><ObjectName>Worktable</ObjectName></Root>")
            zf.writestr("Objects/water.xlqc", "<Root><ObjectName>Water</ObjectName></Root>")
        return archive

    def test_application_services_return_stable_inspection_and_readiness_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._complete_archive(Path(tmp))
            inspected = inspect_input(InputInspectionRequest(archive))
            validated = validate_full_export(FullExportValidationRequest(input_path=archive))

        self.assertTrue(inspected.ok)
        self.assertEqual(inspected.to_dict()["schema_version"], "tecan.input_inspection.v1")
        self.assertEqual(validated.readiness["status"], "complete")
        self.assertTrue(validated.ok)
        self.assertEqual(validated.to_dict()["offline_validation"]["source"], "tecan_reader.full_export_readiness")

    def test_cli_registers_inspect_validate_and_keeps_json_on_stdout(self) -> None:
        self.assertEqual(_build_parser().parse_args(["inspect", "--input", "input.zeia"]).cmd, "inspect")
        self.assertEqual(_build_parser().parse_args(["validate", "--input", "input.zeia"]).cmd, "validate")
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.zeia"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["inspect", "--input", str(missing), "--json"])
        self.assertEqual(exit_code, 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["diagnostics"][0]["code"], DiagnosticCode.INPUT_NOT_FOUND)
        self.assertEqual(payload["classification"], "missing")


if __name__ == "__main__":
    unittest.main()
