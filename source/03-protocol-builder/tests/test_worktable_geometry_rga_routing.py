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


if __name__ == "__main__":
    unittest.main()
