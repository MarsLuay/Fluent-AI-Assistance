"""Tests for ZEIA ``*.xlqc`` → liquid_classes.json export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.liquid_classes_export import (
    LIQUID_CLASSES_SCHEMA_VERSION,
    alias_maps_from_liquid_classes_catalog,
    analyze_liquid_class_use,
    build_liquid_classes_catalog,
    diff_liquid_class_entries,
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
            self.assertEqual(catalog["schema_version"], LIQUID_CLASSES_SCHEMA_VERSION)
            self.assertEqual(catalog["schema_version"], "tecan.liquid_classes.v3")
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
        # No invented Volume payloads — only source-backed fields.
        self.assertNotIn("Volume", str(script))
        aspirate_section = by_name["Aspirate"]
        self.assertTrue(aspirate_section.get("fingerprint"))
        records = aspirate_section.get("command_records") or []
        by_type = {item.get("type"): item for item in records}
        self.assertIn("ConditionalGroup", by_type)
        children = (by_type["ConditionalGroup"].get("children") or [])
        self.assertEqual(children[0].get("type"), "AspirateLiquidMicroCommandDataV2")
        fields = children[0].get("fields") or {}
        self.assertEqual(fields.get("submergeDepth"), 1)
        self.assertEqual(fields.get("errPressureOutOfRange"), 1)
        air = by_type["AspirateAirMicroCommandDataV2"]
        unknown = (air.get("source_metadata") or {}).get("unknown_children") or {}
        empty = unknown.get("empty_variables") or {}
        self.assertIn("pmpEvaluationModel", empty)
        self.assertIn("ExtraUnparsedPayload", unknown.get("structured_children") or [])
        self.assertTrue(fca.get("fingerprint"))
        pressure = fca.get("pressure_supervision") or {}
        self.assertEqual(pressure.get("presence"), "present")
        self.assertEqual((pressure.get("by_section") or {}).get("microscript", {}).get("err_pressure_out_of_range"), 1)

    def test_water_free_single_preserves_pressure_supervision_and_formulas(self) -> None:
        fixture = RICH_XLQC
        self.assertTrue(fixture.is_file(), f"missing fixture {fixture}")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dddddddd-dddd-4ddd-8ddd-dddddddddddd.xlqc"
            path.write_bytes(fixture.read_bytes())
            parsed = parse_xlqc(path)
        fca = next((item for item in (parsed.get("profiles") or []) if item.get("head") == "Fca"), None)
        self.assertIsNotNone(fca)
        assert fca is not None
        detection = (fca.get("detection") or {}).get("aspirate") or {}
        self.assertEqual(detection.get("err_pressure_out_of_range"), 1)
        self.assertEqual(detection.get("err_pressure_out_of_range_retry"), 1)
        self.assertEqual(detection.get("adp_sensitivity"), 0.4)
        self.assertEqual(detection.get("adp_rise_threshold"), 20)
        self.assertEqual(detection.get("adp_drop_threshold"), -30)
        pressure = fca.get("pressure_supervision") or {}
        self.assertEqual(pressure.get("presence"), "present")
        self.assertIsNone(pressure.get("threshold_recommendation"))
        self.assertNotIn("1000", json.dumps(pressure))
        self.assertIn("volume", fca.get("formula_dependencies") or [])
        self.assertTrue(parsed.get("fingerprint"))
        cloned = json.loads(json.dumps(parsed))
        cloned["profiles"][0]["aspirate"]["delay_ms"] = 999
        cloned["fingerprint"] = "changed"
        diff = diff_liquid_class_entries(parsed, cloned)
        self.assertTrue(diff["changed"])
        self.assertTrue(any("aspirate" in str(item.get("path")) for item in diff["changes"]))
        unchanged = diff_liquid_class_entries(parsed, parsed)
        self.assertFalse(unchanged["changed"])

    def test_profile_analysis_surfaces_ambiguity_and_unknown_payload(self) -> None:
        fixture = FIXTURES / "microscript_body_slice.xlqc"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee.xlqc"
            path.write_bytes(fixture.read_bytes())
            parsed = parse_xlqc(path)
        catalog = {
            "schema_version": LIQUID_CLASSES_SCHEMA_VERSION,
            "entries": [
                {
                    "name": parsed["name"],
                    "guid": parsed["guid"],
                    "aliases": [parsed["name"]],
                    "profiles": parsed["profiles"],
                    "fingerprint": parsed.get("fingerprint"),
                }
            ],
        }
        unknown = analyze_liquid_class_use(
            name=parsed["name"],
            operation="aspirate",
            catalog=catalog,
        )
        self.assertTrue(
            any(item.get("reason") == "unknown_micro_command_payload" for item in unknown["reviews"])
        )
        faithful = analyze_liquid_class_use(
            name=parsed["name"],
            operation="aspirate",
            catalog=catalog,
            faithful_generation=True,
        )
        self.assertTrue(
            any(item.get("reason") == "unknown_micro_command_payload" for item in faithful["failures"])
        )
        missing_head = analyze_liquid_class_use(
            name=parsed["name"],
            operation="aspirate",
            catalog=catalog,
            head="LiHa",
        )
        self.assertTrue(
            any(item.get("reason") == "liquid_class_head_missing" for item in missing_head["failures"])
        )
        two_profiles = json.loads(json.dumps(catalog))
        extra = json.loads(json.dumps(parsed["profiles"][0]))
        extra["tip"] = "DiTi 200ul"
        two_profiles["entries"][0]["profiles"].append(extra)
        ambiguous = analyze_liquid_class_use(
            name=parsed["name"],
            operation="aspirate",
            catalog=two_profiles,
        )
        self.assertTrue(
            any(item.get("reason") == "ambiguous_liquid_class_profile" for item in ambiguous["reviews"])
        )
        from fluent_pipeline.gates.evaluators import evaluate_liquid_class_compatibility
        from fluent_pipeline.gates.models import ValidationContext

        def make_gate(gid, status, summary, details=None):
            return {"id": gid, "status": status, "summary": summary, "details": details or {}}

        ir = {
            "liquid_classes": [{"name": parsed["name"]}],
            "steps": [
                {
                    "id": "s1",
                    "operation": "aspirate",
                    "liquid_class": parsed["name"],
                }
            ],
        }
        gate = evaluate_liquid_class_compatibility(
            ValidationContext(
                make_gate=make_gate,
                domain_ir=ir,
                source_manifest={"liquid_classes": [parsed["name"]]},
                validation_options={"liquid_classes_catalog": catalog},
            )
        )
        self.assertEqual(gate["status"], "needs_review")
        clean_catalog = json.loads(json.dumps(catalog))
        for profile in clean_catalog["entries"][0]["profiles"]:
            for section in profile.get("microscript") or []:
                for record in section.get("command_records") or []:
                    record.pop("source_metadata", None)
                    for child in record.get("children") or []:
                        child.pop("source_metadata", None)
        passed = evaluate_liquid_class_compatibility(
            ValidationContext(
                make_gate=make_gate,
                domain_ir=ir,
                source_manifest={"liquid_classes": [parsed["name"]]},
                validation_options={"liquid_classes_catalog": clean_catalog},
            )
        )
        self.assertEqual(passed["status"], "passed")


if __name__ == "__main__":
    unittest.main()
