"""Tests for ZEIA ``*.xlqc`` → liquid_classes.json export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tecan_common import native_acceleration
import fluent_pipeline.liquid_classes_export as liquid_classes_export
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
    def test_scalar_coercion_uses_native_numeric_parsers_with_python_fallback(
        self,
    ) -> None:
        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in ("parse_i64_text", "parse_f64_decimal_text")
        ):
            self.skipTest("optional Rust scalar parsers are not built")
        cases = [
            (" 42 ", 42),
            ("-7", -7),
            ("+3", 3),
            ("1_000", 1000),
            ("١٢", 12),
            (
                "999999999999999999999999999999999",
                999999999999999999999999999999999,
            ),
            ("4.0", 4.0),
            ("1e2", 100.0),
            ("true", True),
            ("NaN", "NaN"),
            ("not numeric", "not numeric"),
            ("   ", ""),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                liquid_classes_export._coerce_scalar(value) for value, _ in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                liquid_classes_export,
                "_native_parse_i64_text",
                wraps=native_acceleration.parse_i64_text,
            ) as native_int_call:
                with patch.object(
                    liquid_classes_export,
                    "_native_parse_f64_decimal_text",
                    wraps=native_acceleration.parse_f64_decimal_text,
                ) as native_float_call:
                    actual = [
                        liquid_classes_export._coerce_scalar(value)
                        for value, _ in cases
                    ]
                    self.assertEqual(native_int_call.call_count, 7)
                    self.assertEqual(native_float_call.call_count, 3)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual, [expected_value for _, expected_value in cases]
        )

    def test_xml_scalar_queries_use_native_helpers_with_python_fallback(
        self,
    ) -> None:
        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in ("xml_first_element_by_name", "xml_clean_text")
        ):
            self.skipTest("optional Rust XML scalar helpers are not built")
        root = liquid_classes_export.ET.fromstring(
            '<Root xmlns:x="urn:test"><x:Target>  first value  </x:Target>'
            "<Target>later value</Target></Root>"
        )
        first = list(root)[0]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_element = liquid_classes_export._find(root, "Target")
            expected_text = liquid_classes_export._text(first)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                liquid_classes_export,
                "_native_xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as native_find_call:
                with patch.object(
                    liquid_classes_export,
                    "_native_xml_clean_text",
                    wraps=native_acceleration.xml_clean_text,
                ) as native_text_call:
                    actual_element = liquid_classes_export._find(root, "Target")
                    actual_text = liquid_classes_export._text(first)
                    native_find_call.assert_called_once_with(
                        root, "Target", liquid_classes_export.ET.Element
                    )
                    native_text_call.assert_called_once_with(first.text)
        self.assertIs(actual_element, expected_element)
        self.assertEqual(actual_text, expected_text)
        self.assertEqual(actual_text, "first value")

        class StringSubclass(str):
            pass

        fallback_root = liquid_classes_export.ET.Element("Root")
        fallback_child = liquid_classes_export.ET.SubElement(fallback_root, "Target")
        fallback_child.tag = StringSubclass("Target")
        fallback_child.text = StringSubclass("  fallback value  ")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_element = liquid_classes_export._find(fallback_root, "Target")
            fallback_text = liquid_classes_export._text(fallback_child)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                liquid_classes_export._native_xml_first_element_by_name(
                    fallback_root, "Target", liquid_classes_export.ET.Element
                )
            )
            self.assertIsNone(
                liquid_classes_export._native_xml_clean_text(fallback_child.text)
            )
            native_fallback_element = liquid_classes_export._find(
                fallback_root, "Target"
            )
            native_fallback_text = liquid_classes_export._text(fallback_child)
        self.assertIs(native_fallback_element, fallback_element)
        self.assertEqual(native_fallback_text, fallback_text)

    def test_direct_xml_child_text_uses_native_selector_with_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_first_direct_child_by_name"
        ):
            self.skipTest("optional Rust direct-child XML selector is not built")
        root = liquid_classes_export.ET.fromstring(
            '<Root xmlns:x="urn:test"><Nested><Target>nested</Target></Nested>'
            "<x:Target> first </x:Target><Target>later</Target></Root>"
        )
        empty_first = liquid_classes_export.ET.fromstring(
            "<Root><Target>   </Target><Target>later</Target></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = liquid_classes_export._child_text(root, "Target")
            expected_empty = liquid_classes_export._child_text(empty_first, "Target")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                liquid_classes_export,
                "_native_xml_first_direct_child_by_name",
                wraps=native_acceleration.xml_first_direct_child_by_name,
            ) as native_call:
                actual = liquid_classes_export._child_text(root, "Target")
                actual_empty = liquid_classes_export._child_text(
                    empty_first, "Target"
                )
                self.assertEqual(native_call.call_count, 2)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "first")
        self.assertEqual(actual_empty, expected_empty)
        self.assertEqual(actual_empty, "")

        class StringSubclass(str):
            pass

        unsupported = liquid_classes_export.ET.Element("Root")
        child = liquid_classes_export.ET.SubElement(unsupported, "Target")
        child.tag = StringSubclass("Target")
        child.text = StringSubclass(" fallback ")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = liquid_classes_export._child_text(unsupported, "Target")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                liquid_classes_export._native_xml_first_direct_child_by_name(
                    unsupported, "Target", liquid_classes_export.ET.Element
                )
            )
            actual_fallback = liquid_classes_export._child_text(
                unsupported, "Target"
            )
        self.assertEqual(actual_fallback, fallback)

    def test_supported_head_scan_uses_native_selector_with_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        root = liquid_classes_export.ET.fromstring(
            '<PayloadData xmlns:x="urn:test">'
            "<x:PipettingDeviceType>Primary</x:PipettingDeviceType>"
            "<PipettingDeviceType>Secondary</PipettingDeviceType>"
            "<PipettingDeviceType>Primary</PipettingDeviceType>"
            "</PayloadData>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = liquid_classes_export._supported_heads(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                liquid_classes_export,
                "_native_xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual = liquid_classes_export._supported_heads(root)
                native_call.assert_called_once_with(
                    root, ("PipettingDeviceType",), liquid_classes_export.ET.Element
                )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["Primary", "Secondary"])
        self.assertEqual(liquid_classes_export._supported_heads(None), [])

        class StringSubclass(str):
            pass

        unsupported = liquid_classes_export.ET.Element("PayloadData")
        subclass_tag = liquid_classes_export.ET.SubElement(
            unsupported, "PipettingDeviceType"
        )
        subclass_tag.tag = StringSubclass("PipettingDeviceType")
        subclass_tag.text = "Custom"
        native_tag = liquid_classes_export.ET.SubElement(
            unsupported, "PipettingDeviceType"
        )
        native_tag.text = "Standard"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = liquid_classes_export._supported_heads(unsupported)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                liquid_classes_export._native_xml_elements_by_name(
                    unsupported,
                    ("PipettingDeviceType",),
                    liquid_classes_export.ET.Element,
                )
            )
            native_fallback = liquid_classes_export._supported_heads(unsupported)
        self.assertEqual(native_fallback, fallback)
        self.assertEqual(native_fallback, ["Custom", "Standard"])

    def test_profile_source_scan_uses_native_ordered_selector_with_fallback(
        self,
    ) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        root = liquid_classes_export.ET.fromstring(
            '<Root xmlns:x="urn:test">'
            "<Other />"
            "<x:Tecan.Core.Pipetting.LiquidClassEquationSet />"
            "<Tecan.Core.Pipetting.LiquidClassDetectionAndPositioningSet />"
            "<Tecan.Core.Pipetting.LiquidSubClass />"
            "</Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = liquid_classes_export._profile_source_elements(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                liquid_classes_export,
                "_native_xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual = liquid_classes_export._profile_source_elements(root)
                native_call.assert_called_once_with(
                    root,
                    (
                        liquid_classes_export._EQ_SET_LOCAL,
                        liquid_classes_export._DPS_SET_LOCAL,
                        liquid_classes_export._SUBCLASS_LOCAL,
                    ),
                    liquid_classes_export.ET.Element,
                )
        self.assertEqual(len(actual), 3)
        self.assertTrue(all(a is e for a, e in zip(actual, expected)))

        commented = liquid_classes_export.ET.Element("Root")
        comment = liquid_classes_export.ET.Comment("ignored")
        commented.append(comment)
        candidate = liquid_classes_export.ET.SubElement(
            commented, liquid_classes_export._SUBCLASS_LOCAL
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = liquid_classes_export._profile_source_elements(commented)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                liquid_classes_export._native_xml_elements_by_name(
                    commented,
                    (
                        liquid_classes_export._EQ_SET_LOCAL,
                        liquid_classes_export._DPS_SET_LOCAL,
                        liquid_classes_export._SUBCLASS_LOCAL,
                    ),
                    liquid_classes_export.ET.Element,
                )
            )
            native_fallback = liquid_classes_export._profile_source_elements(
                commented
            )
        self.assertEqual(fallback, [candidate])
        self.assertEqual(native_fallback, fallback)

    def test_legacy_section_scan_uses_native_casefolded_selector(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native,
            "xml_elements_by_ascii_casefolded_name",
        ):
            self.skipTest("optional Rust case-folded XML selector is not built")
        root = liquid_classes_export.ET.fromstring(
            "<Root><Other /><ASPIRATE /><Mixing /><EMPTY_TIPS /></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = liquid_classes_export._section_source_elements(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                liquid_classes_export,
                "_native_xml_elements_by_ascii_casefolded_name",
                wraps=native_acceleration.xml_elements_by_ascii_casefolded_name,
            ) as native_call:
                actual = liquid_classes_export._section_source_elements(root)
                native_call.assert_called_once_with(
                    root,
                    tuple(liquid_classes_export._SECTION_KINDS),
                    liquid_classes_export.ET.Element,
                )
        self.assertTrue(all(a is e for a, e in zip(actual, expected)))
        self.assertEqual([e.tag for e in actual], ["ASPIRATE", "Mixing", "EMPTY_TIPS"])

        unicode_root = liquid_classes_export.ET.Element("Root")
        unicode_root.append(liquid_classes_export.ET.Comment("preserved fallback"))
        unusual_section = liquid_classes_export.ET.SubElement(unicode_root, "Section")
        unusual_section.tag = "aſpirate"
        liquid_classes_export.ET.SubElement(unicode_root, "Dispense")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = liquid_classes_export._section_source_elements(unicode_root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                liquid_classes_export._native_xml_elements_by_ascii_casefolded_name(
                    unicode_root,
                    tuple(liquid_classes_export._SECTION_KINDS),
                    liquid_classes_export.ET.Element,
                )
            )
            native_fallback = liquid_classes_export._section_source_elements(
                unicode_root
            )
        self.assertEqual(
            [element.tag for element in fallback], ["aſpirate", "Dispense"]
        )
        self.assertEqual(native_fallback, fallback)

    def test_nested_text_scan_uses_native_selector_and_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native,
            "xml_elements_by_ascii_casefolded_name",
        ):
            self.skipTest("optional Rust case-folded XML selector is not built")
        root = liquid_classes_export.ET.fromstring(
            "<PipettingDeviceType><Container><PipettingDeviceType />"
            "</Container><Other><PIPETTINGDEVICETYPE>later</PIPETTINGDEVICETYPE>"
            "</Other></PipettingDeviceType>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = liquid_classes_export._direct_or_nested_text(
                root, "PipettingDeviceType"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                liquid_classes_export,
                "_native_xml_elements_by_ascii_casefolded_name",
                wraps=native_acceleration.xml_elements_by_ascii_casefolded_name,
            ) as native_call:
                actual = liquid_classes_export._direct_or_nested_text(
                    root, "PipettingDeviceType"
                )
                native_call.assert_called_once_with(
                    root, ("pipettingdevicetype",), liquid_classes_export.ET.Element
                )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "later")

        unicode_root = liquid_classes_export.ET.Element("Root")
        unicode_root.append(liquid_classes_export.ET.Comment("fallback"))
        container = liquid_classes_export.ET.SubElement(unicode_root, "Container")
        unusual = liquid_classes_export.ET.SubElement(container, "Unusual")
        unusual.tag = "aſpirate"
        unusual.text = "unicode match"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = liquid_classes_export._direct_or_nested_text(
                unicode_root, "aspirate"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                liquid_classes_export._native_xml_elements_by_ascii_casefolded_name(
                    unicode_root, ("aspirate",), liquid_classes_export.ET.Element
                )
            )
            native_fallback = liquid_classes_export._direct_or_nested_text(
                unicode_root, "aspirate"
            )
        self.assertEqual(fallback, "unicode match")
        self.assertEqual(native_fallback, fallback)

    def test_microscript_child_scans_use_native_selector_with_fallback(
        self,
    ) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_direct_elements_by_name"
        ):
            self.skipTest("optional Rust direct-child XML selector is not built")
        root = liquid_classes_export.ET.fromstring(
            '<Root xmlns:x="urn:test">'
            "<x:MicroScriptSection><Name>First</Name><Objects>"
            '<Object Type="Namespace.CommandOne" />'
            "</Objects></x:MicroScriptSection>"
            "<Other />"
            "<microscriptsection><Name>Ignored</Name></microscriptsection>"
            "<MicroScriptSection><Name>Second</Name><Objects>"
            '<Object Type="Namespace.CommandTwo" />'
            "</Objects></MicroScriptSection></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = liquid_classes_export._mine_microscript(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                liquid_classes_export,
                "_native_xml_direct_elements_by_name",
                wraps=native_acceleration.xml_direct_elements_by_name,
            ) as native_call:
                actual = liquid_classes_export._mine_microscript(root)
                self.assertGreaterEqual(native_call.call_count, 5)
        self.assertEqual(actual, expected)
        self.assertEqual([item["name"] for item in actual], ["First", "Second"])
        self.assertEqual(
            [item["commands"] for item in actual],
            [["CommandOne"], ["CommandTwo"]],
        )

        limited_root = liquid_classes_export.ET.fromstring(
            "<Root><MicroScriptSection><Name>Limited</Name><Objects>"
            '<Object Type="Command.First" /><Other />'
            '<Object Type="Command.Second" />'
            "</Objects></MicroScriptSection></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            limited_expected = liquid_classes_export._mine_microscript(
                limited_root, max_commands=1
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            limited_actual = liquid_classes_export._mine_microscript(
                limited_root, max_commands=1
            )
        self.assertEqual(limited_actual, limited_expected)
        self.assertEqual(limited_actual[0]["commands"], ["First"])
        self.assertTrue(limited_actual[0]["commands_truncated"])

        commented_root = liquid_classes_export.ET.Element("Root")
        commented_root.append(liquid_classes_export.ET.Comment("fallback"))
        section = liquid_classes_export.ET.SubElement(
            commented_root, "MicroScriptSection"
        )
        liquid_classes_export.ET.SubElement(section, "Name").text = "Commented"
        objects = liquid_classes_export.ET.SubElement(section, "Objects")
        liquid_classes_export.ET.SubElement(
            objects, "Object", {"Type": "Namespace.Command"}
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = liquid_classes_export._mine_microscript(commented_root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                liquid_classes_export._native_xml_direct_elements_by_name(
                    commented_root,
                    ("MicroScriptSection",),
                    liquid_classes_export.ET.Element,
                )
            )
            native_fallback = liquid_classes_export._mine_microscript(
                commented_root
            )
        self.assertEqual(native_fallback, fallback)

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
