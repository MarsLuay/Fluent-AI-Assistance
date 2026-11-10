import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_reader import script
from tecan_reader.script import inspect_xscr, inspect_xscr_text


SAMPLE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="3">
  <Payload>
    <ObjectName>UnitTestScript</ObjectName>
    <Reference>
      <Guid>00000001-4321-eeee-ffff-000000000001</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>Base Worktable</ObjectName>
    </Reference>
    <PayloadData>
      <Script version="2.0">
        <Properties>
          <VariableDeclarations>
            <VariableDeclarations>
              <VariableDeclarations>
                <anyType xmlns:i="http://www.w3.org/2001/XMLSchema-instance" i:type="VariableDefinitionHelper">
                  <Name>Vol</Name>
                  <TypeName>Floating Point</TypeName>
                  <Scope>Script</Scope>
                </anyType>
              </VariableDeclarations>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
        <Commands>
          <Object Type="FluentControl.Fca.FcaPipettingCommand" family="LiHa/FCA">
            <LiquidClassName>Water Free Single</LiquidClassName>
            <SourceLabwareName>SourcePlate</SourceLabwareName>
          </Object>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum>PATTERN</Checksum>
</sd:VxData>
"""


class ScriptTests(unittest.TestCase):
    def test_inspect_xscr_reads_from_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test.xscr"
            tmp_path.write_text(SAMPLE_XSCR)

            report = inspect_xscr(tmp_path)

            self.assertEqual(report["object_name"], "UnitTestScript")
            self.assertEqual(report["kind"], "xscr")
            self.assertEqual(report["source"], str(tmp_path))
            self.assertEqual(report["command_count"], 1)

    def test_ordered_command_counts_use_rust_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ordered_ascii_string_counts"
        ):
            self.skipTest("optional Rust ordered counter is not built")
        values = ["Zeta", "Alpha", "Zeta", "Beta", "Alpha", "Gamma"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_counts = script._ordered_counts(values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ordered_ascii_string_counts",
                wraps=native_acceleration.ordered_ascii_string_counts,
            ) as native_call:
                actual_counts = script._ordered_counts(values)
                native_call.assert_called_once_with(values)
        self.assertEqual(list(actual_counts.items()), list(expected_counts.items()))
        self.assertEqual(
            list(actual_counts.items()),
            [("Zeta", 2), ("Alpha", 2), ("Beta", 1), ("Gamma", 1)],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.ordered_ascii_string_counts(["É", "A", "É"])
            )
            self.assertEqual(
                script._ordered_counts(["É", "A", "É"]), {"É": 2, "A": 1}
            )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_report = inspect_xscr_text(SAMPLE_XSCR, source_name="test.xscr")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ordered_ascii_string_counts",
                wraps=native_acceleration.ordered_ascii_string_counts,
            ) as native_call:
                actual_report = inspect_xscr_text(
                    SAMPLE_XSCR, source_name="test.xscr"
                )
                self.assertEqual(native_call.call_count, 2)
        self.assertEqual(actual_report, expected_report)

    def test_inspect_xscr_uses_explicit_source_name(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test.xscr"
            tmp_path.write_text(SAMPLE_XSCR)

            report = inspect_xscr(tmp_path, source_name="custom_script.xscr")

            self.assertEqual(report["object_name"], "UnitTestScript")
            self.assertEqual(report["kind"], "xscr")
            self.assertEqual(report["source"], "custom_script.xscr")
            self.assertEqual(report["command_count"], 1)
