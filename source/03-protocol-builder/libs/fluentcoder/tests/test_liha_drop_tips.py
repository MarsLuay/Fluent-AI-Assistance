"""Regression coverage for channel-specific LiHa/FCA tip release."""

from __future__ import annotations

from pathlib import Path

import pytest

from fluentcoder import MissingTipsError, Worktable
from fluentcoder.decompiler import emit_python, parse_xscr
from fluentcoder.ir.schema import LihaDropTipsStep
from tests.conftest import bind_offline_authoring


def _drop_xscr(*, extra: str = "", selected: str = "<Object Type=\"System.Int32\"><int>1</int></Object>") -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Drop Tips Regression</ObjectName>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaDropTipsScriptCommandDataV2">
          <LihaDropTipsScriptCommandDataV2>
            <SkipIfNothingMounted>True</SkipIfNothingMounted>
            {extra}
            <Data>
              <LiHaScriptCommandUsingTipSelectionBaseDataV1>
                <SerializedTipsIndexes />
                <SelectedTipsIndexes>{selected}</SelectedTipsIndexes>
                <TipMask>mask</TipMask>
                <TipOffset>2</TipOffset>
                <TipSpacing>9</TipSpacing>
                <Data>
                  <LihaScriptCommandDataV1>
                    <Data>
                      <ScriptCommandCommonDataV2>
                        <LabwareName>Waste</LabwareName>
                        <Data>
                          <DeviceAliasStatementBaseDataV1>
                            <Alias><DeviceAlias>Instrument=1/Device=LIHA:1</DeviceAlias></Alias>
                            <ID><AvailableID>USB:TECAN,FLUENT,0000000000/LIHA:1</AvailableID></ID>
                          </DeviceAliasStatementBaseDataV1>
                        </Data>
                      </ScriptCommandCommonDataV2>
                    </Data>
                  </LihaScriptCommandDataV1>
                </Data>
              </LiHaScriptCommandUsingTipSelectionBaseDataV1>
            </Data>
          </LihaDropTipsScriptCommandDataV2>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
'''


def _parsed_drop_step(tmp_path: Path, *, extra: str = "", selected: str | None = None):
    source = tmp_path / "drop_tips.xscr"
    source.write_text(
        _drop_xscr(extra=extra, selected=selected or '<Object Type="System.Int32"><int>1</int></Object>'),
        encoding="utf-8",
    )
    return parse_xscr(source).groups[0].steps[0]


def test_drop_tips_parser_preserves_channels_and_device_binding(tmp_path: Path) -> None:
    step = _parsed_drop_step(
        tmp_path,
        selected=(
            '<Object Type="System.Int32"><int>1</int></Object>'
            '<Object Type="System.Int32"><int>3</int></Object>'
        ),
    )

    assert isinstance(step, LihaDropTipsStep)
    assert step.tip_channels == [1, 3]
    assert step.skip_if_nothing_mounted is True
    assert step.tip_mask == "mask"
    assert step.tip_offset == 2
    assert step.tip_spacing == 9
    assert step.device_alias == "Instrument=1/Device=LIHA:1"
    assert step.available_id == "USB:TECAN,FLUENT,0000000000/LIHA:1"
    assert step.raw_xml is None

    rendered = emit_python(parse_xscr(tmp_path / "drop_tips.xscr"))
    assert "skip_if_nothing_mounted=True" in rendered
    assert "tip_mask='mask'" in rendered
    assert "tip_offset=2" in rendered
    assert "tip_spacing=9" in rendered
    assert "device_alias='Instrument=1/Device=LIHA:1'" in rendered
    assert "available_id='USB:TECAN,FLUENT,0000000000/LIHA:1'" in rendered


def test_authored_one_channel_drop_renders_and_reparses_exact_selection(tmp_path: Path) -> None:
    wt = bind_offline_authoring(Worktable(name="one-channel drop"))
    wt.device_alias = "Instrument=1/Device=LIHA:1"
    wt.available_id = "USB:TECAN,FLUENT,0000000000/LIHA:1"
    wt.group("Setup")
    wt.liha.drop_tips("Waste", tip_channels=[1])

    output = tmp_path / "one_channel_drop.xscr"
    wt.compile(output)
    step = parse_xscr(output).groups[0].steps[0]

    assert isinstance(step, LihaDropTipsStep)
    assert step.tip_channels == [1]
    assert step.device_alias == wt.device_alias
    assert step.available_id == wt.available_id


def test_sequential_channel_drops_leave_other_mounted_channels_untouched() -> None:
    wt = bind_offline_authoring(Worktable(name="sequential drops"))
    wt.group("Setup")
    wt.liha.get_tips(tip_channels=[0, 1, 2, 3])
    for channel in range(4):
        wt.liha.drop_tips("Waste", tip_channels=[channel])

    wt.simulate()

    assert len(wt.snapshots) == 5
    for step_index, dropped_channel in enumerate(range(4), start=1):
        tips = wt.snapshots[step_index].liha_tips
        assert tips[dropped_channel] is None
        assert all(tips[channel] is not None for channel in range(dropped_channel + 1, 4))


def test_drop_of_unmounted_selected_channel_fails_loudly() -> None:
    wt = bind_offline_authoring(Worktable(name="unmounted selected drop"))
    wt.group("Setup")
    wt.liha.get_tips(tip_channels=[0])
    wt.liha.drop_tips("Waste", tip_channels=[1])

    with pytest.raises(MissingTipsError, match="selected channel"):
        wt.simulate()


def test_default_drop_releases_all_mounted_channels() -> None:
    wt = bind_offline_authoring(Worktable(name="default drop"))
    wt.group("Setup")
    wt.liha.get_tips(tip_channels=[0, 2])
    wt.liha.drop_tips("Waste")

    wt.simulate()

    assert all(tip is None for tip in wt.snapshots[-1].liha_tips)


def test_unknown_drop_field_is_preserved_as_raw_xml(tmp_path: Path) -> None:
    step = _parsed_drop_step(tmp_path, extra="<FutureEjectMode>LowerDiTi</FutureEjectMode>")

    assert isinstance(step, LihaDropTipsStep)
    assert step.raw_xml is not None
    assert "FutureEjectMode" in step.raw_xml
    rendered = emit_python(parse_xscr(tmp_path / "drop_tips.xscr"))
    assert "wt.raw_xml_step('LihaDropTips'" in rendered
    assert "FutureEjectMode" in rendered
