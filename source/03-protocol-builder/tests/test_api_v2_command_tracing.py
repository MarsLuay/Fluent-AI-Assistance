import unittest

from fluent_pipeline.api_v2.command_tracing import (
    format_set_location_trace,
    merge_set_location_traces_into_details,
    set_location_trace_for_stepped_command,
)
from fluent_pipeline.api_v2.commands import SetLocation
from fluent_pipeline.api_v2_stepped_inventory import ICommand


_SET_LOCATION_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<Object Type="Tecan.Core.Scripting.Worktable.SetLocationStatement">
  <SetLocationStatement>
    <Labware>Plate1</Labware>
    <Location>NestPlatform</Location>
    <Site>3</Site>
    <Rotation>90</Rotation>
    <LineNumber>12</LineNumber>
  </SetLocationStatement>
</Object>
"""

_ADD_LABWARE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
  <AddLabwareDataV1>
    <LabwareType>96 Well Flat</LabwareType>
    <LabwareLable>Plate1</LabwareLable>
    <Location>Site</Location>
    <Position>1</Position>
    <Rotation>0</Rotation>
    <HasLid>False</HasLid>
  </AddLabwareDataV1>
</Object>
"""


class ApiV2CommandTracingTests(unittest.TestCase):
    def test_format_set_location_trace_omits_zero_rotation(self):
        trace = format_set_location_trace(
            labware="Plate1",
            location="NestPlatform",
            site=3,
            rotation=0,
        )
        self.assertEqual(
            trace,
            "SetLocation(LabwareName='Plate1', Location='NestPlatform', Site=3)",
        )

    def test_format_set_location_trace_includes_rotation(self):
        trace = format_set_location_trace(
            labware="Plate1",
            location="NestPlatform",
            site=3,
            rotation=90,
        )
        self.assertIn("Rotation=90", trace)

    def test_set_location_to_string_matches_trace_helper(self):
        command = SetLocation(
            labware="Plate1",
            location="NestPlatform",
            site=3,
            rotation=90,
        )
        self.assertEqual(
            command.to_string(),
            format_set_location_trace(
                labware="Plate1",
                location="NestPlatform",
                site=3,
                rotation=90,
            ),
        )

    def test_set_location_trace_for_stepped_command_from_xscr(self):
        command = ICommand(
            type_name="SetLocationStatement",
            index=0,
            group="Setup",
            payload_xml=_SET_LOCATION_XSCR,
            operation="set_location",
            api_v2_type="SetLocation",
        )
        trace = set_location_trace_for_stepped_command(command)
        self.assertEqual(
            trace,
            "SetLocation(LabwareName='Plate1', Location='NestPlatform', Site=3, Rotation=90)",
        )

    def test_set_location_trace_for_add_labware_deck_step(self):
        command = ICommand(
            type_name="AddLabwareDataV1",
            index=0,
            group="Setup",
            payload_xml=_ADD_LABWARE_XSCR,
            operation="add_labware",
            api_v2_type="AddLabware",
        )
        trace = set_location_trace_for_stepped_command(command)
        self.assertEqual(
            trace,
            "SetLocation(LabwareName='Plate1', Location='Site', Site=1)",
        )

    def test_merge_set_location_traces_into_details(self):
        details: dict = {}
        merge_set_location_traces_into_details(
            details,
            [
                {
                    "index": 0,
                    "ir_step_id": "step_000",
                    "trace": "SetLocation(LabwareName='Plate1', Location='Site', Site=1)",
                }
            ],
        )
        self.assertEqual(len(details["command_traces"]), 1)
        self.assertEqual(details["command_traces"][0]["command_type"], "SetLocation")
        self.assertIn("Plate1", details["command_traces"][0]["trace"])


if __name__ == "__main__":
    unittest.main()
