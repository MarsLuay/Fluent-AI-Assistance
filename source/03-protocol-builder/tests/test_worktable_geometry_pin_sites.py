"""Tests for worktable_geometry pin / nest-cap site mining."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline import worktable_geometry as wg


def _write_xsit(
    path: Path,
    *,
    guid: str,
    location_group: str,
    type_name: str,
    object_name: str = "",
) -> None:
    path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<Root>
  <Payload>
    <ObjectName>{object_name or location_group or type_name}</ObjectName>
    <PayloadData>
      <SiteTemplate>
        <GUID>{guid}</GUID>
        <LocationGroupName>{location_group}</LocationGroupName>
        <TypeName>{type_name}</TypeName>
      </SiteTemplate>
    </PayloadData>
  </Payload>
</Root>
""",
        encoding="utf-8",
    )


class ClassifySiteKindTests(unittest.TestCase):
    def test_worktable_pin_still_pin(self) -> None:
        self.assertEqual(
            wg.classify_site_kind("WorktablePin_MiddleFront", "Worktable_Segment_WorktablePin_MiddleFront", ""),
            "pin",
        )
        self.assertTrue(wg._looks_like_pin_name("WorktablePin_MiddleFront"))

    def test_cap_nest_typename_and_location(self) -> None:
        self.assertEqual(
            wg.classify_site_kind("Falcon50_Cap_nest_1", "CapHolder_long_Cap_nest", ""),
            "cap_nest",
        )
        self.assertEqual(wg.classify_site_kind("", "CapHolder_Demo_Cap_nest", ""), "cap_nest")
        self.assertEqual(wg.classify_site_kind("", "CapHolder_long", ""), "cap_nest")

    def test_nest_platform_and_nest_typename(self) -> None:
        self.assertEqual(wg.classify_site_kind("NestPlatform", "Labware on nest platform", ""), "nest")
        self.assertEqual(wg.classify_site_kind("7mm Nest[001]", "7mm Nest", ""), "nest")

    def test_plain_deck_site_not_mined(self) -> None:
        self.assertEqual(wg.classify_site_kind("Grid_A1", "Fluent ID Left 4 Grid Site", ""), "")
        self.assertFalse(wg._looks_like_pin_name("Fluent ID Left 4 Grid Site"))


class ParseSitePinMiningTests(unittest.TestCase):
    def test_parse_site_mines_cap_nest_without_pin_substring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cap-nest.xsit"
            _write_xsit(
                path,
                guid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                location_group="Falcon50_Cap_nest_1",
                type_name="CapHolder_long_Cap_nest",
            )
            site = wg.parse_site(path)
            self.assertEqual(site["site_kind"], "cap_nest")
            self.assertEqual(site["pin_name"], "Falcon50_Cap_nest_1")
            self.assertEqual(site["type_name"], "CapHolder_long_Cap_nest")

    def test_build_geometry_includes_cap_nest_in_pin_sites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pin_path = root / "pin.xsit"
            cap_path = root / "cap.xsit"
            grid_path = root / "grid.xsit"
            _write_xsit(
                pin_path,
                guid="11111111-1111-4111-8111-111111111111",
                location_group="WorktablePin_MiddleFront",
                type_name="Worktable_Segment_WorktablePin_MiddleFront",
            )
            _write_xsit(
                cap_path,
                guid="22222222-2222-4222-8222-222222222222",
                location_group="Falcon50_Cap_nest_2",
                type_name="CapHolder_long_44mm_Cap_nest",
            )
            _write_xsit(
                grid_path,
                guid="33333333-3333-4333-8333-333333333333",
                location_group="GridCutout_1",
                type_name="Fluent ID Left 4 Grid Site",
            )
            geometry = wg.build_worktable_geometry(
                {
                    "root": str(root),
                    "objects": [
                        {"extracted_path": str(pin_path), "kind": "site"},
                        {"extracted_path": str(cap_path), "kind": "site"},
                        {"extracted_path": str(grid_path), "kind": "site"},
                    ],
                }
            )
            self.assertEqual(geometry["site_count"], 3)
            pin_names = {site.get("pin_name") for site in geometry.get("pin_sites") or []}
            self.assertIn("WorktablePin_MiddleFront", pin_names)
            self.assertIn("Falcon50_Cap_nest_2", pin_names)
            self.assertNotIn("GridCutout_1", pin_names)
            nest_cap_names = {site.get("pin_name") for site in geometry.get("nest_cap_sites") or []}
            self.assertEqual(nest_cap_names, {"Falcon50_Cap_nest_2"})
            kinds = {site.get("site_kind") for site in geometry.get("pin_sites") or []}
            self.assertEqual(kinds, {"pin", "cap_nest"})


if __name__ == "__main__":
    unittest.main()
