from tests.conftest import bind_offline_authoring
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import FCA1000Box, Plate96, Reagent, Worktable
from fluentcoder.catalog.catalog import index_exists, list_by_category
from fluentcoder.decompiler import emit_python, parse_xscr
from fluentcoder.decompiler.xscr_parser import _parse_condition_string
from fluentcoder.simulator import Simulator
from fluentcoder.ir.schema import (
    AddLabwareStep,
    ConditionalStep,
    DelayStep,
    ExecuteApplicationStep,
    ExportVariableStep,
    GenericStep,
    ImportVariableStep,
    LoopStep,
    Mca384EmptyTipsStep,
    Mca384MixStep,
    QueryVariableStep,
    Group,
    Protocol,
    SetLocationStep,
    SetVariableStep,
    StartTimerStep,
    UserPromptStep,
    WaitForTimerStep,
    WaitStep,
)
from tests._module_loader import load_module


def test_statement_aliases_decode_to_structured_steps(tmp_path: Path) -> None:
    src = tmp_path / "statements.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Statements</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.SetVariableStatement">
          <SetVariableStatement><Name>cycles</Name><Value>3</Value></SetVariableStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.WaitStatement">
          <WaitStatement><Duration>60</Duration></WaitStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.DelayStatement">
          <DelayStatement><Delay>2000</Delay></DelayStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.UserPromptStatement">
          <UserPromptStatement><Prompt>Load plate</Prompt><Timeout>5</Timeout></UserPromptStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.StartTimerStatement">
          <StartTimerStatement><Timer>2</Timer></StartTimerStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.WaitForTimerStatement">
          <WaitForTimerStatement><Timer>2</Timer><Duration>120</Duration></WaitForTimerStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.VariableExportImport.ExportVariableStatement">
          <ExportVariableStatement>
            <Variables><Object Type="System.String"><string>cycles</string></Object></Variables>
            <ExportFile>"C:\\tmp\\vars.txt"</ExportFile>
            <WriteHeader>True</WriteHeader>
            <ReplaceExistingFile>False</ReplaceExistingFile>
            <ExportStringsWithQuotes>False</ExportStringsWithQuotes>
            <DelimiterCode>59</DelimiterCode>
          </ExportVariableStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.VariableExportImport.ImportVariableStatement">
          <ImportVariableStatement>
            <Variables><Object Type="System.String"><string>cycles</string></Object></Variables>
            <ImportFile>"C:\\tmp\\vars.txt"</ImportFile>
            <ReadLine>True</ReadLine><Line>4</Line>
            <StartInColumn>True</StartInColumn><Column>2</Column>
            <HasHeader>True</HasHeader><DelimiterCode>59</DelimiterCode>
          </ImportVariableStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.QueryVariableStatement">
          <QueryVariableStatement><Name>cycles</Name><QueryPrompt>Cycles?</QueryPrompt><LimitRange>True</LimitRange></QueryVariableStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.ExecuteApplicationStatement">
          <ExecuteApplicationStatement><Application>tool.exe</Application><Arguments>--x</Arguments><Wait>False</Wait><StoreReturn>True</StoreReturn><Variable>rc</Variable></ExecuteApplicationStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.Worktable.SetLocationStatement">
          <SetLocationStatement><Labware>Lid</Labware><Location>Nest</Location><Site>4</Site><Rotation>90</Rotation></SetLocationStatement>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps

    assert isinstance(steps[0], SetVariableStep)
    assert steps[0].variable_name == "cycles"
    assert steps[0].value == 3
    assert isinstance(steps[1], WaitStep)
    assert steps[1].duration_seconds == 60.0
    assert isinstance(steps[2], DelayStep)
    assert steps[2].delay == 2000
    assert isinstance(steps[3], UserPromptStep)
    assert isinstance(steps[4], StartTimerStep)
    assert isinstance(steps[5], WaitForTimerStep)
    assert isinstance(steps[6], ExportVariableStep)
    assert steps[6].export_file == r"C:\tmp\vars.txt"
    assert isinstance(steps[7], ImportVariableStep)
    assert steps[7].line == 4
    assert isinstance(steps[8], QueryVariableStep)
    assert isinstance(steps[9], ExecuteApplicationStep)
    assert isinstance(steps[10], SetLocationStep)
    assert steps[10].location == "Nest"


def test_alternate_group_attaches_to_conditional_else(tmp_path: Path) -> None:
    src = tmp_path / "alternate.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Alternate</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.ConditionalGroup">
          <ConditionalGroup>
            <Name>If pH</Name><Condition>ph=7</Condition>
            <Objects>
              <Object Type="Tecan.Core.Scripting.WaitStatement"><WaitStatement><Duration>1</Duration></WaitStatement></Object>
            </Objects>
          </ConditionalGroup>
        </Object>
        <Object Type="Tecan.Core.Scripting.AlternateGroup">
          <AlternateGroup>
            <Name>Else pH</Name>
            <Objects>
              <Object Type="Tecan.Core.Scripting.WaitStatement"><WaitStatement><Duration>2</Duration></WaitStatement></Object>
            </Objects>
          </AlternateGroup>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps

    assert len(steps) == 1
    cond = steps[0]
    assert isinstance(cond, ConditionalStep)
    assert cond.name == "If pH"
    assert cond.left_variable == "ph"
    assert cond.operator == "=="
    assert cond.right_value == 7
    assert len(cond.then_steps) == 1
    assert isinstance(cond.then_steps[0], WaitStep)
    assert cond.then_steps[0].duration_seconds == 1.0
    assert len(cond.else_steps) == 1
    assert isinstance(cond.else_steps[0], WaitStep)
    assert cond.else_steps[0].duration_seconds == 2.0


def _conditional_else_xscr() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Alternate</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.ConditionalGroup">
          <ConditionalGroup>
            <Name>If pH</Name><Condition>ph=7</Condition>
            <Objects>
              <Object Type="Tecan.Core.Scripting.WaitStatement"><WaitStatement><Duration>1</Duration></WaitStatement></Object>
            </Objects>
          </ConditionalGroup>
        </Object>
        <Object Type="Tecan.Core.Scripting.AlternateGroup">
          <AlternateGroup>
            <Name>Else pH</Name>
            <Objects>
              <Object Type="Tecan.Core.Scripting.WaitStatement"><WaitStatement><Duration>2</Duration></WaitStatement></Object>
            </Objects>
          </AlternateGroup>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
"""


def test_conditional_else_codegen_emits_else_branch(tmp_path: Path) -> None:
    src = tmp_path / "alternate.xscr"
    src.write_text(_conditional_else_xscr(), encoding="utf-8")
    py_src = emit_python(parse_xscr(src), source_xscr=str(src))

    assert "with wt.conditional(" in py_src
    assert ") as _cond_" in py_src
    assert "with wt.else_branch(_cond_" in py_src
    assert "AlternateGroup" not in py_src
    assert "wt.raw_xml_step(" not in py_src


def test_disabled_conditional_roundtrips_and_skips_undeclared_condition(tmp_path: Path) -> None:
    src = tmp_path / "disabled-conditional.xscr"
    src.write_text(
        _conditional_else_xscr().replace(
            "<Name>If pH</Name><Condition>ph=7</Condition>",
            "<Name>Disabled</Name><Condition>undeclared=1</Condition>"
            "<IsDisabledForExecution>True</IsDisabledForExecution>",
        ),
        encoding="utf-8",
    )

    protocol = parse_xscr(src)
    conditional = protocol.groups[0].steps[0]
    assert isinstance(conditional, ConditionalStep)
    assert conditional.disabled is True

    py_src = emit_python(protocol, source_xscr=str(src))
    assert "disabled=True" in py_src

    wt = Worktable(name="disabled conditional")
    wt.group("Steps")
    with wt.conditional(left="undeclared", op="==", right=1, disabled=True):
        wt.wait(1)
    Simulator(wt).run()
    assert all(not isinstance(snapshot.step, WaitStep) for snapshot in wt.snapshots)


def test_disabled_typed_steps_preserve_execution_metadata_through_codegen(tmp_path: Path) -> None:
    from fluentcoder.compiler.renderer import Renderer

    authored = bind_offline_authoring(Worktable(name="disabled metadata"), with_device=False)
    authored.group("Steps")
    authored.place(Plate96("DisabledPlate", catalog="96 Well Flat"), "Nest", 1)
    authored.to_protocol().groups[0].steps[0].disabled = True
    src = tmp_path / "disabled-add-labware.xscr"
    src.write_text(Renderer().render(authored.to_protocol()), encoding="utf-8")

    parsed = parse_xscr(src)
    assert parsed.groups[0].steps[0].disabled is True

    py_src = emit_python(parsed, source_xscr=str(src))
    assert "wt.disable_next_step()" in py_src

    prompt = bind_offline_authoring(Worktable(name="disabled prompt"), with_device=False)
    prompt.group("Steps")
    prompt.disable_next_step()
    prompt.user_prompt("Ignore me")
    prompt_src = tmp_path / "disabled-prompt.xscr"
    prompt_src.write_text(Renderer().render(prompt.to_protocol()), encoding="utf-8")
    assert parse_xscr(prompt_src).groups[0].steps[0].disabled is True

    application = bind_offline_authoring(Worktable(name="disabled application"), with_device=False)
    application.group("Steps")
    application.disable_next_step()
    application.execute_application("TubeEye.exe")
    application_src = tmp_path / "disabled-application.xscr"
    application_src.write_text(Renderer().render(application.to_protocol()), encoding="utf-8")
    application_parsed = parse_xscr(application_src)
    assert application_parsed.groups[0].steps[0].disabled is True
    assert "wt.disable_next_step()" in emit_python(application_parsed, source_xscr=str(application_src))

    wt = Worktable(name="next disabled")
    wt.group("Steps")
    wt.disable_next_step()
    wt.set_variable("ignored", 1)
    assert wt.to_protocol().groups[0].steps[0].disabled is True


def test_decompiled_add_labware_preserves_unmapped_workspace_location(
    synthetic_catalog,
) -> None:
    protocol = Protocol(
        name="Unmapped location",
        groups=[
            Group(
                name="Setup",
                steps=[
                    AddLabwareStep(
                        labware_type="96 Well Flat",
                        label="FilterDWP[001]",
                        location="96Deepwell2ml_CoverSite_2",
                        position=1,
                    )
                ],
            )
        ],
    )

    generated = emit_python(protocol)
    assert "96Deepwell2ml_CoverSite_2" in generated
    assert "allow_invalid_slot=True" in generated

    wt = Worktable(name="source slot")
    wt.valid_slots = {("Nest", 1)}
    wt.group("Setup")
    plate = wt.place(
        Plate96("FilterDWP[001]", catalog="96 Well Flat"),
        "96Deepwell2ml_CoverSite_2",
        1,
        allow_invalid_slot=True,
    )
    assert plate.slot == ("96Deepwell2ml_CoverSite_2", 1)
    assert wt.to_protocol().groups[0].steps[0].location == "96Deepwell2ml_CoverSite_2"


def test_decompiled_add_labware_preserves_location_when_catalog_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty catalog must not drop unmapped workspace locations from codegen."""
    monkeypatch.setattr(
        "fluentcoder.decompiler.codegen.index_exists",
        lambda *args, **kwargs: False,
    )
    protocol = Protocol(
        name="Unmapped location offline",
        groups=[
            Group(
                name="Setup",
                steps=[
                    AddLabwareStep(
                        labware_type="96 Well Flat",
                        label="FilterDWP[001]",
                        location="96Deepwell2ml_CoverSite_2",
                        position=1,
                    )
                ],
            )
        ],
    )
    generated = emit_python(protocol)
    assert "96Deepwell2ml_CoverSite_2" in generated
    assert "allow_invalid_slot=True" in generated
    assert "Plate96" in generated


def test_conditional_else_simulator_executes_false_branch() -> None:
    wt = bind_offline_authoring(Worktable(name="conditional-else"), with_device=False)
    wt.set_sim_value("ph", 6)
    wt.group("Steps")
    with wt.conditional(left="ph", op="==", right=7, name="If pH") as cond:
        wt.wait(1)
    with wt.else_branch(cond):
        wt.wait(2)

    Simulator(wt).run()

    wait_durations = [
        s.step.duration_seconds
        for s in wt.snapshots
        if isinstance(s.step, WaitStep)
    ]
    assert wait_durations == [2.0]


def test_conditional_else_renderer_emits_alternate_group(tmp_path: Path) -> None:
    from fluentcoder.compiler.renderer import Renderer

    src = tmp_path / "alternate.xscr"
    src.write_text(_conditional_else_xscr(), encoding="utf-8")
    proto = parse_xscr(src)
    proto.worktable_guid = "11111111-1234-aaaa-ffff-000000000222"
    proto.worktable_name = "Synthetic Offline Workspace"
    xml = Renderer().render(proto)

    assert "ConditionalGroup" in xml
    assert "AlternateGroup" in xml
    assert xml.index("ConditionalGroup") < xml.index("AlternateGroup")


def test_parse_fluent_function_condition_with_not_equal_operator() -> None:
    left, op, right = _parse_condition_string('MountedFESfinger()<>"Eccentric[001]"')

    assert left == "MountedFESfinger()"
    assert op == "!="
    assert right == "Eccentric[001]"


def test_parse_first_clause_from_compound_fluent_condition() -> None:
    left, op, right = _parse_condition_string("teye_status<>1 AND simulation<1")

    assert left == "teye_status"
    assert op == "!="
    assert right == 1


@pytest.mark.usefixtures("synthetic_catalog")
def test_mca384_mix_and_empty_tips_decode_and_codegen_executes(tmp_path: Path) -> None:
    src = tmp_path / "mca.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>MCA</ObjectName>
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
        <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384MixScriptCommandDataV2">
          <Mca384MixScriptCommandDataV2><Cycles>4</Cycles><Data><Mca384PipettingWithVolumesScriptCommandDataV2><LiquidClassName>Water Mix</LiquidClassName><Volume>5</Volume><Data><Mca384ScriptCommandUsingWellSelectionBaseDataV6><Data><ScriptCommandCommonDataV2><LabwareName>Plate1</LabwareName></ScriptCommandCommonDataV2></Data></Mca384ScriptCommandUsingWellSelectionBaseDataV6></Data></Mca384PipettingWithVolumesScriptCommandDataV2></Data></Mca384MixScriptCommandDataV2>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.Mca.Mca384.Scripting.Mca384EmptyTipsScriptCommandDataV2">
          <Mca384EmptyTipsScriptCommandDataV2><Data><Mca384PipettingWithVolumesScriptCommandDataV2><LiquidClassName>Empty Tip</LiquidClassName><Volume>5</Volume><Data><Mca384ScriptCommandUsingWellSelectionBaseDataV6><Data><ScriptCommandCommonDataV2><LabwareName>Plate1</LabwareName></ScriptCommandCommonDataV2></Data></Mca384ScriptCommandUsingWellSelectionBaseDataV6></Data></Mca384PipettingWithVolumesScriptCommandDataV2></Data></Mca384EmptyTipsScriptCommandDataV2>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    proto = parse_xscr(src)

    mix = proto.groups[0].steps[1]
    empty = proto.groups[0].steps[2]
    assert isinstance(mix, Mca384MixStep)
    assert mix.labware_name == "Plate1"
    assert mix.volume == 5.0
    assert mix.cycles == 4
    assert mix.liquid_class == "Water Mix"
    assert isinstance(empty, Mca384EmptyTipsStep)
    assert empty.labware_name == "Plate1"
    assert empty.volume == 5.0
    assert empty.liquid_class == "Empty Tip"

    py = tmp_path / "decompiled.py"
    py.write_text(emit_python(proto, source_xscr=str(src)), encoding="utf-8")
    text = py.read_text(encoding="utf-8")
    assert "head.mix(" in text
    assert "head.empty_tips(" in text
    load_module(py).build_worktable()


def test_complex_liha_commands_are_raw_preserved(tmp_path: Path) -> None:
    src = tmp_path / "complex_liha.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Complex LiHa</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaPickUpScriptCommandDataV1">
          <LihaPickUpScriptCommandDataV1><LabwareName>Tips50</LabwareName></LihaPickUpScriptCommandDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
          <LihaAspirateScriptCommandDataV5>
            <Data>
              <LihaPipettingWithVolumesScriptCommandDataV7>
                <Volumes>
                  <Object Type="System.String"><string>1.5*var_aspirate_volume</string></Object>
                </Volumes>
                <LiquidClassName>Water Free Single</LiquidClassName>
                <LiquidClassNameByExpression>var_liquidclass1</LiquidClassNameByExpression>
                <IsLiquidClassNameByExpressionEnabled>True</IsLiquidClassNameByExpressionEnabled>
                <LabwareName>Source</LabwareName>
                <SelectedWellsString>A2 - H2</SelectedWellsString>
                <SerializedWellIndexes>8&gt;1&gt;15;</SerializedWellIndexes>
                <TipSpacing>18</TipSpacing>
              </LihaPipettingWithVolumesScriptCommandDataV7>
            </Data>
          </LihaAspirateScriptCommandDataV5>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps

    assert isinstance(steps[0], GenericStep)
    assert steps[0].parameters["raw_xml"].find("LihaPickUpScriptCommandDataV1") >= 0
    assert isinstance(steps[1], GenericStep)
    assert "1.5*var_aspirate_volume" in steps[1].parameters["raw_xml"]
    assert "A2 - H2" in steps[1].parameters["raw_xml"]


def test_loop_group_decodes_to_structured_step(tmp_path: Path) -> None:
    src = tmp_path / "loop.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Loop</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.LoopGroup">
          <LoopGroup>
            <Name>Repeat</Name>
            <LoopVariable>cycles</LoopVariable>
            <NumberOfLoops>3</NumberOfLoops>
            <Objects>
              <Object Type="Tecan.Core.Scripting.WaitStatement">
                <WaitStatement><Duration>5</Duration></WaitStatement>
              </Object>
            </Objects>
          </LoopGroup>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps

    assert len(steps) == 1
    loop = steps[0]
    assert isinstance(loop, LoopStep)
    assert loop.name == "Repeat"
    assert loop.loop_variable == "cycles"
    assert loop.number_of_loops == 3
    assert loop.iterations == 3
    assert len(loop.steps) == 1
    assert isinstance(loop.steps[0], WaitStep)
    assert loop.steps[0].duration_seconds == 5.0
    assert "with wt.loop(times=3, name='Repeat', loop_variable='cycles'):" in emit_python(
        parse_xscr(src), source_xscr=str(src)
    )


def test_decompiled_corpus_control_flow_fixture() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "decompiled_corpus" / "control_flow_steps.xscr"
    steps = parse_xscr(fixture).groups[0].steps

    assert isinstance(steps[0], DelayStep)
    assert steps[0].delay == 1500
    assert isinstance(steps[1], LoopStep)
    assert steps[1].name == "Cycle waits"
    assert isinstance(steps[1].steps[0], WaitStep)


def test_simulator_set_variable_mix_and_empty_tips() -> None:
    wt = Worktable(name="sim")
    wt.group("Steps")
    if index_exists():
        plate_catalog = list_by_category("plate")[0].name
        tip_catalog = list_by_category("tip_box")[0].name
        src_lw = Plate96("Source", catalog=plate_catalog)
        tips_lw = FCA1000Box("Tips", catalog=tip_catalog)
    else:
        src_lw = Plate96("Source")
        tips_lw = FCA1000Box("Tips")
    src = wt.place(src_lw, "Site", 1)
    tip_box = wt.place(tips_lw, "Site", 2)
    src.fill_all(Reagent("water"), 20)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tip_box)
    head.aspirate(src, 5, liquid_class="Water Free Single")
    wt.set_variable("cycles", 2)
    head.mix(src, "cycles", cycles="cycles", liquid_class="Water Mix")
    head.empty_tips(src, 5)

    wt.simulate()

    assert wt.sim_values["cycles"] == 2
    assert sum(t.volume_ul for t in wt.snapshots[-1].mca_tips) == 0

