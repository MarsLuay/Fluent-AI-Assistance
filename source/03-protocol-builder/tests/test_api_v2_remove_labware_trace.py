"""Tests for api-v2-079 RemoveLabware ToString() ExecuteCommand tracing."""

from __future__ import annotations

import unittest
from fluent_pipeline import xml_compat as ET

from fluent_pipeline.api_v2.command_tracing import (
    command_trace_for_stepped,
    format_remove_labware_trace,
    merge_remove_labware_traces_into_details,
    stepped_command_trace,
)
from fluent_pipeline.api_v2.commands import RemoveLabware, remove_labware_from_xscr_object
from fluent_pipeline.api_v2.types import SteppedCommand

_REMOVE_LABWARE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Remove Labware Demo</ObjectName>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
          <ScriptGroupDataV1>
            <Name>Cleanup</Name>
            <Statements>
              <Object Type="Tecan.Core.Scripting.Worktable.Data.RemoveLabwareDataV1">
                <RemoveLabwareDataV1>
                  <LabwareName>Protein Plate[001]</LabwareName>
                  <Data Type="Tecan.Core.Scripting.Programming.ProgrammingStatementBaseDataV1">
                    <ProgrammingStatementBaseDataV1>
                      <LineNumber>3</LineNumber>
                    </ProgrammingStatementBaseDataV1>
                  </Data>
                </RemoveLabwareDataV1>
              </Object>
            </Statements>
          </ScriptGroupDataV1>
        </Object>
      </Objects>
    </ScriptGroup>
  </Payload>
</VxData>
"""


class RemoveLabwareTraceTests(unittest.TestCase):
    def test_remove_labware_to_string_matches_trace_formatter(self):
        command = RemoveLabware(labware_name="Protein Plate[001]")
        expected = format_remove_labware_trace(labware="Protein Plate[001]")
        self.assertEqual(command.to_string(), expected)

    def test_remove_labware_parsed_from_xscr(self):
        root = ET.fromstring(_REMOVE_LABWARE_XSCR)
        element = next(root.iter("Object"))
        for node in root.iter("Object"):
            if "RemoveLabwareDataV1" in (node.attrib.get("Type") or ""):
                element = node
                break
        typed = remove_labware_from_xscr_object(element)
        self.assertIsInstance(typed, RemoveLabware)
        self.assertEqual(typed.labware_name, "Protein Plate[001]")
        self.assertIn("LabwareName='Protein Plate[001]'", typed.to_string())

    def test_command_trace_for_stepped_without_xml(self):
        command = SteppedCommand(
            type_name="RemoveLabwareDataV1",
            index=0,
            group="Cleanup",
            api_v2_type="RemoveLabware",
            payload_xml=(
                '<Object Type="Tecan.Core.Scripting.Worktable.Data.RemoveLabwareDataV1">'
                "<RemoveLabwareDataV1><LabwareName>Wash Plate[002]</LabwareName></RemoveLabwareDataV1>"
                "</Object>"
            ),
        )
        trace, extra = stepped_command_trace(command)
        self.assertIn("Wash Plate[002]", trace)
        self.assertEqual(extra.get("labware_name"), "Wash Plate[002]")
        self.assertIn("Wash Plate[002]", command_trace_for_stepped(command))

    def test_merge_remove_labware_traces_into_details(self):
        details: dict = {}
        merge_remove_labware_traces_into_details(
            details,
            [
                {
                    "trace": "RemoveLabware(LabwareName='TipBox[001]')",
                    "ir_step_id": "step_004",
                    "labware_name": "TipBox[001]",
                }
            ],
        )
        self.assertEqual(len(details["command_traces"]), 1)
        self.assertEqual(details["command_traces"][0]["command_type"], "RemoveLabware")


if __name__ == "__main__":
    unittest.main()
