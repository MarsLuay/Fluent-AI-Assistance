"""Tests for IR subroutine variable-mapping normalization (subroutine_load_review parity)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fluent_pipeline.subroutine_dependencies import norm_subroutine_key
from fluent_pipeline.subroutine_variable_mappings import (
    filter_variable_mappings,
    normalize_ir_subroutine_variable_mappings,
    reconcile_ir_subroutine_variable_definitions,
    subroutine_mappings_match_for_parity,
    valid_mapping_targets_for_subroutine,
    variable_definitions_from_xscr,
)

CAPBC_SUBROUTINE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared">
  <Payload>
    <ObjectName>SUB_CapBCScanHandeling_50mL_v0.2</ObjectName>
    <PayloadData>
      <Script>
        <Properties>
          <VariableDeclarations>
            <VariableDeclarations>
              <anyType i:type="d3p1:VariableDefinitionHelper">
                <d3p1:Name>InputNumSampleCount</d3p1:Name>
              </anyType>
              <anyType i:type="d3p1:VariableDefinitionHelper">
                <d3p1:Name>capoffset</d3p1:Name>
              </anyType>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""

CAPTURE_BARCODE_SUBROUTINE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared">
  <Payload>
    <ObjectName>SUB_CapBCScanHandeling_50mL_v0.2</ObjectName>
    <PayloadData>
      <Script>
        <Properties>
          <VariableDeclarations>
            <VariableDeclarations>
              <anyType i:type="d3p1:VariableDefinitionHelper">
                <d3p1:Name>CaptureBarcode</d3p1:Name>
                <d3p1:QueryOnStartup>true</d3p1:QueryOnStartup>
                <d3p1:QueryOnStartupString>Scan or confirm the source tube barcode.</d3p1:QueryOnStartupString>
                <d3p1:ReadOnly>false</d3p1:ReadOnly>
                <d3p1:Scope>Run</d3p1:Scope>
                <d3p1:TypeName>String</d3p1:TypeName>
                <d3p1:Values><d2p1:string xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays"></d2p1:string></d3p1:Values>
              </anyType>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""


class SubroutineVariableMappingsTests(unittest.TestCase):
    def test_variable_definitions_from_xscr_reads_declaration_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cap.xscr"
            path.write_text(CAPBC_SUBROUTINE_XSCR, encoding="utf-8")
            defs = variable_definitions_from_xscr(path)
            self.assertEqual(set(defs), {"InputNumSampleCount", "capoffset"})

    def test_filter_variable_mappings_drops_absent_targets(self):
        kept, removed = filter_variable_mappings(
            [
                {"target": "InputNumSampleCount", "source": "1"},
                {"target": "capholderoffset", "source": "0"},
                {"target": "capoffset", "source": "0"},
            ],
            {"InputNumSampleCount", "capoffset"},
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["target"], "capholderoffset")

    def test_normalize_ir_subroutine_variable_mappings_strips_stale_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            subroutine = tmp_path / "cap.xscr"
            subroutine.write_text(CAPBC_SUBROUTINE_XSCR, encoding="utf-8")
            lookup = {
                norm_subroutine_key("Demo\\SUB_CapBCScanHandeling_50mL_v0.2"): {
                    "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                    "resolved_path": str(subroutine),
                }
            }
            ir = {
                "steps": [
                    {
                        "id": "step_capbc",
                        "operation": "call_subroutine",
                        "parameters": {
                            "subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                            "variable_mappings_start": [
                                {"target": "InputNumSampleCount", "source": "1"},
                                {"target": "capholderoffset", "source": "0"},
                                {"target": "capoffset", "source": "0"},
                            ],
                        },
                    }
                ]
            }
            fixups = normalize_ir_subroutine_variable_mappings(
                ir,
                lookup,
                context_root=tmp_path,
            )
            mappings = ir["steps"][0]["parameters"]["variable_mappings_start"]
            self.assertEqual(len(fixups), 1)
            self.assertEqual(fixups[0]["target"], "capholderoffset")
            self.assertEqual(
                [item["target"] for item in mappings],
                ["InputNumSampleCount", "capoffset"],
            )
            self.assertEqual(ir["source"]["subroutine_variable_mappings"]["ir_fixup_count"], 1)

    def test_subroutine_mappings_match_for_parity_ignores_stale_ir_targets(self):
        ir_pairs = [
            ("InputNumSampleCount", "1"),
            ("capholderoffset", "0"),
            ("capoffset", "0"),
        ]
        compiled_pairs = [
            ("InputNumSampleCount", "1"),
            ("capoffset", "0"),
        ]
        valid = {"InputNumSampleCount", "capoffset"}
        self.assertTrue(
            subroutine_mappings_match_for_parity(
                ir_pairs,
                compiled_pairs,
                valid_targets=valid,
            )
        )

    def test_valid_mapping_targets_for_subroutine_resolves_manifest_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            subroutine = tmp_path / "cap.xscr"
            subroutine.write_text(CAPBC_SUBROUTINE_XSCR, encoding="utf-8")
            lookup = {
                norm_subroutine_key("SUB_CapBCScanHandeling_50mL_v0.2"): {
                    "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                    "extracted_path": "cap.xscr",
                }
            }
            targets = valid_mapping_targets_for_subroutine(
                "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                lookup,
                context_root=tmp_path,
            )
            self.assertEqual(targets, {"InputNumSampleCount", "capoffset"})

    def test_reconcile_conflicting_main_variable_matches_called_subroutine(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            subroutine = tmp_path / "cap.xscr"
            subroutine.write_text(CAPTURE_BARCODE_SUBROUTINE_XSCR, encoding="utf-8")
            lookup = {
                norm_subroutine_key("Demo\\SUB_CapBCScanHandeling_50mL_v0.2"): {
                    "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                    "resolved_path": str(subroutine),
                }
            }
            ir = {
                "variables": [{"name": "CaptureBarcode", "scope": "Script", "type": "Floating Point", "value": 0}],
                "steps": [
                    {
                        "id": "step_capbc",
                        "operation": "call_subroutine",
                        "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                    }
                ],
            }

            fixups = reconcile_ir_subroutine_variable_definitions(ir, lookup, context_root=tmp_path)

            self.assertEqual(len(fixups), 1)
            self.assertEqual(fixups[0]["action"], "matched_main_variable_to_subroutine")
            self.assertEqual(ir["variables"][0]["name"], "CaptureBarcode")
            self.assertEqual(ir["variables"][0]["scope"], "Run")
            self.assertEqual(ir["variables"][0]["type"], "String")
            self.assertTrue(ir["variables"][0]["query_at_startup"])
            self.assertEqual(ir["variables"][0]["query_prompt"], "Scan or confirm the source tube barcode.")
            self.assertEqual(ir["source"]["subroutine_variable_definitions"]["fixup_count"], 1)

    def test_reconcile_untyped_main_variable_fills_subroutine_scope_and_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            subroutine = tmp_path / "cap.xscr"
            subroutine.write_text(CAPTURE_BARCODE_SUBROUTINE_XSCR, encoding="utf-8")
            lookup = {
                norm_subroutine_key("Demo\\SUB_CapBCScanHandeling_50mL_v0.2"): {
                    "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                    "resolved_path": str(subroutine),
                }
            }
            ir = {
                "variables": [{"name": "CaptureBarcode", "value": 0}],
                "steps": [
                    {
                        "id": "step_capbc",
                        "operation": "call_subroutine",
                        "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                    }
                ],
            }

            fixups = reconcile_ir_subroutine_variable_definitions(ir, lookup, context_root=tmp_path)

            self.assertEqual(len(fixups), 1)
            self.assertEqual(fixups[0]["action"], "matched_main_variable_to_subroutine")
            self.assertEqual(ir["variables"][0]["name"], "CaptureBarcode")
            self.assertEqual(ir["variables"][0]["scope"], "Run")
            self.assertEqual(ir["variables"][0]["type"], "String")
            self.assertEqual(ir["variables"][0]["query_prompt"], "Scan or confirm the source tube barcode.")

    def test_reconcile_query_variable_prompt_text_matches_subroutine(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            subroutine = tmp_path / "cap.xscr"
            subroutine.write_text(CAPTURE_BARCODE_SUBROUTINE_XSCR, encoding="utf-8")
            lookup = {
                norm_subroutine_key("Demo\\SUB_CapBCScanHandeling_50mL_v0.2"): {
                    "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                    "resolved_path": str(subroutine),
                }
            }
            ir = {
                "variables": [{"name": "CaptureBarcode", "scope": "Script", "type": "String"}],
                "steps": [
                    {
                        "id": "step_query",
                        "operation": "query_variable",
                        "parameters": {"variable": "CaptureBarcode", "prompt": ""},
                    },
                    {
                        "id": "step_capbc",
                        "operation": "call_subroutine",
                        "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                    },
                ],
            }

            fixups = reconcile_ir_subroutine_variable_definitions(ir, lookup, context_root=tmp_path)

            self.assertEqual(len(fixups), 1)
            self.assertEqual(fixups[0]["prompt_changes"], "1")
            self.assertEqual(
                ir["steps"][0]["parameters"]["prompt"],
                "Scan or confirm the source tube barcode.",
            )

    def test_reconcile_conflicting_local_variable_renames_main_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            subroutine = tmp_path / "cap.xscr"
            subroutine.write_text(CAPTURE_BARCODE_SUBROUTINE_XSCR, encoding="utf-8")
            lookup = {
                norm_subroutine_key("Demo\\SUB_CapBCScanHandeling_50mL_v0.2"): {
                    "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                    "resolved_path": str(subroutine),
                }
            }
            ir = {
                "variables": [{"name": "CaptureBarcode", "scope": "Script", "type": "Floating Point", "value": 0}],
                "steps": [
                    {
                        "id": "step_local",
                        "operation": "add_labware",
                        "target_labware": "FilterDWP[CaptureBarcode]",
                        "parameters": {
                            "label": "FilterDWP[CaptureBarcode]",
                            "location": "NestPlatform",
                            "position": 1,
                        },
                    },
                    {
                        "id": "step_capbc",
                        "operation": "call_subroutine",
                        "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                    },
                ],
            }

            fixups = reconcile_ir_subroutine_variable_definitions(ir, lookup, context_root=tmp_path)

            self.assertEqual(len(fixups), 1)
            self.assertEqual(fixups[0]["action"], "renamed_local_variable_and_matched_subroutine")
            self.assertEqual(fixups[0]["local_name"], "CaptureBarcode_Main")
            variables = {item["name"]: item for item in ir["variables"]}
            self.assertEqual(variables["CaptureBarcode"]["scope"], "Run")
            self.assertEqual(variables["CaptureBarcode"]["type"], "String")
            self.assertEqual(variables["CaptureBarcode"]["query_prompt"], "Scan or confirm the source tube barcode.")
            self.assertEqual(variables["CaptureBarcode_Main"]["scope"], "Script")
            self.assertEqual(ir["steps"][0]["target_labware"], "FilterDWP[CaptureBarcode_Main]")
            self.assertEqual(ir["steps"][0]["parameters"]["label"], "FilterDWP[CaptureBarcode_Main]")


if __name__ == "__main__":
    unittest.main()
