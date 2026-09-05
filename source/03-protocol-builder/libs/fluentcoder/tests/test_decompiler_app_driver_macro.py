"""Decompiler/codegen/simulator coverage for ApplicationDriverMacro steps."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.decompiler import emit_python, parse_xscr
from fluentcoder.catalog.catalog import index_exists
from fluentcoder.ir.schema import ApplicationDriverMacroStep, GenericStep, StepType
from fluentcoder.simulator import EffectKind, Simulator
from fluentcoder.worktable import Worktable
from tests._module_loader import load_module


EXECUTE_VECTOR_SETTINGS = (
    "&amp;lt;ExecuteSingleVectorCommandParameters "
    'xmlns:i="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Drivers.RobotDriverBase.ExecuteSingleVector"'
    "&amp;gt;&amp;lt;ExecutePostAction&amp;gt;false&amp;lt;/ExecutePostAction&amp;gt;"
    "&amp;lt;ExecutePreAction&amp;gt;false&amp;lt;/ExecutePreAction&amp;gt;"
    "&amp;lt;GripForce&amp;gt;GripForce&amp;lt;/GripForce&amp;gt;"
    "&amp;lt;Location&amp;gt;Microhawk&amp;lt;/Location&amp;gt;"
    "&amp;lt;MoveAction&amp;gt;FromSafePositionToEndPosition&amp;lt;/MoveAction&amp;gt;"
    "&amp;lt;MoveBackToSource&amp;gt;false&amp;lt;/MoveBackToSource&amp;gt;"
    "&amp;lt;PostActionGripAction&amp;gt;Open&amp;lt;/PostActionGripAction&amp;gt;"
    "&amp;lt;PreActionGripAction&amp;gt;Open&amp;lt;/PreActionGripAction&amp;gt;"
    "&amp;lt;Site&amp;gt;1&amp;lt;/Site&amp;gt;"
    "&amp;lt;Speed&amp;gt;Speed&amp;lt;/Speed&amp;gt;"
    "&amp;lt;VectorName&amp;gt;Narrow&amp;lt;/VectorName&amp;gt;"
    "&amp;lt;ZOffset&amp;gt;0&amp;lt;/ZOffset&amp;gt;"
    "&amp;lt;/ExecuteSingleVectorCommandParameters&amp;gt;"
)

TRANSFER_SETTINGS = (
    "&amp;lt;TransferLabwareCommandParameters "
    'xmlns:i="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Drivers.RobotDriverBase"'
    "&amp;gt;&amp;lt;FixedSite&amp;gt;true&amp;lt;/FixedSite&amp;gt;"
    "&amp;lt;Labware&amp;gt;AdapterA200&amp;lt;/Labware&amp;gt;"
    "&amp;lt;Location&amp;gt;LocationNameA200&amp;lt;/Location&amp;gt;"
    "&amp;lt;MoveToBase&amp;gt;false&amp;lt;/MoveToBase&amp;gt;"
    "&amp;lt;OnTheFlyTool&amp;gt;&amp;lt;/OnTheFlyTool&amp;gt;"
    "&amp;lt;Site&amp;gt;LocationPosA200&amp;lt;/Site&amp;gt;"
    "&amp;lt;UseOnTheFlyTool&amp;gt;false&amp;lt;/UseOnTheFlyTool&amp;gt;"
    "&amp;lt;/TransferLabwareCommandParameters&amp;gt;"
)


def _minimal_xscr(*objects: str) -> str:
    body = "\n".join(objects)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>AppDriverMacro Test</ObjectName>
    <Comment />
    <Reference>
      <Guid>11111111-1234-aaaa-ffff-000000000222</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <ScriptGroup>
      <Objects>
{body}
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
"""


EXECUTE_VECTOR_OBJECT = f"""        <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
          <ApplicationDriverMacro Version="1" Name="RGA1_ExecuteSingleVector" ModuleName="RGA 1" ExecutionTime="PT2S" IsBreakpoint="false" IsDisabledForExecution="false" LineNumber="2">
            <ExecutionSettings>{EXECUTE_VECTOR_SETTINGS}</ExecutionSettings>
          </ApplicationDriverMacro>
        </Object>"""

TRANSFER_OBJECT = f"""        <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
          <ApplicationDriverMacro Version="1" Name="RGA1_TransferLabware" ModuleName="RGA 1" ExecutionTime="PT2S" IsBreakpoint="false" IsDisabledForExecution="false" LineNumber="3">
            <ExecutionSettings>{TRANSFER_SETTINGS}</ExecutionSettings>
          </ApplicationDriverMacro>
        </Object>"""


def test_parse_application_driver_macro_not_generic(tmp_path: Path) -> None:
    src = tmp_path / "macros.xscr"
    src.write_text(_minimal_xscr(EXECUTE_VECTOR_OBJECT, TRANSFER_OBJECT), encoding="utf-8")

    steps = parse_xscr(src).groups[0].steps
    assert len(steps) == 2
    assert all(isinstance(step, ApplicationDriverMacroStep) for step in steps)
    assert not any(isinstance(step, GenericStep) for step in steps)

    vector, transfer = steps
    assert vector.macro_name == "RGA1_ExecuteSingleVector"
    assert vector.module_name == "RGA 1"
    assert vector.parameters["Location"] == "Microhawk"
    assert vector.parameters["VectorName"] == "Narrow"
    assert vector.raw_xml
    assert "ApplicationDriverMacro" in vector.raw_xml

    assert transfer.macro_name == "RGA1_TransferLabware"
    assert transfer.parameters["Labware"] == "AdapterA200"
    assert transfer.parameters["Location"] == "LocationNameA200"


def test_codegen_emits_application_driver_macro(tmp_path: Path) -> None:
    src = tmp_path / "macros.xscr"
    src.write_text(_minimal_xscr(EXECUTE_VECTOR_OBJECT), encoding="utf-8")
    proto = parse_xscr(src)
    py_src = emit_python(proto, source_xscr=str(src))

    assert "wt.application_driver_macro(" in py_src
    assert "RGA1_ExecuteSingleVector" in py_src
    assert "execution_settings=" in py_src
    assert "wt.raw_xml_step(" not in py_src


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_compile_roundtrip_preserves_macro(tmp_path: Path) -> None:
    src = tmp_path / "macros.xscr"
    src.write_text(_minimal_xscr(EXECUTE_VECTOR_OBJECT), encoding="utf-8")
    proto = parse_xscr(src)
    py_path = tmp_path / "decompiled.py"
    py_path.write_text(emit_python(proto, source_xscr=str(src)), encoding="utf-8")

    module = load_module(py_path, alias="app_driver_macro_decompiled")
    wt = module.build_worktable()
    out_xscr = tmp_path / "recompiled.xscr"
    wt.compile(out_xscr)

    xml = out_xscr.read_text(encoding="utf-8")
    assert "RGA1_ExecuteSingleVector" in xml
    assert "Microhawk" in xml
    assert "ExecuteSingleVectorCommandParameters" in xml


def test_renderer_roundtrip_preserves_execution_settings() -> None:
    from fluentcoder import xml_compat as ET

    from fluentcoder.compiler.renderer import Renderer
    from fluentcoder.decompiler.xscr_parser import _parse_application_driver_macro

    obj = ET.fromstring(EXECUTE_VECTOR_OBJECT.strip().replace("        ", "", 1))
    step = _parse_application_driver_macro(obj)
    step.raw_xml = None
    renderer = Renderer()
    xml = renderer._render_application_driver_macro_step(
        step,
        params={
            "LineNumber": "2",
            "IsBreakpoint": "false",
            "IsDisabledForExecution": "false",
        },
    )
    assert "RGA1_ExecuteSingleVector" in xml
    assert "Microhawk" in xml
    assert "ExecuteSingleVectorCommandParameters" in xml
    assert "&amp;lt;ExecuteSingleVectorCommandParameters" in xml


def test_renderer_reparses_transfer_macro_parameters_after_roundtrip() -> None:
    from fluentcoder.compiler.renderer import Renderer
    from fluentcoder.decompiler.xscr_parser import _parse_application_driver_macro
    from fluentcoder import xml_compat as ET

    source_object = ET.fromstring(TRANSFER_OBJECT.strip().replace("        ", "", 1))
    step = _parse_application_driver_macro(source_object)
    step.raw_xml = None
    rendered = Renderer()._render_application_driver_macro_step(
        step,
        params={
            "LineNumber": "3",
            "IsBreakpoint": "false",
            "IsDisabledForExecution": "false",
        },
    )
    reparsed_object = ET.fromstring(rendered.replace("                        ", ""))
    reparsed = _parse_application_driver_macro(reparsed_object)

    assert reparsed.parameters["Labware"] == "AdapterA200"
    assert reparsed.parameters["Location"] == "LocationNameA200"


def test_renderer_raw_xml_passthrough() -> None:
    from fluentcoder.compiler.renderer import Renderer
    from fluentcoder.ir.schema import Group, Protocol
    from fluentcoder import xml_compat as ET
    from fluentcoder.decompiler.xscr_parser import _parse_application_driver_macro

    obj = ET.fromstring(EXECUTE_VECTOR_OBJECT.strip().replace("        ", "", 1))
    step = _parse_application_driver_macro(obj)
    renderer = Renderer()
    rendered = renderer._render_step(step, Protocol(name="t"), Group(name="Steps"))
    assert "ApplicationDriverMacro" in rendered
    assert step.raw_xml.strip() in rendered.replace("                        ", "")


def test_simulator_treats_macros_as_non_motion() -> None:
    """automated_motion_review: parsed ApplicationDriverMacro IR steps are non-motion by default.

    The offline simulator must not replay driver macros as deck/labware motion.
    Typed ``ApplicationDriverMacroStep`` values are ``VALIDATION_ONLY``, not
    ``OPAQUE``/``LABWARE_MOVEMENT``, so verification scripts can simulate with
    ``fail_on_opaque=True`` without treating preserved macros as automated motion.
    """
    wt = Worktable(name="macro sim")
    wt.group("Steps")
    wt.application_driver_macro(
        "RGA1_ExecuteSingleVector",
        execution_settings=EXECUTE_VECTOR_SETTINGS,
        parameters={"Location": "Microhawk", "VectorName": "Narrow"},
    )
    wt.application_driver_macro(
        "RGA1_TransferLabware",
        execution_settings=TRANSFER_SETTINGS,
        parameters={"Labware": "AdapterA200", "Location": "LocationNameA200"},
    )

    Simulator(wt).run(fail_on_opaque=True)

    report = wt.simulation_report
    assert report is not None
    assert not report.opaque_events
    for coverage in report.steps:
        assert coverage.step_type == "ApplicationDriverMacroStep"
        assert coverage.effect == EffectKind.VALIDATION_ONLY
        assert coverage.command_id == StepType.APPLICATION_DRIVER_MACRO.value


def test_prompt_only_macro_stays_validation_only() -> None:
    wt = Worktable(name="prompt-only macro")
    wt.group("Steps")
    wt.application_driver_macro(
        "RGA1_ExecuteSingleVector",
        execution_settings=EXECUTE_VECTOR_SETTINGS,
        parameters={"prompt_only": True},
    )

    Simulator(wt).run()
    coverage = wt.simulation_report.steps[0]
    assert coverage.effect == EffectKind.VALIDATION_ONLY
    assert "prompt-only" in (coverage.message or "")
