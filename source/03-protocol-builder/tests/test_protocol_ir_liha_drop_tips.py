"""Canonical protocol-IR coverage for selected LiHa/FCA tip release."""

from __future__ import annotations

from pathlib import Path

from fluent_pipeline.protocol_ir import protocol_ir_from_python, protocol_ir_from_xscr, render_python_draft


def _drop_xscr(*, extra: str = "") -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>LiHa drop tips</ObjectName>
    <PayloadData>
      <Script>
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Instrument.Devices.Scripting.Data.LihaDropTipsScriptCommandDataV2">
                <LihaDropTipsScriptCommandDataV2>
                  <SkipIfNothingMounted>True</SkipIfNothingMounted>
                  {extra}
                  <Data>
                    <LiHaScriptCommandUsingTipSelectionBaseDataV1>
                      <SerializedTipsIndexes />
                      <SelectedTipsIndexes>
                        <Object Type="System.Int32"><int>1</int></Object>
                        <Object Type="System.Int32"><int>3</int></Object>
                      </SelectedTipsIndexes>
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
                                  <ID><AvailableID>USB:TECAN,FLUENT,1/LIHA:1</AvailableID></ID>
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
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
'''


def _write_xscr(tmp_path: Path, *, extra: str = "") -> Path:
    path = tmp_path / "liha_drop_tips.xscr"
    path.write_text(_drop_xscr(extra=extra), encoding="utf-8")
    return path


def test_xscr_selected_liha_drop_tips_roundtrips_through_canonical_ir(tmp_path: Path) -> None:
    ir = protocol_ir_from_xscr(_write_xscr(tmp_path))

    step = ir["steps"][0]
    assert step["operation"] == "liha_drop_tips"
    assert step["target_labware"] == "Waste"
    assert step["parameters"]["tip_channels"] == [1, 3]
    assert step["parameters"]["skip_if_nothing_mounted"] is True
    assert step["parameters"]["tip_mask"] == "mask"
    assert step["parameters"]["tip_offset"] == 2
    assert step["parameters"]["tip_spacing"] == 9
    assert step["parameters"]["device_alias"] == "Instrument=1/Device=LIHA:1"
    assert step["parameters"]["available_id"] == "USB:TECAN,FLUENT,1/LIHA:1"
    assert "raw_xml" not in step["parameters"]

    rendered = render_python_draft(ir)
    assert "wt.liha.drop_tips('Waste', tip_channels=[1, 3])" in rendered

    roundtrip_path = tmp_path / "roundtrip.py"
    roundtrip_path.write_text(rendered, encoding="utf-8")
    roundtrip = protocol_ir_from_python(roundtrip_path)
    roundtrip_step = roundtrip["steps"][0]
    assert roundtrip_step["operation"] == "liha_drop_tips"
    assert roundtrip_step["parameters"]["tip_channels"] == [1, 3]


def test_fca_and_mca384_drop_tips_have_distinct_canonical_operations(tmp_path: Path) -> None:
    source = tmp_path / "heads.py"
    source.write_text(
        "from fluentcoder import Worktable\n\n"
        "def build_worktable() -> Worktable:\n"
        "    wt = Worktable.from_workspace('WT')\n"
        "    wt.group('Steps')\n"
        "    wt.fca.drop_tips('Waste', tip_channels=[2])\n"
        "    wt.mca384.drop_tips('Trash')\n"
        "    return wt\n",
        encoding="utf-8",
    )

    steps = protocol_ir_from_python(source)["steps"]
    assert [step["operation"] for step in steps] == ["liha_drop_tips", "mca384_drop_tips"]
    assert steps[0]["parameters"]["tip_channels"] == [2]


def test_unmodeled_liha_drop_field_preserves_source_xml(tmp_path: Path) -> None:
    ir = protocol_ir_from_xscr(_write_xscr(tmp_path, extra="<FutureEjectMode>LowerDiTi</FutureEjectMode>"))
    step = ir["steps"][0]

    assert "FutureEjectMode" in step["parameters"]["raw_xml"]
    rendered = render_python_draft(ir)
    assert "wt.raw_xml_step('LihaDropTipsScriptCommandDataV2'" in rendered
    assert "FutureEjectMode" in rendered
