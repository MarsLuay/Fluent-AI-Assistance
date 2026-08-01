"""Tests for ZEIA ``*.xlqc`` → liquid_classes.json export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.liquid_classes_export import (
    alias_maps_from_liquid_classes_catalog,
    build_liquid_classes_catalog,
    parse_xlqc,
    resolve_liquid_class_guid,
    write_liquid_classes_catalog,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RICH_XLQC = FIXTURES / "water_free_single_slice.xlqc"

SAMPLE_XLQC = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Water Free Single</ObjectName>
    <PayloadData>
      <PipettingDeviceType>Fca</PipettingDeviceType>
      <PipettingDeviceType>Mca96</PipettingDeviceType>
    </PayloadData>
  </Payload>
</VxData>
"""


class LiquidClassesExportTests(unittest.TestCase):
    def test_parse_xlqc_uses_filename_guid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            path = Path(tmp) / f"{guid}.xlqc"
            path.write_text(SAMPLE_XLQC, encoding="utf-8")
            parsed = parse_xlqc(path)
        self.assertEqual(parsed["guid"], guid)
        self.assertEqual(parsed["name"], "Water Free Single")
        self.assertEqual(parsed["head"], "Fca")
        self.assertEqual(parsed["supported_heads"], ["Fca", "Mca96"])

    def test_build_from_datastore_liquid_classes_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquid_dir = root / "SystemSpecific" / "LiquidClasses"
            liquid_dir.mkdir(parents=True)
            guid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            (liquid_dir / f"{guid}.xlqc").write_text(SAMPLE_XLQC, encoding="utf-8")
            catalog = build_liquid_classes_catalog(datastore_root=root)
            self.assertEqual(catalog["schema_version"], "tecan.liquid_classes.v2")
            self.assertEqual(catalog["entry_count"], 1)
            entry = catalog["entries"][0]
            self.assertEqual(entry["guid"], guid)
            self.assertEqual(entry["name"], "Water Free Single")
            self.assertEqual(entry["supported_heads"], ["Fca", "Mca96"])
            self.assertEqual(resolve_liquid_class_guid("Water Free Single", catalog), guid)
            self.assertIsNone(resolve_liquid_class_guid("Missing LC", catalog))

    def test_manifest_objects_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extracted = root / "extracted"
            extracted.mkdir()
            guid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            xlqc = extracted / f"{guid}.xlqc"
            xlqc.write_text(SAMPLE_XLQC, encoding="utf-8")
            manifest = {
                "root": str(root),
                "extracted_dir": str(extracted),
                "objects": [
                    {
                        "kind": "liquid_class",
                        "entry": f"{guid}.xlqc",
                        "extracted_path": str(xlqc),
                        "object_name": "Water Free Single",
                    }
                ],
            }
            dest = root / "liquid_classes.json"
            written = write_liquid_classes_catalog(dest, manifest=manifest, context_root=root)
            self.assertEqual(written, dest)
            payload = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(payload["entry_count"], 1)
            maps = alias_maps_from_liquid_classes_catalog(payload)
            self.assertEqual(maps["liquid_class_aliases"]["Water Free Single"], "Water Free Single")

    def test_empty_returns_none_on_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "liquid_classes.json"
            self.assertIsNone(write_liquid_classes_catalog(dest, manifest={"objects": []}))
            self.assertFalse(dest.exists())

    def test_rich_xlqc_mines_head_tip_profiles(self) -> None:
        self.assertTrue(RICH_XLQC.is_file(), f"missing fixture {RICH_XLQC}")
        guid = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{guid}.xlqc"
            path.write_bytes(RICH_XLQC.read_bytes())
            parsed = parse_xlqc(path)

        self.assertEqual(parsed["name"], "Water Free Single")
        self.assertIn("Fca", parsed.get("supported_heads") or [])
        profiles = parsed.get("profiles") or []
        self.assertTrue(profiles, "expected head×tip profiles")

        fca = next(
            (
                item
                for item in profiles
                if item.get("head") == "Fca" and item.get("tip") == "Standard Fixed Tip"
            ),
            None,
        )
        self.assertIsNotNone(fca)
        assert fca is not None

        aspirate = fca.get("aspirate") or {}
        dispense = fca.get("dispense") or {}
        self.assertIn("flow_rate_formula", aspirate)
        self.assertIn("leading_air_gap_formula", aspirate)
        self.assertEqual(aspirate.get("delay_ms"), 200)
        self.assertEqual(dispense.get("flow_rate"), 600)
        self.assertEqual(dispense.get("acceleration"), 10000)

        detection = (fca.get("detection") or {}).get("aspirate") or {}
        self.assertEqual(detection.get("submerge_depth"), 1)
        self.assertEqual(detection.get("z_offset"), 0)
        self.assertEqual(detection.get("move_speed"), 20)
        self.assertEqual(detection.get("retract_speed"), 20)
        self.assertEqual(detection.get("plld"), 0)
        self.assertEqual(detection.get("clld"), 1)

        sections = fca.get("microscript_sections") or []
        self.assertIn("Aspirate", sections)
        self.assertIn("Dispense", sections)

        # Top-level summary still present for v1 consumers.
        self.assertIn("flow_rate_formula", parsed.get("aspirate") or {})
        self.assertEqual((parsed.get("dispense") or {}).get("flow_rate"), 600)

    def test_microscript_body_command_types(self) -> None:
        fixture = FIXTURES / "microscript_body_slice.xlqc"
        self.assertTrue(fixture.is_file(), f"missing fixture {fixture}")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee.xlqc"
            path.write_bytes(fixture.read_bytes())
            parsed = parse_xlqc(path)

        profiles = parsed.get("profiles") or []
        fca = next((item for item in profiles if item.get("head") == "Fca"), None)
        self.assertIsNotNone(fca)
        assert fca is not None
        script = fca.get("microscript") or []
        by_name = {item.get("name"): item for item in script}
        self.assertIn("Aspirate", by_name)
        aspirate_cmds = by_name["Aspirate"].get("commands") or []
        self.assertEqual(
            aspirate_cmds,
            [
                "MoveValveMicroCommandDataV1",
                "AspirateAirMicroCommandDataV2",
                "ConditionalGroup",
                "AspirateLiquidMicroCommandDataV2",
                "RetractTipMicroCommandDataV1",
            ],
        )
        self.assertEqual(
            (by_name.get("Dispense") or {}).get("commands"),
            ["DispenseLiquidMicroCommandDataV2"],
        )
        # No full payload invent — commands are Type leaf names only.
        self.assertNotIn("Volume", str(script))


if __name__ == "__main__":
    unittest.main()
