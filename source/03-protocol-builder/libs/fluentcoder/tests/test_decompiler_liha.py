from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.catalog.catalog import index_exists
from fluentcoder.decompiler import emit_python, parse_xscr
from fluentcoder.ir.schema import LihaAspirateStep, LihaDispenseStep, ScriptGroupStep
from tests._module_loader import load_module
from tests.conftest import bind_offline_authoring


def test_decompile_nested_script_group_with_liha_body(tmp_path: Path) -> None:
    src = tmp_path / "nested_liha.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Nested LiHa</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
          <ScriptGroupDataV1>
            <Data>
              <ScriptGroupBaseDataV1>
                <Name>outer</Name>
                <Statements>
                  <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
                    <ScriptGroupDataV1>
                      <Data>
                        <ScriptGroupBaseDataV1>
                          <Name>liha body</Name>
                          <Statements>
                            <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
                              <LihaAspirateScriptCommandDataV5>
                                <Data>
                                  <LihaPipettingWithVolumesScriptCommandDataV7>
                                    <Volumes><Object Type="System.String"><string>20</string></Object></Volumes>
                                    <LiquidClassName>Water Free Single</LiquidClassName>
                                    <LabwareName>SourcePlate</LabwareName>
                                    <WellOffset>3</WellOffset>
                                  </LihaPipettingWithVolumesScriptCommandDataV7>
                                </Data>
                              </LihaAspirateScriptCommandDataV5>
                            </Object>
                            <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaDispenseScriptCommandDataV6">
                              <LihaDispenseScriptCommandDataV6>
                                <Data>
                                  <LihaPipettingWithVolumesScriptCommandDataV7>
                                    <Volumes><Object Type="System.String"><string>20</string></Object></Volumes>
                                    <LiquidClassName>Water Free Single</LiquidClassName>
                                    <LabwareName>DestPlate</LabwareName>
                                  </LihaPipettingWithVolumesScriptCommandDataV7>
                                </Data>
                              </LihaDispenseScriptCommandDataV6>
                            </Object>
                          </Statements>
                        </ScriptGroupBaseDataV1>
                      </Data>
                    </ScriptGroupDataV1>
                  </Object>
                </Statements>
              </ScriptGroupBaseDataV1>
            </Data>
          </ScriptGroupDataV1>
        </Object>
      </Objects>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    proto = parse_xscr(src)

    assert len(proto.groups) == 1
    nested = proto.groups[0].steps[0]
    assert isinstance(nested, ScriptGroupStep)
    assert nested.name == "liha body"
    assert isinstance(nested.steps[0], LihaAspirateStep)
    assert nested.steps[0].labware_name == "SourcePlate"
    assert nested.steps[0].volume == 20.0
    assert nested.steps[0].well_offset == 3
    assert isinstance(nested.steps[1], LihaDispenseStep)


def test_parse_xscr_preserves_workspace_reference(tmp_path: Path) -> None:
    src = tmp_path / "workspace_ref.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Workspace Ref</ObjectName>
    <Comment />
    <Reference>
      <Guid>11111111-1234-aaaa-ffff-000000000222</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <ScriptGroup>
      <Objects />
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    proto = parse_xscr(src)
    assert proto.worktable_guid == "11111111-1234-aaaa-ffff-000000000222"
    assert proto.worktable_name == "780_Empty"

    rendered = emit_python(proto, source_xscr=str(src))
    assert "Worktable.from_workspace('780_Empty'" in rendered
    assert "workspace_guid='11111111-1234-aaaa-ffff-000000000222'" in rendered


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_decompiled_liha_python_executes_and_recompiles(tmp_path: Path) -> None:
    src = tmp_path / "flat_liha.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Flat LiHa</ObjectName>
    <Comment />
    <Reference>
      <Guid>11111111-1234-aaaa-ffff-000000000222</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>96 Well Flat</LabwareType>
            <LabwareLable>Plate1</LabwareLable>
            <Location>Site</Location>
            <Position>1</Position>
          </AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaPickUpScriptCommandDataV1">
          <LihaPickUpScriptCommandDataV1><LabwareName>Plate1</LabwareName></LihaPickUpScriptCommandDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
          <LihaAspirateScriptCommandDataV5>
            <Data>
              <LihaPipettingWithVolumesScriptCommandDataV7>
                <Volumes><Object Type="System.String"><string>5</string></Object></Volumes>
                <LiquidClassName>Water Free Single</LiquidClassName>
                <LabwareName>Plate1</LabwareName>
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
    py = tmp_path / "decompiled.py"
    py.write_text(emit_python(parse_xscr(src), source_xscr=str(src)), encoding="utf-8")

    text = py.read_text(encoding="utf-8")
    assert "Worktable.from_workspace('780_Empty'" in text
    assert "ExternalLabware" not in text
    assert "wt.raw_xml_step('LihaGetTips'" in text
    assert "liha.aspirate" in text

    wt = load_module(py).build_worktable()
    bind_offline_authoring(wt)
    out = tmp_path / "recompiled.xscr"
    wt.compile(out)
    xml = out.read_text(encoding="utf-8-sig")
    assert "LihaPickUpScriptCommandDataV1" in xml
    assert "LihaAspirateScriptCommandDataV5" in xml
    assert "96 Well Flat" in xml


@pytest.mark.usefixtures("synthetic_catalog")
def test_decompiled_python_fails_loudly_on_unresolved_labware_catalog(tmp_path: Path) -> None:
    src = tmp_path / "unknown_labware.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Unknown labware</ObjectName>
    <Comment />
    <Reference>
      <Guid>11111111-1234-aaaa-ffff-000000000222</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1><LabwareType>Custom Plate Not In Index</LabwareType><LabwareLable>Plate1</LabwareLable><Location>Site</Location><Position>1</Position></AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
          <LihaAspirateScriptCommandDataV5>
            <Data>
              <LihaPipettingWithVolumesScriptCommandDataV7>
                <Volumes><Object Type="System.String"><string>5</string></Object></Volumes>
                <LiquidClassName>Water Free Single</LiquidClassName>
                <LabwareName>Plate1</LabwareName>
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

    rendered = emit_python(parse_xscr(src), source_xscr=str(src))
    assert "ExternalLabware" not in rendered

    py = tmp_path / "decompiled_unknown_labware.py"
    py.write_text(rendered, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Custom Plate Not In Index"):
        load_module(py).build_worktable()


def test_decompiled_python_fails_loudly_without_workspace_reference(tmp_path: Path) -> None:
    src = tmp_path / "no_workspace.xscr"
    src.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>No workspace</ObjectName>
    <Comment />
    <ScriptGroup>
      <Objects />
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )

    rendered = emit_python(parse_xscr(src), source_xscr=str(src))
    assert "missing its WorktableWorkspace reference" in rendered

    py = tmp_path / "decompiled_missing_workspace.py"
    py.write_text(rendered, encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing its WorktableWorkspace reference"):
        load_module(py).build_worktable()

