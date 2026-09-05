"""Build the committed synthetic FluentControl install + catalog index for CI.

Run manually after editing fixture XML:

    python tests/fixtures/synthetic_catalog/bootstrap.py
"""

from __future__ import annotations

import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
from fluentcoder.catalog.indexer import build_index

INSTALL_ROOT = ROOT / "install"
WORKTABLE = INSTALL_ROOT / "SystemSpecific" / "Worktable"
COMPONENTS = WORKTABLE / "Components"
WORKSPACES = WORKTABLE / "Workspaces"
SITES = WORKTABLE / "Sites"
LIQUID_CLASSES = INSTALL_ROOT / "SystemSpecific" / "LiquidClasses"
INDEX_DB = ROOT / "install_index.db"

PLATE_GUID = "a1111111-1111-1111-1111-111111111101"
FCA_GUID = "b2222222-2222-2222-2222-222222222202"
FCA_SBS_GUID = "c3333333-3333-3333-3333-333333333303"
TROUGH_GUID = "d4444444-4444-4444-4444-444444444404"
NEST_SITE_GUID = "f1111111-1234-aaaa-ffff-000000000049"
NEST7MM_SITE_GUID = "f1111111-1234-aaaa-ffff-00000000007"
SAT_WORKSPACE_GUID = "291ba293-6361-4f8f-aa8d-7c2643d3f096"
SAT_REV4_GUID = "2baf8c89-406a-455a-9a91-6378fc41a0a5"
EMPTY_780_WORKSPACE_GUID = "11111111-1234-aaaa-ffff-000000000222"
MCA96_TIP_GUID = "e5555555-5555-5555-5555-555555555505"
MAGNET_PLATE_GUID = "a6666666-6666-6666-6666-666666666666"
TROUGH_CARRIER_GUID = "a82e129c-c3d4-46fc-8985-7c78d63ac0cb"
WATER_FREE_SINGLE_GUID = "e80ad9f8-534b-41b7-96fb-c2c81cfc5c03"

EMPTY_780_SITE_COUNT = 56
LIQUID_CLASS_COUNT = 30

TROUGH_SITE_GUIDS = tuple(
    str(uuid.uuid5(uuid.NAMESPACE_DNS, f"synthetic-trough-site:{index}"))
    for index in range(1, 7)
)

# Extra inference-sample components (beyond the core protocol fixtures).
INFERENCE_COMPONENTS: tuple[tuple[str, str, tuple[int, int] | None], ...] = (
    ("96 Deep Well 0.5ml", "Labware.Deep Well", (12, 8)),
    ("384 Well", "Labware.Microplate", (24, 16)),
    ("MCA96, 50ul, Box_new", "Labware.MCA96 DiTi", (12, 8)),
    ("FCA, 200ul SBS", "Labware.FCA DiTi", (12, 8)),
    ("MCA384, 50ul", "Labware.MCA384 DiTi", (24, 16)),
    ("25ml_short", "Labware.Trough", (1, 1)),
    ("LV_Alpaqua_A000350", "Labware.Microplate", (12, 8)),
    ("LV_Alpaqua_A000350_1", "Labware.Microplate", (12, 8)),
    ("LV_Alpaqua_384", "Labware.Microplate", (24, 16)),
    ("Landscape Nest Magnet Teleshake Segment", "Carrier.Deck Segment", None),
    ("MCA96 Wash Station", "Carrier.Device", None),
    ("Wash Station Cleaner Back Tube Rotator", "Carrier.Device", None),
    ("MCA Thru Deck Waste Chute", "Labware.Wash and Waste", None),
    ("FCA Thru Deck Waste Chute", "Labware.Wash and Waste", None),
    ("15 Microplate Passive Stack", "Carrier.Hotel", None),
    ("9 Nest Hotel", "Carrier.Hotel", None),
    ("Teleshake Adapter Plate", "Labware.Miscellaneous", (12, 8)),
    ("1x16 15ml Falcon Tube Runner", "Carrier.Miscellaneous", None),
    ("3x32 10mm Tube Runner no Tubes", "Carrier.Miscellaneous", None),
    ("1x4 100ml Trough Runner", "Carrier.Miscellaneous", None),
    ("Stacker Right", "Carrier.Grid Segment", None),
    ("Fluent ID Left 5 Grid", "Carrier.Grid Segment", None),
)

EXPECTED_COMPONENT_COUNT = (
    5  # plate, trough, 2x FCA tips, MCA96 tip box
    + 1  # 24 Magnet Plate
    + 1  # 6_Trough_Carrier_Reservoir
    + len(INFERENCE_COMPONENTS)
)

_XSI = "http://www.w3.org/2001/XMLSchema-instance"


def _guid_for(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"synthetic-catalog:{name}"))


def _pipettable_block(*, sites_x: int, sites_y: int) -> str:
    return f"""        <Pipettable>
          <XNumberOfWells>{sites_x}</XNumberOfWells>
          <YNumberOfWells>{sites_y}</YNumberOfWells>
          <XSpacing>9.0</XSpacing>
          <YSpacing>9.0</YSpacing>
          <PositionOfFirstWell><X>14.38</X><Y>11.24</Y><Z>0</Z></PositionOfFirstWell>
          <Cavity>
            <CavityShape xsi:type="TruncatedCone">
              <Height>11.26</Height>
              <DiameterTop>6.96</DiameterTop>
              <DiameterBottom>6.35</DiameterBottom>
            </CavityShape>
          </Cavity>
        </Pipettable>"""


def _component_xcmp(
    *,
    name: str,
    guid: str,
    functional_group: str,
    dim: tuple[float, float, float] = (127.76, 85.48, 14.35),
    grid: tuple[int, int] | None = (12, 8),
    site_template_guids: tuple[str, ...] = (),
) -> str:
    pipettable = ""
    if grid is not None:
        pipettable = _pipettable_block(sites_x=grid[0], sites_y=grid[1])

    site_ids = ""
    if site_template_guids:
        entries = "\n".join(
            f"""            <KeyValueOfintstring>
              <Key>{index}</Key>
              <Value>{site_guid}</Value>
            </KeyValueOfintstring>"""
            for index, site_guid in enumerate(site_template_guids)
        )
        site_ids = f"""            <SiteTemplateIdentifiers>
{entries}
            </SiteTemplateIdentifiers>
"""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:xsi="{_XSI}">
  <Payload>
    <ObjectName>{name}</ObjectName>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>{guid}</GUID>
        <Dimension><X>{dim[0]}</X><Y>{dim[1]}</Y><Z>{dim[2]}</Z></Dimension>
        <FunctionalGroup>{functional_group}</FunctionalGroup>
        <Arrangements>
          <ArrangementTemplate>
            <SitesInX>1</SitesInX>
            <SitesInY>1</SitesInY>
            <SitesInZ>1</SitesInZ>
            <SiteSpacingInX>0</SiteSpacingInX>
            <SiteSpacingInY>0</SiteSpacingInY>
            <SiteSpacingInZ>0</SiteSpacingInZ>
            <PositionInParent><X>0</X><Y>0</Y><Z>0</Z></PositionInParent>
{site_ids}          </ArrangementTemplate>
        </Arrangements>
{pipettable}
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


def _plate_xcmp() -> str:
    return _component_xcmp(
        name="96 Well Flat",
        guid=PLATE_GUID,
        functional_group="Labware.Microplate",
    )


def _trough_xcmp() -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:xsi="{_XSI}">
  <Payload>
    <ObjectName>100ml Trough 156mm</ObjectName>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>{TROUGH_GUID}</GUID>
        <Dimension><X>156.0</X><Y>85.0</Y><Z>44.0</Z></Dimension>
        <FunctionalGroup>Labware.Trough</FunctionalGroup>
        <Arrangements>
          <ArrangementTemplate>
            <SitesInX>1</SitesInX>
            <SitesInY>1</SitesInY>
            <SitesInZ>1</SitesInZ>
            <SiteSpacingInX>0</SiteSpacingInX>
            <SiteSpacingInY>0</SiteSpacingInY>
            <SiteSpacingInZ>0</SiteSpacingInZ>
            <PositionInParent><X>0</X><Y>0</Y><Z>0</Z></PositionInParent>
          </ArrangementTemplate>
        </Arrangements>
        <Pipettable>
          <XNumberOfWells>1</XNumberOfWells>
          <YNumberOfWells>1</YNumberOfWells>
          <XSpacing>0</XSpacing>
          <YSpacing>0</YSpacing>
          <PositionOfFirstWell><X>78.0</X><Y>42.5</Y><Z>0</Z></PositionOfFirstWell>
          <Cavity>
            <CavityShape xsi:type="Cylinder">
              <Height>40.0</Height>
              <Diameter>70.0</Diameter>
            </CavityShape>
          </Cavity>
        </Pipettable>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


def _fca_tip_xcmp(*, guid: str, name: str) -> str:
    return _component_xcmp(
        name=name,
        guid=guid,
        functional_group="Labware.FCA DiTi",
        dim=(127.76, 85.48, 55.0),
    )


def _mca96_tip_xcmp() -> str:
    return _component_xcmp(
        name="MCA96, 100ul, Box",
        guid=MCA96_TIP_GUID,
        functional_group="Labware.MCA96 DiTi",
        dim=(127.76, 85.48, 55.0),
    )


def _magnet_plate_xcmp() -> str:
    return _component_xcmp(
        name="24 Magnet Plate",
        guid=MAGNET_PLATE_GUID,
        functional_group="Labware.Microplate",
    )


def _trough_carrier_xcmp() -> str:
    return _component_xcmp(
        name="6_Trough_Carrier_Reservoir",
        guid=TROUGH_CARRIER_GUID,
        functional_group="Carrier.Grid Segment",
        dim=(156.0, 85.0, 44.0),
        grid=None,
        site_template_guids=TROUGH_SITE_GUIDS,
    )


def _xsit(*, guid: str, location_group_name: str, type_name: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <PayloadData>
      <SiteTemplate>
        <GUID>{guid}</GUID>
        <LocationGroupName>{location_group_name}</LocationGroupName>
        <TypeName>{type_name}</TypeName>
      </SiteTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


def _nest_xsit() -> str:
    return _xsit(
        guid=NEST_SITE_GUID,
        location_group_name="Nest61mm_Pos",
        type_name="Nest61mm",
    )


def _nest7mm_xsit() -> str:
    return _xsit(
        guid=NEST7MM_SITE_GUID,
        location_group_name="Nest7mm_Pos",
        type_name="Nest7mm",
    )


def _workspace_xwsp(
    *,
    name: str,
    site_count: int,
    site_guid: str | None = None,
    base_worktable_guid: str | None = None,
    base_worktable_name: str | None = None,
) -> str:
    site_blocks = "\n".join(
        f"""                    <KeyValueOfintSite>
                      <Key>{key}</Key>
                      <Value>
                        {f"<BaseTemplateGuid>{site_guid}</BaseTemplateGuid>" if site_guid else ""}
                      </Value>
                    </KeyValueOfintSite>"""
        for key in range(site_count)
    )
    base_ref = ""
    if base_worktable_guid and base_worktable_name:
        base_ref = f"""    <Reference>
      <Guid>{base_worktable_guid}</Guid>
      <TypeId>WorktableComponent</TypeId>
      <ObjectName>{base_worktable_name}</ObjectName>
    </Reference>
"""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{name}</ObjectName>
{base_ref}    <PayloadData>
      <Worktables>
        <KeyValueOfstringWorktable>
          <Value>
            <Frame>
              <Arrangements>
                <Arrangement>
                  <Sites>
{site_blocks}
                  </Sites>
                </Arrangement>
              </Arrangements>
            </Frame>
          </Value>
        </KeyValueOfstringWorktable>
      </Worktables>
    </PayloadData>
  </Payload>
</VxData>
"""


def _xlqc(*, guid: str, name: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{name}</ObjectName>
    <PayloadData>
      <PipettingDeviceType>Fca</PipettingDeviceType>
      <PipettingDeviceType>Mca96</PipettingDeviceType>
    </PayloadData>
  </Payload>
</VxData>
"""


def write_fixture_tree() -> None:
    COMPONENTS.mkdir(parents=True, exist_ok=True)
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    SITES.mkdir(parents=True, exist_ok=True)
    LIQUID_CLASSES.mkdir(parents=True, exist_ok=True)

    (COMPONENTS / f"{PLATE_GUID}.xcmp").write_text(_plate_xcmp(), encoding="utf-8")
    (COMPONENTS / f"{FCA_GUID}.xcmp").write_text(
        _fca_tip_xcmp(guid=FCA_GUID, name="FCA, 1000ul"),
        encoding="utf-8",
    )
    (COMPONENTS / f"{FCA_SBS_GUID}.xcmp").write_text(
        _fca_tip_xcmp(guid=FCA_SBS_GUID, name="FCA, 1000ul SBS"),
        encoding="utf-8",
    )
    (COMPONENTS / f"{TROUGH_GUID}.xcmp").write_text(_trough_xcmp(), encoding="utf-8")
    (COMPONENTS / f"{MCA96_TIP_GUID}.xcmp").write_text(_mca96_tip_xcmp(), encoding="utf-8")
    (COMPONENTS / f"{MAGNET_PLATE_GUID}.xcmp").write_text(_magnet_plate_xcmp(), encoding="utf-8")
    (COMPONENTS / f"{TROUGH_CARRIER_GUID}.xcmp").write_text(
        _trough_carrier_xcmp(),
        encoding="utf-8",
    )

    for name, functional_group, grid in INFERENCE_COMPONENTS:
        dim = (127.76, 85.48, 14.35)
        if name == "Teleshake Adapter Plate":
            dim = (127.76, 85.48, 15.0)
        if functional_group == "Labware.Trough":
            dim = (80.0, 40.0, 30.0)
        (COMPONENTS / f"{_guid_for(name)}.xcmp").write_text(
            _component_xcmp(
                name=name,
                guid=_guid_for(name),
                functional_group=functional_group,
                dim=dim,
                grid=grid,
            ),
            encoding="utf-8",
        )

    (SITES / f"{NEST_SITE_GUID}.xsit").write_text(_nest_xsit(), encoding="utf-8")
    (SITES / f"{NEST7MM_SITE_GUID}.xsit").write_text(_nest7mm_xsit(), encoding="utf-8")
    for index, site_guid in enumerate(TROUGH_SITE_GUIDS, start=1):
        (SITES / f"{site_guid}.xsit").write_text(
            _xsit(
                guid=site_guid,
                location_group_name=f"TroughMP_{index}",
                type_name="TroughMP",
            ),
            encoding="utf-8",
        )

    (WORKSPACES / f"{EMPTY_780_WORKSPACE_GUID}.xwsp").write_text(
        _workspace_xwsp(name="780_Empty", site_count=EMPTY_780_SITE_COUNT),
        encoding="utf-8",
    )
    (WORKSPACES / f"{SAT_WORKSPACE_GUID}.xwsp").write_text(
        _workspace_xwsp(
            name="SAT_Fluent_780_Rev3",
            site_count=6,
            site_guid=NEST_SITE_GUID,
            base_worktable_guid=EMPTY_780_WORKSPACE_GUID,
            base_worktable_name="780 Base Unit",
        ),
        encoding="utf-8",
    )
    (WORKSPACES / f"{SAT_REV4_GUID}.xwsp").write_text(
        _workspace_xwsp(
            name="SAT_Fluent_780_Rev4",
            site_count=6,
            site_guid=NEST7MM_SITE_GUID,
            base_worktable_guid=EMPTY_780_WORKSPACE_GUID,
            base_worktable_name="780 Base Unit",
        ),
        encoding="utf-8",
    )

    (LIQUID_CLASSES / f"{WATER_FREE_SINGLE_GUID}.xlqc").write_text(
        _xlqc(guid=WATER_FREE_SINGLE_GUID, name="Water Free Single"),
        encoding="utf-8",
    )
    for index in range(2, LIQUID_CLASS_COUNT + 1):
        guid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"synthetic-liquid-class:{index}"))
        (LIQUID_CLASSES / f"{guid}.xlqc").write_text(
            _xlqc(guid=guid, name=f"Synthetic Liquid Class {index:02d}"),
            encoding="utf-8",
        )


def _index_is_current() -> bool:
    if not INDEX_DB.exists():
        return False
    try:
        import sqlite3

        conn = sqlite3.connect(str(INDEX_DB))
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM components").fetchone()
            liquid_row = conn.execute("SELECT COUNT(*) AS n FROM liquid_classes").fetchone()
            workspace_row = conn.execute(
                "SELECT file_path FROM workspaces WHERE guid = ?",
                (SAT_WORKSPACE_GUID,),
            ).fetchone()
            expected_workspace = (WORKSPACES / f"{SAT_WORKSPACE_GUID}.xwsp").resolve()
            return bool(
                row
                and row[0] >= EXPECTED_COMPONENT_COUNT
                and liquid_row
                and liquid_row[0] >= LIQUID_CLASS_COUNT
                and workspace_row
                and Path(workspace_row[0]).resolve() == expected_workspace
                and expected_workspace.exists()
            )
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def build_synthetic_index(*, force: bool = False) -> Path:
    write_fixture_tree()
    if not force and _index_is_current():
        return INDEX_DB
    counts = build_index(install_path=INSTALL_ROOT, db_path=INDEX_DB)
    if counts.get("components", 0) < EXPECTED_COMPONENT_COUNT:
        raise RuntimeError(f"synthetic catalog index build produced unexpected counts: {counts}")
    if counts.get("liquid_classes", 0) < LIQUID_CLASS_COUNT:
        raise RuntimeError(f"synthetic liquid-class index build produced unexpected counts: {counts}")
    return INDEX_DB


def main() -> None:
    path = build_synthetic_index(force=True)
    print(f"Wrote synthetic catalog index: {path}")


if __name__ == "__main__":
    main()
