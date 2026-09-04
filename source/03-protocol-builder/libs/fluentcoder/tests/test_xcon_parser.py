"""Tests for ``.xcon`` connector parsing."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.catalog.xcon import load_xcon, parse_connector  # noqa: E402
from fluentcoder.catalog.xcmp import XcmpArrangement, XcmpComponent, XwspWorkspace  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sample_connector.xcon"

CONNECTOR_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CONNECTOR_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CONNECTOR_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
COMPONENT_GUID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
SITE_GUID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


def _minimal_xcon(
    *,
    guid: str,
    name: str,
    component_guid: str = COMPONENT_GUID,
    site_guid: str = SITE_GUID,
    is_default: bool = False,
) -> str:
    default_tag = "true" if is_default else "false"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData">
  <Payload>
    <ObjectName>{name}</ObjectName>
    <PayloadData>
      <WorktableConnector>
        <ConnectorTemplate xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Worktable.Core">
          <ComponentGuid>{component_guid}</ComponentGuid>
          <GUID>{guid}</GUID>
          <IsDefaultConnector>{default_tag}</IsDefaultConnector>
          <SiteGuid>{site_guid}</SiteGuid>
        </ConnectorTemplate>
      </WorktableConnector>
    </PayloadData>
  </Payload>
</sd:VxData>
"""


def _make_connector_install(root: Path) -> Path:
    install = root / "install"
    components = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites = install / "SystemSpecific" / "Worktable" / "Sites"
    connectors = install / "SystemSpecific" / "Worktable" / "Connectors"
    for directory in (components, workspaces, sites, connectors):
        directory.mkdir(parents=True)

    (components / "deck.xcmp").write_text("<Component/>", encoding="utf-8")
    (workspaces / "layout.xwsp").write_text("<Workspace/>", encoding="utf-8")
    (sites / f"{SITE_GUID}.xsit").write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <Reference>
      <TypeId>WorktableConnector</TypeId>
      <Guid>{CONNECTOR_A}</Guid>
    </Reference>
    <PayloadData>
      <SiteTemplate>
        <GUID>{SITE_GUID}</GUID>
        <LocationGroupName>Test_Pos</LocationGroupName>
        <TypeName>TestSite</TypeName>
      </SiteTemplate>
    </PayloadData>
  </Payload>
</VxData>
""",
        encoding="utf-8",
    )
    (connectors / f"{CONNECTOR_A}.xcon").write_text(
        _minimal_xcon(guid=CONNECTOR_A, name="Connector Alpha"),
        encoding="utf-8",
    )
    (connectors / f"{CONNECTOR_B}.xcon").write_text(
        _minimal_xcon(guid=CONNECTOR_B, name="Connector Beta"),
        encoding="utf-8",
    )
    (connectors / f"{CONNECTOR_C}.xcon").write_text(
        _minimal_xcon(guid=CONNECTOR_C, name="Connector Gamma"),
        encoding="utf-8",
    )
    return install


def _arrangement() -> XcmpArrangement:
    return XcmpArrangement(
        sites_in_x=1,
        sites_in_y=1,
        sites_in_z=1,
        site_spacing_mm=(0.0, 0.0, 0.0),
        position_in_parent_mm=(0.0, 0.0, 0.0),
    )


def _fake_component(path: Path) -> XcmpComponent:
    return XcmpComponent(
        guid=f"{path.stem}-guid",
        name=path.stem.title(),
        file_path=path,
        arrangement=_arrangement(),
    )


def _fake_workspace(path: Path) -> XwspWorkspace:
    return XwspWorkspace(guid=f"{path.stem}-guid", name=path.stem.title(), file_path=path)


def test_parse_connector_fixture() -> None:
    conn = parse_connector(FIXTURE)
    assert conn.guid == "1931bff4-102c-4934-949e-6f974fa50e53"
    assert conn.component_guid == "326ece41-56eb-4264-9577-3c87c7158a57"
    assert conn.site_guid == "c0a22689-32b3-4ff6-a6d2-79807846b757"
    assert conn.is_default is True
    assert "15 Microplate Passive Stack" in conn.name
    assert conn.description is not None
    assert "15 Microplate Passive Stack" in conn.description


def test_load_xcon_alias_matches_parse_connector() -> None:
    assert load_xcon(FIXTURE) == parse_connector(FIXTURE)


def test_index_connector_paths(tmp_path: Path) -> None:
    from fluentcoder.catalog import index_connector_paths, resolve_connector_by_guid

    db_path = tmp_path / "index.db"
    indexed = index_connector_paths([FIXTURE], db_path=db_path)
    assert indexed == 1

    entry = resolve_connector_by_guid(
        "1931bff4-102c-4934-949e-6f974fa50e53",
        db_path=db_path,
    )
    assert entry is not None
    assert entry.component_guid == "326ece41-56eb-4264-9577-3c87c7158a57"
    assert entry.site_guid == "c0a22689-32b3-4ff6-a6d2-79807846b757"


def test_parse_connector_missing_payload_raises() -> None:
    bad = FIXTURE.parent / "_bad_connector.xcon"
    bad.write_text("<root></root>", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="no <Payload>"):
            parse_connector(bad)
    finally:
        bad.unlink(missing_ok=True)


def test_build_index_default_indexes_only_site_referenced_connectors(tmp_path: Path) -> None:
    from fluentcoder.catalog.indexer import build_index

    install = _make_connector_install(tmp_path)
    db_path = tmp_path / "index.db"
    with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=_fake_component), mock.patch(
        "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
    ):
        counts = build_index(install_path=install, db_path=db_path)

    assert counts["connectors"] == 1


def test_build_index_include_all_connectors_indexes_every_xcon(tmp_path: Path) -> None:
    from fluentcoder.catalog import resolve_connector_by_guid, resolve_connector_by_name
    from fluentcoder.catalog.indexer import build_index

    install = _make_connector_install(tmp_path)
    db_path = tmp_path / "index.db"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("FLUENTCODER_INDEX_ALL_CONNECTORS", raising=False)
    try:
        with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=_fake_component), mock.patch(
            "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
        ):
            counts = build_index(
                install_path=install,
                db_path=db_path,
                include_all_connectors=True,
            )
    finally:
        monkeypatch.undo()

    assert counts["connectors"] == 3

    alpha = resolve_connector_by_name("Connector Alpha", db_path=db_path)
    beta = resolve_connector_by_name("Connector Beta", db_path=db_path)
    gamma = resolve_connector_by_guid(CONNECTOR_C, db_path=db_path)
    assert alpha is not None and alpha.guid == CONNECTOR_A
    assert beta is not None and beta.guid == CONNECTOR_B
    assert gamma is not None and gamma.name == "Connector Gamma"


def test_build_index_honors_index_all_connectors_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fluentcoder.catalog.indexer import build_index

    monkeypatch.setenv("FLUENTCODER_INDEX_ALL_CONNECTORS", "1")
    install = _make_connector_install(tmp_path)
    db_path = tmp_path / "index.db"
    with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=_fake_component), mock.patch(
        "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
    ):
        counts = build_index(install_path=install, db_path=db_path)

    assert counts["connectors"] == 3

