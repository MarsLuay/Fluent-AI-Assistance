import copy
import tempfile
import unittest
import zipfile
from pathlib import Path

import fluent_pipeline.protocol_ir as protocol_ir_module
import fluent_pipeline.protocol_ir_compat as protocol_ir_compat_module
import fluent_pipeline.ir.rga_move_policy as rga_move_policy_module
from fluent_pipeline.protocol_ir import (
    CATALOG_DEFAULT_CLASS_NAMES,
    CANONICAL_IR_BUNDLE_VERSION,
    CANONICAL_IR_VERSION,
    annotate_verification_prompts_with_media,
    apply_rga_move_pattern_policy,
    apply_touchtools_media_path_map_to_xscr,
    build_media_path_map,
    convert_unsafe_rga_adapter_moves_to_prompts,
    protocol_ir_bundle_from_zeia,
    protocol_ir_from_python,
    protocol_ir_from_xscr,
    load_protocol_ir,
    media_slot_specs,
    normalize_operator_prompt_text,
    prompt_step_worktable_media_path,
    render_gwl,
    render_python_draft,
    render_recreate_markdown,
    required_media_slot_specs,
    sync_verification_prompt_target_labware,
    prompt_has_media_boilerplate,
    prompt_looks_like_external_initialization_check,
    write_protocol_ir,
)
from fluent_pipeline.protocol_ir_schema import ProtocolIRValidationError


PYTHON_DRAFT = """from fluentcoder import MCA100Box, Plate96, Reagent, Worktable


def build_worktable() -> Worktable:
    input_dna = Reagent("Input gDNA")
    wt = Worktable.from_workspace(
        "780_Empty",
        auto_place=False,
        protocol_name="Pipeline simple transfer",
        comment="Move liquid from one plate to another",
    )
    wt.group("Setup")
    src = wt.place(Plate96("SourcePlate", catalog="96 Well Flat"), "Site", 1)
    wt.place(Plate96("DestPlate", catalog="96 Well Flat"), "Site", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Site", 4)
    src.fill_all(input_dna, 50.0)
    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    head.dispense(wt.labware_by_label("DestPlate"), 20.0, liquid_class="Water Free Single")
    head.return_tips(tips)
    head.drop_adapter()
    return wt
"""


XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Pipeline simple transfer</ObjectName>
    <Comment>Move liquid from one plate to another</Comment>
    <Reference>
      <Guid>workspace-guid</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <Reference>
      <Guid>lc-guid</Guid>
      <TypeId>LiquidClass</TypeId>
      <ObjectName>Water Free Single</ObjectName>
    </Reference>
    <PayloadData>
      <Script>
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
                <ScriptGroupDataV1>
                  <Name>Setup</Name>
                  <Data>
                    <Statements>
                      <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
                        <AddLabwareDataV1>
                          <LabwareType>96 Well Flat</LabwareType>
                          <LabwareLable>SourcePlate</LabwareLable>
                          <Location>Site</Location>
                          <Position>1</Position>
                          <Rotation>0</Rotation>
                          <HasLid>False</HasLid>
                          <Data><LineNumber>2</LineNumber></Data>
                        </AddLabwareDataV1>
                      </Object>
                    </Statements>
                  </Data>
                </ScriptGroupDataV1>
              </Object>
              <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
                <ScriptGroupDataV1>
                  <Name>Transfer</Name>
                  <Data>
                    <Statements>
                      <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384AspirateScriptCommandDataV2">
                        <Mca384AspirateScriptCommandDataV2>
                          <LiquidClassName>Water Free Single</LiquidClassName>
                          <Volume>20</Volume>
                          <ScriptCommandCommonDataV2>
                            <LabwareName>SourcePlate</LabwareName>
                            <DeviceAlias>Instrument=1/Device=MCA384:1</DeviceAlias>
                            <AvailableID>USB:TECAN,FLUENT,1/MCA384:1</AvailableID>
                            <LineNumber>3</LineNumber>
                          </ScriptCommandCommonDataV2>
                        </Mca384AspirateScriptCommandDataV2>
                      </Object>
                    </Statements>
                  </Data>
                </ScriptGroupDataV1>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""


class ProtocolIRTests(unittest.TestCase):
    def test_public_helpers_come_from_protocol_ir_compat(self):
        self.assertIs(CATALOG_DEFAULT_CLASS_NAMES, protocol_ir_module.CATALOG_DEFAULT_CLASS_NAMES)
        self.assertIs(CATALOG_DEFAULT_CLASS_NAMES, protocol_ir_compat_module.CATALOG_DEFAULT_CLASS_NAMES)

        for helper in (
            normalize_operator_prompt_text,
            prompt_has_media_boilerplate,
            prompt_looks_like_external_initialization_check,
        ):
            self.assertIs(helper, getattr(protocol_ir_module, helper.__name__))
            self.assertIs(helper, getattr(protocol_ir_compat_module, helper.__name__))
            self.assertEqual(helper.__module__, "fluent_pipeline.protocol_ir_compat")

    def test_worktable_media_annotation_requires_still_image_path_by_default(self):
        ir = {
            "protocol": {"name": "MediaPromptDemo"},
            "steps": [
                {
                    "id": "step_001",
                    "operation": "prompt_user",
                    "parameters": {
                        "prompt": "Confirm AdapterA200 is seated.",
                        "deck_presence_check": True,
                        "worktable_labware": {
                            "labware": "AdapterA200",
                            "labware_type": "Adapter A200",
                        },
                    },
                }
            ],
        }

        annotate_verification_prompts_with_media(ir, default_rup_kind="mixed")
        params = ir["steps"][0]["parameters"]
        image, video = params["media_placeholders"]

        self.assertEqual(ir["steps"][0]["command_id"], "RUPWorktableStatement")
        self.assertTrue(image["worktable_display"])
        self.assertFalse(video.get("worktable_display", False))
        self.assertEqual(prompt_step_worktable_media_path(params), "media/step_001_image.png")

        required = required_media_slot_specs(media_slot_specs(ir))
        self.assertEqual([spec["slot"] for spec in required], ["step_001_image"])

        path_map = build_media_path_map(
            ir,
            r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images",
            subfolder="MediaPromptDemo_media",
        )
        worktable_entries = [
            entry for entry in path_map["entries"] if entry["drives_worktable_detail_path"]
        ]
        self.assertEqual(len(worktable_entries), 1)
        self.assertEqual(worktable_entries[0]["filename"], "step_001_image.png")
        self.assertTrue(
            worktable_entries[0]["absolute_path"].endswith(
                r"MediaPromptDemo_media\step_001_image.png"
            )
        )

    def test_media_path_rewrite_updates_actual_attachment_only_tag_occurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "demo.xscr"
            xscr.write_text(
                "<VxData><Payload><PayloadData><Script><Commands>"
                "<SelectedImagePath>media/step_004_image.png</SelectedImagePath>"
                "</Commands></Script></PayloadData></Payload></VxData>",
                encoding="utf-8",
            )
            target = (
                r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
                r"\Demo_media\step_004_image.png"
            )
            path_map = {
                "touchtools_dir": r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images",
                "subfolder": "Demo_media",
                "entries": [
                    {
                        "filename": "step_004_image.png",
                        "bundle_relative_path": "media/step_004_image.png",
                        "absolute_path": target,
                        "drives_selected_image_path": False,
                        "drives_worktable_detail_path": False,
                        "attachment_only": True,
                        "kind": "image",
                    }
                ],
            }

            fixups = apply_touchtools_media_path_map_to_xscr(xscr, path_map)

            text = xscr.read_text(encoding="utf-8")
            self.assertIn(f"<SelectedImagePath>{target}</SelectedImagePath>", text)
            self.assertEqual(fixups, [{"from": "media/step_004_image.png", "to": target, "tag": "SelectedImagePath"}])

    def test_python_user_prompt_and_comment_export_to_ir(self):
        draft_text = """
from fluentcoder import Worktable

def build_worktable() -> Worktable:
    wt = Worktable.from_workspace('780_Empty', auto_place=False, protocol_name='Prompt Draft')
    wt.group('Setup')
    wt.add_comment('Warm up')
    wt.user_prompt('Hello ASAP!', timeout=10, auto_close=True)
    return wt
"""
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "prompt_draft.py"
            draft.write_text(draft_text, encoding="utf-8")
            ir = protocol_ir_from_python(draft)
            self.assertEqual(
                [step["operation"] for step in ir["steps"]],
                ["comment", "prompt_user"],
            )
            self.assertEqual(ir["steps"][0]["parameters"]["comment"], "Warm up")
            self.assertEqual(ir["steps"][1]["parameters"]["prompt"], "Hello ASAP!")
            self.assertEqual(ir["steps"][1]["parameters"]["timeout"], 10)
            self.assertTrue(ir["steps"][1]["parameters"]["auto_close"])

    def test_python_draft_exports_canonical_ir_and_renderers(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "simple_transfer.py"
            draft.write_text(PYTHON_DRAFT, encoding="utf-8")

            ir = protocol_ir_from_python(draft)

            self.assertEqual(ir["ir_version"], CANONICAL_IR_VERSION)
            self.assertEqual(ir["protocol"]["name"], "Pipeline simple transfer")
            self.assertEqual(ir["worktable"]["name"], "780_Empty")
            self.assertEqual([item["label"] for item in ir["labware"]], ["SourcePlate", "DestPlate", "Tips"])
            self.assertEqual(ir["labware"][0]["initial_contents"]["reagent"], "Input gDNA")
            self.assertEqual(
                [step["operation"] for step in ir["steps"]],
                [
                    "add_labware",
                    "add_labware",
                    "add_labware",
                    "get_head_adapter",
                    "pick_up_tips",
                    "aspirate",
                    "dispense",
                    "set_tips_back",
                    "drop_head_adapter",
                ],
            )

            rendered_python = render_python_draft(ir)
            self.assertIn("Worktable.from_workspace('780_Empty'", rendered_python)
            self.assertIn("head.aspirate(sourceplate, 20.0, liquid_class='Water Free Single')", rendered_python)

            gwl = render_gwl(ir)
            self.assertIn("A;SourcePlate;;96 Well Flat;1;;20;Water Free Single;;;", gwl)
            self.assertIn("D;DestPlate;;96 Well Flat;1;;20;Water Free Single;;;", gwl)

            recreate = render_recreate_markdown(ir)
            self.assertIn("# Recreate Script: Pipeline simple transfer", recreate)
            self.assertIn("This guide is generated from the same canonical protocol IR", recreate)
            self.assertIn("## Manual FluentControl Steps", recreate)
            self.assertIn("2. Load worktable: `780_Empty`.", recreate)
            self.assertIn("3. Confirm labware:", recreate)
            self.assertIn("- `SourcePlate` at carrier position `Site 1`", recreate)
            self.assertIn("5. Pick up tips from `Tips`.", recreate)
            self.assertIn("6. Aspirate 20 uL from `SourcePlate` using liquid class `Water Free Single`.", recreate)
            self.assertIn("7. Dispense 20 uL into `DestPlate` using liquid class `Water Free Single`.", recreate)
            self.assertIn("## IR Command Reference", recreate)
            self.assertIn("Path to find it", recreate)

            ir_path = Path(tmp) / "simple_transfer.protocol-ir.json"
            write_protocol_ir(ir, ir_path)
            self.assertEqual(load_protocol_ir(ir_path)["ir_version"], CANONICAL_IR_VERSION)

    def test_python_draft_validates_before_rendering(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "expression_schema_version": "fluent_control.expression.v1",
            "id": "invalid_set_variable_target",
            "protocol": {"name": "Invalid SetVariable target"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "variables": [],
            "steps": [
                {
                    "id": "step_set_tube_name",
                    "index": 1,
                    "operation": "set_variable",
                    "command_id": "SetVariableStatement",
                    "name": "Set Variable",
                    "parameters": {
                        "variable": "TubeName",
                        "value_expression": {
                            "kind": "string_literal",
                            "value": "Falcon",
                        },
                    },
                }
            ],
        }

        with self.assertRaises(ProtocolIRValidationError) as raised:
            render_python_draft(ir)

        self.assertIn("undefined_assignment_target", str(raised.exception))
        self.assertIn("TubeName", str(raised.exception))

    def test_python_draft_renders_expression_labware_position(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "expression_position",
            "protocol": {"name": "Expression position"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "labware": [
                {
                    "label": "ElutionRack[001]",
                    "catalog": "Elution Rack",
                    "location": "Nest17",
                    "position": 'GetCoverSiteIndex("ParkAdapter")',
                }
            ],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "add_labware",
                    "target_labware": "ElutionRack[001]",
                    "parameters": {
                        "label": "ElutionRack[001]",
                        "labware_type": "Elution Rack",
                        "location": "Nest17",
                        "position": 'GetCoverSiteIndex("ParkAdapter")',
                    },
                }
            ],
        }

        rendered_python = render_python_draft(ir)

        self.assertIn(
            "wt.place(Plate96('ElutionRack[001]', catalog='Elution Rack'), "
            "'Nest17', parse_expression('GetCoverSiteIndex(\"ParkAdapter\")'))",
            rendered_python,
        )

    def test_python_draft_uses_expression_only_volume(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "expression_volume",
            "protocol": {"name": "Expression volume"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "labware": [
                {
                    "label": "SourcePlate",
                    "catalog": "96 Well Flat",
                    "location": "Site",
                    "position_expression": {"kind": "number_literal", "value": 1},
                }
            ],
            "liquid_classes": [{"name": "Water"}],
            "variables": [{"name": "TransferVolume", "type": "Integer", "default_value": 100}],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "group": "Transfer",
                    "operation": "aspirate",
                    "name": "Aspirate",
                    "target_labware": "SourcePlate",
                    "volume_ul_expression": {
                        "kind": "variable_reference",
                        "name": "TransferVolume",
                    },
                    "liquid_class": "Water",
                    "parameters": {},
                }
            ],
        }

        rendered_python = render_python_draft(ir)

        self.assertIn(
            "head.aspirate(sourceplate, parse_expression('TransferVolume'), liquid_class='Water')",
            rendered_python,
        )

    def test_python_draft_rejects_missing_variable_declarations(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "missing_variable",
            "protocol": {"name": "Missing variable"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "labware": [
                {
                    "label": "SourcePlate",
                    "catalog": "96 Well Flat",
                    "location": "Site",
                    "position_expression": {"kind": "number_literal", "value": 1},
                }
            ],
            "liquid_classes": [{"name": "Water"}],
            "variables": [],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "group": "Transfer",
                    "operation": "aspirate",
                    "name": "Aspirate",
                    "target_labware": "SourcePlate",
                    "volume_ul_expression": {
                        "kind": "variable_reference",
                        "name": "TransferVolume",
                    },
                    "liquid_class": "Water",
                    "parameters": {},
                }
            ],
        }

        with self.assertRaises(ProtocolIRValidationError) as raised:
            render_python_draft(ir)

        self.assertIn("TransferVolume", str(raised.exception))
        self.assertIn("undefined_variable", str(raised.exception))

    def test_expression_discovery_covers_plural_lists_and_subroutine_source_expressions(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "expression_discovery",
            "protocol": {"name": "Expression discovery"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "labware": [],
            "liquid_classes": [],
            "variables": [
                {"name": "WaterVol", "type": "Floating Point", "default_value": 10},
                {"name": "PlateCount", "type": "Integer", "default_value": 1},
                {"name": "RuntimeSeed", "type": "Integer", "default_value": 2},
                {
                    "name": "Choice",
                    "type": "Integer",
                    "value_expressions": [
                        {
                            "kind": "binary_expression",
                            "operator": "+",
                            "left": {"kind": "variable_reference", "name": "RuntimeSeed"},
                            "right": {"kind": "number_literal", "value": 1},
                        }
                    ],
                },
            ],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "group": "Discovery",
                    "operation": "comment",
                    "name": "Expression-only parameter list",
                    "parameters": {
                        "comment": "Expression import check",
                        "volume_expressions": [
                            {"kind": "variable_reference", "name": "WaterVol"},
                        ],
                    },
                },
                {
                    "id": "step_002",
                    "index": 2,
                    "group": "Discovery",
                    "operation": "call_subroutine",
                    "name": "Call subroutine",
                    "parameters": {
                        "subroutine": "sub.xscr",
                        "variable_mappings_start": [
                            {
                                "target": "LocalCount",
                                "source_expression": {
                                    "kind": "binary_expression",
                                    "operator": "+",
                                    "left": {"kind": "variable_reference", "name": "PlateCount"},
                                    "right": {"kind": "number_literal", "value": 1},
                                },
                            }
                        ],
                    },
                },
            ],
        }

        refs = protocol_ir_module._referenced_variable_names(ir)
        rendered_python = render_python_draft(ir)

        self.assertTrue({"WaterVol", "PlateCount", "RuntimeSeed"}.issubset(refs))
        self.assertIn("from fluentcoder import Reagent, VariableMapping, Worktable, parse_expression", rendered_python)
        self.assertIn("wt.declare_variable('WaterVol'", rendered_python)
        self.assertIn("wt.declare_variable('PlateCount'", rendered_python)
        self.assertIn("wt.declare_variable('RuntimeSeed'", rendered_python)
        self.assertIn("VariableMapping(target='LocalCount', source=parse_expression('(PlateCount + 1)'))", rendered_python)

    def test_xscr_exports_canonical_ir(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "simple_transfer.xscr"
            xscr.write_text(XSCR, encoding="utf-8")

            ir = protocol_ir_from_xscr(xscr)

            self.assertEqual(ir["ir_version"], CANONICAL_IR_VERSION)
            self.assertEqual(ir["protocol"]["name"], "Pipeline simple transfer")
            self.assertEqual(ir["worktable"]["guid"], "workspace-guid")
            self.assertEqual(ir["liquid_classes"][0]["name"], "Water Free Single")
            self.assertEqual([step["operation"] for step in ir["steps"]], ["add_labware", "aspirate"])
            self.assertEqual(ir["steps"][1]["compiled_path"], "Commands -> Transfer -> Line 3 -> Mca384AspirateScriptCommandDataV2")

    def test_xscr_prompt_and_comment_render_to_fluentcoder_api(self):
        extra = """
                      <Object Type="Tecan.Core.Scripting.CommentStatement">
                        <CommentStatement>
                          <Comment>Initialize TubeEye software</Comment>
                          <Data><LineNumber>3</LineNumber></Data>
                        </CommentStatement>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.UserPromptStatement">
                        <UserPromptStatement>
                          <Prompt>Close the instrument door and continue the script</Prompt>
                          <Timeout>1</Timeout>
                          <Data><LineNumber>4</LineNumber></Data>
                        </UserPromptStatement>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "prompt_and_comment.xscr"
            xscr.write_text(XSCR.replace("</Statements>", extra + "                    </Statements>", 1), encoding="utf-8")

            ir = protocol_ir_from_xscr(xscr)
            rendered_python = render_python_draft(ir)

            self.assertEqual([step["operation"] for step in ir["steps"]], ["add_labware", "comment", "prompt_user", "aspirate"])
            self.assertEqual(ir["steps"][1]["parameters"]["comment"], "Initialize TubeEye software")
            self.assertEqual(ir["steps"][2]["parameters"]["prompt"], "Close the instrument door and continue the script")
            self.assertEqual(ir["steps"][2]["parameters"]["timeout"], 1)
            self.assertIn("wt.add_comment('Initialize TubeEye software')", rendered_python)
            self.assertIn("wt.user_prompt('Close the instrument door and continue the script', timeout=1)", rendered_python)
            recreate = render_recreate_markdown(ir)
            self.assertIn("Comment: Initialize TubeEye software.", recreate)
            self.assertIn("Prompt user: Close the instrument door and continue the script.", recreate)

    def test_xscr_subroutine_call_is_first_class_ir(self):
        extra = """
                      <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
                        <SubRoutineStatement>
                          <SubRoutine>"Demo\\SUB_Get_Fingers_v1.0"</SubRoutine>
                          <ExecutionMode>JoinSubroutine</ExecutionMode>
                          <Data><LineNumber>4</LineNumber></Data>
                        </SubRoutineStatement>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "subroutine_call.xscr"
            xscr.write_text(XSCR.replace("</Statements>", extra + "                    </Statements>", 1), encoding="utf-8")

            ir = protocol_ir_from_xscr(xscr)
            rendered_python = render_python_draft(ir)
            recreate = render_recreate_markdown(ir)

            self.assertEqual([step["operation"] for step in ir["steps"]], ["add_labware", "call_subroutine", "aspirate"])
            self.assertEqual(ir["steps"][1]["parameters"]["subroutine"], "Demo\\SUB_Get_Fingers_v1.0")
            self.assertEqual(ir["steps"][1]["parameters"]["execution_mode"], "JoinSubroutine")
            subroutine_deps = [
                item for item in ir["dependencies"] if item.get("dependency_role") == "subroutine"
            ]
            self.assertEqual(subroutine_deps[0]["kind"], "Script")
            self.assertEqual(subroutine_deps[0]["name"], "Demo\\SUB_Get_Fingers_v1.0")
            self.assertIn("SubRoutineStatement", subroutine_deps[0]["source_path"])
            self.assertIn(
                "wt.call_subroutine('Demo\\\\SUB_Get_Fingers_v1.0', execution_mode='JoinSubroutine')",
                rendered_python,
            )
            self.assertIn("Call FluentControl subroutine `Demo\\SUB_Get_Fingers_v1.0`", recreate)

    def test_move_plate_renders_to_gripper_move(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "move_plate_test",
            "protocol": {"name": "Move plate test"},
            "source": {"format": "test", "path": ""},
            "worktable": {"name": "780_Empty"},
            "labware": [
                {"label": "AdapterA200", "catalog": "Adapter A200", "location": "Demo_Nest_Pos", "position": 1},
                {"label": "FilterDWP[platecount]", "catalog": "24 Filter Plate", "location": "NestPlatform", "position": 3},
            ],
            "reagents": [],
            "liquid_classes": [],
            "variables": [
                {
                    "name": "platecount",
                    "type": "Integer",
                    "default_expression": {"kind": "number_literal", "value": 1},
                }
            ],
            "worklists": [],
            "dependencies": [],
            "safety_assumptions": [],
            "steps": [
                {
                    "index": 1,
                    "group": "Move",
                    "operation": "move_plate",
                    "name": "Move Adapter",
                    "target_labware": "AdapterA200",
                    "parameters": {"labware": "AdapterA200", "destination_location": "Demo_Device_Pos", "destination_site": 1},
                    "safety_flags": ["rga_motion"],
                },
                {
                    "index": 2,
                    "group": "Move",
                    "operation": "move_plate",
                    "name": "Move Filter Plate",
                    "target_labware": "FilterDWP[platecount]",
                    "parameters": {"labware": "FilterDWP[platecount]", "onto_labware": "AdapterA200"},
                },
                {
                    "index": 3,
                    "group": "Move",
                    "operation": "move_plate",
                    "name": "Return Filter Plate",
                    "target_labware": "FilterDWP[platecount]",
                    "parameters": {"labware": "FilterDWP[platecount]", "destination_location": "NestPlatform", "destination_site": 3},
                },
            ],
        }

        rendered_python = render_python_draft(ir)
        recreate = render_recreate_markdown(ir)

        self.assertIn(
            "wt.declare_variable('platecount', 1, type_name='Integer')",
            rendered_python,
        )
        self.assertIn("wt.set_sim_value('platecount', 1)", rendered_python)
        self.assertIn("wt.gripper.move(filterdwp_platecount, onto=adaptera200)", rendered_python)
        self.assertIn("wt.gripper.move(filterdwp_platecount, to=('NestPlatform', 3))", rendered_python)
        self.assertIn("Move `FilterDWP[platecount]` onto `AdapterA200` with the RGA gripper.", recreate)

    def test_move_plate_renders_authoritative_site_expression(self):
        site_expression = {"kind": "variable_reference", "name": "DestinationSite"}
        for expression_key in (
            "site_expression",
            "destination_site_expression",
            "to_site_expression",
        ):
            with self.subTest(expression_key=expression_key):
                ir = {
                    "ir_version": CANONICAL_IR_VERSION,
                    "id": "move_plate_site_expression_test",
                    "protocol": {"name": "Move plate site expression test"},
                    "source": {"format": "test", "path": ""},
                    "worktable": {"name": "780_Empty"},
                    "labware": [
                        {
                            "label": "SourcePlate",
                            "catalog": "96 Well Plate",
                            "location": "NestPlatform",
                            "position": 1,
                        }
                    ],
                    "reagents": [],
                    "liquid_classes": [],
                    "variables": [
                        {
                            "name": "DestinationSite",
                            "type": "Integer",
                            "default_expression": {"kind": "number_literal", "value": 3},
                        }
                    ],
                    "worklists": [],
                    "dependencies": [],
                    "safety_assumptions": [],
                    "steps": [
                        {
                            "index": 1,
                            "group": "Move",
                            "operation": "move_plate",
                            "name": "Move Source Plate",
                            "target_labware": "SourcePlate",
                            "parameters": {
                                "labware": "SourcePlate",
                                "destination_location": "NestPlatform",
                                expression_key: copy.deepcopy(site_expression),
                            },
                        }
                    ],
                }

                normalized = protocol_ir_module.migrate_protocol_ir(copy.deepcopy(ir))
                normalized_params = normalized["steps"][0]["parameters"]
                self.assertIn("site_expression", normalized_params)
                self.assertNotIn("destination_site_expression", normalized_params)
                self.assertNotIn("to_site_expression", normalized_params)

                rendered_python = render_python_draft(ir)

                self.assertIn(
                    "wt.gripper.move(sourceplate, to=('NestPlatform', parse_expression('DestinationSite')))",
                    rendered_python,
                )
                self.assertNotIn("wt.gripper.move(sourceplate, to=('NestPlatform', 1))", rendered_python)

    def test_unsafe_rga_adapter_onto_move_converts_to_prompt(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "move_plate_test",
            "protocol": {"name": "Move plate test"},
            "source": {"format": "test", "path": ""},
            "worktable": {"name": "780_Empty"},
            "labware": [
                {"label": "AdapterA200", "catalog": "Adapter A200", "location": "Demo_Nest_Pos", "position": 1},
                {"label": "FilterDWP[platecount]", "catalog": "24 Filter Plate", "location": "NestPlatform", "position": 3},
            ],
            "reagents": [],
            "liquid_classes": [],
            "variables": [],
            "worklists": [],
            "dependencies": [],
            "safety_assumptions": [],
            "steps": [
                {
                    "index": 1,
                    "group": "Move",
                    "operation": "move_plate",
                    "name": "Move Adapter",
                    "target_labware": "AdapterA200",
                    "parameters": {"labware": "AdapterA200", "destination_location": "Demo_Device_Pos", "destination_site": 1},
                    "safety_flags": ["rga_motion"],
                },
                {
                    "index": 2,
                    "group": "Move",
                    "operation": "move_plate",
                    "name": "Move Filter Plate",
                    "target_labware": "FilterDWP[platecount]",
                    "parameters": {"labware": "FilterDWP[platecount]", "onto_labware": "AdapterA200"},
                    "safety_flags": ["rga_motion"],
                }
            ],
        }

        converted = convert_unsafe_rga_adapter_moves_to_prompts(ir)
        rendered_python = render_python_draft(converted)

        self.assertEqual(converted["steps"][0]["operation"], "prompt_user")
        self.assertEqual(converted["steps"][1]["operation"], "prompt_user")
        self.assertEqual(converted["steps"][0]["parameters"]["reason"], "rga_adapter_onto_move_requires_manual_verification")
        self.assertIn("rga_adapter_manual_check", converted["steps"][0]["safety_flags"])
        self.assertNotIn("wt.gripper.move(adaptera200, to=('Demo_Device_Pos', 1))", rendered_python)
        self.assertNotIn("wt.gripper.move(filterdwp_platecount, onto=adaptera200)", rendered_python)
        self.assertIn("Manual verification only", rendered_python)
        self.assertIn("rga_adapter_moves_prompt_only", {item["id"] for item in converted["safety_assumptions"]})

    def test_rga_move_policy_preserves_pattern_backed_move(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "pattern_backed_rga_move",
            "protocol": {"name": "Pattern backed RGA move"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "labware": [
                {"label": "SourcePlate", "catalog": "96 Well Plate", "location": "Site", "position": 1},
                {"label": "DestNest", "catalog": "Fixed Nest", "location": "Site", "position": 2},
            ],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "move_plate",
                    "name": "Move source plate",
                    "target_labware": "SourcePlate",
                    "parameters": {
                        "labware": "SourcePlate",
                        "onto_labware": "DestNest",
                        "source_pattern_id": 42,
                        "source_pattern_type": "move_plate",
                        "source_script": "KnownGoodRgaMove",
                        "command_index": 17,
                    },
                }
            ],
        }

        converted = apply_rga_move_pattern_policy(ir)
        rendered_python = render_python_draft(converted)
        policy = converted["source"]["rga_move_policy"]

        self.assertEqual(converted["steps"][0]["operation"], "move_plate")
        self.assertIn("wt.gripper.move(sourceplate, onto=destnest)", rendered_python)
        self.assertEqual(policy["pattern_backed"][0]["source_pattern"]["source_pattern_id"], 42)
        self.assertEqual(policy["manual_fallback"], [])

    def test_rga_move_policy_preserves_approved_automated_verification_motion(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "approved_automated_rga_move",
            "protocol": {"name": "Approved automated RGA move"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "labware": [
                {"label": "AdapterA200", "catalog": "Adapter A200_ElutionRack", "location": "Demo_Nest_Pos", "position": 1},
            ],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "move_plate",
                    "name": "Move adapter",
                    "target_labware": "AdapterA200",
                    "safety_flags": ["automated_verification_motion"],
                    "parameters": {
                        "labware": "AdapterA200",
                        "destination_location": "Demo_Device_Pos",
                        "destination_site": 1,
                        "allow_automated_verification_motion": True,
                    },
                }
            ],
        }

        converted = apply_rga_move_pattern_policy(ir)
        rendered_python = render_python_draft(converted)
        policy = converted["source"]["rga_move_policy"]

        self.assertEqual(converted["steps"][0]["operation"], "move_plate")
        self.assertNotIn("Manual verification only", rendered_python)
        self.assertEqual(policy["approved_automated"][0]["labware"], "AdapterA200")
        self.assertEqual(policy["manual_fallback"], [])

    def test_rga_move_policy_converts_unbacked_non_adapter_move(self):
        ir = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "unbacked_rga_move",
            "protocol": {"name": "Unbacked RGA move"},
            "source": {"format": "test"},
            "worktable": {"name": "780_Empty"},
            "labware": [{"label": "PlateA", "catalog": "96 Well Plate", "location": "Site", "position": 1}],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "move_plate",
                    "name": "Move plate A",
                    "target_labware": "PlateA",
                    "parameters": {"labware": "PlateA", "destination_location": "Site", "destination_site": 2},
                }
            ],
        }

        converted = apply_rga_move_pattern_policy(ir)

        self.assertEqual(converted["steps"][0]["operation"], "prompt_user")
        self.assertEqual(converted["steps"][0]["parameters"]["reason"], "rga_move_requires_mined_source_pattern")
        self.assertIn("rga_move_manual_check", converted["steps"][0]["safety_flags"])
        self.assertEqual(converted["source"]["rga_move_policy"]["manual_fallback"][0]["labware"], "PlateA")

    def test_rga_move_policy_new_module_and_legacy_facade_are_same_api(self):
        self.assertIs(
            protocol_ir_module.apply_rga_move_pattern_policy,
            rga_move_policy_module.apply_rga_move_pattern_policy,
        )
        self.assertIs(
            protocol_ir_module.convert_unsafe_rga_adapter_moves_to_prompts,
            rga_move_policy_module.convert_unsafe_rga_adapter_moves_to_prompts,
        )

    def test_recreate_guide_includes_original_request_prompt(self):
        ir = {
            "ir_version": "tecan.protocol_ir.v1",
            "id": "demo",
            "protocol": {"name": "Demo Protocol"},
            "source": {"intent": "Use these ZEIA files to make a new wash script"},
            "worktable": {"name": "780_Empty"},
            "labware": [],
            "steps": [],
        }
        recreate = render_recreate_markdown(ir)
        self.assertIn("## Original Request", recreate)
        self.assertIn("Use these ZEIA files to make a new wash script", recreate)

    def test_recreate_guide_header_links_request_spec(self):
        ir = {
            "ir_version": "tecan.protocol_ir.v1",
            "id": "demo",
            "protocol": {"name": "Demo Protocol"},
            "worktable": {"name": "780_Empty"},
            "labware": [],
            "steps": [],
        }
        recreate = render_recreate_markdown(
            ir,
            generated_files={
                "request_spec": "source/request.spec.yaml",
                "ir": "source/protocol.ir.json",
                "python": "source/protocol_draft.py",
                "xscr": "direct-imports/scripts/full-script/generated_script.xscr",
                "zeia": "direct-imports/projects/full-project/generated_project.zeia",
            },
        )
        self.assertIn("- Source of truth: `source/protocol.ir.json`", recreate)
        self.assertIn("- Script name: `Demo Protocol`", recreate)
        self.assertIn("- Request spec / prompt: `source/request.spec.yaml`", recreate)
        self.assertIn("- Python draft: `source/protocol_draft.py`", recreate)
        self.assertIn("- Direct import file: `direct-imports/scripts/full-script/generated_script.xscr`", recreate)
        self.assertIn("- One-file project import: `direct-imports/projects/full-project/generated_project.zeia`", recreate)

    def test_recreate_guide_notes_missing_original_request(self):
        ir = {
            "ir_version": "tecan.protocol_ir.v1",
            "id": "demo",
            "protocol": {"name": "Demo Protocol"},
            "worktable": {"name": "780_Empty"},
            "labware": [],
            "steps": [],
        }
        recreate = render_recreate_markdown(ir)
        self.assertIn("## Original Request", recreate)
        self.assertIn("No original request prompt was recorded", recreate)

    def test_xscr_rga_transfer_macro_is_move_plate_ir(self):
        extra = """
                      <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
                        <ApplicationDriverMacro Version="1" Name="RGA1_TransferLabware" ModuleName="RGA 1" ExecutionTime="PT2S" IsBreakpoint="false" IsDisabledForExecution="false" LineNumber="4">
                          <ExecutionSettings>&lt;TransferLabwareCommandParameters xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Drivers.RobotDriverBase"&gt;&lt;FixedSite&gt;true&lt;/FixedSite&gt;&lt;Labware&gt;FilterDWP[platecount]&lt;/Labware&gt;&lt;Location&gt;NestPlatform&lt;/Location&gt;&lt;MoveToBase&gt;false&lt;/MoveToBase&gt;&lt;Site&gt;3&lt;/Site&gt;&lt;/TransferLabwareCommandParameters&gt;</ExecutionSettings>
                        </ApplicationDriverMacro>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "rga_transfer.xscr"
            xscr.write_text(XSCR.replace("</Statements>", extra + "                    </Statements>", 1), encoding="utf-8")

            ir = protocol_ir_from_xscr(xscr)
            move = ir["steps"][1]

            self.assertEqual([step["operation"] for step in ir["steps"]], ["add_labware", "move_plate", "aspirate"])
            self.assertEqual(move["target_labware"], "FilterDWP[platecount]")
            self.assertEqual(move["parameters"]["labware"], "FilterDWP[platecount]")
            self.assertEqual(move["parameters"]["destination_location"], "NestPlatform")
            self.assertEqual(move["parameters"]["destination_site"], 3)
            self.assertFalse(move["parameters"]["move_to_base"])

    def test_xscr_rga_transfer_macro_inside_conditional_group_is_move_plate_ir(self):
        """Verified moves compile inside toggle ConditionalGroups; reparse must keep them."""
        nested = """
                      <Object Type="Tecan.Core.Scripting.ConditionalGroup">
                        <ConditionalGroup>
                          <Name>RunA200Adapter</Name>
                          <Objects>
                            <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
                              <ApplicationDriverMacro Version="1" Name="RGA1_TransferLabware" ModuleName="RGA 1" ExecutionTime="PT2S" IsBreakpoint="false" IsDisabledForExecution="false" LineNumber="4">
                                <ExecutionSettings>&lt;TransferLabwareCommandParameters xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Drivers.RobotDriverBase"&gt;&lt;FixedSite&gt;true&lt;/FixedSite&gt;&lt;Labware&gt;AdapterA200&lt;/Labware&gt;&lt;Location&gt;Demo_Device_Pos&lt;/Location&gt;&lt;MoveToBase&gt;false&lt;/MoveToBase&gt;&lt;Site&gt;1&lt;/Site&gt;&lt;/TransferLabwareCommandParameters&gt;</ExecutionSettings>
                              </ApplicationDriverMacro>
                            </Object>
                          </Objects>
                        </ConditionalGroup>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "rga_transfer_conditional.xscr"
            xscr.write_text(XSCR.replace("</Statements>", nested + "                    </Statements>", 1), encoding="utf-8")

            ir = protocol_ir_from_xscr(xscr)
            operations = [step["operation"] for step in ir["steps"]]
            self.assertIn("move_plate", operations)
            move = next(step for step in ir["steps"] if step["operation"] == "move_plate")
            self.assertEqual(move["parameters"]["labware"], "AdapterA200")
            self.assertEqual(move["parameters"]["destination_location"], "Demo_Device_Pos")
            self.assertEqual(move["parameters"]["destination_site"], 1)

    def test_xscr_execute_single_vector_inside_conditional_group_is_preserved(self):
        """Non-transfer driver macros must survive source-to-IR conversion verbatim."""
        nested = """
                      <Object Type="Tecan.Core.Scripting.ConditionalGroup">
                        <ConditionalGroup>
                          <Name>RunCapTest</Name>
                          <Objects>
                            <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
                              <ApplicationDriverMacro Version="1" Name="RGA1_ExecuteSingleVector" ModuleName="RGA 1" ExecutionTime="PT2S" IsBreakpoint="false" IsDisabledForExecution="false" LineNumber="4">
                                <ExecutionSettings>&lt;ExecuteSingleVectorCommandParameters&gt;&lt;Location&gt;VialGripper_Right&lt;/Location&gt;&lt;VectorName&gt;cap_50mL&lt;/VectorName&gt;&lt;ZOffset&gt;8.5&lt;/ZOffset&gt;&lt;/ExecuteSingleVectorCommandParameters&gt;</ExecutionSettings>
                              </ApplicationDriverMacro>
                            </Object>
                          </Objects>
                        </ConditionalGroup>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "execute_single_vector_conditional.xscr"
            xscr.write_text(XSCR.replace("</Statements>", nested + "                    </Statements>", 1), encoding="utf-8")

            ir = protocol_ir_from_xscr(xscr)
            driver = next(step for step in ir["steps"] if step["operation"] == "application_driver_macro")
            rendered_python = render_python_draft(ir)

            self.assertEqual(driver["command_id"], "ApplicationDriverMacro")
            self.assertIn("RGA1_ExecuteSingleVector", driver["parameters"]["raw_xml"])
            self.assertIn("cap_50mL", driver["parameters"]["raw_xml"])
            self.assertIn("ZOffset&gt;8.5", driver["parameters"]["raw_xml"])
            self.assertIn("wt.raw_xml_step('ApplicationDriverMacro'", rendered_python)

    def test_xscr_branch_uses_its_direct_condition_expression(self):
        """Nested branch conditions must not replace the enclosing branch condition."""
        command = protocol_ir_module.ET.fromstring(
            """
            <Object Type="Tecan.Core.Scripting.ConditionalGroup">
              <ConditionalGroup>
                <Objects>
                  <Object Type="Tecan.Core.Scripting.ConditionalGroup">
                    <ConditionalGroup>
                      <Objects />
                      <Condition>InnerFlag=1</Condition>
                      <Name>Inner</Name>
                    </ConditionalGroup>
                  </Object>
                </Objects>
                <Condition>OuterFlag=1</Condition>
                <Name>Outer</Name>
              </ConditionalGroup>
            </Object>
            """
        )

        step = protocol_ir_module._xscr_branch_step(
            command,
            "Test",
            "conditional_branch",
            "ConditionalGroup",
            "Commands -> Test",
        )

        self.assertEqual(step["parameters"]["condition"], "OuterFlag=1")
        self.assertEqual(
            step["parameters"]["condition_expression"],
            {
                "kind": "binary_expression",
                "operator": "=",
                "left": {"kind": "variable_reference", "name": "OuterFlag"},
                "right": {"kind": "number_literal", "value": 1},
            },
        )
    def test_xscr_models_fluentcontrol_variables_runtime_prompts_and_branch_defaults(self):
        declarations = """
        <Properties>
          <VariableDeclarations>
            <VariableDeclarations xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine">
              <VariableDeclarations xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays">
                <d2p1:anyType xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper">
                  <d3p1:Name>StartupVolume</d3p1:Name>
                  <d3p1:QueryOnStartup>true</d3p1:QueryOnStartup>
                  <d3p1:QueryOnStartupString>Enter startup volume</d3p1:QueryOnStartupString>
                  <d3p1:ReadOnly>false</d3p1:ReadOnly>
                  <d3p1:Scope>Parameter</d3p1:Scope>
                  <d3p1:TypeName>Integer</d3p1:TypeName>
                  <d3p1:Values><d2p1:string>25</d2p1:string></d3p1:Values>
                </d2p1:anyType>
              </VariableDeclarations>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
"""
        extra = """
                      <Object Type="Tecan.Core.Scripting.QueryVariableStatement">
                        <QueryVariableStatement>
                          <Name>StartupVolume</Name>
                          <QueryPrompt>Confirm startup volume</QueryPrompt>
                          <MinimumText>1</MinimumText>
                          <MaximumText>100</MaximumText>
                          <LineNumber>3</LineNumber>
                        </QueryVariableStatement>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.SetVariableStatement">
                        <SetVariableStatement>
                          <Name>TubeCount</Name>
                          <Value>StartupVolume+1</Value>
                          <LineNumber>4</LineNumber>
                        </SetVariableStatement>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.SetVariableStatement">
                        <SetVariableStatement>
                          <Name>RemainingRuntime</Name>
                          <Value>00:05:00</Value>
                          <LineNumber>5</LineNumber>
                        </SetVariableStatement>
                      </Object>
                      <Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPVariableStatement">
                        <RUPVariableStatement>
                          <VariableDatas>
                            <VariableDataModel>
                              <Variables>
                                <RupVariableItem>
                                  <VariableName>OperatorName</VariableName>
                                  <VariableType>System.String</VariableType>
                                  <DisplayText>Operator name</DisplayText>
                                  <DisplayType>TextBox</DisplayType>
                                  <VariableValue>Mars</VariableValue>
                                  <AllowedValues>Mars;Tecan</AllowedValues>
                                  <IsEnabled>true</IsEnabled>
                                  <MinValue>0</MinValue>
                                  <MaxValue>0</MaxValue>
                                  <IsMinMaxValueUsed>false</IsMinMaxValueUsed>
                                </RupVariableItem>
                              </Variables>
                            </VariableDataModel>
                          </VariableDatas>
                          <RUPScreenTitle>User Input</RUPScreenTitle>
                          <RUPDisplayAndWait>True</RUPDisplayAndWait>
                          <RUPAutoClose>False</RUPAutoClose>
                          <RUPTimeOut>1</RUPTimeOut>
                          <LineNumber>6</LineNumber>
                        </RUPVariableStatement>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.ConditionalGroup">
                        <ConditionalGroup>
                          <Objects />
                          <Condition>TubeCount&gt;0</Condition>
                          <Name>Has tubes</Name>
                          <IsDisabledForExecution>False</IsDisabledForExecution>
                          <LineNumber>7</LineNumber>
                        </ConditionalGroup>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.AlternateGroup">
                        <AlternateGroup>
                          <Objects />
                          <Name>No tubes</Name>
                          <IsDisabledForExecution>True</IsDisabledForExecution>
                          <LineNumber>8</LineNumber>
                        </AlternateGroup>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "variables.xscr"
            xscr.write_text(
                XSCR.replace("<Commands>", declarations + "        <Commands>").replace(
                    "</Statements>",
                    extra + "                    </Statements>",
                    1,
                ),
                encoding="utf-8",
            )

            ir = protocol_ir_from_xscr(xscr)
            rendered_python = render_python_draft(ir)

            self.assertEqual(
                [step["operation"] for step in ir["steps"]],
                [
                    "add_labware",
                    "query_variable",
                    "set_variable",
                    "set_remaining_runtime",
                    "runtime_variable_prompt",
                    "conditional_branch",
                    "default_branch",
                    "aspirate",
                ],
            )
            variables = {item["name"]: item for item in ir["variables"]}
            self.assertTrue(variables["StartupVolume"]["query_at_startup"])
            self.assertEqual(variables["StartupVolume"]["default_value"], 25)
            self.assertEqual(variables["StartupVolume"]["queries"][0]["prompt"], "Confirm startup volume")
            self.assertEqual(variables["TubeCount"]["assignments"][0]["value"], "StartupVolume+1")
            self.assertEqual(variables["RemainingRuntime"]["kind"], "internal")
            self.assertEqual(variables["RemainingRuntime"]["assignments"][0]["operation"], "set_remaining_runtime")
            self.assertEqual(variables["OperatorName"]["allowed_values"], ["Mars", "Tecan"])
            self.assertEqual(ir["steps"][5]["parameters"]["condition"], "TubeCount>0")
            self.assertTrue(ir["steps"][6]["parameters"]["is_default_branch"])
            self.assertTrue(ir["steps"][6]["parameters"]["is_disabled_for_execution"])
            self.assertIn("wt.raw_xml_step('SetVariableStatement'", rendered_python)

    def test_xscr_unrendered_source_operation_preserves_raw_xml(self):
        extra = """
                      <Object Type="Tecan.Core.Scripting.LoopGroup">
                        <LoopGroup>
                          <Objects />
                          <LoopVariable>platecount</LoopVariable>
                          <NumberOfLoops>2</NumberOfLoops>
                          <LineNumber>3</LineNumber>
                        </LoopGroup>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "loop_group.xscr"
            xscr.write_text(XSCR.replace("</Statements>", extra + "                    </Statements>", 1), encoding="utf-8")

            ir = protocol_ir_from_xscr(xscr)
            rendered_python = render_python_draft(ir)
            loop_step = next(step for step in ir["steps"] if step["operation"] == "loop_over_wells")

            self.assertIn("raw_xml", loop_step["parameters"])
            self.assertIn("wt.raw_xml_step('LoopGroup'", rendered_python)

    def test_xscr_nested_script_group_is_not_rendered_twice(self):
        extra = """
                      <Object Type="Tecan.Core.Scripting.LoopGroup">
                        <LoopGroup>
                          <Objects>
                            <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
                              <ScriptGroupDataV1>
                                <Name>Nested A200</Name>
                                <Data>
                                  <Statements>
                                    <Object Type="Tecan.Core.Scripting.CommentStatement">
                                      <CommentStatement>
                                        <Comment>Nested A200 command</Comment>
                                        <LineNumber>4</LineNumber>
                                      </CommentStatement>
                                    </Object>
                                  </Statements>
                                </Data>
                              </ScriptGroupDataV1>
                            </Object>
                          </Objects>
                          <LoopVariable>platecount</LoopVariable>
                          <NumberOfLoops>2</NumberOfLoops>
                          <LineNumber>3</LineNumber>
                        </LoopGroup>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "nested_group.xscr"
            xscr.write_text(
                XSCR.replace("</Statements>", extra + "                    </Statements>", 1),
                encoding="utf-8",
            )

            ir = protocol_ir_from_xscr(xscr)
            rendered_python = render_python_draft(ir)

        nested = next(
            step
            for step in ir["steps"]
            if step.get("parameters", {}).get("comment") == "Nested A200 command"
        )
        self.assertEqual(nested["parameters"]["embedded_in_raw_command"], "LoopGroup")
        self.assertEqual(rendered_python.count("Nested A200 command"), 1)

    def test_xscr_reinspects_commands_nested_in_conditional_groups(self):
        extra = """
                      <Object Type="Tecan.Core.Scripting.ConditionalGroup">
                        <ConditionalGroup>
                          <Objects>
                            <Object Type="Tecan.Core.Scripting.UserPromptStatement">
                              <UserPromptStatement>
                                <Prompt>Nested verification prompt</Prompt>
                                <AutoClose>False</AutoClose>
                                <Timeout>1</Timeout>
                                <LineNumber>3</LineNumber>
                              </UserPromptStatement>
                            </Object>
                          </Objects>
                          <Condition>RunSection="yes"</Condition>
                          <Name>Conditional section</Name>
                        </ConditionalGroup>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "conditional_group.xscr"
            xscr.write_text(
                XSCR.replace("</Statements>", extra + "                    </Statements>", 1),
                encoding="utf-8",
            )

            ir = protocol_ir_from_xscr(xscr)

        prompts = [
            step
            for step in ir["steps"]
            if step["operation"] == "prompt_user"
            and step.get("parameters", {}).get("prompt") == "Nested verification prompt"
        ]
        self.assertEqual(len(prompts), 1)
        self.assertFalse(any(step["operation"] == "conditional_branch" for step in ir["steps"]))

    def test_xscr_preserves_external_runtime_commands_and_unsupported_conditional(self):
        extra = """
                      <Object Type="Tecan.Core.Scripting.ExecuteApplicationStatement">
                        <ExecuteApplicationStatement>
                          <Application>"C:\\Tools\\Client.exe"</Application>
                          <Arguments>"-i"</Arguments>
                          <Wait>True</Wait>
                          <StoreReturn>False</StoreReturn>
                          <Variable />
                          <LineNumber>3</LineNumber>
                        </ExecuteApplicationStatement>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.ExecuteVbScriptStatement">
                        <ExecuteVbScriptStatement>
                          <VbScript>"C:\\Tools\\Read.vb"</VbScript>
                          <LineNumber>4</LineNumber>
                        </ExecuteVbScriptStatement>
                      </Object>
                      <Object Type="Tecan.Core.Scripting.ConditionalGroup">
                        <ConditionalGroup>
                          <Objects>
                            <Object Type="Tecan.Core.Scripting.RaiseErrorStatement">
                              <RaiseErrorStatement><ErrorMessage>Stop</ErrorMessage></RaiseErrorStatement>
                            </Object>
                          </Objects>
                          <Condition>1&gt;0</Condition>
                          <Name>check for error</Name>
                        </ConditionalGroup>
                      </Object>
"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "runtime_commands.xscr"
            xscr.write_text(
                XSCR.replace("</Statements>", extra + "                    </Statements>", 1),
                encoding="utf-8",
            )
            ir = protocol_ir_from_xscr(xscr)

        operations = [step["operation"] for step in ir["steps"]]
        self.assertIn("execute_application", operations)
        self.assertIn("execute_vb_script", operations)
        self.assertIn("conditional_branch", operations)

    def test_worktable_prompt_target_tracks_resolved_binding(self):
        ir = {
            "steps": [
                {
                    "operation": "prompt_user",
                    "target_labware": "CapHolder",
                    "parameters": {
                        "worktable_labware": {"labware": "CapHolder[001]"},
                    },
                }
            ]
        }

        sync_verification_prompt_target_labware(ir)

        self.assertEqual(ir["steps"][0]["target_labware"], "CapHolder[001]")

    def test_render_python_draft_blanks_variable_indexed_worktable_labware(self):
        ir = {
            "worktable": {"name": "WT"},
            "steps": [
                {
                    "operation": "prompt_user",
                    "command_id": "RUPWorktableStatement",
                    "parameters": {
                        "prompt": "Place the tube.",
                        "image_path": "media/tube.png",
                        "worktable_labware": {
                            "labware": "SampleSourceTube[NumSourceTubes_Main]",
                            "labware_type": "Tube",
                            "grid": 31,
                            "site": 1,
                        },
                    },
                },
                {
                    "operation": "prompt_user",
                    "command_id": "RUPWorktableStatement",
                    "parameters": {
                        "prompt": "Place the cap holder.",
                        "image_path": "media/caps.png",
                        "worktable_labware": {
                            "labware": "CapHolder[001]",
                            "labware_type": "MPBoxFlat",
                            "grid": 9,
                            "site": 2,
                        },
                    },
                },
            ],
        }

        draft = render_python_draft(ir)

        self.assertNotIn("SampleSourceTube[NumSourceTubes_Main]", draft)
        self.assertNotIn("selected_labware_type='Tube'", draft)
        self.assertIn("grid=31", draft)
        self.assertIn("site=1", draft)
        self.assertIn("selected_labware_name='CapHolder[001]'", draft)
        self.assertIn("selected_labware_type='MPBoxFlat'", draft)

    def test_zeia_exports_ir_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "project.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("Scripts/simple_transfer.xscr", XSCR)

            bundle = protocol_ir_bundle_from_zeia(archive)

            self.assertEqual(bundle["ir_version"], CANONICAL_IR_BUNDLE_VERSION)
            self.assertEqual(bundle["protocol_count"], 1)
            self.assertEqual(bundle["protocols"][0]["source"]["archive_entry"], "Scripts/simple_transfer.xscr")

    def test_xscr_reads_direct_root_script_group_commands_in_order(self):
        xscr_text = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
  <Object Type="Tecan.Core.Scripting.LoopGroup">
    <LoopGroup><Name>Root loop</Name><LoopVariable>platecount</LoopVariable><NumberOfLoops>2</NumberOfLoops><Objects /></LoopGroup>
  </Object>
  <Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement">
    <RUPStandardStatement><StandardProperties><StandardStatementDataClass><MessageText>All done.</MessageText></StandardStatementDataClass></StandardProperties><RUPScreenTitle>Done</RUPScreenTitle><RUPTimeOut>1</RUPTimeOut></RUPStandardStatement>
  </Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "root_commands.xscr"
            path.write_text(xscr_text, encoding="utf-8")
            ir = protocol_ir_from_xscr(path)

        self.assertEqual(
            [step["operation"] for step in ir["steps"]],
            ["loop_over_wells", "prompt_user"],
        )
        self.assertEqual(ir["steps"][0]["parameters"]["number_of_loops_expression"]["value"], 2)
        self.assertEqual(ir["steps"][1]["parameters"]["prompt"], "All done.")


if __name__ == "__main__":
    unittest.main()
