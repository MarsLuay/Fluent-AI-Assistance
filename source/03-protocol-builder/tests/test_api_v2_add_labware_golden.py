"""Tests for api-v2-007 AddLabware.ToXML() verification_recipe golden XSCR diff."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.api_v2.add_labware_golden import (
    compare_add_labware_payloads,
    compare_verification_recipe_add_labware_golden,
    enrich_compiled_inventory_with_golden_compare,
    is_verification_recipe_ir,
    verification_recipe_add_labware_summary,
)
from fluent_pipeline.api_v2.commands import AddLabware, command_to_xml
from fluent_pipeline.generation_workflow import build_ir_from_recipe
from fluent_pipeline.request_spec import normalize_request_spec, verification_recipe
from fluent_pipeline.validation import _gate_post_compile_xscr, validate_ready_to_import

ADD_LABWARE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
  <AddLabwareDataV1>
    <LabwareType>Adapter A200</LabwareType>
    <LabwareLable>AdapterA200</LabwareLable>
    <Location>Demo_Nest_Pos</Location>
    <Position>1</Position>
    <Rotation>0</Rotation>
    <HasLid>False</HasLid>
    <Data><LineNumber>1</LineNumber></Data>
  </AddLabwareDataV1>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""


def _recipe_ir():
    spec = normalize_request_spec(
        {
            "request": {"intent": "RGA verification from recipe"},
            "verification_recipe": {
                "worktable": "Demo_WT",
                "labware": [
                    {
                        "label": "AdapterA200",
                        "catalog": "Adapter A200",
                        "location": "Demo_Nest_Pos",
                        "site": 1,
                    }
                ],
                "groups": [
                    {
                        "name": "Operator setup",
                        "steps": [{"prompt": "Confirm A200 connected."}],
                    }
                ],
            },
        }
    )
    recipe = verification_recipe(spec)
    assert recipe is not None
    return build_ir_from_recipe(recipe, intent="recipe test", context=None, protocol_name="Recipe_Test")


class ApiV2AddLabwareGoldenTests(unittest.TestCase):
    def test_is_verification_recipe_ir(self):
        ir = _recipe_ir()
        self.assertTrue(is_verification_recipe_ir(ir))
        self.assertFalse(is_verification_recipe_ir({"source": {"format": "manual"}}))

    def test_golden_matches_compiled_xscr(self):
        ir = _recipe_ir()
        findings = compare_verification_recipe_add_labware_golden(ir, xscr_text=ADD_LABWARE_XSCR)
        summary = verification_recipe_add_labware_summary(findings)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["mismatch_count"], 0)

    def test_detects_labware_type_drift(self):
        ir = _recipe_ir()
        drifted = ADD_LABWARE_XSCR.replace("Adapter A200", "Adapter B200")
        findings = compare_verification_recipe_add_labware_golden(ir, xscr_text=drifted)
        summary = verification_recipe_add_labware_summary(findings)
        self.assertEqual(summary["status"], "needs_review")
        self.assertEqual(summary["mismatch_count"], 1)
        self.assertIn("LabwareType", findings[0].get("field_drifts", [""])[0])

    def test_golden_drift_does_not_create_fluentcontrol_findings(self):
        ir = _recipe_ir()
        drifted = ADD_LABWARE_XSCR.replace("Adapter A200", "Adapter B200")
        inventory = {"fluentcontrol_findings": []}
        enrich_compiled_inventory_with_golden_compare(inventory, ir=ir, xscr_text=drifted)

        summary = inventory["verification_recipe_add_labware_golden"]
        self.assertEqual(summary["status"], "needs_review")
        self.assertEqual(summary["mismatch_count"], 1)
        self.assertEqual(inventory["fluentcontrol_findings"], [])

    def test_payload_compare_fields(self):
        drifts = compare_add_labware_payloads(
            {
                "LabwareType": "Adapter A200",
                "LabwareLable": "AdapterA200",
                "Location": "Demo_Nest_Pos",
                "Position": "1",
                "Rotation": "0",
                "HasLid": "False",
            },
            {
                "LabwareType": "Adapter A200",
                "LabwareLable": "AdapterA200",
                "Location": "Demo_Device_Pos",
                "Position": "1",
                "Rotation": "0",
                "HasLid": "False",
            },
        )
        self.assertEqual(drifts, ["Location: golden 'Demo_Nest_Pos' vs compiled 'Demo_Device_Pos'"])

    def test_add_labware_to_xml_offline_serializer(self):
        command = AddLabware(
            labware_type="Adapter A200",
            labware_label="AdapterA200",
            location="Demo_Nest_Pos",
            site=1,
        )
        xml = command_to_xml(command)
        self.assertIn("Adapter A200", xml)
        self.assertIn("AdapterA200", xml)
        self.assertIn("Demo_Nest_Pos", xml)

    def test_gate_11_includes_recipe_labware_golden_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protocol.xscr"
            path.write_text(ADD_LABWARE_XSCR, encoding="utf-8")
            report = validate_ready_to_import(compiled_xscr=path)
            gate = next(item for item in report["gates"] if item["id"] == "post_compile_xscr_reinspect")
            compare = gate["details"].get("fc_native_xml_compare") or {}
            self.assertIn("status", compare)
            self.assertGreaterEqual(compare.get("matched_count", 0), 1)

    def test_gate_11_needs_review_on_recipe_labware_mismatch(self):
        inventory = {
            "command_ids": ["AddLabwareDataV1"],
            "fluentcontrol_findings": [],
            "verification_recipe_add_labware_golden": {
                "status": "needs_review",
                "summary": "AddLabware.ToXML() golden diff: 1 mismatch(es)",
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
