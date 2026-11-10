"""Tests for pipettable / grip / site-template / compatible mining."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tecan_common import native_acceleration

from fluent_pipeline import worktable_geometry
from fluent_pipeline.labware_catalog_export import build_labware_catalog_from_geometry
from fluent_pipeline.worktable_geometry import parse_component


RICH_XCMP = """<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Payload>
    <ObjectName>Rich Plate</ObjectName>
    <Reference>
      <TypeId>WorktableMesh</TypeId>
      <Guid>11111111-1111-1111-1111-111111111111</Guid>
      <ObjectName>PlateMesh</ObjectName>
    </Reference>
    <Reference>
      <TypeId>WorktableComponent</TypeId>
      <Guid>22222222-2222-2222-2222-222222222222</Guid>
      <ObjectName>Lid Companion</ObjectName>
    </Reference>
    <Reference>
      <TypeId>WorktableSite</TypeId>
      <Guid>33333333-3333-3333-3333-333333333333</Guid>
      <ObjectName>33333333-3333-3333-3333-333333333333</ObjectName>
    </Reference>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa</GUID>
        <Dimension><X>127.8</X><Y>85.5</Y><Z>14.4</Z></Dimension>
        <FunctionalGroup>Labware.Microplate</FunctionalGroup>
        <FootPrint>Microplate</FootPrint>
        <Arrangements>
          <ArrangementTemplate>
            <SitesInX>1</SitesInX>
            <SitesInY>1</SitesInY>
            <SitesInZ>1</SitesInZ>
            <SiteSpacingInX>0</SiteSpacingInX>
            <SiteSpacingInY>0</SiteSpacingInY>
            <SiteSpacingInZ>0</SiteSpacingInZ>
            <PositionInParent><X>0</X><Y>0</Y><Z>0</Z></PositionInParent>
            <SiteTemplateIdentifiers>
              <KeyValueOfintguid>
                <Key>0</Key>
                <Value>33333333-3333-3333-3333-333333333333</Value>
              </KeyValueOfintguid>
            </SiteTemplateIdentifiers>
            <AllowedGripModes>
              <KeyValueOfintArrayOfstring>
                <Key>0</Key>
                <Value>
                  <KeyValueOfstringstring>
                    <Key>Narrow</Key>
                    <Value>true</Value>
                  </KeyValueOfstringstring>
                  <KeyValueOfstringstring>
                    <Key>Wide</Key>
                    <Value>true</Value>
                  </KeyValueOfstringstring>
                </Value>
              </KeyValueOfintArrayOfstring>
            </AllowedGripModes>
          </ArrangementTemplate>
        </Arrangements>
        <Pipettable>
          <XNumberOfWells>12</XNumberOfWells>
          <YNumberOfWells>8</YNumberOfWells>
          <XSpacing>9</XSpacing>
          <YSpacing>-9</YSpacing>
          <PositionOfFirstWell><X>14.4</X><Y>11.2</Y><Z>0</Z></PositionOfFirstWell>
          <Cavity>
            <CavityShape xsi:type="TruncatedCone">
              <Height>10.9</Height>
              <DiameterBottom>6.58</DiameterBottom>
              <DiameterTop>6.96</DiameterTop>
            </CavityShape>
          </Cavity>
          <ZHeights>
            <KeyValueOfstringdouble>
              <Key>ZTravel</Key>
              <Value>2.0</Value>
            </KeyValueOfstringdouble>
          </ZHeights>
        </Pipettable>
        <CustomAttributes>
          <KeyValueOfstringCustomAttribute>
            <Key>Force</Key>
            <Value>
              <StringContent>&lt;int&gt;20&lt;/int&gt;</StringContent>
            </Value>
          </KeyValueOfstringCustomAttribute>
        </CustomAttributes>
        <CompatibleComponents>
          <KeyValueOfstringguid>
            <Key>Nested Insert</Key>
            <Value>44444444-4444-4444-4444-444444444444</Value>
          </KeyValueOfstringguid>
        </CompatibleComponents>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


class WorktableGeometryRichCatalogTests(unittest.TestCase):
    def test_int_text_uses_native_i64_and_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "parse_i64_text"
        ):
            self.skipTest("optional Rust integer parser is not built")
        cases = [
            (" +42 ", 42),
            ("-7", -7),
            ("001", 1),
            ("1_000", 1000),
            ("١٢", 12),
            (str(1 << 100), 1 << 100),
            ("not-an-int", None),
        ]

        def element(text: str):
            value = worktable_geometry.ET.Element("Value")
            value.text = text
            return value

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._int_text(element(text)) for text, _ in cases]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "parse_i64_text",
                wraps=native_acceleration.parse_i64_text,
            ) as native_call:
                actual = [worktable_geometry._int_text(element(text)) for text, _ in cases]
                self.assertEqual(native_call.call_count, len(cases))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [expected_value for _, expected_value in cases])

    def test_parse_component_mines_pipettable_grip_compat_and_site_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rich.xcmp"
            path.write_text(RICH_XCMP, encoding="utf-8")
            component = parse_component(path)

        self.assertEqual(component["name"], "Rich Plate")
        pipettable = component["pipettable"]
        self.assertEqual(pipettable["cols"], 12)
        self.assertEqual(pipettable["rows"], 8)
        self.assertEqual(pipettable["pitch_x_mm"], 9.0)
        self.assertEqual(pipettable["pitch_y_mm"], -9.0)
        self.assertAlmostEqual(pipettable["well_diameter_mm"], 6.96)
        self.assertAlmostEqual(pipettable["well_depth_mm"], 10.9)
        self.assertEqual(pipettable["well_shape"], "round")
        self.assertGreater(pipettable["max_volume_ul"], 390.0)
        self.assertLess(pipettable["max_volume_ul"], 395.0)

        arrangement = component["arrangements"][0]
        self.assertEqual(
            arrangement["site_template_identifiers"]["0"],
            "33333333-3333-3333-3333-333333333333",
        )
        self.assertEqual(arrangement["allowed_grip_modes"]["0"], ["Narrow", "Wide"])
        self.assertEqual(component["custom_attributes"]["Force"], "20")
        self.assertIn("Lid Companion", component["compatible_component_names"])
        self.assertIn("Nested Insert", component["compatible_component_names"])
        self.assertIn("22222222-2222-2222-2222-222222222222", component["compatible_component_guids"])
        self.assertIn("44444444-4444-4444-4444-444444444444", component["compatible_component_guids"])

        catalog = build_labware_catalog_from_geometry(
            {
                "components": [component],
                "sites": [
                    {
                        "guid": "33333333-3333-3333-3333-333333333333",
                        "location_group_name": "Nest",
                        "type_name": "NestPlatform",
                        "site_kind": "nest",
                    }
                ],
                "workspaces": [],
            }
        )
        entry = catalog["entries"][0]
        self.assertEqual(entry["rows"], 8)
        self.assertEqual(entry["cols"], 12)
        self.assertEqual(entry["pitch_y_mm"], 9.0)
        self.assertEqual(entry["grip"]["force"], "20")
        self.assertEqual(entry["grip"]["allowed_modes"]["0"], ["Narrow", "Wide"])
        self.assertEqual(entry["site_templates"][0]["guid"], "33333333-3333-3333-3333-333333333333")
        self.assertIn("Nested Insert", entry["compatible_component_names"])


if __name__ == "__main__":
    unittest.main()
