"""Regression coverage for source-backed RGA/carrier capability mining."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.worktable_geometry import parse_component


RGA_XCMP = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Source-backed carrier</ObjectName>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa</GUID>
        <RobotVectors>
          <RobotVector>
            <GUID>vector-guid-1</GUID>
            <Name>source-vector</Name>
            <RobotIdentifier>rga-1</RobotIdentifier>
            <SafePosition><X>1</X><Y>2</Y><Z>3</Z></SafePosition>
            <StartPosition><X>4</X><Y>5</Y><Z>6</Z></StartPosition>
            <EndPosition><X>7</X><Y>8</Y><Z>9</Z></EndPosition>
            <IntermediateWaypoints>
              <Waypoint><X>10</X><Y>11</Y><Z>12</Z></Waypoint>
            </IntermediateWaypoints>
            <VendorSpecific><UnmodeledField>retain-me</UnmodeledField></VendorSpecific>
          </RobotVector>
        </RobotVectors>
        <RegripStations>
          <RegripStation>
            <GUID>regrip-guid-1</GUID>
            <Name>source-regrip</Name>
          </RegripStation>
        </RegripStations>
        <Arrangements>
          <ArrangementTemplate>
            <SitesInX>1</SitesInX><SitesInY>1</SitesInY><SitesInZ>1</SitesInZ>
            <SiteTemplateIdentifiers>
              <KeyValueOfintguid><Key>0</Key><Value>site-guid-1</Value></KeyValueOfintguid>
            </SiteTemplateIdentifiers>
            <AllowedVectors>
              <KeyValueOfintArrayOfstring>
                <Key>0</Key><Value><string>vector-guid-1</string></Value>
              </KeyValueOfintArrayOfstring>
            </AllowedVectors>
            <AllowedGripModes>
              <KeyValueOfintArrayOfstring>
                <Key>0</Key><Value><KeyValueOfstringstring><Key>Narrow</Key><Value>true</Value></KeyValueOfstringstring></Value>
              </KeyValueOfintArrayOfstring>
            </AllowedGripModes>
          </ArrangementTemplate>
        </Arrangements>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


NO_VECTOR_XCMP = """<VxData><Payload><ObjectName>No vector evidence</ObjectName><PayloadData>
<CarrierOrLabwareTemplate><GUID>bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb</GUID>
<Arrangements><ArrangementTemplate><SitesInX>1</SitesInX><SitesInY>1</SitesInY><SitesInZ>1</SitesInZ></ArrangementTemplate></Arrangements>
</CarrierOrLabwareTemplate></PayloadData></Payload></VxData>"""


EMPTY_VECTOR_XCMP = """<VxData><Payload><ObjectName>Empty vector evidence</ObjectName><PayloadData>
<CarrierOrLabwareTemplate><GUID>cccccccc-cccc-cccc-cccc-cccccccccccc</GUID>
<RobotVectors/><Arrangements><ArrangementTemplate><SitesInX>1</SitesInX><SitesInY>1</SitesInY><SitesInZ>1</SitesInZ></ArrangementTemplate></Arrangements>
</CarrierOrLabwareTemplate></PayloadData></Payload></VxData>"""


VERSIONED_TAG_VARIANTS_XCMP = """<?xml version="1.0" encoding="utf-8"?>
<VxData dataStoreVersion="3">
  <Payload>
    <ObjectName>Versioned tag variants carrier</ObjectName>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>dddddddd-dddd-dddd-dddd-dddddddddddd</GUID>
        <RgaRobotVectors>
          <RgaRobotVector>
            <GUID>vector-v3-1</GUID>
            <Name>v3-vector</Name>
            <DeviceId>RGA 1</DeviceId>
            <SafePoint><X>10.5</X><Y>20.5</Y><Z>30.5</Z></SafePoint>
            <StartPoint><X>40.0</X><Y>50.0</Y><Z>60.0</Z></StartPoint>
            <EndPoint><X>70.0</X><Y>80.0</Y><Z>90.0</Z></EndPoint>
            <Waypoints>
              <Position><X>15.0</X><Y>25.0</Y><Z>35.0</Z></Position>
            </Waypoints>
            <VendorCustomTag><CustomData attribute="preserve">test-custom</CustomData></VendorCustomTag>
          </RgaRobotVector>
        </RgaRobotVectors>
        <RegripSites>
          <RegripSite>
            <GUID>regrip-site-v3</GUID>
            <Name>v3-regrip</Name>
          </RegripSite>
        </RegripSites>
        <Arrangements>
          <ArrangementTemplate>
            <SitesInX>1</SitesInX><SitesInY>1</SitesInY><SitesInZ>1</SitesInZ>
            <SiteTemplateIdentifiers>
              <KeyValueOfintguid><Key>0</Key><Value>site-guid-v3</Value></KeyValueOfintguid>
            </SiteTemplateIdentifiers>
            <AllowedVectorIds>
              <KeyValueOfintArrayOfstring>
                <Key>0</Key><Value><string>vector-v3-1</string></Value>
              </KeyValueOfintArrayOfstring>
            </AllowedVectorIds>
            <AllowedGripModes>
              <KeyValueOfintArrayOfstring>
                <Key>0</Key><Value><KeyValueOfstringstring><Key>Wide</Key><Value>true</Value></KeyValueOfstringstring></Value>
              </KeyValueOfintArrayOfstring>
            </AllowedGripModes>
          </ArrangementTemplate>
        </Arrangements>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


STORAGE_CARRIER_XCMP = """<?xml version="1.0" encoding="utf-8"?>
<VxData dataStoreVersion="2">
  <Payload>
    <ObjectName>Storage Hotel Carrier</ObjectName>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee</GUID>
        <StorageCarrier>
          <StoragePosition>
            <GUID>hotel-slot-1</GUID>
            <Name>Slot 1</Name>
            <Order>0</Order>
            <SiteIndex>1</SiteIndex>
            <GroupId>HotelGroupA</GroupId>
            <TransferSiteId>xfer-site-1</TransferSiteId>
          </StoragePosition>
          <StoragePosition>
            <GUID>hotel-slot-2</GUID>
            <Name>Slot 2</Name>
            <Order>1</Order>
            <SiteIndex>2</SiteIndex>
            <GroupId>HotelGroupA</GroupId>
            <TransferSiteId>xfer-site-1</TransferSiteId>
          </StoragePosition>
        </StorageCarrier>
        <Arrangements>
          <ArrangementTemplate>
            <SitesInX>1</SitesInX><SitesInY>2</SitesInY><SitesInZ>1</SitesInZ>
          </ArrangementTemplate>
        </Arrangements>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


MALFORMED_COORDINATES_XCMP = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Malformed Vector Carrier</ObjectName>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>ffffffff-ffff-ffff-ffff-ffffffffffff</GUID>
        <RobotVectors>
          <RobotVector>
            <GUID>vector-corrupt</GUID>
            <Name>corrupt-vector</Name>
            <SafePosition><X>not-a-number</X><Y>2</Y><Z>3</Z></SafePosition>
          </RobotVector>
        </RobotVectors>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


class WorktableGeometryRgaRoutingTests(unittest.TestCase):
    def _parse(self, name: str, text: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_text(text, encoding="utf-8")
            return parse_component(path)

    def test_mines_vectors_site_capabilities_and_provenance_without_inference(self):
        component = self._parse("carrier.xcmp", RGA_XCMP)
        routing = component["rga_routing"]

        self.assertEqual(routing["schema_version"], "tecan.rga_routing.v1")
        self.assertEqual(routing["evidence_status"], "present")
        self.assertEqual(routing["vectors"][0]["id"], "vector-guid-1")
        self.assertEqual(routing["vectors"][0]["robot_or_device"], "rga-1")
        self.assertEqual(routing["vectors"][0]["safe_position"], {"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertEqual(routing["vectors"][0]["waypoints"], [{"x": 10.0, "y": 11.0, "z": 12.0}])
        self.assertIn("retain-me", routing["vectors"][0]["raw_xml"])
        self.assertEqual(routing["site_capabilities"][0]["allowed_vector_ids"], ["vector-guid-1"])
        self.assertEqual(routing["site_capabilities"][0]["allowed_grip_modes"], ["Narrow"])
        self.assertTrue(routing["fingerprint"])
        self.assertTrue(routing["provenance"]["source_path"].endswith("carrier.xcmp"))

    def test_missing_and_explicitly_empty_vector_evidence_are_distinct(self):
        missing = self._parse("missing.xcmp", NO_VECTOR_XCMP)["rga_routing"]
        empty = self._parse("empty.xcmp", EMPTY_VECTOR_XCMP)["rga_routing"]

        self.assertEqual(missing["evidence_status"], "unknown")
        self.assertEqual(empty["evidence_status"], "present_empty")
        self.assertNotEqual(missing["fingerprint"], empty["fingerprint"])

    def test_repeated_parse_has_deterministic_fingerprint(self):
        first = self._parse("carrier.xcmp", RGA_XCMP)["rga_routing"]
        second = self._parse("carrier.xcmp", RGA_XCMP)["rga_routing"]
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_versioned_carrier_formats_and_tag_variants(self):
        component = self._parse("versioned.xcmp", VERSIONED_TAG_VARIANTS_XCMP)
        routing = component["rga_routing"]

        self.assertEqual(routing["evidence_status"], "present")
        self.assertEqual(len(routing["vectors"]), 1)
        vector = routing["vectors"][0]
        self.assertEqual(vector["id"], "vector-v3-1")
        self.assertEqual(vector["robot_or_device"], "RGA 1")
        self.assertEqual(vector["safe_position"], {"x": 10.5, "y": 20.5, "z": 30.5})
        self.assertEqual(vector["start_position"], {"x": 40.0, "y": 50.0, "z": 60.0})
        self.assertEqual(vector["end_position"], {"x": 70.0, "y": 80.0, "z": 90.0})
        self.assertEqual(vector["waypoints"], [{"x": 15.0, "y": 25.0, "z": 35.0}])
        self.assertIn("test-custom", vector["raw_xml"])

        self.assertEqual(len(routing["regrip_stations"]), 1)
        self.assertEqual(routing["regrip_stations"][0]["id"], "regrip-site-v3")

        site = routing["site_capabilities"][0]
        self.assertEqual(site["allowed_vector_ids"], ["vector-v3-1"])
        self.assertEqual(site["allowed_grip_modes"], ["Wide"])

    def test_storage_carrier_indexed_sites_and_order_metadata(self):
        from fluent_pipeline.rga_topology import normalize_rga_topology

        component = self._parse("storage.xcmp", STORAGE_CARRIER_XCMP)
        routing = component["rga_routing"]

        self.assertEqual(routing["evidence_status"], "present")
        self.assertEqual(len(routing["storage_sites"]), 2)
        s1, s2 = routing["storage_sites"]
        self.assertEqual(s1["id"], "hotel-slot-1")
        self.assertEqual(s1["order"], 0)
        self.assertEqual(s1["site_index"], 1)
        self.assertEqual(s1["group_id"], "HotelGroupA")
        self.assertEqual(s1["transfer_site_id"], "xfer-site-1")

        self.assertEqual(s2["id"], "hotel-slot-2")
        self.assertEqual(s2["order"], 1)
        self.assertEqual(s2["site_index"], 2)

        topology = normalize_rga_topology(
            {
                "guid": component["guid"],
                "carrier_type": "storage_hotel",
                "storage_sites": routing["storage_sites"],
            }
        )
        self.assertEqual(topology["status"], "verified")
        self.assertEqual(topology["storage_order_status"], "verified")

    def test_malformed_vector_coordinates_fail_closed_safely(self):
        component = self._parse("malformed.xcmp", MALFORMED_COORDINATES_XCMP)
        routing = component["rga_routing"]

        self.assertEqual(routing["evidence_status"], "present")
        self.assertEqual(len(routing["vectors"]), 1)
        vector = routing["vectors"][0]
        self.assertEqual(vector["id"], "vector-corrupt")
        self.assertNotIn("safe_position", vector)


if __name__ == "__main__":
    unittest.main()
