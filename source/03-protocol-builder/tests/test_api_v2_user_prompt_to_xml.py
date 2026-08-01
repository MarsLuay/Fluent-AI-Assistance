"""Tests for api-v2-045 UserPrompt.ToXML()."""

from __future__ import annotations

import unittest
from fluent_pipeline import xml_compat as ET

from fluent_pipeline.api_v2.commands import (
    UserPrompt,
    command_from_ir_step,
    command_from_xscr_object,
    command_to_xml,
    user_prompt_from_ir_step,
)
from fluent_pipeline.api_v2.golden_compare import compare_xscr_commands_to_native_xml, golden_compare_summary
from fluent_pipeline.api_v2.types import ApiV2ValidationError
from fluent_pipeline.api_v2.xml_compare import compare_command_xml, extract_command_objects_from_xscr
from fluent_pipeline.protocol_ir import MEDIA_PLACEHOLDER_BEGIN


USER_PROMPT_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.UserPromptStatement">
  <UserPromptStatement>
    <Prompt>Confirm deck setup.</Prompt>
    <AutoClose>False</AutoClose>
    <Timeout>0</Timeout>
    <SoundFile />
    <RepeatSound>False</RepeatSound>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString>#FFFFFF00</StatusLightColorString>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>7</LineNumber>
  </UserPromptStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

USER_PROMPT_TIMEOUT_ONE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.UserPromptStatement">
  <UserPromptStatement>
    <Prompt>Confirm to continue.</Prompt>
    <AutoClose>False</AutoClose>
    <Timeout>1</Timeout>
    <SoundFile />
    <RepeatSound>False</RepeatSound>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString>#FFFFFF00</StatusLightColorString>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>6</LineNumber>
  </UserPromptStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

USER_PROMPT_DISABLED_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.UserPromptStatement">
  <UserPromptStatement>
    <Prompt>Disabled host-only prompt.</Prompt>
    <AutoClose>True</AutoClose>
    <Timeout>1</Timeout>
    <SoundFile />
    <RepeatSound>False</RepeatSound>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString>#FFFFFF00</StatusLightColorString>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>True</IsDisabledForExecution>
    <LineNumber>3</LineNumber>
  </UserPromptStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

RUP_WORKTABLE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPWorktableStatement">
  <RUPWorktableStatement>
    <WorktableProperties>
      <WorktableStatementDataClass>
        <SelectedLabware>
          <ConfigureDataLabwareDataModel>
            <LabwareName></LabwareName>
            <LabwareTypeName></LabwareTypeName>
            <IsLabwareExistenceMandatory>false</IsLabwareExistenceMandatory>
            <LabwareImageState>UseAsLabwareImageOnTheLeft</LabwareImageState>
            <Grid>0</Grid>
            <Site>0</Site>
            <LabwareState>Highlighted</LabwareState>
            <LabwareDescription />
            <LabwareDescriptionGui>Confirm the adapter is seated.</LabwareDescriptionGui>
            <ActiveWorktableVisibility>Collapsed</ActiveWorktableVisibility>
            <ActiveWorktable>0</ActiveWorktable>
            <CustomDetailImageFilePath>media/step_009_video.gif</CustomDetailImageFilePath>
            <IsCustomDetailImageUsed>true</IsCustomDetailImageUsed>
            <IsCarrier>false</IsCarrier>
            <MarkerColorAlpha>192</MarkerColorAlpha>
            <MarkerColorRed>255</MarkerColorRed>
            <MarkerColorGreen>153</MarkerColorGreen>
            <MarkerColorBlue>0</MarkerColorBlue>
          </ConfigureDataLabwareDataModel>
        </SelectedLabware>
        <WorktableFilePath />
        <IsCustomWorktableShown>false</IsCustomWorktableShown>
        <IsAdvancedLabwaresettingsUsed>false</IsAdvancedLabwaresettingsUsed>
        <DefaultItemState>Deactivated</DefaultItemState>
        <IsUserAproveRequired>false</IsUserAproveRequired>
      </WorktableStatementDataClass>
    </WorktableProperties>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>9</LineNumber>
    <RUPScreenTitle>Adapter check</RUPScreenTitle>
    <RUPDisplayAndWait>True</RUPDisplayAndWait>
    <RUPAutoClose>False</RUPAutoClose>
    <RUPTimeOut>1</RUPTimeOut>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString />
  </RUPWorktableStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

RUP_WORKTABLE_BOUND_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPWorktableStatement">
  <RUPWorktableStatement>
    <WorktableProperties>
      <WorktableStatementDataClass>
        <SelectedLabware>
          <ConfigureDataLabwareDataModel>
            <LabwareName>AdapterA200</LabwareName>
            <LabwareTypeName>Microplate</LabwareTypeName>
            <IsLabwareExistenceMandatory>false</IsLabwareExistenceMandatory>
            <LabwareImageState>UseAsLabwareImageOnTheLeft</LabwareImageState>
            <Grid>9</Grid>
            <Site>2</Site>
            <LabwareState>Highlighted</LabwareState>
            <LabwareDescription />
            <LabwareDescriptionGui>Confirm the adapter is seated.</LabwareDescriptionGui>
            <ActiveWorktableVisibility>Collapsed</ActiveWorktableVisibility>
            <ActiveWorktable>0</ActiveWorktable>
            <CustomDetailImageFilePath>media/step_009_video.gif</CustomDetailImageFilePath>
            <IsCustomDetailImageUsed>true</IsCustomDetailImageUsed>
            <IsCarrier>false</IsCarrier>
            <MarkerColorAlpha>192</MarkerColorAlpha>
            <MarkerColorRed>255</MarkerColorRed>
            <MarkerColorGreen>153</MarkerColorGreen>
            <MarkerColorBlue>0</MarkerColorBlue>
          </ConfigureDataLabwareDataModel>
        </SelectedLabware>
        <WorktableFilePath />
        <IsCustomWorktableShown>false</IsCustomWorktableShown>
        <IsAdvancedLabwaresettingsUsed>false</IsAdvancedLabwaresettingsUsed>
        <DefaultItemState>Deactivated</DefaultItemState>
        <IsUserAproveRequired>false</IsUserAproveRequired>
      </WorktableStatementDataClass>
    </WorktableProperties>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>9</LineNumber>
    <RUPScreenTitle>Adapter check</RUPScreenTitle>
    <RUPDisplayAndWait>True</RUPDisplayAndWait>
    <RUPAutoClose>False</RUPAutoClose>
    <RUPTimeOut>1</RUPTimeOut>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString />
  </RUPWorktableStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

RUP_PROMPT_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement">
  <RUPStandardStatement>
    <StandardProperties>
      <StandardStatementDataClass>
        <SelectedImagePath>media/step_001_image.png</SelectedImagePath>
        <MessageText>Make sure the fingers are parallel.</MessageText>
        <SelectedSoundPath />
        <RepeatValue />
        <PercentCompleteString />
        <PercentComplete>0</PercentComplete>
        <ShowProgress>false</ShowProgress>
        <DisplayStartTime>false</DisplayStartTime>
        <DisplayEndTime>false</DisplayEndTime>
        <DisplayPercentage>false</DisplayPercentage>
        <DisplayTimeRemaining>false</DisplayTimeRemaining>
        <DisplayTimeElapsed>false</DisplayTimeElapsed>
        <VariableScope />
      </StandardStatementDataClass>
    </StandardProperties>
    <CheckMessagePrompt>Make sure the fingers are parallel.</CheckMessagePrompt>
    <PercentComplete>0</PercentComplete>
    <PercentCompleteString></PercentCompleteString>
    <RepeatValue></RepeatValue>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>3</LineNumber>
    <RUPScreenTitle>Verification</RUPScreenTitle>
    <RUPDisplayAndWait>True</RUPDisplayAndWait>
    <RUPAutoClose>False</RUPAutoClose>
    <RUPTimeOut>0</RUPTimeOut>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString></StatusLightColorString>
  </RUPStandardStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

RUP_PROMPT_VARIABLE_SCOPE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement">
  <RUPStandardStatement>
    <StandardProperties>
      <StandardStatementDataClass>
        <SelectedImagePath>media/step_001_image.png</SelectedImagePath>
        <MessageText>Make sure the fingers are parallel.</MessageText>
        <SelectedSoundPath />
        <RepeatValue />
        <PercentCompleteString>0</PercentCompleteString>
        <PercentComplete>0</PercentComplete>
        <ShowProgress>false</ShowProgress>
        <DisplayStartTime>false</DisplayStartTime>
        <DisplayEndTime>false</DisplayEndTime>
        <DisplayPercentage>false</DisplayPercentage>
        <DisplayTimeRemaining>false</DisplayTimeRemaining>
        <DisplayTimeElapsed>false</DisplayTimeElapsed>
        <VariableScope>Method|Verification_Script1|1</VariableScope>
      </StandardStatementDataClass>
    </StandardProperties>
    <CheckMessagePrompt>Make sure the fingers are parallel.</CheckMessagePrompt>
    <PercentComplete>0</PercentComplete>
    <PercentCompleteString>0</PercentCompleteString>
    <RepeatValue></RepeatValue>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>3</LineNumber>
    <RUPScreenTitle>Verification</RUPScreenTitle>
    <RUPDisplayAndWait>True</RUPDisplayAndWait>
    <RUPAutoClose>False</RUPAutoClose>
    <RUPTimeOut>1</RUPTimeOut>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString></StatusLightColorString>
  </RUPStandardStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""


class UserPromptToXmlTests(unittest.TestCase):
    def test_plain_user_prompt_roundtrip_from_xscr(self):
        records = extract_command_objects_from_xscr(USER_PROMPT_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, UserPrompt)
        self.assertFalse(command.is_rup_standard)
        result = compare_command_xml(records[0]["xml"], command_to_xml(command))
        self.assertTrue(result.equal, result.diff_hint)

    def test_plain_user_prompt_preserves_false_autoclose_with_timeout_one(self):
        records = extract_command_objects_from_xscr(USER_PROMPT_TIMEOUT_ONE_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        xml = command_to_xml(command)
        self.assertIn("<AutoClose>False</AutoClose>", xml)
        self.assertIn("<Timeout>1</Timeout>", xml)
        result = compare_command_xml(records[0]["xml"], xml)
        self.assertTrue(result.equal, result.diff_hint)

    def test_plain_user_prompt_preserves_disabled_flag(self):
        records = extract_command_objects_from_xscr(USER_PROMPT_DISABLED_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertTrue(command.is_disabled_for_execution)
        xml = command_to_xml(command)
        self.assertIn("<IsDisabledForExecution>True</IsDisabledForExecution>", xml)
        result = compare_command_xml(records[0]["xml"], xml)
        self.assertTrue(result.equal, result.diff_hint)

    def test_rup_standard_roundtrip_from_xscr(self):
        records = extract_command_objects_from_xscr(RUP_PROMPT_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, UserPrompt)
        self.assertTrue(command.is_rup_standard)
        reserialized = command_to_xml(command)
        self.assertIn("RUPStandardStatement", reserialized)
        self.assertIn("media/step_001_image.png", reserialized)
        self.assertIn("Make sure the fingers are parallel.", reserialized)
        reparsed = command_from_xscr_object(
            ET.fromstring(reserialized),
            command_id="RUPStandardStatement",
        )
        self.assertIsInstance(reparsed, UserPrompt)
        result = compare_command_xml(reserialized, command_to_xml(reparsed))
        self.assertTrue(result.equal, result.diff_hint)

    def test_rup_standard_preserves_variable_scope_from_xscr(self):
        records = extract_command_objects_from_xscr(RUP_PROMPT_VARIABLE_SCOPE_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertEqual(command.variable_scope, "Method|Verification_Script1|1")
        xml = command_to_xml(command)
        self.assertIn(
            "<VariableScope>Method|Verification_Script1|1</VariableScope>",
            xml,
        )
        result = compare_command_xml(records[0]["xml"], xml)
        self.assertTrue(result.equal, result.diff_hint)

    def test_rup_worktable_roundtrip_from_xscr(self):
        records = extract_command_objects_from_xscr(RUP_WORKTABLE_XSCR)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["command_id"], "RUPWorktableStatement")
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, UserPrompt)
        self.assertEqual(command.rup_kind, "worktable")
        self.assertFalse(command.is_rup_standard)
        self.assertEqual(command.image_path, "media/step_009_video.gif")
        self.assertEqual(command.prompt, "Confirm the adapter is seated.")
        reserialized = command_to_xml(command)
        self.assertIn("RUPWorktableStatement", reserialized)
        self.assertIn("CustomDetailImageFilePath", reserialized)
        self.assertIn("LabwareDescriptionGui", reserialized)
        result = compare_command_xml(records[0]["xml"], reserialized)
        self.assertTrue(result.equal, result.diff_hint)

    def test_rup_worktable_preserves_selected_labware_metadata_from_xscr(self):
        records = extract_command_objects_from_xscr(RUP_WORKTABLE_BOUND_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, UserPrompt)
        self.assertEqual(command.selected_labware_name, "AdapterA200")
        self.assertEqual(command.selected_labware_type, "Microplate")
        self.assertEqual(command.grid, "9")
        self.assertEqual(command.site, "2")
        result = compare_command_xml(records[0]["xml"], command_to_xml(command))
        self.assertTrue(result.equal, result.diff_hint)

    def test_worktable_user_prompt_emits_detail_image_and_labware_gui(self):
        command = UserPrompt(
            prompt="Confirm the adapter is seated.",
            image_path="media/step_009_video.gif",
            screen_title="Adapter check",
            line_number=9,
            rup_kind="worktable",
        )
        xml = command_to_xml(command)
        self.assertIn("RUPWorktableStatement", xml)
        self.assertIn("<CustomDetailImageFilePath>media/step_009_video.gif</CustomDetailImageFilePath>", xml)
        self.assertIn(
            "<LabwareDescriptionGui>Confirm the adapter is seated.</LabwareDescriptionGui>",
            xml,
        )
        self.assertIn("<Grid>0</Grid>", xml)
        self.assertIn("<Site>0</Site>", xml)
        self.assertNotIn("RUPStandardStatement", xml)
        self.assertNotIn("SelectedImagePath", xml)

    def test_worktable_user_prompt_blanks_variable_indexed_selected_labware(self):
        command = UserPrompt(
            prompt="Put the first capped 50 mL source tube in the tube runner.",
            image_path="media/step_016_image.png",
            screen_title="Tube check",
            line_number=16,
            rup_kind="worktable",
            selected_labware_name="SampleSourceTube[NumSourceTubes_Main]",
            selected_labware_type="Tube",
            grid=31,
            site=1,
        )

        xml = command_to_xml(command)

        self.assertIn("<LabwareName></LabwareName>", xml)
        self.assertIn("<LabwareTypeName></LabwareTypeName>", xml)
        self.assertNotIn("NumSourceTubes_Main", xml)
        self.assertIn("<Grid>31</Grid>", xml)
        self.assertIn("<Site>1</Site>", xml)

    def test_user_prompt_from_ir_step_prefers_image_slot_for_worktable(self):
        command = user_prompt_from_ir_step(
            {
                "operation": "prompt_user",
                "line_number": 9,
                "parameters": {
                    "prompt": "Confirm the adapter is seated.",
                    "screen_title": "Adapter check",
                    "media_placeholders": [
                        {
                            "kind": "image",
                            "slot": "step_009",
                            "path": "media/step_009_image.png",
                        },
                        {
                            "kind": "video",
                            "slot": "step_009",
                            "path": "media/step_009_video.gif",
                        },
                    ],
                },
            },
        )
        self.assertEqual(command.rup_kind, "worktable")
        self.assertEqual(command.image_path, "media/step_009_image.png")
        xml = command_to_xml(command)
        self.assertIn("RUPWorktableStatement", xml)
        self.assertIn("media/step_009_image.png", xml)
        self.assertNotIn("media/step_009_video.gif", xml)

    def test_ir_prompt_user_with_image_emits_rup_worktable(self):
        command = command_from_ir_step(
            {
                "operation": "prompt_user",
                "parameters": {
                    "prompt": "Check fingers",
                    "image_path": "media/step_001_image.png",
                    "screen_title": "Arm verification",
                },
                "line_number": 5,
            },
        )
        self.assertIsInstance(command, UserPrompt)
        xml = command_to_xml(command)
        self.assertIn("RUPWorktableStatement", xml)
        self.assertIn("CustomDetailImageFilePath", xml)
        self.assertIn("media/step_001_image.png", xml)
        self.assertIn("Arm verification", xml)

    def test_media_marker_stripped_from_prompt_text(self):
        prompt = f"Confirm deck state. {MEDIA_PLACEHOLDER_BEGIN} attach image here]"
        command = UserPrompt(prompt=prompt)
        xml = command_to_xml(command)
        self.assertIn("Confirm deck state.", xml)
        self.assertNotIn(MEDIA_PLACEHOLDER_BEGIN, xml)

    def test_auto_close_when_timeout_set(self):
        command = UserPrompt(prompt="Wait five seconds", timeout=5, line_number=2)
        xml = command_to_xml(command)
        self.assertIn("<AutoClose>True</AutoClose>", xml)
        self.assertIn("<Timeout>5</Timeout>", xml)

    def test_plain_user_prompt_can_play_sound_and_auto_close(self):
        command = UserPrompt(
            prompt="Audio cue.",
            timeout=1,
            auto_close=True,
            sound_path=r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Script_media\step001.mp3",
        )
        xml = command_to_xml(command)
        self.assertIn("UserPromptStatement", xml)
        self.assertIn("<AutoClose>True</AutoClose>", xml)
        self.assertIn("<Timeout>1</Timeout>", xml)
        self.assertIn("<SoundFile>C:\\ProgramData\\Tecan\\VisionX\\TouchToolsData\\Images\\Script_media\\step001.mp3</SoundFile>", xml)

    def test_rup_standard_uses_real_percent_complete_string_zero(self):
        command = UserPrompt(
            prompt="Make sure the fingers are parallel.",
            timeout=0,
            rup_timeout=1,
            image_path="media/step_001_image.png",
            screen_title="Verification",
            line_number=3,
            rup_kind="standard",
        )
        xml = command_to_xml(command)
        self.assertIn("<PercentCompleteString>0</PercentCompleteString>", xml)
        self.assertIn("<RUPTimeOut>1</RUPTimeOut>", xml)

    def test_generated_rup_standard_template_uses_real_percent_complete_string_zero(self):
        from fluentcoder.compiler.renderer import Renderer

        template = Renderer().commands["UserPromptImage"]["template"]
        self.assertIn("<PercentCompleteString>0</PercentCompleteString>", template)

    def test_validate_blocks_empty_prompt_before_to_xml(self):
        with self.assertRaises(ApiV2ValidationError):
            UserPrompt(prompt="   ").to_xml()

    def test_validate_blocks_shared_placeholder_prompt(self):
        with self.assertRaises(ApiV2ValidationError):
            UserPrompt(prompt="n/a").to_xml()

    def test_golden_compare_matches_user_prompt_in_xscr(self):
        findings = compare_xscr_commands_to_native_xml(xscr_text=USER_PROMPT_XSCR)
        summary = golden_compare_summary(findings)
        self.assertEqual(summary["mismatch_count"], 0)
        self.assertGreaterEqual(summary["matched_count"], 1)


if __name__ == "__main__":
    unittest.main()
