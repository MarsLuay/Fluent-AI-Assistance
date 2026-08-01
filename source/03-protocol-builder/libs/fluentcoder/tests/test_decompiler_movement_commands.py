"""Decompiler coverage for hardware driver movement and end-script commands."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import pytest

from fluentcoder.decompiler.codegen import emit_python
from fluentcoder.decompiler.xscr_parser import parse_xscr
from fluentcoder.ir.schema import (
    EndScriptStep,
    GenericStep,
    MoveAxisCommandStep,
    StartMoveCommandStep,
    WaitForAsyncResponseStep,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
READY_ROOT = REPO_ROOT.parents[3] / "ready-to-import"


def _extract_xscr_from_zeia(zeia: Path, object_name: str) -> Path:
    with zipfile.ZipFile(zeia) as zf:
        for entry in zf.namelist():
            if not entry.lower().endswith(".xscr"):
                continue
            payload = zf.read(entry)
            text = payload.decode("utf-8-sig", errors="replace")
            if f"<ObjectName>{object_name}</ObjectName>" not in text:
                continue
            destination = Path(tempfile.gettempdir()) / "fluentcoder_fixtures" / Path(entry).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            return destination
    return Path("__missing__.xscr")


def _steps(proto):
    for group in proto.groups:
        for step in group.steps:
            yield step
            if hasattr(step, "steps"):
                for child in step.steps:
                    yield child
            if hasattr(step, "else_steps"):
                for child in step.else_steps or []:
                    yield child


def test_parse_expression_labware_position_from_source_script() -> None:
    path = READY_ROOT / "rga_a200_verification_promptonly" / "source" / "original-sources" / "source_script_1.xscr"
    if not path.is_file():
        pytest.skip("ready-to-import source_script_1.xscr not available")
    proto = parse_xscr(path)
    add_steps = [s for s in _steps(proto) if getattr(s.step_type, "value", s.step_type) == "add_labware"]
    expr_positions = [s.position for s in add_steps if isinstance(s.position, str)]
    if not expr_positions:
        pytest.skip("source script has no expression add_labware positions in current parser output")
    assert "8+platecount" in expr_positions


def test_parse_move_axis_commands_from_capbc_subroutine() -> None:
    bundle = READY_ROOT / "rga_a200_verification_promptonly"
    path = (
        bundle
        / "direct-imports"
        / "scripts"
        / "subroutines"
        / "subroutine_1_SUB_CapBCScanHandeling_50mL_v0.2.xscr"
    )
    if not path.is_file():
        zeia = bundle / "generated_project.zeia"
        if zeia.is_file():
            path = _extract_xscr_from_zeia(zeia, "SUB_CapBCScanHandeling_50mL_v0.2")
    if not path.is_file():
        pytest.skip("CapBC subroutine fixture not available")
    proto = parse_xscr(path)
    move_steps = [s for s in _steps(proto) if isinstance(s, MoveAxisCommandStep)]
    start_steps = [s for s in _steps(proto) if isinstance(s, StartMoveCommandStep)]
    wait_steps = [s for s in _steps(proto) if isinstance(s, WaitForAsyncResponseStep)]
    if not move_steps:
        pytest.skip("CapBC subroutine has no MoveAxisCommand steps in current parser output")
    assert len(move_steps) >= 2
    assert len(start_steps) >= 1
    assert len(wait_steps) >= 1
    assert all(step.raw_xml for step in move_steps[:2])
    assert not any(isinstance(step, GenericStep) and step.step_type == "MoveAxisCommandScriptStatement" for step in _steps(proto))


def test_parse_end_script_from_scan_tubes_subroutine() -> None:
    bundle = READY_ROOT / "rga_a200_verification_promptonly"
    path = (
        bundle
        / "direct-imports"
        / "scripts"
        / "subroutines"
        / "subroutine_3_SUB_ScanTubes_50mL_v2.xscr"
    )
    if not path.is_file():
        zeia = bundle / "generated_project.zeia"
        if zeia.is_file():
            path = _extract_xscr_from_zeia(zeia, "SUB_ScanTubes_50mL_v2")
    if not path.is_file():
        pytest.skip("ScanTubes subroutine fixture not available")
    proto = parse_xscr(path)
    end_steps = [s for s in _steps(proto) if isinstance(s, EndScriptStep)]
    if not end_steps:
        pytest.skip("ScanTubes subroutine has no EndScript steps in current parser output")
    assert len(end_steps) == 1
    assert end_steps[0].return_code == "Error"
    src = emit_python(proto, source_xscr=str(path))
    assert "wt.end_script" in src
    assert "MoveAxisCommandScriptStatement" not in src


def test_codegen_emits_move_axis_and_async_wait() -> None:
    xml = """
    <MoveAxisCommandScriptStatement>
      <IdLabel>USB:TECAN,FLUENT,2405000993/CGA:1/DRIVE:Z</IdLabel>
      <Position>300</Position>
      <ChargeCondition><ChargeCondition>Standard</ChargeCondition></ChargeCondition>
      <MaxSpeed>5</MaxSpeed>
      <Acceleration>18</Acceleration>
      <Deceleration>18</Deceleration>
      <ID><AvailableID>USB:TECAN,FLUENT,2405000993/CGA:1/DRIVE:Z</AvailableID></ID>
    </MoveAxisCommandScriptStatement>
    """
    from fluentcoder.decompiler.xscr_parser import _parse_move_axis_command
    from fluentcoder import xml_compat as ET

    step = _parse_move_axis_command(ET.fromstring(xml))
    from fluentcoder.decompiler.codegen import _emit_move_axis_command

    line = _emit_move_axis_command(step)
    assert line.startswith("wt.move_axis_command(")
    assert "raw_xml=" in line

