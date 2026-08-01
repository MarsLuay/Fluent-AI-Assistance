"""Tests for api-v2-004 GenericCommand.ToXML() offline passthrough routing."""

from __future__ import annotations

import unittest
from fluent_pipeline import xml_compat as ET

from fluent_pipeline.api_v2.commands import GenericCommand, command_to_xml
from fluent_pipeline.api_v2.generic_passthrough import (
    stepped_command_from_xscr,
    uses_generic_command_passthrough,
    validate_generic_passthrough_execute_xml,
)


_PASSTHROUGH_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
          <ScriptGroupDataV1>
            <Name>Driver checks</Name>
            <Statements>
              <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.MoveAxisCommandScriptStatement">
                <MoveAxisCommandScriptStatement>
                  <DeviceAlias>RGA1</DeviceAlias>
                  <LineNumber>3</LineNumber>
                </MoveAxisCommandScriptStatement>
              </Object>
            </Statements>
          </ScriptGroupDataV1>
        </Object>
      </Objects>
    </ScriptGroup>
  </Payload>
</VxData>
"""


class ApiV2GenericPassthroughTests(unittest.TestCase):
    def test_stepped_command_from_xscr_routes_passthrough_to_generic_command(self):
        root = ET.fromstring(_PASSTHROUGH_XSCR)
        statement = next(
            obj
            for obj in root.iter()
            if obj.attrib.get("Type", "").endswith("MoveAxisCommandScriptStatement")
        )
        api_v2_type, execute_xml, operation = stepped_command_from_xscr(
            statement,
            command_id="MoveAxisCommandScriptStatement",
        )

        self.assertEqual(api_v2_type, "GenericCommand")
        self.assertIsNone(operation)
        self.assertIn("MoveAxisCommandScriptStatement", execute_xml)
        self.assertTrue(
            uses_generic_command_passthrough(
                api_v2_type=api_v2_type,
                command_id="MoveAxisCommandScriptStatement",
            )
        )

    def test_generic_command_to_xml_returns_compiled_payload(self):
        payload = (
            '<Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.MoveAxisCommandScriptStatement">'
            "<MoveAxisCommandScriptStatement><LineNumber>1</LineNumber></MoveAxisCommandScriptStatement>"
            "</Object>"
        )
        command = GenericCommand(
            object_type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.MoveAxisCommandScriptStatement",
            payload_xml=payload,
            command_id="MoveAxisCommandScriptStatement",
            line_number=1,
        )
        self.assertEqual(command_to_xml(command), payload)

    def test_validate_generic_passthrough_execute_xml_skips_typed_commands(self):
        error = validate_generic_passthrough_execute_xml(
            type_name="UserPromptStatement",
            api_v2_type="UserPrompt",
            execute_xml="<Object />",
            payload_xml="<Object />",
            line_number="1",
        )
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
