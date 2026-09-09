import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tecan_reader.script import inspect_xscr


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

    def test_inspect_xscr_uses_explicit_source_name(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test.xscr"
            tmp_path.write_text(SAMPLE_XSCR)

            report = inspect_xscr(tmp_path, source_name="custom_script.xscr")

            self.assertEqual(report["object_name"], "UnitTestScript")
            self.assertEqual(report["kind"], "xscr")
            self.assertEqual(report["source"], "custom_script.xscr")
            self.assertEqual(report["command_count"], 1)
