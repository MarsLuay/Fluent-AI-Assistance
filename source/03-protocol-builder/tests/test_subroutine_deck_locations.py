"""Tests for CapBC deck-location resolution and IR/XSCR fixups."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

DEMO_GRIP_CLOSE = 11
DEMO_GRIP_OPEN = 22

from fluent_pipeline.subroutine_deck_locations import (
    apply_capbc_prep_fixups_to_xscr,
    apply_deck_location_fixups_to_xscr,
    apply_subroutine_deck_location_bindings,
    CAPBC_PREP_GROUP_NAME,
    capbc_prep_emit_order,
    emit_capbc_prep_set_variable_steps,
    extract_set_variable_defaults_from_xscr,
    extract_set_variable_order_from_xscr,
    normalize_recipe_subroutine_deck_locations,
    normalize_variable_mappings,
    resolve_capbc_prep_defaults,
    resolve_tube_deck_location,
    worktable_location_names,
)


ROOT = Path(__file__).resolve().parents[1]
FULL_EXPORT = ROOT / "projects" / "full-export-20260611-142347-rga-a200-verification"
SOURCE_SCRIPT_FIXTURE = (
    FULL_EXPORT
    / "extracted"
    / "DataStore"
    / "UserSpecific"
    / "308c805a-1822-45aa-a284-441eca70c7b7.xscr"
)


def _sample_manifest() -> dict:
    return {
        "worktable_geometry": {
            "workspaces": [
                {
                    "name": "Demo_Worktable_A",
                    "guid": "aaaaaaaa-bbbb-4ccc-8ddd-111111111111",
                    "location_names": [
                        "NestPlatform",
                        "Demo_Tube_Pos_1",
                        "Demo_Tube_Cap",
                        "Demo_Device_Pos",
                    ],
                    "pin_sites": ["NestPlatform"],
                }
            ]
        },
        "scripts": [
            {
                "object_name": "Demo\\Demo_Tube_Script_A",
                "startup_variables": [
                    {"name": "TubeLocationName", "default_values": ['"Demo_Tube_Pos_1"']},
                ],
            }
        ],
    }


class SubroutineDeckLocationTests(unittest.TestCase):
    def _require_source_script_fixture(self) -> None:
        if not SOURCE_SCRIPT_FIXTURE.exists():
            self.skipTest("source script XSCR fixture not available")

    def test_worktable_alone_does_not_invent_location(self):
        recipe = {"worktable": "Demo_Worktable_A"}
        location, reason = resolve_tube_deck_location(
            recipe,
            manifest={
                "worktable_geometry": _sample_manifest()["worktable_geometry"],
                "scripts": [],
            },
        )
        self.assertIsNone(location)
        self.assertEqual(reason, "unresolved")

    def test_resolve_from_source_script_defaults(self):
        recipe = {"worktable": "Demo_Worktable_A"}
        location, reason = resolve_tube_deck_location(recipe, manifest=_sample_manifest())
        self.assertEqual(location, "Demo_Tube_Pos_1")
        self.assertIn("source script defaults on worktable", reason)
        self.assertIn("Demo_Worktable_A", reason)

    def test_worktable_location_names_from_geometry(self):
        names = worktable_location_names(_sample_manifest(), "Demo_Worktable_A")
        self.assertIn("Demo_Tube_Pos_1", names)
        self.assertIn("NestPlatform", names)

    def test_prefer_script_default_present_on_worktable(self):
        manifest = {
            "worktable_geometry": _sample_manifest()["worktable_geometry"],
            "scripts": [
                {
                    "object_name": "Demo\\Other_Script",
                    "startup_variables": [
                        {"name": "TubeLocationName", "default_values": ['"OffTable_Pos"']},
                    ],
                },
                {
                    "object_name": "Demo\\Demo_Tube_Script_A",
                    "startup_variables": [
                        {"name": "TubeLocationName", "default_values": ['"Demo_Tube_Pos_1"']},
                    ],
                },
            ],
        }
        location, reason = resolve_tube_deck_location(
            {"worktable": "Demo_Worktable_A"},
            manifest=manifest,
        )
        self.assertEqual(location, "Demo_Tube_Pos_1")
        self.assertIn("on worktable", reason)

    def test_resolve_from_ir_variables(self):
        location, reason = resolve_tube_deck_location(
            {"worktable": "Demo_Worktable_A"},
            manifest={"scripts": [], "worktable_geometry": _sample_manifest()["worktable_geometry"]},
            ir={"variables": [{"name": "TubeLocationName", "value": "Demo_Tube_Pos_1"}]},
        )
        self.assertEqual(location, "Demo_Tube_Pos_1")
        self.assertEqual(reason, "protocol IR variables")

    def test_nests_alone_do_not_become_tube_location(self):
        manifest = {
            "worktable_geometry": {
                "workspaces": [
                    {
                        "name": "Demo_Worktable_A",
                        "location_names": ["Demo_Nest_Pos", "NestPlatform", "Demo_Device_Pos"],
                    }
                ]
            },
            "scripts": [],
        }
        location, reason = resolve_tube_deck_location({"worktable": "Demo_Worktable_A"}, manifest=manifest)
        self.assertIsNone(location)
        self.assertEqual(reason, "unresolved")

    def test_resolve_explicit_recipe_override(self):
        recipe = {
            "worktable": "Demo_Worktable_A",
            "tube_deck_location": "Custom_Pos",
        }
        location, reason = resolve_tube_deck_location(recipe, manifest=_sample_manifest())
        self.assertEqual(location, "Custom_Pos")
        self.assertEqual(reason, "verification_recipe.tube_deck_location")

    def test_normalize_variable_mappings_replaces_tube_location_name(self):
        mappings = [
            {"target": "InputSubLocation", "source": "TubeLocationName"},
            {"target": "InputSubTubeName", "source": '"SampleSourceTube"'},
        ]
        updated, count = normalize_variable_mappings(mappings, "Demo_Tube_Pos_1")
        self.assertEqual(count, 1)
        self.assertEqual(updated[0]["source"], '"Demo_Tube_Pos_1"')
        self.assertEqual(updated[1]["source"], '"SampleSourceTube"')

    def test_normalize_recipe_capbc_subroutine_mappings(self):
        recipe = {
            "groups": [
                {
                    "name": "Cap/scan",
                    "steps": [
                        {
                            "subroutine": {
                                "name": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                                "variable_mappings_start": [
                                    {"target": "InputSubLocation", "source": "TubeLocationName"},
                                ],
                            }
                        }
                    ],
                }
            ]
        }
        fixups = normalize_recipe_subroutine_deck_locations(recipe, "Demo_Tube_Pos_1")
        self.assertEqual(len(fixups), 1)
        mapping = recipe["groups"][0]["steps"][0]["subroutine"]["variable_mappings_start"][0]
        self.assertEqual(mapping["source"], '"Demo_Tube_Pos_1"')

    def test_apply_bindings_to_ir(self):
        ir = {
            "worktable": {"name": "Demo_Worktable_A"},
            "variables": [{"name": "TubeLocationName", "value": 0}],
            "steps": [
                {
                    "id": "step_039",
                    "operation": "call_subroutine",
                    "parameters": {
                        "subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                        "variable_mappings_start": [
                            {"target": "InputSubLocation", "source": "TubeLocationName"},
                        ],
                    },
                }
            ],
        }
        recipe = {"worktable": "Demo_Worktable_A"}

        class _Ctx:
            manifest = _sample_manifest()

        report = apply_subroutine_deck_location_bindings(ir, recipe=recipe, context=_Ctx())
        self.assertEqual(report["tube_deck_location"], "Demo_Tube_Pos_1")
        self.assertTrue(report["variable_fixups"])
        self.assertEqual(ir["variables"][0]["value"], "Demo_Tube_Pos_1")
        capbc_step = next(
            step
            for step in ir["steps"]
            if isinstance(step, dict)
            and step.get("operation") == "call_subroutine"
        )
        mapping = capbc_step["parameters"]["variable_mappings_start"][0]
        self.assertEqual(mapping["source"], '"Demo_Tube_Pos_1"')

    def test_resolve_capbc_prep_from_source_script(self):
        self._require_source_script_fixture()

        class _Ctx:
            root = FULL_EXPORT
            manifest = {
                "scripts": [
                    {
                        "object_name": "Demo_Script_2_50mL_v3.2",
                        "extracted_path": (
                            "extracted/DataStore/UserSpecific/"
                            "308c805a-1822-45aa-a284-441eca70c7b7.xscr"
                        ),
                    }
                ],
                "worktable_geometry": _sample_manifest()["worktable_geometry"],
            }

        extracted = extract_set_variable_defaults_from_xscr(
            SOURCE_SCRIPT_FIXTURE,
            {"GripperClose", "GripperOpen", "TubeLocationName"},
        )
        prep = resolve_capbc_prep_defaults(
            {"worktable": "Demo_Worktable_A"},
            context=_Ctx(),
            source_script_name="Demo_Script_2_50mL_v3.2",
        )
        variables = prep["prep_variables"]
        self.assertEqual(float(variables["GripperClose"]), float(extracted["GripperClose"]))
        self.assertEqual(float(variables["GripperOpen"]), float(extracted["GripperOpen"]))
        self.assertNotEqual(float(variables["GripperClose"]), 0.0)
        self.assertIn("SetVariable", prep["prep_sources"].get("GripperClose", ""))

    def test_apply_bindings_sets_gripper_defaults(self):
        self._require_source_script_fixture()

        ir = {
            "worktable": {"name": "Demo_Worktable_A"},
            "variables": [
                {"name": "GripperClose", "value": 0},
                {"name": "GripperOpen", "value": 0},
                {"name": "TubeLocationName", "value": 0},
            ],
            "steps": [],
        }

        class _Ctx:
            root = FULL_EXPORT
            manifest = {
                "scripts": [
                    {
                        "object_name": "Demo_Script_2_50mL_v3.2",
                        "extracted_path": (
                            "extracted/DataStore/UserSpecific/"
                            "308c805a-1822-45aa-a284-441eca70c7b7.xscr"
                        ),
                    }
                ],
                "worktable_geometry": _sample_manifest()["worktable_geometry"],
            }

        report = apply_subroutine_deck_location_bindings(
            ir,
            recipe={"worktable": "Demo_Worktable_A"},
            context=_Ctx(),
            source_script_name="Demo_Script_2_50mL_v3.2",
        )
        extracted = extract_set_variable_defaults_from_xscr(
            SOURCE_SCRIPT_FIXTURE,
            {"GripperClose", "GripperOpen"},
        )
        values = {row["name"]: row["value"] for row in ir["variables"] if isinstance(row, dict)}
        self.assertEqual(float(values["GripperClose"]), float(extracted["GripperClose"]))
        self.assertEqual(float(values["GripperOpen"]), float(extracted["GripperOpen"]))
        self.assertNotEqual(float(values["GripperClose"]), 0.0)
        self.assertIn("GripperClose", report["prep_variables"])

    def test_resolve_grips_from_manifest_startup_variables(self):
        manifest = {
            "scripts": [
                {
                    "object_name": "Demo\\Demo_Tube_Script_A",
                    "startup_variables": [
                        {"name": "GripperClose", "default_values": [str(DEMO_GRIP_CLOSE)]},
                        {"name": "GripperOpen", "default_values": [str(DEMO_GRIP_OPEN)]},
                        {"name": "TubeLocationName", "default_values": ['"Demo_Tube_Pos_1"']},
                    ],
                }
            ],
            "worktable_geometry": _sample_manifest()["worktable_geometry"],
        }
        prep = resolve_capbc_prep_defaults(
            {"worktable": "Demo_Worktable_A"},
            manifest=manifest,
        )
        variables = prep["prep_variables"]
        self.assertEqual(variables["GripperClose"], DEMO_GRIP_CLOSE)
        self.assertEqual(variables["GripperOpen"], DEMO_GRIP_OPEN)
        self.assertIn("startup defaults", prep["prep_sources"]["GripperClose"])
        self.assertEqual(variables["TubeLocationName"], "Demo_Tube_Pos_1")

    def test_does_not_invent_grip_widths_without_zeia(self):
        prep = resolve_capbc_prep_defaults(
            {"worktable": "Demo_Worktable_A"},
            manifest={"scripts": [], "worktable_geometry": _sample_manifest()["worktable_geometry"]},
        )
        self.assertNotIn("GripperClose", prep["prep_variables"])
        self.assertNotIn("GripperOpen", prep["prep_variables"])

    def test_emit_capbc_prep_set_variable_steps_before_first_capbc(self):

        ir = {
            "steps": [
                {"id": "step_001", "index": 1, "operation": "comment", "parameters": {"comment": "setup"}},
                {
                    "id": "step_002",
                    "index": 2,
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                },
            ]
        }
        prep = {
            "TubeLocationName": "Demo_Tube_Pos_1",
            "GripperOpen": DEMO_GRIP_OPEN,
            "GripperClose": DEMO_GRIP_CLOSE,
        }
        emitted = emit_capbc_prep_set_variable_steps(ir, prep)
        self.assertEqual(len(emitted), 3)
        ops = [step.get("operation") for step in ir["steps"]]
        capbc_idx = ops.index("call_subroutine")
        prep_ops = ops[:capbc_idx]
        self.assertEqual(prep_ops, ["comment", "set_variable", "set_variable", "set_variable"])
        set_steps = [step for step in ir["steps"] if step.get("operation") == "set_variable"]
        self.assertEqual(set_steps[0]["group"], CAPBC_PREP_GROUP_NAME)
        # Fallback order (no source XSCR): TubeLocationName before grips.
        self.assertEqual(
            [step["parameters"]["variable"] for step in set_steps],
            ["TubeLocationName", "GripperOpen", "GripperClose"],
        )

    def test_emit_order_follows_source_xscr_set_variable_sequence(self):
        ir = {
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_CapBC_Example"},
                }
            ]
        }
        prep = {
            "TubeLocationName": "Demo_Tube_Pos_1",
            "GripperOpen": DEMO_GRIP_OPEN,
            "GripperClose": DEMO_GRIP_CLOSE,
            "TubeRunnerName": "Demo_Runner",
        }
        source_order = ["GripperClose", "TubeRunnerName", "GripperOpen", "TubeLocationName"]
        emit_capbc_prep_set_variable_steps(ir, prep, source_order=source_order)
        set_steps = [step for step in ir["steps"] if step.get("operation") == "set_variable"]
        self.assertEqual(
            [step["parameters"]["variable"] for step in set_steps],
            source_order,
        )
        self.assertEqual(
            capbc_prep_emit_order(prep, source_order=source_order),
            source_order,
        )

    def test_extract_set_variable_order_from_xscr_document_order(self):
        import tempfile
        from pathlib import Path as _Path

        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.SetVariableStatement">
  <SetVariableStatement><Name>GripperClose</Name><Value>11</Value></SetVariableStatement>
</Object>
<Object Type="Tecan.Core.Scripting.SetVariableStatement">
  <SetVariableStatement><Name>TubeLocationName</Name><Value>"Demo_Tube_Pos_1"</Value></SetVariableStatement>
</Object>
<Object Type="Tecan.Core.Scripting.SetVariableStatement">
  <SetVariableStatement><Name>GripperOpen</Name><Value>22</Value></SetVariableStatement>
</Object>
<Object Type="Tecan.Core.Scripting.SetVariableStatement">
  <SetVariableStatement><Name>OtherVar</Name><Value>1</Value></SetVariableStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _Path(tmp) / "demo.xscr"
            path.write_text(xscr, encoding="utf-8")
            order = extract_set_variable_order_from_xscr(
                path,
                {"GripperClose", "GripperOpen", "TubeLocationName", "TubeRunnerName"},
            )
            self.assertEqual(order, ["GripperClose", "TubeLocationName", "GripperOpen"])
            values = extract_set_variable_defaults_from_xscr(
                path,
                {"GripperClose", "GripperOpen", "TubeLocationName"},
            )
            self.assertEqual(values["GripperClose"], "11")
            self.assertEqual(values["TubeLocationName"], '"Demo_Tube_Pos_1"')

    def test_emit_capbc_prep_set_variable_steps_can_target_call_group(self):
        ir = {
            "steps": [
                {"id": "step_001", "index": 1, "operation": "comment", "parameters": {"comment": "setup"}},
                {
                    "id": "step_002",
                    "index": 2,
                    "group": "RunFirstTubeCap - first tube cap handling",
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                },
            ]
        }
        prep = {
            "TubeLocationName": "Demo_Tube_Pos_1",
            "GripperOpen": DEMO_GRIP_OPEN,
            "GripperClose": DEMO_GRIP_CLOSE,
        }
        emitted = emit_capbc_prep_set_variable_steps(
            ir,
            prep,
            group_name="RunFirstTubeCap - first tube cap handling",
        )
        self.assertEqual(len(emitted), 3)
        set_steps = [step for step in ir["steps"] if step.get("operation") == "set_variable"]
        self.assertEqual(
            {step.get("group") for step in set_steps},
            {"RunFirstTubeCap - first tube cap handling"},
        )

    def test_apply_bindings_emits_prep_steps_when_capbc_present(self):
        self._require_source_script_fixture()

        ir = {
            "variables": [],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "call_subroutine",
                    "parameters": {
                        "subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                        "variable_mappings_start": [
                            {"target": "InputSubLocation", "source": "TubeLocationName"},
                        ],
                    },
                }
            ],
        }

        class _Ctx:
            root = FULL_EXPORT
            manifest = {
                **_sample_manifest(),
                "scripts": [
                    {
                        "object_name": "Demo_Script_2_50mL_v3.2",
                        "extracted_path": (
                            "extracted/DataStore/UserSpecific/"
                            "308c805a-1822-45aa-a284-441eca70c7b7.xscr"
                        ),
                    }
                ],
            }

        report = apply_subroutine_deck_location_bindings(
            ir,
            recipe={"worktable": "Demo_Worktable_A"},
            context=_Ctx(),
            source_script_name="Demo_Script_2_50mL_v3.2",
            emit_prep_steps=True,
        )
        set_steps = [step for step in ir["steps"] if step.get("operation") == "set_variable"]
        self.assertGreaterEqual(len(set_steps), 3)
        self.assertTrue(report.get("prep_steps_emitted"))

    def test_xscr_fixup_replaces_tube_location_mapping(self):
        xscr = """<?xml version='1.0' encoding='utf-8'?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData">
  <Payload>
    <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
      <LineNumber>39</LineNumber>
      <SubRoutineStatement>
        <SubRoutine>Demo\\SUB_CapBCScanHandeling_50mL_v0.2</SubRoutine>
        <VariableMappingsStart>
          <Object Type="Tecan.Core.Scripting.VariableMapping">
            <VariableMapping>
              <Target>InputSubLocation</Target>
              <Source>TubeLocationName</Source>
            </VariableMapping>
          </Object>
        </VariableMappingsStart>
      </SubRoutineStatement>
    </Object>
  </Payload>
</sd:VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.xscr"
            path.write_text(xscr, encoding="utf-8")
            fixups = apply_capbc_prep_fixups_to_xscr(path, {"TubeLocationName": "Demo_Tube_Pos_1"})
            self.assertEqual(len(fixups), 1)
            updated = path.read_text(encoding="utf-8")
            self.assertIn('<Source>"Demo_Tube_Pos_1"</Source>', updated)
            self.assertNotIn("<Source>TubeLocationName</Source>", updated)

    def test_schema_from_mappings_and_subroutine_decls(self):
        from fluent_pipeline.subroutine_deck_locations import (
            build_tube_prep_schema,
            grip_values_from_subroutine_decls,
            mine_prep_schema_from_mappings,
            mine_tube_runner_from_placements,
        )

        mappings = [
            {"target": "GripTubeClose", "source": "GripperClose"},
            {"target": "InputSubLocation", "source": "TubeLocationName"},
        ]
        mined = mine_prep_schema_from_mappings(mappings)
        self.assertIn("GripperClose", mined)
        self.assertIn("GripTubeClose", mined)
        self.assertIn("TubeLocationName", mined)

        capbc_xscr = """<?xml version='1.0' encoding='utf-8'?>
<VxData xmlns:i="http://www.w3.org/2001/XMLSchema-instance"
        xmlns:d2p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared">
  <Payload>
    <ObjectName>Demo\\SUB_CapBC_Example</ObjectName>
    <Properties>
      <VariableDeclarations>
        <d2p1:anyType i:type="d2p1:VariableDefinitionHelper">
          <Name>GripTubeClose</Name>
          <TypeName>Floating Point</TypeName>
          <Values><string>15</string></Values>
        </d2p1:anyType>
        <d2p1:anyType i:type="d2p1:VariableDefinitionHelper">
          <Name>GripTubeOpen</Name>
          <TypeName>Floating Point</TypeName>
          <Values><string>28</string></Values>
        </d2p1:anyType>
        <d2p1:anyType i:type="d2p1:VariableDefinitionHelper">
          <Name>UnrelatedFlag</Name>
          <TypeName>String</TypeName>
          <Values><string>no</string></Values>
        </d2p1:anyType>
      </VariableDeclarations>
    </Properties>
  </Payload>
</VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub_path = root / "extracted" / "capbc.xscr"
            sub_path.parent.mkdir(parents=True)
            sub_path.write_text(capbc_xscr, encoding="utf-8")

            class Ctx:
                def __init__(self):
                    self.root = root
                    self.manifest = {
                        "scripts": [
                            {
                                "object_name": "Demo\\SUB_CapBC_Example",
                                "extracted_path": str(sub_path),
                                "resolved_path": str(sub_path),
                            }
                        ],
                        "worktable_geometry": {
                            "workspaces": [
                                {
                                    "name": "Demo_WT",
                                    "placements": [
                                        {
                                            "catalog": "Demo Tube Runner_SiteLab",
                                            "label": "Demo Tube Runner_SiteLab[001]",
                                            "site_name": "Demo_Tube_Pos_1",
                                        }
                                    ],
                                    "location_names": ["Demo_Tube_Pos_1"],
                                }
                            ]
                        },
                    }

            ctx = Ctx()
            schema = build_tube_prep_schema(
                recipe={
                    "worktable": "Demo_WT",
                    "groups": [
                        {
                            "steps": [
                                {
                                    "subroutine": {
                                        "name": "Demo\\SUB_CapBC_Example",
                                        "variable_mappings_start": mappings,
                                    }
                                }
                            ]
                        }
                    ],
                },
                context=ctx,
                manifest=ctx.manifest,
            )
            self.assertIn("GripTubeClose", schema["names"])
            self.assertIn("GripperClose", schema["names"])
            self.assertNotIn("UnrelatedFlag", schema["names"])
            grips = grip_values_from_subroutine_decls(schema["declarations"])
            self.assertEqual(grips.get("GripperClose"), 15)
            self.assertEqual(grips.get("GripperOpen"), 28)

            runners = mine_tube_runner_from_placements(ctx.manifest, "Demo_WT")
            self.assertEqual(runners.get("TubeRunnerName"), "Demo Tube Runner_SiteLab")

            prep = resolve_capbc_prep_defaults(
                {"worktable": "Demo_WT"},
                context=ctx,
                manifest=ctx.manifest,
                ir={
                    "worktable": {"name": "Demo_WT"},
                    "steps": [
                        {
                            "operation": "call_subroutine",
                            "parameters": {
                                "subroutine": "Demo\\SUB_CapBC_Example",
                                "variable_mappings_start": mappings,
                            },
                        }
                    ],
                },
            )
            self.assertEqual(prep["prep_variables"].get("GripperClose"), 15)
            self.assertEqual(prep["prep_variables"].get("GripperOpen"), 28)
            self.assertEqual(prep["prep_variables"].get("TubeRunnerName"), "Demo Tube Runner_SiteLab")
            self.assertIn("subroutine VariableDefinition", prep["prep_sources"].get("GripperClose", ""))
            self.assertIn("runner placement", prep["prep_sources"].get("TubeRunnerName", ""))
            self.assertIn("GripTubeClose", prep["prep_schema"])


if __name__ == "__main__":
    unittest.main()
