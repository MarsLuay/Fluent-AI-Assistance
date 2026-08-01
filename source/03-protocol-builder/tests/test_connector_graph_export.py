"""Tests for ZEIA worktable_geometry → connector_graph export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.connector_graph_export import (
    build_connector_graph_from_geometry,
    write_connector_graph,
)


class ConnectorGraphExportTests(unittest.TestCase):
    def test_builds_snap_edges_from_geometry(self) -> None:
        parent = "11111111-1111-4111-8111-111111111111"
        child = "22222222-2222-4222-8222-222222222222"
        site = "33333333-3333-4333-8333-333333333333"
        connector = "44444444-4444-4444-8444-444444444444"
        geometry = {
            "components": [
                {
                    "guid": parent,
                    "name": "Demo_CapHolder",
                    "site_guids": [site],
                },
                {
                    "guid": child,
                    "name": "Demo_Tube_Runner",
                },
            ],
            "sites": [
                {
                    "guid": site,
                    "location_group_name": "Demo_Cap_Nest",
                    "type_name": "Demo_CapHolder_Nest",
                    "dimension_mm": {"x": 10, "y": 20, "z": 30},
                    "connector_guids": [connector],
                }
            ],
            "connectors": [
                {
                    "guid": connector,
                    "component_guid": child,
                    "site_guid": site,
                    "component_name": "Demo_Tube_Runner",
                    "is_default": True,
                    "position_in_parent_mm": {"x": 1.5, "y": 2.5, "z": 3.5},
                }
            ],
        }
        graph = build_connector_graph_from_geometry(geometry)
        assert graph is not None
        self.assertEqual(graph["kind"], "fluent-connector-graph")
        self.assertEqual(graph["sources"]["installSourceType"], "zeia")
        self.assertEqual(graph["summary"]["connectorCount"], 1)
        self.assertEqual(graph["connectors"][0]["childComponentGuid"], child)
        anchors = graph["snapAnchorsByComponent"][parent]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["siteGuid"], site)
        self.assertEqual(anchors[0]["compatibleChildGuids"], [child])
        self.assertEqual(anchors[0]["snapPoint"]["positionMm"], [1.5, 2.5, 3.5])
        by_id = {row["id"]: row for row in graph["verification"]}
        # Coverage is mined from geometry site parents — not soft CapHolder family invent.
        self.assertIn("demo_capholder", by_id)
        self.assertEqual(by_id["demo_capholder"]["matchedComponentGuids"], [parent])
        self.assertEqual(by_id["demo_capholder"]["actualCount"], 1)

    def test_empty_geometry_returns_none(self) -> None:
        self.assertIsNone(build_connector_graph_from_geometry({}))
        self.assertIsNone(build_connector_graph_from_geometry({"components": [{"name": "x"}]}))

    def test_write_skips_stub_source_type_for_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_connector_graph(Path(tmp) / "connector_graph.json", {})
            self.assertIsNone(path)

    def test_write_round_trip(self) -> None:
        geometry = {
            "components": [
                {
                    "guid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "name": "Demo_Device",
                    "site_guids": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],
                }
            ],
            "sites": [
                {
                    "guid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "location_group_name": "Demo_Device_Pos",
                }
            ],
            "connectors": [
                {
                    "guid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    "component_guid": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    "site_guid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = write_connector_graph(Path(tmp) / "connector_graph.json", geometry)
            assert path is not None
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], "fluent-connector-graph")
            self.assertEqual(payload["summary"]["connectorCount"], 1)

    def test_datastore_snap_walk_beats_empty_geometry(self) -> None:
        """Large ZEIA skips detailed geometry — still emit full Connectors/*.xcon edges."""
        from fluent_pipeline.connector_graph_export import (
            build_connector_graph_for_package,
            build_connector_graph_from_datastore,
        )

        parent = "11111111-1111-4111-8111-111111111111"
        child = "22222222-2222-4222-8222-222222222222"
        site = "33333333-3333-4333-8333-333333333333"
        connector = "44444444-4444-4444-8444-444444444444"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktable = root / "SystemSpecific" / "Worktable"
            (worktable / "Connectors").mkdir(parents=True)
            (worktable / "Components").mkdir(parents=True)
            (worktable / "Sites").mkdir(parents=True)
            (worktable / "Connectors" / f"{connector}.xcon").write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>DemoSnap</ObjectName>
    <PayloadData>
      <ConnectorTemplate>
        <GUID>{connector}</GUID>
        <ComponentGuid>{child}</ComponentGuid>
        <SiteGuid>{site}</SiteGuid>
        <IsDefaultConnector>true</IsDefaultConnector>
        <PositionInParent><X>1</X><Y>2</Y><Z>3</Z></PositionInParent>
      </ConnectorTemplate>
    </PayloadData>
  </Payload>
</VxData>
""",
                encoding="utf-8",
            )
            (worktable / "Components" / f"{parent}.xcmp").write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Demo_Parent</ObjectName>
    <Reference>
      <TypeId>WorktableSite</TypeId>
      <Guid>{site}</Guid>
      <ObjectName>{site}</ObjectName>
    </Reference>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>{parent}</GUID>
        <Dimension><X>10</X><Y>20</Y><Z>30</Z></Dimension>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
""",
                encoding="utf-8",
            )
            (worktable / "Sites" / f"{site}.xsit").write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{site}</ObjectName>
    <PayloadData>
      <SiteTemplate>
        <GUID>{site}</GUID>
        <LocationGroupName>Demo_Nest</LocationGroupName>
        <TypeName>NestPlatform</TypeName>
        <Dimension><X>4</X><Y>5</Y><Z>6</Z></Dimension>
      </SiteTemplate>
    </PayloadData>
  </Payload>
</VxData>
""",
                encoding="utf-8",
            )

            skipped = {
                "schema_version": "tecan.worktable_geometry.v1",
                "component_count": 0,
                "site_count": 0,
                "connector_count": 0,
                "components": [],
                "sites": [],
                "connectors": [],
                "errors": [{"reason": "large_export_geometry_skipped"}],
            }
            graph = build_connector_graph_for_package(skipped, datastore_root=root)
            assert graph is not None
            self.assertEqual(graph["summary"]["connectorCount"], 1)
            self.assertTrue(graph["sources"]["includeAllConnectors"])
            self.assertEqual(graph["connectors"][0]["guid"], connector)
            self.assertEqual(graph["connectors"][0]["childComponentGuid"], child)
            anchors = graph["snapAnchorsByComponent"][parent]
            self.assertEqual(anchors[0]["siteGuid"], site)
            self.assertEqual(anchors[0]["compatibleChildGuids"], [child])
            self.assertEqual(anchors[0]["locationGroupName"], "Demo_Nest")

            richer = build_connector_graph_from_datastore(root)
            assert richer is not None
            self.assertGreater(richer["summary"]["connectorCount"], 0)

    def test_select_richer_prefers_more_edges(self) -> None:
        from fluent_pipeline.connector_graph_export import select_richer_connector_graph

        left = {"summary": {"connectorCount": 2}, "connectors": [1, 2]}
        right = {"summary": {"connectorCount": 5}, "connectors": [1, 2, 3, 4, 5]}
        chosen = select_richer_connector_graph(left, right)
        assert chosen is not None
        self.assertEqual(chosen["summary"]["connectorCount"], 5)


if __name__ == "__main__":
    unittest.main()
