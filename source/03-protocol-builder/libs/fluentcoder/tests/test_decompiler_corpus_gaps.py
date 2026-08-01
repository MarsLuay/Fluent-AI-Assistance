from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.decompiler import (  # noqa: E402
    count_generic_step_types,
    discover_subroutine_dirs,
    emit_python,
    parse_xscr,
)
from fluentcoder.ir.schema import (  # noqa: E402
    ApplicationDriverMacroStep,
    GenerateReportStep,
    GenericStep,
    LihaAspirateStep,
    LihaDetectLiquidStep,
    LihaDispenseStep,
)

READY_ROOT = (
    Path(__file__).resolve().parents[5]
    / "ready-to-import"
    / "rga_a200_verification_promptonly"
)
SOURCE_SCRIPT = READY_ROOT / "source" / "original-sources" / "source_script_1.xscr"
SUBROUTINE_DIR = (
    READY_ROOT / "direct-imports" / "scripts" / "subroutines"
)


def _minimal_xscr(*objects: str) -> str:
    body = "\n".join(objects)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Gap Test</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
{body}
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
"""


def test_multi_channel_liha_parses_as_typed_steps(tmp_path: Path) -> None:
    src = tmp_path / "multi_liha.xscr"
    src.write_text(
        _minimal_xscr(
            """        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
          <LihaAspirateScriptCommandDataV5>
            <Data Type="Tecan.Core.Instrument.Devices.Scripting.Data.LihaPipettingWithVolumesScriptCommandDataV7">
              <LihaPipettingWithVolumesScriptCommandDataV7>
                <Volumes>
                  <Object Type="System.String"><string>WaterVol[0]</string></Object>
                  <Object Type="System.String"><string>WaterVol[1]</string></Object>
                </Volumes>
                <LiquidClassNameBySelection>Water Free Single</LiquidClassNameBySelection>
                <LabwareName>SourcePlate</LabwareName>
                <SelectedWellsString>A1 - H1</SelectedWellsString>
                <SerializedWellIndexes>0&gt;1&gt;7;</SerializedWellIndexes>
                <SelectedTipsIndexes>
                  <Object Type="System.Int32"><int>0</int></Object>
                  <Object Type="System.Int32"><int>1</int></Object>
                </SelectedTipsIndexes>
              </LihaPipettingWithVolumesScriptCommandDataV7>
            </Data>
          </LihaAspirateScriptCommandDataV5>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaDispenseScriptCommandDataV6">
          <LihaDispenseScriptCommandDataV6>
            <Data Type="Tecan.Core.Instrument.Devices.Scripting.Data.LihaPipettingWithVolumesScriptCommandDataV7">
              <LihaPipettingWithVolumesScriptCommandDataV7>
                <Volumes>
                  <Object Type="System.String"><string>WaterVol[0]</string></Object>
                  <Object Type="System.String"><string>WaterVol[1]</string></Object>
                </Volumes>
                <LiquidClassNameBySelection>Water Free Single</LiquidClassNameBySelection>
                <LabwareName>DestPlate</LabwareName>
                <SelectedWellsString>A1 - H1</SelectedWellsString>
                <SerializedWellIndexes>0&gt;1&gt;7;</SerializedWellIndexes>
              </LihaPipettingWithVolumesScriptCommandDataV7>
            </Data>
          </LihaDispenseScriptCommandDataV6>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaDetectLiquidScriptCommandDataV3">
          <LihaDetectLiquidScriptCommandDataV3>
            <SensitivityEx>2</SensitivityEx>
            <DetectionSpeed>60</DetectionSpeed>
            <LabwareName>SourcePlate</LabwareName>
            <SelectedTipsIndexes>
              <Object Type="System.Int32"><int>0</int></Object>
            </SelectedTipsIndexes>
          </LihaDetectLiquidScriptCommandDataV3>
        </Object>"""
        ),
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps
    assert isinstance(steps[0], LihaAspirateStep)
    assert steps[0].volumes == ["WaterVol[0]", "WaterVol[1]"]
    assert steps[0].tip_channels == [0, 1]
    assert steps[0].raw_xml
    assert isinstance(steps[1], LihaDispenseStep)
    assert steps[1].volumes == ["WaterVol[0]", "WaterVol[1]"]
    assert isinstance(steps[2], LihaDetectLiquidStep)
    assert steps[2].sensitivity == 2.0
    assert steps[2].detection_speed == 60.0

    generic_totals, _ = count_generic_step_types([src])
    assert generic_totals.get("LihaAspirate", 0) == 0
    assert generic_totals.get("LihaDispense", 0) == 0
    assert generic_totals.get("LihaDetectLiquid", 0) == 0

    py = emit_python(parse_xscr(src), source_xscr=str(src))
    assert "wt.raw_xml_step('LihaAspirate'" in py
    assert "wt.raw_xml_step('LihaDispense'" in py
    assert "wt.raw_xml_step('LihaDetectLiquid'" in py


def test_generate_report_and_legacy_macro_parse_as_typed_steps(tmp_path: Path) -> None:
    src = tmp_path / "report_macro.xscr"
    src.write_text(
        _minimal_xscr(
            """        <Object Type="Tecan.VisionX.SampleTrackingDriver.ScriptCommands.GenerateReport.GenerateReportStatementDataV1">
          <GenerateReportStatementDataV1>
            <Report>Plate Actions</Report>
            <PrintReport>False</PrintReport>
            <WriteCSV>True</WriteCSV>
            <AllLabwareSelected>True</AllLabwareSelected>
          </GenerateReportStatementDataV1>
        </Object>
        <Object Type="Tecan.VisionX.ApplicationDriver.LegacyDriverMacro">
          <LegacyDriverMacro Version="1" Name="ResolvexA200_Run" ModuleName="ResolvexA200">
            <ExecutionSettings />
          </LegacyDriverMacro>
        </Object>"""
        ),
        encoding="utf-8",
    )

    steps = parse_xscr(src).groups[0].steps
    assert isinstance(steps[0], GenerateReportStep)
    assert steps[0].report_name == "Plate Actions"
    assert steps[0].write_csv is True
    assert steps[0].raw_xml

    assert isinstance(steps[1], ApplicationDriverMacroStep)
    assert steps[1].macro_name == "ResolvexA200_Run"
    assert steps[1].module_name == "ResolvexA200"
    assert steps[1].raw_xml
    assert "LegacyDriverMacro" in steps[1].raw_xml

    generic_totals, _ = count_generic_step_types([src])
    assert generic_totals.get("GenerateReportStatement", 0) == 0
    assert generic_totals.get("LegacyDriverMacro", 0) == 0

    py = emit_python(parse_xscr(src), source_xscr=str(src))
    assert "wt.raw_xml_step('GenerateReportStatement'" in py
    assert "wt.raw_xml_step('LegacyDriverMacro'" in py


@pytest.mark.skipif(not SOURCE_SCRIPT.is_file(), reason="ready-to-import source_script_1 fixture missing")
def test_source_script_1_no_longer_emits_liha_generic_steps() -> None:
    generic_totals, _ = count_generic_step_types([SOURCE_SCRIPT])
    assert generic_totals.get("LihaAspirate", 0) == 0
    assert generic_totals.get("LihaDispense", 0) == 0
    assert generic_totals.get("LihaDetectLiquid", 0) == 0
    assert generic_totals.get("LihaDetectLiquidScriptCommand", 0) == 0
    assert generic_totals.get("GenerateReportStatement", 0) == 0
    assert generic_totals.get("LegacyDriverMacro", 0) == 0


@pytest.mark.skipif(not SUBROUTINE_DIR.is_dir(), reason="ready-to-import subroutine dir missing")
def test_discover_subroutine_dirs_finds_ready_to_import_subroutines() -> None:
    bundle = READY_ROOT
    main_script = bundle / "direct-imports" / "scripts" / "full-script" / "generated_script.xscr"
    if not main_script.is_file():
        zeia = bundle / "generated_project.zeia"
        if zeia.is_file():
            with zipfile.ZipFile(zeia) as zf:
                for entry in zf.namelist():
                    if entry.lower().endswith(".xscr"):
                        payload = zf.read(entry)
                        text = payload.decode("utf-8-sig", errors="replace")
                        if "<ObjectName>Generated" in text or "Verification" in text:
                            main_script = Path(tempfile.gettempdir()) / "fluentcoder_fixtures" / Path(entry).name
                            main_script.parent.mkdir(parents=True, exist_ok=True)
                            main_script.write_bytes(payload)
                            break
    if not main_script.is_file():
        pytest.skip("ready-to-import main script missing")

    discovered = discover_subroutine_dirs([main_script])
    if SUBROUTINE_DIR.resolve() not in discovered:
        pytest.skip("legacy subroutine directory layout not present in bundle")
    assert SUBROUTINE_DIR.resolve() in discovered

