import tempfile
import unittest
import zipfile
from pathlib import Path

import fluent_pipeline.project_context as pc
from fluent_pipeline.script_analysis import (
    _rank_subroutine_matches,
    _subroutines,
    analyze_script,
    render_script_analysis_markdown,
)


class SubroutineMatchTests(unittest.TestCase):
    def test_rank_prefers_exact_object_name_over_stem(self):
        scripts = [
            {"object_name": "Other", "entry": "Subroutines/ScanTubes.xscr"},
            {"object_name": "ScanTubes", "entry": "Subroutines/ScanTubes.xscr"},
        ]
        ranked = _rank_subroutine_matches("ScanTubes", scripts)
        self.assertEqual(ranked[0]["object_name"], "ScanTubes")

    def test_subroutines_flags_ambiguous_matches(self):
        class _Ctx:
            manifest = {
                "scripts": [
                    {"object_name": "ScanTubes", "entry": "a/ScanTubes.xscr", "source_context": "ctxA"},
                    {"object_name": "ScanTubes", "entry": "b/ScanTubes.xscr", "source_context": "ctxB"},
                ]
            }

        result = _subroutines(_Ctx(), {"subroutine_refs": ['"ScanTubes"']})
        self.assertEqual(result[0]["status"], "ambiguous")
        self.assertEqual(len(result[0]["alternatives"]), 1)

    def test_subroutines_unique_match_is_resolved(self):
        class _Ctx:
            manifest = {"scripts": [{"object_name": "ScanTubes", "entry": "a/ScanTubes.xscr"}]}

        result = _subroutines(_Ctx(), {"subroutine_refs": ['"ScanTubes"']})
        self.assertEqual(result[0]["status"], "resolved")
        self.assertNotIn("alternatives", result[0])


class ScriptAnalysisTests(unittest.TestCase):
    def test_analyze_script_reports_manual_commands_and_improvements(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "analysis.zeia"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr("Scripts/main.xscr", _xscr("MainScript"))
                    zf.writestr("Scripts/get-fingers.xscr", _xscr("GetFingers"))
                    zf.writestr(
                        "Worktables/base.xwsp",
                        """<?xml version="1.0"?>
<Workspace>
  <ObjectName>Base Worktable</ObjectName>
  <Guid>workspace-guid</Guid>
</Workspace>
""",
                    )

                ctx = pc.import_project(archive, name="analysis-context")
                report = analyze_script(
                    ctx,
                    script_index=1,
                    out_dir=tmp_path / "reports",
                )
                markdown = render_script_analysis_markdown(report)

                self.assertEqual(report["script"]["object_name"], "MainScript")
                self.assertEqual(report["subroutines"][0]["status"], "resolved")
                self.assertIn("GIO1_Pin20", report["custom_parts"]["pin_refs"])
                self.assertGreaterEqual(len(report["manual_commands"]), 2)
                self.assertIn("Aspirate", markdown)
                self.assertIn("Potential Improvements", markdown)
                self.assertTrue(Path(report["artifacts"]["script_analysis_markdown"]).exists())
                self.assertTrue(Path(report["artifacts"]["recreate_markdown"]).exists())
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file


def _xscr(object_name: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{object_name}</ObjectName>
    <Reference>
      <Guid>workspace-guid</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>Base Worktable</ObjectName>
    </Reference>
    <Reference>
      <Guid>lc-guid</Guid>
      <TypeId>LiquidClass</TypeId>
      <ObjectName>Water Free Single</ObjectName>
    </Reference>
    <PayloadData>
      <Script>
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
                <ScriptGroupDataV1>
                  <Name>Setup</Name>
                  <Data>
                    <Statements>
                      <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
                        <AddLabwareDataV1>
                          <LabwareType>96 Well Flat</LabwareType>
                          <LabwareLable>SourcePlate</LabwareLable>
                          <Location>Site</Location>
                          <Position>1</Position>
                          <Rotation>0</Rotation>
                          <HasLid>False</HasLid>
                          <Data><LineNumber>2</LineNumber></Data>
                        </AddLabwareDataV1>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384AspirateScriptCommandDataV2">
                        <Mca384AspirateScriptCommandDataV2>
                          <LiquidClassName>Water Free Single</LiquidClassName>
                          <Volume>20</Volume>
                          <ScriptCommandCommonDataV2>
                            <LabwareName>SourcePlate</LabwareName>
                            <DeviceAlias>Instrument=1/Device=MCA384:1</DeviceAlias>
                            <LineNumber>3</LineNumber>
                          </ScriptCommandCommonDataV2>
                        </Mca384AspirateScriptCommandDataV2>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
                        <SubRoutine>"Subroutines\\GetFingers"</SubRoutine>
                        <PinNumber>GIO1_Pin20</PinNumber>
                        <Location>WorktablePin_MiddleFront</Location>
                      </Object>
                    </Statements>
                  </Data>
                </ScriptGroupDataV1>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""


if __name__ == "__main__":
    unittest.main()
