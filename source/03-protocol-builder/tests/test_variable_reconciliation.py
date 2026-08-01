import tempfile
import unittest
from fluent_pipeline import xml_compat as ET
from pathlib import Path

from fluent_pipeline.subroutine_dependencies import norm_subroutine_key
from fluent_pipeline.variable_reconciliation import (
    find_undeclared_variable_references,
    preflight_variable_reconciliation,
    validate_xscr_variable_declarations,
)


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
                <d3p1:Scope>Run</d3p1:Scope>
                <d3p1:TypeName>String</d3p1:TypeName>
                <d3p1:QueryOnStartup>true</d3p1:QueryOnStartup>
                <d3p1:QueryOnStartupString>Scan or confirm the source tube barcode.</d3p1:QueryOnStartupString>
              </anyType>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""


class VariableReconciliationPreflightTests(unittest.TestCase):
    def test_preflight_passes_after_subroutine_authoritative_reconciliation(self):
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
                "source": {},
            }

            report = preflight_variable_reconciliation(
                ir,
                lookup=lookup,
                context_root=tmp_path,
            )

            self.assertTrue(report["ok"])
            self.assertEqual(report["failure_count"], 0)
            self.assertEqual(ir["variables"][0]["scope"], "Run")
            self.assertEqual(ir["variables"][0]["type"], "String")

    def test_preflight_fails_on_unresolved_conflicting_declarations(self):
        ir = {
            "variables": [
                {"name": "TubeCount", "scope": "Script", "type": "Integer", "value": 1},
                {"name": "TubeCount", "scope": "Run", "type": "String", "value": "two"},
            ],
            "steps": [],
            "source": {},
        }

        report = preflight_variable_reconciliation(ir)

        self.assertFalse(report["ok"])
        self.assertEqual(report["failures"][0]["code"], "conflicting_variable_declarations")

    def test_preflight_collapses_identical_duplicate_declarations(self):
        ir = {
            "variables": [
                {"name": "TubeCount", "scope": "Script", "type": "Integer", "value": 1},
                {"name": "TubeCount", "scope": "Script", "type": "Integer", "value": 1},
            ],
            "steps": [],
            "source": {},
        }

        report = preflight_variable_reconciliation(ir)

        self.assertTrue(report["ok"])
        self.assertEqual(len(ir["variables"]), 1)
        self.assertEqual(report["collapsed_duplicates"][0]["name"], "TubeCount")

    def test_preflight_collapses_identical_declarations_across_startup_variables(self):
        ir = {
            "variables": [
                {"name": "TubeCount", "scope": "Script", "type": "Integer", "value": 1},
            ],
            "steps": [],
            "source": {
                "selected_source_scripts": [
                    {
                        "startup_variables": [
                            {"name": "TubeCount", "scope": "Script", "type": "Integer", "value": 1},
                        ]
                    }
                ]
            },
        }

        report = preflight_variable_reconciliation(ir)

        self.assertTrue(report["ok"])
        self.assertEqual(len(ir["variables"]), 1)
        self.assertEqual(ir["source"]["selected_source_scripts"][0]["startup_variables"], [])
        self.assertEqual(report["collapsed_duplicates"][0]["name"], "TubeCount")

    def test_preflight_merges_source_startup_fields_into_recipe_variable(self):
        ir = {
            "variables": [
                {"name": "DemoTubeRunner", "value": "1x10 50ml Falcon Tube Runner Demo"},
            ],
            "steps": [],
            "source": {
                "selected_source_scripts": [
                    {
                        "startup_variables": [
                            {
                                "name": "DemoTubeRunner",
                                "scope": "Script",
                                "type": "String",
                                "default_values": ["1x10 50ml Falcon Tube Runner Demo"],
                            },
                        ]
                    }
                ]
            },
        }

        report = preflight_variable_reconciliation(ir)

        self.assertTrue(report["ok"])
        self.assertEqual(len(ir["variables"]), 1)
        self.assertEqual(ir["variables"][0]["scope"], "Script")
        self.assertEqual(ir["variables"][0]["type"], "String")
        self.assertEqual(ir["source"]["selected_source_scripts"][0]["startup_variables"], [])
        self.assertEqual(report["collapsed_duplicates"][0]["name"], "DemoTubeRunner")

    def test_preflight_preserves_recipe_default_when_source_startup_default_differs(self):
        ir = {
            "variables": [
                {"name": "SubDeCapTube", "value": 1.0},
            ],
            "steps": [],
            "source": {
                "selected_source_scripts": [
                    {
                        "startup_variables": [
                            {
                                "name": "SubDeCapTube",
                                "scope": "Script",
                                "type": "Floating Point",
                                "default_values": ["0.0"],
                            },
                        ]
                    }
                ]
            },
        }

        report = preflight_variable_reconciliation(ir)

        self.assertTrue(report["ok"])
        self.assertEqual(len(ir["variables"]), 1)
        self.assertEqual(ir["variables"][0]["scope"], "Script")
        self.assertEqual(ir["variables"][0]["type"], "Floating Point")
        self.assertEqual(ir["variables"][0]["value"], 1.0)
        self.assertEqual(ir["source"]["selected_source_scripts"][0]["startup_variables"], [])

    def test_preflight_fails_on_undeclared_bracket_reference(self):
        ir = {
            "variables": [],
            "labware": [{"label": "Plate_[PlateIndex]"}],
            "steps": [],
            "source": {},
        }

        report = preflight_variable_reconciliation(ir)

        self.assertFalse(report["ok"])
        self.assertEqual(report["failures"][0]["code"], "undeclared_referenced_variable")

    def test_undeclared_reference_inspection_does_not_materialize_renderer_fallback(self):
        ir = {
            "variables": [],
            "steps": [
                {
                    "id": "step_check_error",
                    "operation": "conditional_branch",
                    "parameters": {"condition": "ErrorEndNow=1"},
                }
            ],
            "source": {},
        }

        missing = find_undeclared_variable_references(ir)
        report = preflight_variable_reconciliation(ir)

        self.assertFalse(report["ok"])
        self.assertEqual(missing[0]["name"], "ErrorEndNow")
        self.assertEqual(missing[0]["path"], "$.steps[0].parameters.condition")
        self.assertEqual(missing[0]["step_id"], "step_check_error")
        self.assertEqual(ir["variables"], [])
        self.assertNotIn("inferred_referenced_variables", ir["source"])
        self.assertEqual(report["failures"][0]["details"]["name"], "ErrorEndNow")

    def test_preflight_fails_when_set_variable_target_is_undeclared(self):
        ir = {
            "variables": [],
            "steps": [
                {
                    "id": "step_set_tube_name",
                    "operation": "set_variable",
                    "parameters": {"variable": "TubeName", "value": "Falcon"},
                }
            ],
            "source": {},
        }

        report = preflight_variable_reconciliation(ir)

        self.assertFalse(report["ok"])
        self.assertEqual(report["failures"][0]["code"], "undeclared_referenced_variable")
        self.assertEqual(report["failures"][0]["details"]["name"], "TubeName")
        self.assertEqual(report["failures"][0]["details"]["step_id"], "step_set_tube_name")
        self.assertEqual(ir["variables"], [])

    def test_preflight_ignores_bracket_placeholder_in_prompt_prose(self):
        ir = {
            "variables": [],
            "labware": [],
            "steps": [
                {
                    "operation": "prompt_user",
                    "parameters": {
                        "prompt": "DestinationTubeName(MixTube[XXX]) is an example worklist column.",
                    },
                }
            ],
            "source": {},
        }

        report = preflight_variable_reconciliation(ir)

        self.assertTrue(report["ok"])

    def test_preflight_uses_opaque_expression_reference_metadata_only(self):
        ir = {
            "variables": [],
            "steps": [
                {
                    "id": "step_preserved",
                    "operation": "set_variable",
                    "parameters": {
                        "variable": "TubeName",
                        "value_expression": {
                            "kind": "source_preserved_expression",
                            "source": "Unsupported.Syntax(A)",
                            "source_hash": "sha256:" + ("1" * 64),
                            "byte_stable": True,
                            "referenced_variables": ["A"],
                            "referenced_functions": ["Unsupported.Syntax"],
                        },
                    },
                }
            ],
            "source": {},
        }

        missing = find_undeclared_variable_references(ir)

        self.assertEqual({item["name"] for item in missing}, {"TubeName", "A"})
        self.assertNotIn("Unsupported", {item["name"] for item in missing})
        self.assertNotIn("Syntax", {item["name"] for item in missing})

    def test_preflight_rejects_opaque_expression_without_reference_metadata(self):
        ir = {
            "variables": [{"name": "TubeName", "scope": "Script", "type": "String", "value": ""}],
            "steps": [
                {
                    "id": "step_preserved",
                    "operation": "set_variable",
                    "parameters": {
                        "variable": "TubeName",
                        "value_expression": {
                            "kind": "source_preserved_expression",
                            "source": "Unsupported.Syntax(A)",
                            "source_hash": "sha256:" + ("2" * 64),
                            "byte_stable": True,
                        },
                    },
                }
            ],
            "source": {},
        }

        report = preflight_variable_reconciliation(ir)

        self.assertFalse(report["ok"])
        self.assertIn(
            "opaque_expression_reference_metadata_missing",
            {item["code"] for item in report["failures"]},
        )

    def test_preflight_fails_on_query_variable_without_declaration(self):
        ir = {
            "variables": [],
            "steps": [
                {
                    "operation": "query_variable",
                    "parameters": {"variable": "StartupVolume", "prompt": "Enter volume"},
                }
            ],
            "source": {},
        }

        report = preflight_variable_reconciliation(ir)

        self.assertFalse(report["ok"])
        self.assertEqual(report["failures"][0]["code"], "query_variable_undeclared")

    def test_validate_xscr_detects_duplicate_variable_definitions(self):
        root = ET.Element("Root")
        for _ in range(2):
            helper = ET.SubElement(root, "VariableDefinitionHelper")
            ET.SubElement(helper, "Name").text = "TubeCount"
            ET.SubElement(helper, "TypeName").text = "Integer"
            ET.SubElement(helper, "QueryOnStartup").text = "false"
            values = ET.SubElement(helper, "Values")
            ET.SubElement(values, "string").text = "8"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dup.xscr"
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            failures = validate_xscr_variable_declarations(path)

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "duplicate_xscr_variable_declaration")

    def test_validate_xscr_rejects_decimal_default_for_integer(self):
        root = ET.Element("Root")
        helper = ET.SubElement(root, "VariableDefinitionHelper")
        ET.SubElement(helper, "Name").text = "NumSourceTubes"
        ET.SubElement(helper, "TypeName").text = "Integer"
        ET.SubElement(helper, "QueryOnStartup").text = "false"
        values = ET.SubElement(helper, "Values")
        ET.SubElement(values, "string").text = "8.0"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid-default.xscr"
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            failures = validate_xscr_variable_declarations(path)

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "invalid_xscr_value_type_default")
        self.assertEqual(failures[0].details["name"], "NumSourceTubes")


if __name__ == "__main__":
    unittest.main()
