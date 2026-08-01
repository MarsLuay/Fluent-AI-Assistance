from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.minimal_edit import compare_xscr_minimal_edit, extract_xscr_command_records


XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>ExistingScript</ObjectName>
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
                          <LabwareType>24 Filter Plate</LabwareType>
                          <LabwareLable>FilterDWP[001]</LabwareLable>
                          <Location>NestPlatform</Location>
                          <Position>3</Position>
                          <Rotation>0</Rotation>
                          <HasLid>False</HasLid>
                          <Data><LineNumber>12</LineNumber></Data>
                        </AddLabwareDataV1>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.UserPromptStatement">
                        <UserPromptStatement>
                          <Prompt>Confirm the stack is seated.</Prompt>
                          <Timeout>0</Timeout>
                          <Data><LineNumber>13</LineNumber></Data>
                        </UserPromptStatement>
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
  <Checksum>original</Checksum>
</VxData>
"""


class MinimalEditTests(unittest.TestCase):
    def test_extracts_statement_records_without_group_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.xscr"
            path.write_text(XSCR, encoding="utf-8")

            records = extract_xscr_command_records(path)

        self.assertEqual([item.command_id for item in records], ["AddLabwareDataV1", "UserPromptStatement"])
        self.assertEqual(records[0].group, "Setup")
        self.assertEqual(records[0].line_number, "12")

    def test_line_number_and_checksum_noise_is_not_a_command_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original.xscr"
            edited = Path(tmp) / "edited.xscr"
            original.write_text(XSCR, encoding="utf-8")
            edited.write_text(
                XSCR.replace("<LineNumber>12</LineNumber>", "<LineNumber>99</LineNumber>").replace(
                    "<Checksum>original</Checksum>",
                    "<Checksum>edited</Checksum>",
                ),
                encoding="utf-8",
            )

            report = compare_xscr_minimal_edit(original, edited)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"]["change_count"], 0)

    def test_unapproved_command_change_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original.xscr"
            edited = Path(tmp) / "edited.xscr"
            original.write_text(XSCR, encoding="utf-8")
            edited.write_text(XSCR.replace("<Position>3</Position>", "<Position>1</Position>"), encoding="utf-8")

            report = compare_xscr_minimal_edit(original, edited)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["summary"]["unapproved_change_count"], 1)
        self.assertEqual(report["changes"][0]["original_index"], 1)

    def test_allowed_command_change_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original.xscr"
            edited = Path(tmp) / "edited.xscr"
            original.write_text(XSCR, encoding="utf-8")
            edited.write_text(XSCR.replace("<Position>3</Position>", "<Position>1</Position>"), encoding="utf-8")

            report = compare_xscr_minimal_edit(original, edited, allowed_command_indexes={1})

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"]["approved_change_count"], 1)
        self.assertEqual(report["changes"][0]["approval_reason"], "original command index 1 was allowed")


if __name__ == "__main__":
    unittest.main()
