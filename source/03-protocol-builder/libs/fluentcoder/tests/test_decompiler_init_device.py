from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.compiler.renderer import Renderer
from fluentcoder.decompiler import emit_python, parse_xscr
from fluentcoder.ir.schema import GenericStep, InitializeDeviceStep
from fluentcoder.worktable import Worktable
from tests._module_loader import load_module

_INIT_DEVICE_XML = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Init Device</ObjectName>
    <Comment />
    <Reference>
      <Guid>11111111-1234-aaaa-ffff-000000000222</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Instrument.Devices.Scripting.InitializeDeviceScriptCommandDataV1">
          <InitializeDeviceScriptCommandDataV1>
            <InitType>Initialize</InitType>
            <Data Type="Tecan.Core.Instrument.Helpers.Scripting.DeviceAliasStatementBaseDataV1">
              <DeviceAliasStatementBaseDataV1>
                <Alias Type="Tecan.Core.Instrument.DeviceAlias.DeviceAlias">
                  <DeviceAlias>Instrument=1/Device=MCA384:1</DeviceAlias>
                </Alias>
                <ID>
                  <AvailableID>USB:00000000:0000:0000:0000:000000000000:MCA384:1</AvailableID>
                </ID>
                <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">
                  <ScriptStatementBaseDataV1>
                    <IsBreakpoint>False</IsBreakpoint>
                    <IsDisabledForExecution>False</IsDisabledForExecution>
                    <GroupLineNumber>0</GroupLineNumber>
                    <LineNumber>1</LineNumber>
                  </ScriptStatementBaseDataV1>
                </Data>
              </DeviceAliasStatementBaseDataV1>
            </Data>
          </InitializeDeviceScriptCommandDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.Scripting.HomeDeviceScriptCommandDataV1">
          <HomeDeviceScriptCommandDataV1>
            <InitType>Home</InitType>
            <Data Type="Tecan.Core.Instrument.Helpers.Scripting.DeviceAliasStatementBaseDataV1">
              <DeviceAliasStatementBaseDataV1>
                <Alias Type="Tecan.Core.Instrument.DeviceAlias.DeviceAlias">
                  <DeviceAlias>Instrument=1/Device=LIHA:1</DeviceAlias>
                </Alias>
                <ID>
                  <AvailableID>USB:00000000:0000:0000:0000:000000000000:LIHA:1</AvailableID>
                </ID>
              </DeviceAliasStatementBaseDataV1>
            </Data>
          </HomeDeviceScriptCommandDataV1>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
"""


def test_parse_initialize_device_steps(tmp_path: Path) -> None:
    src = tmp_path / "init_device.xscr"
    src.write_text(_INIT_DEVICE_XML, encoding="utf-8")

    steps = parse_xscr(src).groups[0].steps

    assert len(steps) == 2
    init_step = steps[0]
    assert isinstance(init_step, InitializeDeviceStep)
    assert init_step.device_alias == "Instrument=1/Device=MCA384:1"
    assert init_step.available_id == "USB:00000000:0000:0000:0000:000000000000:MCA384:1"
    assert init_step.init_type == "Initialize"

    home_step = steps[1]
    assert isinstance(home_step, InitializeDeviceStep)
    assert home_step.device_alias == "Instrument=1/Device=LIHA:1"
    assert home_step.init_type == "Home"


def test_codegen_simulate_and_render_initialize_device(
    tmp_path: Path,
    synthetic_catalog: Path,
) -> None:
    src = tmp_path / "init_device.xscr"
    src.write_text(_INIT_DEVICE_XML, encoding="utf-8")

    proto = parse_xscr(src)
    py = tmp_path / "decompiled_init_device.py"
    py.write_text(emit_python(proto, source_xscr=str(src)), encoding="utf-8")
    text = py.read_text(encoding="utf-8")

    assert "wt.initialize_device(" in text
    assert "device_alias='Instrument=1/Device=MCA384:1'" in text
    assert "available_id='USB:00000000:0000:0000:0000:000000000000:MCA384:1'" in text
    assert "init_type='Home'" in text

    wt = load_module(py).build_worktable()
    wt.simulate()
    assert wt.simulation_report is not None
    assert wt.simulation_report.failure is None

    rendered = Renderer().render(wt.to_protocol())
    assert "InitializeDeviceScriptCommandDataV1" in rendered
    assert "<InitType>Initialize</InitType>" in rendered
    assert "<InitType>Home</InitType>" in rendered
    assert "Instrument=1/Device=MCA384:1" in rendered


def test_author_initialize_device_roundtrip(tmp_path: Path, synthetic_catalog: Path) -> None:
    wt = Worktable.from_workspace(
        "780_Empty",
        workspace_guid="11111111-1234-aaaa-ffff-000000000222",
        auto_place=False,
    )
    wt.group("Steps")
    wt.initialize_device(
        device_alias="Instrument=1/Device=MCA384:1",
        available_id="USB:00000000:0000:0000:0000:000000000000:MCA384:1",
        init_type="Reset",
    )

    out = tmp_path / "authored.xscr"
    wt.compile(out)

    steps = parse_xscr(out).groups[0].steps
    assert len(steps) == 1
    step = steps[0]
    assert isinstance(step, InitializeDeviceStep)
    assert step.init_type == "Reset"
    assert step.device_alias == "Instrument=1/Device=MCA384:1"
    assert not isinstance(step, GenericStep)

