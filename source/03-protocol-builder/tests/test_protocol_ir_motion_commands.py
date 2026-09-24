from __future__ import annotations

import copy
from pathlib import Path

from fluent_pipeline.protocol_ir import protocol_ir_from_python, protocol_ir_from_xscr, render_python_draft
from fluent_pipeline.protocol_ir_schema import validate_protocol_ir_document


MOTION_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Motion sequence</ObjectName>
    <PayloadData>
      <Script>
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.MoveAxisCommandScriptStatement">
                <MoveAxisCommandScriptStatement>
                  <IdLabel>axis-label</IdLabel>
                  <Position>300</Position>
                  <ChargeCondition><ChargeCondition>True</ChargeCondition></ChargeCondition>
                  <MaxSpeed>5</MaxSpeed>
                  <Acceleration>18</Acceleration>
                  <Deceleration>18</Deceleration>
                  <ID><AvailableID>axis-available</AvailableID></ID>
                  <IsBreakpoint>True</IsBreakpoint>
                  <IsDisabledForExecution>False</IsDisabledForExecution>
                  <FutureDriverField>preserve-me</FutureDriverField>
                  <LineNumber>7</LineNumber>
                </MoveAxisCommandScriptStatement>
              </Object>
              <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.StartMoveCommandScriptStatement">
                <StartMoveCommandScriptStatement>
                  <IdLabel>axis-label</IdLabel>
                  <ID><AvailableID>axis-available</AvailableID></ID>
                  <LineNumber>8</LineNumber>
                </StartMoveCommandScriptStatement>
              </Object>
              <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.WaitForAsyncResponseScriptStatement">
                <WaitForAsyncResponseScriptStatement>
                  <LineNumber>9</LineNumber>
                </WaitForAsyncResponseScriptStatement>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""


def test_xscr_motion_sequence_round_trips_through_canonical_ir_and_python(tmp_path: Path) -> None:
    source = tmp_path / "motion.xscr"
    source.write_text(MOTION_XSCR, encoding="utf-8")

    ir = protocol_ir_from_xscr(source)
    assert [step["operation"] for step in ir["steps"]] == [
        "move_axis_command",
        "start_move_command",
        "wait_for_async_response",
    ]

    move, start, wait = ir["steps"]
    move_params = move["parameters"]
    assert move_params["available_id"] == "axis-available"
    assert move_params["id_label"] == "axis-label"
    assert move_params["position_expression"] == {"kind": "number_literal", "value": 300}
    assert move_params["charge_condition_expression"] == {"kind": "boolean_literal", "value": True}
    assert move_params["max_speed"] == "5"
    assert move_params["acceleration"] == "18"
    assert move_params["deceleration"] == "18"
    assert move_params["is_breakpoint"] is True
    assert move_params["is_disabled_for_execution"] is False
    assert move_params["line_number"] == 7
    assert "FutureDriverField" in move_params["raw_xml"]
    assert start["parameters"]["line_number"] == 8
    assert wait["parameters"]["line_number"] == 9

    generated = render_python_draft(ir)
    assert "MoveAxisConfig" in generated
    assert "wt.start_move_command" in generated
    assert "wt.wait_for_async_response" in generated
    assert "FutureDriverField" in generated

    generated_path = tmp_path / "motion.py"
    generated_path.write_text(generated, encoding="utf-8")
    round_tripped = protocol_ir_from_python(generated_path)
    assert [step["operation"] for step in round_tripped["steps"]] == [
        "move_axis_command",
        "start_move_command",
        "wait_for_async_response",
    ]
    assert round_tripped["steps"][0]["parameters"]["position_expression"] == move_params["position_expression"]
    assert round_tripped["steps"][0]["parameters"]["charge_condition_expression"] == move_params["charge_condition_expression"]
    assert round_tripped["steps"][0]["parameters"]["raw_xml"].strip() == move_params["raw_xml"].strip()


def test_fluentcoder_raw_xml_motion_calls_recover_typed_fields(tmp_path: Path) -> None:
    raw_move = """<MoveAxisCommandScriptStatement>
  <IdLabel>axis-label</IdLabel>
  <Position>AxisTarget</Position>
  <ChargeCondition><ChargeCondition>ChargeReady = 1</ChargeCondition></ChargeCondition>
  <ID><AvailableID>axis-available</AvailableID></ID>
  <IsBreakpoint>True</IsBreakpoint>
  <LineNumber>17</LineNumber>
  <FutureDriverField>preserve-me</FutureDriverField>
</MoveAxisCommandScriptStatement>"""
    raw_start = """<StartMoveCommandScriptStatement>
  <IdLabel>axis-label</IdLabel>
  <ID><AvailableID>axis-available</AvailableID></ID>
  <LineNumber>18</LineNumber>
</StartMoveCommandScriptStatement>"""
    raw_wait = """<WaitForAsyncResponseScriptStatement>
  <LineNumber>19</LineNumber>
</WaitForAsyncResponseScriptStatement>"""
    source = tmp_path / "motion.py"
    source.write_text(
        "\n".join(
            [
                "def build_worktable(wt):",
                '    wt.declare_variable("AxisTarget", 0)',
                '    wt.declare_variable("ChargeReady", 0)',
                f"    wt.move_axis_command(MoveAxisConfig(raw_xml={raw_move!r}))",
                f"    wt.start_move_command(raw_xml={raw_start!r})",
                f"    wt.wait_for_async_response(raw_xml={raw_wait!r})",
            ]
        ),
        encoding="utf-8",
    )

    ir = protocol_ir_from_python(source)
    assert [step["operation"] for step in ir["steps"]] == [
        "move_axis_command",
        "start_move_command",
        "wait_for_async_response",
    ]
    move_params = ir["steps"][0]["parameters"]
    assert move_params["available_id"] == "axis-available"
    assert move_params["position_expression"] == {"kind": "variable_reference", "name": "AxisTarget"}
    assert move_params["charge_condition_expression"]["kind"] == "binary_expression"
    assert move_params["is_breakpoint"] is True
    assert move_params["line_number"] == 17
    assert "FutureDriverField" in move_params["raw_xml"]
    assert ir["steps"][1]["parameters"]["available_id"] == "axis-available"
    assert not validate_protocol_ir_document(ir)


def test_motion_operations_reject_missing_required_source_fields(tmp_path: Path) -> None:
    source = tmp_path / "motion.xscr"
    source.write_text(MOTION_XSCR, encoding="utf-8")
    ir = protocol_ir_from_xscr(source)

    missing_position = copy.deepcopy(ir)
    missing_position["steps"][0]["parameters"].pop("position_expression")
    issues = validate_protocol_ir_document(missing_position)
    assert "$.steps[0].parameters.position_expression" in {issue.path for issue in issues}

    missing_identity = copy.deepcopy(ir)
    missing_identity["steps"][1]["parameters"].pop("available_id")
    missing_identity["steps"][1]["parameters"].pop("id_label")
    issues = validate_protocol_ir_document(missing_identity)
    assert "$.steps[1].parameters" in {issue.path for issue in issues}
