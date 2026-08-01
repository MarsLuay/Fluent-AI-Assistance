"""Tests for api-v2-005 ICommand.ToXML() scaffold and golden XML compare."""

from __future__ import annotations

import tempfile
import unittest
from fluent_pipeline import xml_compat as ET
from pathlib import Path

from fluentcoder.expressions import NumberLiteral, StringLiteral
from fluent_pipeline.api_v2.commands import (
    AddLabware,
    DropFingers,
    GenericCommand,
    GetFingers,
    QueryVariable,
    RUPVariable,
    SetVariable,
    TransferLabware,
    UserPrompt,
    command_from_ir_step,
    command_from_xscr_object,
    command_to_xml,
)
from fluent_pipeline.api_v2.golden_compare import (
    compare_xscr_commands_to_native_xml,
    golden_compare_summary,
)
from fluent_pipeline.api_v2.xml_compare import (
    compare_command_xml,
    extract_command_objects_from_xscr,
    normalize_command_xml,
)
from fluent_pipeline.protocol_ir import migrate_protocol_ir
from fluent_pipeline.validation import _gate_post_compile_xscr, validate_ready_to_import


ADD_LABWARE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
  <AddLabwareDataV1>
    <LabwareType>96 Well Flat</LabwareType>
    <LabwareLable>SourcePlate</LabwareLable>
    <Location>Site</Location>
    <Position>1</Position>
    <Rotation>0</Rotation>
    <HasLid>False</HasLid>
    <Data><LineNumber>3</LineNumber></Data>
  </AddLabwareDataV1>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

ADD_LABWARE_DISABLED_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
  <AddLabwareDataV1>
    <LabwareType>Adapter A200</LabwareType>
    <LabwareLable>AdapterA200</LabwareLable>
    <Location>Demo_Nest_Pos</Location>
    <Position>1</Position>
    <Rotation>0</Rotation>
    <HasLid>False</HasLid>
    <Data Type="Tecan.Core.Scripting.Programming.ProgrammingStatementBaseDataV1">
      <ProgrammingStatementBaseDataV1>
        <IsBreakpoint>False</IsBreakpoint>
        <IsDisabledForExecution>True</IsDisabledForExecution>
        <LineNumber>2</LineNumber>
      </ProgrammingStatementBaseDataV1>
    </Data>
  </AddLabwareDataV1>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

USER_PROMPT_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.UserPromptStatement">
  <UserPromptStatement>
    <Prompt>Confirm deck setup.</Prompt>
    <AutoClose>False</AutoClose>
    <Timeout>0</Timeout>
    <LineNumber>7</LineNumber>
  </UserPromptStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

GET_FINGERS_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaGetFingersScriptCommandDataV1">
  <CgaGetFingersScriptCommandDataV1>
    <Data Type="Tecan.Core.Instrument.Helpers.Scripting.ScriptCommandCommonDataV2">
      <ScriptCommandCommonDataV2>
        <LabwareName>Eccentric[001]</LabwareName>
        <Data Type="Tecan.Core.Instrument.Helpers.Scripting.DeviceAliasStatementBaseDataV1">
          <DeviceAliasStatementBaseDataV1>
            <Alias Type="Tecan.Core.Instrument.DeviceAlias.DeviceAlias">
              <DeviceAlias>Instrument=1/Device=CGA:1</DeviceAlias>
            </Alias>
            <ID>
              <AvailableID>USB:TECAN,FLUENT,2405000993/CGA:1</AvailableID>
            </ID>
            <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">
              <ScriptStatementBaseDataV1>
                <IsBreakpoint>False</IsBreakpoint>
                <IsDisabledForExecution>False</IsDisabledForExecution>
                <GroupLineNumber>0</GroupLineNumber>
                <LineNumber>41</LineNumber>
              </ScriptStatementBaseDataV1>
            </Data>
          </DeviceAliasStatementBaseDataV1>
        </Data>
        <LiquidClassVariablesNames />
        <LiquidClassVariablesValues />
      </ScriptCommandCommonDataV2>
    </Data>
  </CgaGetFingersScriptCommandDataV1>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

DROP_FINGERS_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1">
  <CgaDropFingersScriptCommandDataV1>
    <UseSourceAsBackPosition>
      <Backs>BackToPosition</Backs>
    </UseSourceAsBackPosition>
    <Data Type="Tecan.Core.Instrument.Helpers.Scripting.ScriptCommandCommonDataV2">
      <ScriptCommandCommonDataV2>
        <LabwareName>FES Centric Nest[001]</LabwareName>
        <LiquidClassVariablesNames />
        <LiquidClassVariablesValues />
        <Data Type="Tecan.Core.Instrument.Helpers.Scripting.DeviceAliasStatementBaseDataV1">
          <DeviceAliasStatementBaseDataV1>
            <Alias Type="Tecan.Core.Instrument.DeviceAlias.DeviceAlias">
              <DeviceAlias>Instrument=1/Device=CGA:1</DeviceAlias>
            </Alias>
            <ID>
              <AvailableID>USB:TECAN,FLUENT,2405000993/CGA:1</AvailableID>
            </ID>
            <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">
              <ScriptStatementBaseDataV1>
                <IsBreakpoint>False</IsBreakpoint>
                <IsDisabledForExecution>False</IsDisabledForExecution>
                <GroupLineNumber>0</GroupLineNumber>
                <LineNumber>44</LineNumber>
              </ScriptStatementBaseDataV1>
            </Data>
          </DeviceAliasStatementBaseDataV1>
        </Data>
      </ScriptCommandCommonDataV2>
    </Data>
  </CgaDropFingersScriptCommandDataV1>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

TRANSFER_TO_BASE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
  <ApplicationDriverMacro Version="1" Name="RGA1_TransferLabware" ModuleName="RGA 1" ExecutionTime="PT2S" IsBreakpoint="false" IsDisabledForExecution="false" LineNumber="31">
    <ExecutionSettings>&amp;lt;TransferLabwareCommandParameters xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Drivers.RobotDriverBase"&amp;gt;&amp;lt;FixedSite&amp;gt;false&amp;lt;/FixedSite&amp;gt;&amp;lt;Labware&amp;gt;AdapterA200&amp;lt;/Labware&amp;gt;&amp;lt;Location&amp;gt;&amp;amp;lt;BASE&amp;amp;gt;&amp;lt;/Location&amp;gt;&amp;lt;MoveToBase&amp;gt;true&amp;lt;/MoveToBase&amp;gt;&amp;lt;OnTheFlyTool&amp;gt;&amp;lt;/OnTheFlyTool&amp;gt;&amp;lt;Site&amp;gt;&amp;lt;/Site&amp;gt;&amp;lt;UseOnTheFlyTool&amp;gt;false&amp;lt;/UseOnTheFlyTool&amp;gt;&amp;lt;/TransferLabwareCommandParameters&amp;gt;</ExecutionSettings>
  </ApplicationDriverMacro>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

EXECUTE_VECTOR_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
  <ApplicationDriverMacro Version="1" Name="RGA1_ExecuteSingleVector" ModuleName="RGA 1" ExecutionTime="PT2S" IsBreakpoint="false" IsDisabledForExecution="false" LineNumber="16">
    <ExecutionSettings>&amp;lt;ExecuteSingleVectorCommandParameters xmlns:i="http://www.w3.org/2001/XMLSchema-instance"&amp;gt;&amp;lt;VectorName&amp;gt;MoveUp&amp;lt;/VectorName&amp;gt;&amp;lt;/ExecuteSingleVectorCommandParameters&amp;gt;</ExecutionSettings>
  </ApplicationDriverMacro>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

SET_VARIABLE_STRING_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.SetVariableStatement">
  <SetVariableStatement>
    <Name>LocationNameA200</Name>
    <Value>"Demo_Device_Pos"</Value>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>9</LineNumber>
  </SetVariableStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

SET_VARIABLE_NUMERIC_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.SetVariableStatement">
  <SetVariableStatement>
    <Name>GripperClose</Name>
    <Value>11</Value>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>8</LineNumber>
  </SetVariableStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

QUERY_VARIABLE_SIMPLE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.QueryVariableStatement">
  <QueryVariableStatement>
    <Name>user_barcode</Name>
    <QueryPrompt>Enter tube barcode manually</QueryPrompt>
    <LimitRange>False</LimitRange>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>34</LineNumber>
  </QueryVariableStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

QUERY_VARIABLE_LIMITED_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.QueryVariableStatement">
  <QueryVariableStatement>
    <Name>quit_flag</Name>
    <QueryPrompt>Do you want to quit? (0-no 1-yes)</QueryPrompt>
    <LimitRange>True</LimitRange>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>41</LineNumber>
    <Minimum Type="System.Double">
      <double>0</double>
    </Minimum>
    <MinimumText>0</MinimumText>
    <Maximum Type="System.Double">
      <double>1</double>
    </Maximum>
    <MaximumText>1</MaximumText>
  </QueryVariableStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

RUP_VARIABLE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPVariableStatement">
  <RUPVariableStatement>
    <VariableDatas>
      <VariableDataModel>
        <Instructions>Please enter the values below</Instructions>
        <IsLayoutHorizontalOriented>true</IsLayoutHorizontalOriented>
        <UnresolvedNumberOfColumns>3</UnresolvedNumberOfColumns>
        <NumberOfColumns>3</NumberOfColumns>
        <Variables>
          <RupVariableItem>
            <VariableName>CapsOnTube</VariableName>
            <VariableType>System.String</VariableType>
            <DisplayText>Source Tubes have caps?</DisplayText>
            <DisplayType>Combobox</DisplayType>
            <VariableValue />
            <AllowedValues>yes;no</AllowedValues>
            <UnresolvedDisabledWhenValue />
            <IsEnabled>true</IsEnabled>
            <UnresolvedMinValue />
            <MinValue>0</MinValue>
            <UnresolvedMaxValue />
            <MaxValue>0</MaxValue>
            <IsMinMaxValueUsed>false</IsMinMaxValueUsed>
          </RupVariableItem>
        </Variables>
        <VariableScope />
      </VariableDataModel>
    </VariableDatas>
    <IsBreakpoint>False</IsBreakpoint>
    <IsDisabledForExecution>False</IsDisabledForExecution>
    <LineNumber>3</LineNumber>
    <RUPScreenTitle>User Input</RUPScreenTitle>
    <RUPDisplayAndWait>True</RUPDisplayAndWait>
    <RUPAutoClose>False</RUPAutoClose>
    <RUPTimeOut>1</RUPTimeOut>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString></StatusLightColorString>
  </RUPVariableStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""


class ApiV2CommandSerializerTests(unittest.TestCase):
    def test_add_labware_roundtrip_from_xscr(self):
        records = extract_command_objects_from_xscr(ADD_LABWARE_XSCR)
        self.assertEqual(len(records), 1)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, AddLabware)
        native_xml = command_to_xml(command)
        result = compare_command_xml(records[0]["xml"], native_xml)
        self.assertTrue(result.equal, result.diff_hint)

    def test_add_labware_roundtrip_preserves_disabled_flag(self):
        records = extract_command_objects_from_xscr(ADD_LABWARE_DISABLED_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, AddLabware)
        self.assertTrue(command.is_disabled_for_execution)
        native_xml = command_to_xml(command, validate=False)
        self.assertIn("<IsDisabledForExecution>True</IsDisabledForExecution>", native_xml)
        result = compare_command_xml(records[0]["xml"], native_xml)
        self.assertTrue(result.equal, result.diff_hint)

    def test_user_prompt_parses_and_serializes(self):
        records = extract_command_objects_from_xscr(USER_PROMPT_XSCR)
        command = command_from_xscr_object(
            ET.fromstring(records[0]["xml"]),
            command_id=records[0]["command_id"],
        )
        self.assertIsInstance(command, UserPrompt)
        xml = command_to_xml(command)
        self.assertIn("Confirm deck setup.", xml)
        self.assertIn("UserPromptStatement", xml)

    def test_get_fingers_roundtrip_from_xscr(self):
        records = extract_command_objects_from_xscr(GET_FINGERS_XSCR)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["command_id"], "CgaGetFingersScriptCommandDataV1")
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, GetFingers)
        self.assertEqual(command.labware_name, "Eccentric[001]")
        self.assertEqual(command.device_alias, "Instrument=1/Device=CGA:1")
        native_xml = command_to_xml(command)
        self.assertIn("CgaGetFingersScriptCommandDataV1", native_xml)
        self.assertIn("Eccentric[001]", native_xml)

    def test_get_fingers_from_ir_step(self):
        command = command_from_ir_step(
            {
                "operation": "cga_get_fingers",
                "line_number": 12,
                "parameters": {
                    "labware_name": "Centric",
                    "device_alias": "Instrument=1/Device=CGA:1",
                    "available_id": "USB:TECAN,FLUENT,2405000993/CGA:1",
                },
            },
        )
        self.assertIsInstance(command, GetFingers)
        xml = command_to_xml(command)
        self.assertIn("CgaGetFingersScriptCommandDataV1", xml)
        self.assertIn("Centric", xml)
        self.assertIn("Instrument=1/Device=CGA:1", xml)

    def test_drop_fingers_roundtrip_preserves_back_to_position(self):
        records = extract_command_objects_from_xscr(DROP_FINGERS_XSCR)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["command_id"], "CgaDropFingersScriptCommandDataV1")
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, DropFingers)
        self.assertEqual(command.use_source_as_back_position, "BackToPosition")
        native_xml = command_to_xml(command)
        self.assertIn("<Backs>BackToPosition</Backs>", native_xml)
        result = compare_command_xml(records[0]["xml"], native_xml)
        self.assertTrue(result.equal, result.diff_hint)

    def test_add_labware_to_xml_defaults_to_real_nested_data_block(self):
        xml = command_to_xml(
            AddLabware(
                labware_type="Adapter A200",
                labware_label="AdapterA200",
                location="Demo_Nest_Pos",
                site=1,
                line_number=2,
            )
        )
        self.assertIn(
            '<Data Type="Tecan.Core.Scripting.Programming.ProgrammingStatementBaseDataV1">',
            xml,
        )
        self.assertIn("<ProgrammingStatementBaseDataV1>", xml)
        self.assertIn("<LineNumber>2</LineNumber>", xml)

    def test_transfer_labware_roundtrip_preserves_real_macro_payload(self):
        records = extract_command_objects_from_xscr(TRANSFER_TO_BASE_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, TransferLabware)
        native_xml = command_to_xml(command, validate=False)
        self.assertIn('ModuleName="RGA 1"', native_xml)
        self.assertIn('IsBreakpoint="false"', native_xml)
        self.assertIn('LineNumber="31"', native_xml)
        self.assertIn("&amp;lt;Site&amp;gt;&amp;lt;/Site&amp;gt;", native_xml)
        result = compare_command_xml(records[0]["xml"], native_xml)
        self.assertTrue(result.equal, result.diff_hint)

    def test_transfer_labware_from_ir_uses_canonical_site_expression(self):
        ir = {
            "ir_version": "tecan.protocol_ir.v2",
            "expression_schema_version": "fluent_control.expression.v1",
            "id": "transfer_labware_site_expression",
            "protocol": {"name": "TransferLabware site expression"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "variables": [
                {
                    "name": "DestinationSite",
                    "type": "Integer",
                    "default_expression": {"kind": "number_literal", "value": 3},
                }
            ],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "move_plate",
                    "target_labware": "SourcePlate",
                    "parameters": {
                        "labware": "SourcePlate",
                        "destination_location": "NestPlatform",
                        "module_name": "RGA 1",
                        "destination_site_expression": {
                            "kind": "variable_reference",
                            "name": "DestinationSite",
                        },
                    },
                }
            ],
        }

        normalized = migrate_protocol_ir(ir)
        params = normalized["steps"][0]["parameters"]
        self.assertIn("site_expression", params)
        self.assertNotIn("destination_site_expression", params)

        command = command_from_ir_step(normalized["steps"][0])

        self.assertIsInstance(command, TransferLabware)
        self.assertEqual(command.site, "DestinationSite")
        self.assertIn("&amp;lt;Site&amp;gt;DestinationSite&amp;lt;/Site&amp;gt;", command.to_execution_settings())

    def test_non_transfer_application_driver_macro_stays_raw_generic(self):
        records = extract_command_objects_from_xscr(EXECUTE_VECTOR_XSCR)
        element = ET.fromstring(records[0]["xml"])
        command = command_from_xscr_object(element, command_id=records[0]["command_id"])
        self.assertIsInstance(command, GenericCommand)
        self.assertIn("RGA1_ExecuteSingleVector", command_to_xml(command))
        result = compare_command_xml(records[0]["xml"], command_to_xml(command))
        self.assertTrue(result.equal, result.diff_hint)

    def test_command_from_ir_step_prompt_user(self):
        command = command_from_ir_step(
            {
                "operation": "prompt_user",
                "parameters": {"prompt": "Check fingers", "image_path": "media/step_001_image.png"},
            },
        )
        self.assertIsInstance(command, UserPrompt)
        self.assertIn("CustomDetailImageFilePath", command_to_xml(command))
        self.assertIn("RUPWorktableStatement", command_to_xml(command))

    def test_set_variable_roundtrip_from_real_export(self):
        for snippet, expected_value in (
            (SET_VARIABLE_STRING_XSCR, StringLiteral(value="Demo_Device_Pos")),
            (SET_VARIABLE_NUMERIC_XSCR, NumberLiteral(value=11)),
        ):
            records = extract_command_objects_from_xscr(snippet)
            command = command_from_xscr_object(
                ET.fromstring(records[0]["xml"]),
                command_id=records[0]["command_id"],
            )
            self.assertIsInstance(command, SetVariable)
            self.assertEqual(command.value, expected_value)
            result = compare_command_xml(records[0]["xml"], command_to_xml(command))
            self.assertTrue(result.equal, result.diff_hint)

    def test_set_variable_from_ir_step_quotes_string_literals(self):
        command = command_from_ir_step(
            {
                "operation": "set_variable",
                "parameters": {"variable": "TubeLocationName", "value": "Demo_Tube_Pos_1"},
            },
        )
        self.assertIsInstance(command, SetVariable)
        xml = command_to_xml(command)
        self.assertIn('<Value>"Demo_Tube_Pos_1"</Value>', xml)

    def test_query_variable_roundtrip_from_real_exports(self):
        for snippet in (QUERY_VARIABLE_SIMPLE_XSCR, QUERY_VARIABLE_LIMITED_XSCR):
            records = extract_command_objects_from_xscr(snippet)
            command = command_from_xscr_object(
                ET.fromstring(records[0]["xml"]),
                command_id=records[0]["command_id"],
            )
            self.assertIsInstance(command, QueryVariable)
            result = compare_command_xml(records[0]["xml"], command_to_xml(command))
            self.assertTrue(result.equal, result.diff_hint)

    def test_query_variable_from_ir_step_emits_limit_range_blocks(self):
        command = command_from_ir_step(
            {
                "operation": "query_variable",
                "parameters": {
                    "variable": "quit_flag",
                    "prompt": "Do you want to quit? (0-no 1-yes)",
                    "minimum": 0,
                    "maximum": 1,
                },
            },
        )
        self.assertIsInstance(command, QueryVariable)
        self.assertTrue(command.limit_range)
        xml = command_to_xml(command)
        self.assertIn("<LimitRange>True</LimitRange>", xml)
        self.assertIn("<MinimumText>0</MinimumText>", xml)
        self.assertIn("<MaximumText>1</MaximumText>", xml)

    def test_rup_variable_roundtrip_from_real_export(self):
        records = extract_command_objects_from_xscr(RUP_VARIABLE_XSCR)
        command = command_from_xscr_object(
            ET.fromstring(records[0]["xml"]),
            command_id=records[0]["command_id"],
        )
        self.assertIsInstance(command, RUPVariable)
        self.assertEqual(command.variables[0]["name"], "CapsOnTube")
        result = compare_command_xml(records[0]["xml"], command_to_xml(command))
        self.assertTrue(result.equal, result.diff_hint)

    def test_rup_variable_from_ir_step_matches_verification_selector(self):
        command = command_from_ir_step(
            {
                "operation": "runtime_variable_prompt",
                "parameters": {
                    "screen_title": "Select tests",
                    "variables": [
                        {
                            "name": "RunArmCheck",
                            "display_text": "Run arm check?",
                            "allowed_values": ['"yes"', '"no"'],
                        }
                    ],
                },
            },
        )
        self.assertIsInstance(command, RUPVariable)
        xml = command_to_xml(command)
        self.assertIn("RunArmCheck", xml)
        self.assertIn("<AllowedValues>yes;no</AllowedValues>", xml)

    def test_rup_variable_textbox_alias_serializes_as_valid_empty_display_type(self):
        command = command_from_ir_step(
            {
                "operation": "runtime_variable_prompt",
                "parameters": {
                    "screen_title": "TubeEye barcode",
                    "variables": [
                        {
                            "name": "LASTBARCODE",
                            "display_text": "Last TubeEye barcode",
                            "display_type": "TextBox",
                            "enabled": False,
                        }
                    ],
                },
            },
        )
        xml = command_to_xml(command)
        self.assertIn("<DisplayType>Empty</DisplayType>", xml)
        self.assertNotIn("<DisplayType>TextBox</DisplayType>", xml)

    def test_normalize_ignores_line_number(self):
        a = "<Object><Cmd><LineNumber>1</LineNumber><LabwareName>A</LabwareName></Cmd></Object>"
        b = "<Object><Cmd><LineNumber>99</LineNumber><LabwareName>A</LabwareName></Cmd></Object>"
        self.assertEqual(normalize_command_xml(a), normalize_command_xml(b))

    def test_golden_compare_detects_drift(self):
        golden = command_to_xml(
            AddLabware(
                labware_type="96 Well Flat",
                labware_label="SourcePlate",
                location="Site",
                site=1,
            )
        )
        drifted = golden.replace("96 Well Flat", "48 Well Flat")
        result = compare_command_xml(golden, drifted)
        self.assertFalse(result.equal)
        findings = compare_xscr_commands_to_native_xml(xscr_path=None, xscr_text=drifted)
        summary = golden_compare_summary(findings)
        self.assertEqual(summary["matched_count"], 1)


class ApiV2ValidationGateTests(unittest.TestCase):
    def test_gate_11_surfaces_fc_native_xml_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protocol.xscr"
            path.write_text(ADD_LABWARE_XSCR, encoding="utf-8")
            report = validate_ready_to_import(compiled_xscr=path)
            gate = next(item for item in report["gates"] if item["id"] == "post_compile_xscr_reinspect")
            compare = gate["details"].get("fc_native_xml_compare") or {}
            self.assertIn("status", compare)
            self.assertGreaterEqual(compare.get("matched_count", 0), 1)

    def test_gate_11_needs_review_on_xml_mismatch(self):
        inventory = {
            "command_ids": ["AddLabwareDataV1"],
            "fluentcontrol_findings": [],
            "fc_native_xml_compare": {
                "status": "needs_review",
                "summary": "FC-native XML compare: 1 mismatch(es)",
                "mismatch_count": 1,
                "matched_count": 0,
                "compared_count": 1,
            },
        }
        gate = _gate_post_compile_xscr({"steps": [{}]}, "", inventory)
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("needs_review"))


if __name__ == "__main__":
    unittest.main()
