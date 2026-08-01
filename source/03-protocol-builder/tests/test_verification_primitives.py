"""Tests for verification-recipe primitives: toggleable categories, RUP selector
form synthesis, and execute_vb_script."""

from __future__ import annotations

from fluent_pipeline import xml_compat as ET

from fluent_pipeline.generation_options import GenerationOptions
from fluent_pipeline.generation_workflow import (
    _annotate_explicit_recipe_prompt_media,
    _source_tubeeye_startup_command_steps,
    build_ir_from_recipe,
)
from fluent_pipeline.protocol_ir import (
    CANONICAL_IR_VERSION,
    force_worktable_prompt_images,
    render_execute_application_xml,
    render_execute_vb_script_xml,
    render_python_draft,
    render_rup_variable_statement_xml,
    route_unbound_worktable_prompts_to_standard,
)


def _recipe() -> dict:
    return {
        "worktable": "Demo_WT",
        "variables": [{"name": "LASTBARCODE", "value": "none"}],
        "labware": [
            {"label": "AdapterA200", "catalog": "Adapter A200", "location": "Demo_Nest_Pos", "site": 1}
        ],
        "groups": [
            {
                "name": "Arm verification",
                "toggle_variable": "RunArmCheck",
                "toggle_label": "Run arm check?",
                "steps": [
                    {"prompt": "About to verify the arm.", "plain_prompt": True},
                    {"prompt": "Make sure the fingers are parallel."},
                ],
            },
            {
                "name": "A200 movement",
                "toggle_variable": "RunA200",
                "steps": [
                    {"verified_move": {"labware": "AdapterA200", "to_location": "Demo_Device_Pos", "to_site": 1}},
                    {"prompt": "Confirm adapter seated."},
                ],
            },
            {
                "name": "Barcode",
                "toggle_variable": "RunBarcode",
                "steps": [
                    {"execute_vb_script": {"vb_script": r"C:\TubeEye\bin\ParseBarcode.vb"}},
                    {"prompt": "The scanned barcode is shown above."},
                ],
            },
        ],
    }


def test_explicit_recipe_prompt_media_hook_runs_before_ir_export():
    recipe = {
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
                "steps": [
                    {
                        "prompt": "Confirm AdapterA200 is on the deck.",
                        "deck_presence_check": True,
                        "worktable_binding": {"from_labware": "AdapterA200"},
                    }
                ],
            },
            {
                "name": "Arm verification",
                "steps": [{"prompt": "Confirm the gripper fingers are parallel."}],
            },
        ],
    }

    ir = build_ir_from_recipe(
        recipe,
        intent="Build a tiny verification recipe",
        context=None,
        protocol_name="MediaRecipe",
    )
    _annotate_explicit_recipe_prompt_media(
        ir,
        recipe=recipe,
        generation_options=GenerationOptions(verification_prompt_rup="mixed"),
    )
    prompts = [step for step in ir["steps"] if step.get("operation") == "prompt_user"]
    worktable_prompt = next(step for step in prompts if step["parameters"].get("deck_presence_check"))
    standard_prompt = next(
        step for step in prompts if "gripper fingers" in step["parameters"].get("prompt", "")
    )

    assert worktable_prompt["command_id"] == "RUPWorktableStatement"
    worktable_media = worktable_prompt["parameters"]["media_placeholders"]
    assert [item["kind"] for item in worktable_media] == ["image", "video"]
    assert worktable_media[0].get("worktable_display") is True
    assert worktable_media[1].get("worktable_display", False) is False

    assert standard_prompt["command_id"] == "RUPStandardStatement"
    standard_media = standard_prompt["parameters"]["media_placeholders"]
    assert [item["kind"] for item in standard_media] == ["image", "video"]
    assert "worktable_labware" not in standard_prompt["parameters"]


def test_worktable_only_mode_rewrites_preserved_standard_image_prompts():
    ir = {
        "steps": [
            {
                "id": "step_001",
                "index": 1,
                "operation": "prompt_user",
                "command_id": "RUPStandardStatement",
                "parameters": {
                    "prompt": "Confirm the rack is stable.",
                    "image_path": r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Script_media\step001.png",
                    "rup_kind": "standard",
                    "sound_file": r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Script_media\step001.mp3",
                },
            }
        ]
    }

    force_worktable_prompt_images(ir)
    assert len(ir["steps"]) == 2
    prelude = ir["steps"][0]
    step = ir["steps"][1]

    assert [item["index"] for item in ir["steps"]] == [1, 2]
    assert prelude["command_id"] == "UserPromptStatement"
    assert prelude["name"] == "Play Prompt Sound"
    assert prelude["parameters"]["timeout"] == 1
    assert prelude["parameters"]["auto_close"] is True
    assert prelude["parameters"]["sound_file"].endswith("step001.mp3")
    assert step["command_id"] == "RUPWorktableStatement"
    assert step["parameters"]["rup_kind"] == "worktable"
    assert step["parameters"]["worktable_audio_prelude_inserted"] is True
    assert "sound_file" not in step["parameters"]
    rendered = render_python_draft({"steps": ir["steps"]})
    assert "wt.user_prompt(" in rendered
    assert "auto_close=True" in rendered
    assert "sound_path=" in rendered
    assert "wt.user_prompt_worktable(" in rendered
    assert "rup_kind='standard'" not in rendered


def test_unbound_worktable_prompt_routes_to_standard_on_modern_fluentcontrol():
    ir = {
        "steps": [
            {
                "id": "unbound",
                "operation": "prompt_user",
                "command_id": "RUPWorktableStatement",
                "name": "RUP Worktable",
                "parameters": {
                    "rup_kind": "worktable",
                    "deck_presence_check": True,
                    "worktable_labware": {"grid": 31, "site": 1},
                    "media_placeholders": [
                        {"kind": "image", "slot": "unbound_image", "worktable_display": True}
                    ],
                },
            },
            {
                "id": "deck-bound",
                "operation": "prompt_user",
                "command_id": "RUPWorktableStatement",
                "parameters": {
                    "rup_kind": "worktable",
                    "worktable_labware": {
                        "labware": "AdapterA200",
                        "labware_type": "Microplate",
                        "grid": 9,
                        "site": 2,
                    },
                },
            },
        ]
    }

    route_unbound_worktable_prompts_to_standard(ir, allow_standard=True)

    unbound, bound = ir["steps"]
    assert unbound["command_id"] == "RUPStandardStatement"
    assert unbound["parameters"]["rup_kind"] == "standard"
    assert "worktable_display" not in unbound["parameters"]["media_placeholders"][0]
    assert "deck_presence_check" not in unbound["parameters"]
    assert "worktable_labware" not in unbound["parameters"]
    assert bound["command_id"] == "RUPWorktableStatement"


def test_setup_recipe_group_variants_collapse_to_one_setup_group():
    recipe = {
        "labware": [
            {"label": "AdapterA200", "catalog": "Adapter A200", "location": "Demo_Nest_Pos", "site": 1}
        ],
        "groups": [
            {"name": "Setup", "steps": [{"prompt": "Confirm the deck is ready."}]},
            {"name": "Barcode", "steps": [{"prompt": "Scan the tube barcode."}]},
            {"name": "Operator setup", "steps": [{"prompt": "Confirm A200 is powered on."}]},
            {"name": "Deck setup", "steps": [{"prompt": "Confirm AdapterA200 is on the deck."}]},
        ],
    }

    ir = build_ir_from_recipe(
        recipe,
        intent="Build a tiny setup merge verification recipe",
        context=None,
        protocol_name="SetupMergeRecipe",
    )

    groups = [step.get("group") for step in ir["steps"]]
    assert "Operator setup" not in groups
    assert "Deck setup" not in groups
    setup_steps = [step for step in ir["steps"] if step.get("group") == "Setup"]
    assert len(setup_steps) == 4

    rendered = render_python_draft(ir)
    assert rendered.count("wt.group('Setup')") == 1
    assert "wt.group('Operator setup')" not in rendered
    assert "wt.group('Deck setup')" not in rendered
    assert "Confirm the deck is ready." in rendered
    assert "Confirm A200 is powered on." in rendered
    assert "Confirm AdapterA200 is on the deck." in rendered
    assert rendered.index("wt.group('Setup')") < rendered.index("wt.group('Barcode')")


def test_toggleable_categories_emit_conditions_and_selector_form():
    ir = build_ir_from_recipe(_recipe(), intent="t", context=None, protocol_name="T")
    conditions = ir["category_conditions"]
    assert set(conditions) == {"Arm verification", "A200 movement", "Barcode"}
    assert conditions["Arm verification"]["variable"] == "RunArmCheck"

    # A single RUP selector form is injected before the categories.
    selectors = [s for s in ir["steps"] if s.get("operation") == "runtime_variable_prompt"]
    assert len(selectors) == 1
    fields = selectors[0]["parameters"]["variables"]
    field_names = {f["name"] for f in fields}
    assert field_names == {"RunArmCheck", "RunA200", "RunBarcode"}
    assert {f["name"]: f["value"] for f in fields} == {
        "RunArmCheck": "yes",
        "RunA200": "yes",
        "RunBarcode": "yes",
    }

    selector_index = ir["steps"].index(selectors[0])
    assert ir["steps"][selector_index - 1]["operation"] == "prompt_user"
    assert "Script started" in ir["steps"][selector_index - 1]["parameters"]["prompt"]
    assert selectors[0]["parameters"]["line_number"] == selectors[0]["index"]

    # Toggle variables are auto-declared so the conditions resolve at runtime.
    declared = {v["name"] for v in ir["variables"] if isinstance(v, dict)}
    assert {"RunArmCheck", "RunA200", "RunBarcode"} <= declared


def test_verified_move_not_followed_by_park_macro():
    ir = build_ir_from_recipe(_recipe(), intent="t", context=None, protocol_name="T")
    ops = [s.get("operation") for s in ir["steps"]]
    move_idx = ops.index("move_plate")
    assert ops[move_idx + 1] != "application_driver_macro"


def test_render_python_draft_wraps_categories_in_conditional():
    ir = build_ir_from_recipe(_recipe(), intent="t", context=None, protocol_name="T")
    draft = render_python_draft(ir)
    assert "with wt.conditional(left='RunArmCheck', op='==', right='yes'" in draft
    assert "wt.declare_variable('RunArmCheck', 'yes')" in draft
    assert "ExecuteVbScriptStatement" in draft
    assert "RUPVariableStatement" in draft
    assert draft.count("wt.application_driver_macro('RGA1_TransferLabware'") == 1


def test_render_python_draft_declares_variables_used_by_mappings_and_set_variable():
    ir = {
        "ir_version": CANONICAL_IR_VERSION,
        "protocol": {"name": "T", "comment": "t"},
        "worktable": {"name": "WT"},
        "variables": [
            {"name": "GripperClose", "value": 11},
            {"name": "RunArmCheck", "value": "yes"},
        ],
        "category_conditions": {"Arm verification": {"variable": "RunArmCheck", "op": "==", "value": "yes"}},
        "steps": [
            {
                "id": "step_001",
                "index": 1,
                "group": "Arm verification",
                "operation": "set_variable",
                "parameters": {"variable": "GripperClose", "value": 11},
            },
            {
                "id": "step_002",
                "index": 2,
                "group": "Arm verification",
                "operation": "call_subroutine",
                "parameters": {
                    "subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                    "variable_mappings_start": [
                        {"target": "GripTubeClose", "source": "GripperClose"},
                        {
                            "target": "TubePosition",
                            "source": "Demo_Tube_Pos_1",
                            "source_expression": {
                                "kind": "string_literal",
                                "value": "Demo_Tube_Pos_1",
                            },
                        },
                    ],
                },
            },
        ],
    }
    draft = render_python_draft(ir)
    assert (
        "from fluentcoder import Reagent, VariableMapping, Worktable, parse_expression"
        in draft
    )
    assert "wt.declare_variable('GripperClose', 11)" in draft
    assert "wt.declare_variable('RunArmCheck', 'yes')" in draft
    assert "wt.set_variable('GripperClose', 11)" in draft
    assert "wt.call_subroutine('Demo\\\\SUB_CapBCScanHandeling_50mL_v0.2'" in draft
    assert "VariableMapping(target='GripTubeClose', source=parse_expression('GripperClose'))" in draft
    assert (
        "VariableMapping(target='TubePosition', "
        "source=parse_expression('\"Demo_Tube_Pos_1\"'))"
    ) in draft


def test_render_python_draft_preserves_run_scope_for_subroutine_shared_variables():
    ir = {
        "ir_version": CANONICAL_IR_VERSION,
        "protocol": {"name": "T", "comment": "t"},
        "worktable": {"name": "WT"},
        "variables": [
            {"name": "LASTBARCODE", "value": "NOBARCODE", "scope": "Run", "type": "String"},
        ],
        "steps": [
            {
                "id": "step_001",
                "index": 1,
                "group": "Barcode",
                "operation": "call_subroutine",
                "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
            },
        ],
    }

    draft = render_python_draft(ir)

    assert "wt.declare_variable('LASTBARCODE', 'NOBARCODE', scope='Run', type_name='String')" in draft


def test_execute_vb_script_xml_is_well_formed_and_quotes_path():
    xml = render_execute_vb_script_xml({"vb_script": r"C:\TubeEye\bin\ParseBarcode.vb"})
    root = ET.fromstring(xml)
    vb = root.find(".//VbScript")
    assert vb is not None
    assert vb.text == '"C:\\TubeEye\\bin\\ParseBarcode.vb"'
    # An already-quoted path is not double-quoted.
    xml2 = render_execute_vb_script_xml({"vb_script": '"C:\\x.vb"'})
    assert ET.fromstring(xml2).find(".//VbScript").text == '"C:\\x.vb"'


def test_execute_application_xml_is_well_formed_and_preserves_arguments():
    xml = render_execute_application_xml(
        {
            "path": r"C:\TubeEye\bin\TEyeClient.exe",
            "arguments": '"-p teye_50mL.prf" ',
            "wait": True,
            "store_return": False,
        }
    )
    root = ET.fromstring(xml)
    assert root.find(".//Application").text == '"C:\\TubeEye\\bin\\TEyeClient.exe"'
    assert root.find(".//Arguments").text == '"-p teye_50mL.prf" '
    assert root.find(".//Wait").text == "True"
    assert root.find(".//StoreReturn").text == "False"


def test_tubeeye_startup_detector_extracts_nested_launch_block(tmp_path):
    xscr = tmp_path / "nested_tubeeye.xscr"
    xscr.write_text(
        r"""<?xml version="1.0"?>
<Root>
  <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
    <ScriptGroupDataV1>
      <Name>First tube TubeEye launch and barcode scan</Name>
      <Statements>
        <Object Type="Tecan.Core.Scripting.ConditionalGroup">
          <ConditionalGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.CommentStatement">
                <CommentStatement><Comment>Initialize TubeEye software</Comment></CommentStatement>
              </Object>
              <Object Type="Tecan.Core.Scripting.ExecuteApplicationStatement">
                <ExecuteApplicationStatement>
                  <Application>"C:\TubeEye\bin\TEyeClient.exe"</Application>
                  <Arguments>"-i" </Arguments>
                  <Wait>True</Wait>
                  <StoreReturn>True</StoreReturn>
                  <Variable>res</Variable>
                </ExecuteApplicationStatement>
              </Object>
              <Object Type="Tecan.Core.Scripting.ExecuteApplicationStatement">
                <ExecuteApplicationStatement>
                  <Application>"C:\TubeEye\bin\TEyeClient.exe"</Application>
                  <Arguments>"-p teye_50mL.prf" </Arguments>
                  <Wait>True</Wait>
                  <StoreReturn>False</StoreReturn>
                  <Variable />
                </ExecuteApplicationStatement>
              </Object>
              <Object Type="Tecan.Core.Scripting.ConditionalGroup">
                <ConditionalGroup>
                  <Objects>
                    <Object Type="Tecan.Core.Scripting.RaiseErrorStatement">
                      <RaiseErrorStatement><ErrorMessage>Could not start TubeEye.</ErrorMessage></RaiseErrorStatement>
                    </Object>
                  </Objects>
                  <Condition>res&gt;1000 AND simulation=0</Condition>
                  <Name>TubeEye startup failure check</Name>
                </ConditionalGroup>
              </Object>
              <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
                <SubRoutineStatement><SubRoutine>"Demo\SUB_ScanTubes_50mL_v2"</SubRoutine></SubRoutineStatement>
              </Object>
              <Object Type="Tecan.Core.Scripting.ExecuteVbScriptStatement">
                <ExecuteVbScriptStatement><VbScript>"C:\TubeEye\bin\GetLastBarcode.vb"</VbScript></ExecuteVbScriptStatement>
              </Object>
            </Objects>
            <Condition>RunFirstTubeScan="yes"</Condition>
            <Name>Run first tube scan?</Name>
          </ConditionalGroup>
        </Object>
      </Statements>
    </ScriptGroupDataV1>
  </Object>
</Root>
""",
        encoding="utf-8",
    )

    detected = _source_tubeeye_startup_command_steps(xscr)

    assert detected is not None
    assert detected["source_group"] == "First tube TubeEye launch and barcode scan"
    assert [step["operation"] for step in detected["steps"]] == [
        "comment",
        "execute_application",
        "execute_application",
        "conditional_branch",
    ]
    assert "SUB_ScanTubes" not in "\n".join(str(step) for step in detected["steps"])
    assert "GetLastBarcode.vb" not in "\n".join(str(step) for step in detected["steps"])


def test_rup_variable_statement_xml_round_trips_fields():
    xml = render_rup_variable_statement_xml(
        {
            "screen_title": "Select tests",
            "variables": [
                {
                    "name": "RunArmCheck",
                    "display_text": "Run arm check?",
                    "allowed_values": ['"yes"', '"no"'],
                }
            ],
        }
    )
    root = ET.fromstring(xml)
    item = root.find(".//RupVariableItem")
    assert item.find("VariableName").text == "RunArmCheck"
    assert item.find("DisplayType").text == "Combobox"
    assert item.find("AllowedValues").text == "yes;no"


def test_rup_variable_statement_zero_timeout_is_clamped_for_fluent_editor():
    xml = render_rup_variable_statement_xml(
        {
            "screen_title": "Select tests",
            "timeout": 0,
            "variables": [{"name": "RunArmCheck", "allowed_values": ['"yes"', '"no"']}],
        }
    )
    root = ET.fromstring(xml)
    assert root.find(".//RUPTimeOut").text == "1"


def test_external_file_audit_detects_vbscript_paths():
    # ExecuteVbScript paths live in <VbScript> and must be audited/staged like
    # <File>/<Application>/<FileRef> targets.
    from fluent_pipeline.external_file_dependencies import _extract_paths_from_xscr_text

    xscr = '<VbScript>"C:\\TubeEye\\bin\\GetLastBarcode.vb"</VbScript>'
    paths = _extract_paths_from_xscr_text(xscr)
    assert "C:\\TubeEye\\bin\\GetLastBarcode.vb" in paths
