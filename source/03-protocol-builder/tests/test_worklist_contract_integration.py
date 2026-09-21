import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.protocol_ir import protocol_ir_from_gwl, protocol_ir_from_xscr, render_gwl


def _xscr_with_worklists() -> str:
    def command(command_id: str, fields: str, line: int) -> str:
        return f"""
          <Object Type="Tecan.Core.Scripting.Commands.{command_id}">
            <{command_id}>{fields}<Data><LineNumber>{line}</LineNumber></Data></{command_id}>
          </Object>"""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><ObjectName>WorklistContract</ObjectName><PayloadData><Script><Commands><ScriptGroup><Objects>
  <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1"><ScriptGroupDataV1><Name>Worklists</Name><Data><Statements>
    {command("LoadWorklistStatementDataV4", "<WorklistName>first.gwl</WorklistName><SelectedTipsIndexes>5</SelectedTipsIndexes><TipSystem>Fixed Tips</TipSystem>", 1)}
    {command("LoadWorklistStatementDataV4", "<WorklistName>second.gwl</WorklistName><SelectedTipsIndexes>3</SelectedTipsIndexes><UnknownVendorField>kept</UnknownVendorField>", 2)}
    {command("ExecuteWorklistStatementDataV1", "", 3)}
  </Statements></Data></ScriptGroupDataV1></Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>"""


class WorklistContractIntegrationTests(unittest.TestCase):
    def test_gwl_renderer_preserves_explicit_tip_masks_from_ir_steps(self):
        ir = {
            "id": "tip-mask-render",
            "protocol": {"name": "Tip mask render"},
            "labware": [
                {"label": "Source", "catalog": "Plate"},
                {"label": "Destination", "catalog": "Plate"},
            ],
            "steps": [
                {"operation": "aspirate", "target_labware": "Source", "volume_ul": 10, "parameters": {"tip_mask": " 01 "}},
                {"operation": "dispense", "target_labware": "Destination", "volume_ul": 10, "parameters": {"tip_mask": "01"}},
            ],
        }
        rendered = render_gwl(ir)
        self.assertIn("A;Source;;Plate;1;;10;;; 01 ;", rendered)
        self.assertIn("D;Destination;;Plate;1;;10;;;01;", rendered)

    def test_gwl_ir_exposes_tip_semantics_and_typed_state_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "typed.gwl"
            path.write_text(
                "A;Source;;Plate;1;;10;Water;;1;\n"
                "D;Destination;;Plate;1;;10;Water;;1;\n"
                "F;vendor;field\n"
                "S;DiTi;42\n"
                "X;opaque\n",
                encoding="utf-8",
            )
            ir = protocol_ir_from_gwl(path)

        records = ir["worklists"][0]["records"]
        self.assertEqual(records[0]["tip_selection"]["channel"], 1)
        self.assertEqual(records[2]["operation"], "flush")
        self.assertEqual(records[3]["operation"], "set_diti_type")
        self.assertEqual(records[4]["raw_line"], "X;opaque")
        self.assertTrue(ir["worklists"][0]["source_order_preserved"])

    def test_xscr_worklist_commands_are_typed_and_execute_preserves_load_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worklist_contract.xscr"
            path.write_text(_xscr_with_worklists(), encoding="utf-8")
            ir = protocol_ir_from_xscr(path)

        worklist_steps = [step for step in ir["steps"] if step["operation"] == "read_worklist"]
        self.assertEqual(len(worklist_steps), 3)
        self.assertEqual(worklist_steps[0]["parameters"]["worklist_contract"]["selected_tip_channels"], [1, 3])
        self.assertEqual(worklist_steps[1]["parameters"]["worklist_contract"]["extra_fields"], {"UnknownVendorField": "kept"})
        execution = worklist_steps[2]["parameters"]["worklist_execution_contract"]
        self.assertEqual(execution["loads_before_execute"], ["first.gwl", "second.gwl"])
        self.assertEqual(execution["extra_fields"], {})
        self.assertTrue(worklist_steps[0]["parameters"]["raw_xml"])


if __name__ == "__main__":
    unittest.main()
