"""Chunk 6: Decompiled and production-style workflow tests.

Tests realism beyond clean authored protocols:
- .xscr -> parse/decompile -> Python -> simulate
- Inspect wt.simulation_report for modeled coverage and opaque command IDs
- Minimum 3 samples, at least 1 with LiHa/FCA behavior, at least 1 < 100% coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.catalog.catalog import index_exists  # noqa: E402
from fluentcoder.decompiler import emit_python, parse_xscr  # noqa: E402
from tests._module_loader import load_module  # noqa: E402


def _emit_python_checked(tmp_path: Path, name: str, proto) -> Path:
    """Emit decompiled Python and assert unresolved labware is not silently degraded."""
    py = tmp_path / name
    src = emit_python(proto, source_xscr=str(py.with_suffix(".xscr")))
    assert "ExternalLabware" not in src
    py.write_text(src, encoding="utf-8")
    return py


# ── Sample 1: Simple MCA transfer (authored protocol simulation) ───

@pytest.mark.usefixtures("synthetic_catalog")
def test_sample_simple_transfer_simulation() -> None:
    """Simple transfer example: simulate and check report."""
    from examples.simple_transfer import build_worktable

    wt = build_worktable()
    wt.simulate(strict=True)

    report = wt.simulation_report
    assert report is not None

    # Record metrics
    print(f"\n  [simple_transfer] total={report.total_executed_steps}, "
          f"coverage={report.modeled_coverage:.3f}, "
          f"opaque_ids={report.unsupported_command_ids}")

    assert report.total_executed_steps > 0
    assert report.opaque_noop_steps == 0, "simple_transfer should have no opaque steps"
    # All steps in simple_transfer are modeled (AddLabware, GetAdapter, PickUpTips,
    # Aspirate, Dispense, SetTipsBack) → coverage should be 1.0
    assert report.modeled_coverage == pytest.approx(1.0)


# ── Sample 2: LiHa decompiled protocol with raw XML commands ───────

@pytest.mark.skipif(not index_exists(), reason="decompiled labware needs catalog index for well resolution")
def test_sample_decompiled_liha_workflow(tmp_path: Path) -> None:
    """Decompile a .xscr with LiHa commands → simulate → check coverage."""
    src = tmp_path / "liha_protocol.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>LiHa Protocol</ObjectName>
    <Comment />
    <Reference>
      <Guid>11111111-1234-aaaa-ffff-000000000222</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1><LabwareType>96 Well Flat</LabwareType><LabwareLable>SourcePlate</LabwareLable><Location>Site</Location><Position>1</Position></AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1><LabwareType>96 Well Flat</LabwareType><LabwareLable>DestPlate</LabwareLable><Location>Site</Location><Position>2</Position></AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaPickUpScriptCommandDataV1">
          <LihaPickUpScriptCommandDataV1><LabwareName>Tips50</LabwareName></LihaPickUpScriptCommandDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
          <LihaAspirateScriptCommandDataV5>
            <Data><LihaPipettingWithVolumesScriptCommandDataV7>
              <Volumes><Object Type="System.String"><string>20</string></Object></Volumes>
              <LiquidClassName>Water Free Single</LiquidClassName>
              <LabwareName>SourcePlate</LabwareName>
            </LihaPipettingWithVolumesScriptCommandDataV7></Data>
          </LihaAspirateScriptCommandDataV5>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaDispenseScriptCommandDataV6">
          <LihaDispenseScriptCommandDataV6>
            <Data><LihaPipettingWithVolumesScriptCommandDataV7>
              <Volumes><Object Type="System.String"><string>20</string></Object></Volumes>
              <LiquidClassName>Water Free Single</LiquidClassName>
              <LabwareName>DestPlate</LabwareName>
            </LihaPipettingWithVolumesScriptCommandDataV7></Data>
          </LihaDispenseScriptCommandDataV6>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaDropTipsScriptCommandDataV1">
          <LihaDropTipsScriptCommandDataV1><LabwareName>Trash</LabwareName></LihaDropTipsScriptCommandDataV1>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""", encoding="utf-8")

    # Decompile → Python
    proto = parse_xscr(src)
    py = _emit_python_checked(tmp_path, "decompiled_liha.py", proto)

    # Execute and simulate
    wt = load_module(py).build_worktable()
    wt.simulate(strict=True)

    report = wt.simulation_report
    assert report is not None

    print(f"\n  [decompiled_liha] total={report.total_executed_steps}, "
          f"coverage={report.modeled_coverage:.3f}, "
          f"opaque_ids={report.unsupported_command_ids}")

    # Step composition:
    #   AddLabware(SourcePlate) → fully simulated (LABWARE_MOVEMENT)
    #   AddLabware(DestPlate) → fully simulated (LABWARE_MOVEMENT)
    #   LihaPickUp (raw_xml, adapted as GenericStep→LihaGetTipsStep) → fully simulated
    #   LihaAspirate (structured) → fully simulated (LIQUID_TRANSFER)
    #   LihaDispense (structured) → fully simulated (LIQUID_TRANSFER)
    #   LihaDropTips (structured, decoded as LihaDropTipsStep) → fully simulated
    assert report.total_executed_steps == 6
    assert report.fully_simulated_steps == 6
    assert report.validation_only_steps == 0
    assert report.opaque_noop_steps == 0
    assert report.raw_xml_generic_steps == 1  # only LihaPickUp is raw XML
    assert report.modeled_coverage == pytest.approx(1.0)
    assert report.unsupported_command_ids == {}


# ── Sample 3: Mixed protocol with opaque commands (coverage < 100%) ─

@pytest.mark.skipif(not index_exists(), reason="decompiled labware needs catalog index for well resolution")
def test_sample_mixed_with_opaque_coverage(tmp_path: Path) -> None:
    """Protocol with both modeled and opaque steps → coverage reported honestly."""
    src = tmp_path / "mixed_protocol.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Mixed Protocol</ObjectName>
    <Comment />
    <Reference>
      <Guid>11111111-1234-aaaa-ffff-000000000222</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1><LabwareType>96 Well Flat</LabwareType><LabwareLable>Plate1</LabwareLable><Location>Site</Location><Position>1</Position></AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.WaitStatement">
          <WaitStatement><Duration>5</Duration></WaitStatement>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaPickUpScriptCommandDataV1">
          <LihaPickUpScriptCommandDataV1><LabwareName>Tips</LabwareName></LihaPickUpScriptCommandDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
          <LihaAspirateScriptCommandDataV5>
            <Data><LihaPipettingWithVolumesScriptCommandDataV7>
              <Volumes><Object Type="System.String"><string>10</string></Object></Volumes>
              <LiquidClassName>Water Free Single</LiquidClassName>
              <LabwareName>Plate1</LabwareName>
            </LihaPipettingWithVolumesScriptCommandDataV7></Data>
          </LihaAspirateScriptCommandDataV5>
        </Object>
        <!-- Unknown command → opaque -->
        <Object Type="Tecan.Core.Scripting.ExecuteApplicationStatement">
          <ExecuteApplicationStatement><Application>external_tool.exe</Application><Arguments>--run</Arguments></ExecuteApplicationStatement>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""", encoding="utf-8")

    proto = parse_xscr(src)
    py = _emit_python_checked(tmp_path, "decompiled_mixed.py", proto)

    wt = load_module(py).build_worktable()
    wt.simulate(strict=True)

    report = wt.simulation_report
    assert report is not None

    print(f"\n  [mixed_opaque] total={report.total_executed_steps}, "
          f"coverage={report.modeled_coverage:.3f}, "
          f"opaque_ids={report.unsupported_command_ids}")

    # Step composition:
    #   AddLabware(Plate1) → fully simulated (LABWARE_MOVEMENT)
    #   WaitStatement → validation_only
    #   LihaPickUp (raw_xml, adapted as GenericStep→LihaGetTipsStep) → fully simulated
    #   LihaAspirate (structured) → fully simulated (LIQUID_TRANSFER)
    #   ExecuteApplication → opaque
    assert report.total_executed_steps == 5
    assert report.fully_simulated_steps == 3  # AddLabware + PickUp + Aspirate
    assert report.validation_only_steps == 1  # WaitStatement
    assert report.opaque_noop_steps == 1      # ExecuteApplication
    assert report.raw_xml_generic_steps == 1  # LihaPickUp
    assert report.modeled_coverage == pytest.approx(4.0 / 5.0)  # 80%
    assert "execute_application" in report.unsupported_command_ids


# ── Sample 4: Loop/conditional protocol simulation ─────────────────

@pytest.mark.usefixtures("synthetic_catalog")
def test_sample_loop_conditional_simulation() -> None:
    """Loop + conditional example: simulate and verify report counters."""
    from examples.loop_conditional import build_worktable

    wt = build_worktable()
    wt.simulate(strict=True)

    report = wt.simulation_report
    assert report is not None

    print(f"\n  [loop_conditional] total={report.total_executed_steps}, "
          f"coverage={report.modeled_coverage:.3f}, "
          f"opaque_ids={report.unsupported_command_ids}")

    # Loop and conditional steps are validation_only; inner steps vary.
    assert report.total_executed_steps > 0
    # No opaque steps expected in the example
    assert report.opaque_noop_steps == 0
    # All steps modeled (loop/conditional = validation_only, which counts as modeled)
    assert report.modeled_coverage == pytest.approx(1.0)


# ── Summary record ─────────────────────────────────────────────────

@pytest.mark.skipif(not index_exists(), reason="decompiled labware needs catalog index for well resolution")
def test_decompiled_workflow_summary(tmp_path: Path) -> None:
    """Run all 3 samples and produce a summary record."""
    results = []

    # Sample A: simple_transfer (authored, fully modeled)
    from examples.simple_transfer import build_worktable as bt_simple
    wt_a = bt_simple()
    wt_a.simulate(strict=True)
    r_a = wt_a.simulation_report
    results.append({
        "name": "simple_transfer",
        "total_steps": r_a.total_executed_steps,
        "coverage": r_a.modeled_coverage,
        "opaque_ids": list(r_a.unsupported_command_ids.keys()),
        "liquid_changed": bool(r_a.final_labware),
    })

    # Sample B: decompiled LiHa workflow
    src_b = tmp_path / "liha.xscr"
    src_b.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload>
<ObjectName>LiHa</ObjectName><Comment />
<Reference><Guid>11111111-1234-aaaa-ffff-000000000222</Guid><TypeId>WorktableWorkspace</TypeId><ObjectName>780_Empty</ObjectName></Reference>
<ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
<AddLabwareDataV1><LabwareType>96 Well Flat</LabwareType><LabwareLable>S</LabwareLable><Location>Site</Location><Position>1</Position></AddLabwareDataV1></Object>
<Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaPickUpScriptCommandDataV1">
<LihaPickUpScriptCommandDataV1><LabwareName>Tips</LabwareName></LihaPickUpScriptCommandDataV1></Object>
<Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
<LihaAspirateScriptCommandDataV5><Data><LihaPipettingWithVolumesScriptCommandDataV7>
<Volumes><Object Type="System.String"><string>10</string></Object></Volumes>
<LiquidClassName>Water Free Single</LiquidClassName><LabwareName>S</LabwareName>
</LihaPipettingWithVolumesScriptCommandDataV7></Data></LihaAspirateScriptCommandDataV5></Object>
</Objects><Name>Steps</Name></ScriptGroup></Payload></VxData>""", encoding="utf-8")
    py_b = _emit_python_checked(tmp_path, "liha_decompiled.py", parse_xscr(src_b))
    wt_b = load_module(py_b).build_worktable()
    wt_b.simulate(strict=True)
    r_b = wt_b.simulation_report
    results.append({
        "name": "decompiled_liha",
        "total_steps": r_b.total_executed_steps,
        "coverage": r_b.modeled_coverage,
        "opaque_ids": list(r_b.unsupported_command_ids.keys()),
        "liquid_changed": bool(r_b.final_labware),
    })

    # Sample C: mixed with opaque (coverage < 100%)
    src_c = tmp_path / "mixed.xscr"
    src_c.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload>
<ObjectName>Mixed</ObjectName><Comment />
<Reference><Guid>11111111-1234-aaaa-ffff-000000000222</Guid><TypeId>WorktableWorkspace</TypeId><ObjectName>780_Empty</ObjectName></Reference>
<ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
<AddLabwareDataV1><LabwareType>96 Well Flat</LabwareType><LabwareLable>P</LabwareLable><Location>Site</Location><Position>1</Position></AddLabwareDataV1></Object>
<Object Type="Tecan.Core.Scripting.WaitStatement"><WaitStatement><Duration>1</Duration></WaitStatement></Object>
<Object Type="Tecan.Core.Scripting.ExecuteApplicationStatement">
<ExecuteApplicationStatement><Application>x.exe</Application></ExecuteApplicationStatement></Object>
</Objects><Name>Steps</Name></ScriptGroup></Payload></VxData>""", encoding="utf-8")
    py_c = _emit_python_checked(tmp_path, "mixed_decompiled.py", parse_xscr(src_c))
    wt_c = load_module(py_c).build_worktable()
    wt_c.simulate(strict=True)
    r_c = wt_c.simulation_report
    results.append({
        "name": "mixed_opaque",
        "total_steps": r_c.total_executed_steps,
        "coverage": r_c.modeled_coverage,
        "opaque_ids": list(r_c.unsupported_command_ids.keys()),
        "liquid_changed": bool(r_c.final_labware),
    })

    # Assertions on summary — exact expectations for each sample
    # Sample A: simple_transfer — fully modeled, no opaque steps
    assert results[0]["coverage"] == pytest.approx(1.0)
    assert len(results[0]["opaque_ids"]) == 0
    assert results[0]["liquid_changed"] is True  # final_labware has entries

    # Sample B: decompiled LiHa — fully modeled (PickUp adapted, Aspirate structured)
    assert results[1]["coverage"] == pytest.approx(1.0)
    assert len(results[1]["opaque_ids"]) == 0

    # Sample C: mixed with opaque — coverage < 100%, execute_application is opaque
    assert results[2]["coverage"] == pytest.approx(2.0 / 3.0)  # AddLabware + Wait modeled, ExecApp opaque
    assert "execute_application" in results[2]["opaque_ids"]


# ── Verification-script command decoding ─────────────────────────────

def test_verification_command_fixture_decodes_structured_steps() -> None:
    """Common verification commands should not fall back to GenericStep."""
    from fluentcoder.ir.schema import (
        CommentStep,
        ConditionalStep,
        DelayStep,
        GenericStep,
        SetVariableStep,
        StartTimerStep,
        SubRoutineStep,
        UserPromptStep,
        WaitForTimerStep,
    )

    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "decompiled_corpus"
        / "verification_commands.xscr"
    )
    steps = parse_xscr(fixture).groups[0].steps

    assert not any(isinstance(step, GenericStep) for step in steps)
    assert isinstance(steps[0], CommentStep)
    assert steps[0].comment == "Operator setup"
    assert isinstance(steps[1], SetVariableStep)
    assert steps[1].variable_name == "cycles"
    assert isinstance(steps[2], DelayStep)
    assert steps[2].delay == 500
    assert isinstance(steps[3], StartTimerStep)
    assert isinstance(steps[4], WaitForTimerStep)
    assert steps[4].duration_seconds == 30.0
    assert isinstance(steps[5], UserPromptStep)
    assert steps[5].prompt == "Confirm deck state"
    assert steps[5].image_path is None
    assert isinstance(steps[6], UserPromptStep)
    assert steps[6].prompt == "Seat the adapter"
    assert steps[6].image_path == "media/step_001_image.png"
    assert steps[6].screen_title == "Adapter check"
    assert steps[6].timeout == 15
    assert isinstance(steps[7], SubRoutineStep)
    assert steps[7].subroutine == r"TEST\SUB_Minimal_v1"
    assert isinstance(steps[8], ConditionalStep)
    assert isinstance(steps[8].then_steps[0], UserPromptStep)


def test_rup_standard_empty_image_decodes_to_user_prompt(tmp_path: Path) -> None:
    src = tmp_path / "rup_empty_image.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>RUP Empty Image</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement">
          <RUPStandardStatement>
            <StandardProperties>
              <StandardStatementDataClass>
                <SelectedImagePath />
                <MessageText>All Done!</MessageText>
              </StandardStatementDataClass>
            </StandardProperties>
            <RUPScreenTitle>The End</RUPScreenTitle>
            <RUPTimeOut>1</RUPTimeOut>
          </RUPStandardStatement>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    from fluentcoder.ir.schema import GenericStep, UserPromptStep

    step = parse_xscr(src).groups[0].steps[0]
    assert isinstance(step, UserPromptStep)
    assert not isinstance(step, GenericStep)
    assert step.prompt == "All Done!"
    assert step.image_path == ""
    assert step.screen_title == "The End"
    assert step.rup_kind == "standard"

    rendered = emit_python(parse_xscr(src), source_xscr=str(src))
    assert "rup_kind='standard'" in rendered


def test_nested_script_group_verification_steps_decode(tmp_path: Path) -> None:
    """ScriptGroupDataV1 + Statements layout used by production verification scripts."""
    src = tmp_path / "nested_group.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Nested Group</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
          <ScriptGroupDataV1>
            <Name>Arm verification</Name>
            <Data Type="Tecan.Core.Scripting.Helpers.ScriptGroupBaseDataV1">
              <ScriptGroupBaseDataV1>
                <Statements>
                  <Object Type="Tecan.Core.Scripting.CommentStatement">
                    <CommentStatement><Comment>Check fingers</Comment></CommentStatement>
                  </Object>
                  <Object Type="Tecan.Core.Scripting.UserPromptStatement">
                    <UserPromptStatement><Prompt>Parallel?</Prompt><Timeout>0</Timeout></UserPromptStatement>
                  </Object>
                  <Object Type="Tecan.Core.Scripting.DelayStatement">
                    <DelayStatement><Delay>1000</Delay></DelayStatement>
                  </Object>
                </Statements>
              </ScriptGroupBaseDataV1>
            </Data>
          </ScriptGroupDataV1>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    from fluentcoder.ir.schema import CommentStep, DelayStep, GenericStep, UserPromptStep

    proto = parse_xscr(src)
    assert len(proto.groups) == 1
    assert proto.groups[0].name == "Arm verification"
    steps = proto.groups[0].steps
    assert len(steps) == 3
    assert not any(isinstance(step, GenericStep) for step in steps)
    assert isinstance(steps[0], CommentStep)
    assert isinstance(steps[1], UserPromptStep)
    assert isinstance(steps[2], DelayStep)

