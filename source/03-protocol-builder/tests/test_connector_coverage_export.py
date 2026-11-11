"""Tests for ZEIA-derived connector coverage export."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import fluent_pipeline.connector_coverage_export as connector_coverage_export_module
from tecan_common import native_acceleration
from fluent_pipeline.connector_coverage_export import (
    CONNECTOR_COUNT_PROFILES,
    build_connector_coverage_from_geometry,
)


class ConnectorCoverageExportTests(unittest.TestCase):
    def test_guid_text_normalization_preserves_nil_and_unicode_fallback(self):
        values = [
            "  ABCD-1234  ",
            "00000000-0000-0000-0000-000000000000",
            "ſcript",
            None,
            0,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [connector_coverage_export_module._norm_guid(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                connector_coverage_export_module,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [connector_coverage_export_module._norm_guid(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(native_acceleration.ascii_casefold_trim("ſcript"))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["abcd-1234", "", "script", "", ""])

    def test_profiles_mined_from_geometry_components_only(self):
        demo_device = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        demo_nest = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        geometry = {
            "components": [
                {
                    "guid": demo_device,
                    "name": "Demo_Device_Pos",
                    "connector_guids": ["c1", "c2", "c3"],
                },
                {
                    "guid": demo_nest,
                    "name": "Demo_Nest_Pos",
                    "connector_guids": ["c4"],
                },
            ],
            "connectors": [
                {"component_guid": demo_device},
                {"component_guid": demo_device},
                {"component_guid": demo_device},
                {"component_guid": demo_nest},
            ],
        }
        coverage = build_connector_coverage_from_geometry(geometry)
        self.assertEqual(coverage["schema_version"], "tecan.connector_coverage.v1")
        self.assertEqual(CONNECTOR_COUNT_PROFILES, ())
        by_id = {row["id"]: row for row in coverage["profiles"]}
        self.assertEqual(set(by_id), {"demo_device_pos", "demo_nest_pos"})
        self.assertNotIn("resolvex_a200", by_id)
        self.assertNotIn("capholder_long", by_id)
        self.assertEqual(by_id["demo_device_pos"]["actualCount"], 3)
        self.assertTrue(by_id["demo_device_pos"]["matches"])
        self.assertEqual(by_id["demo_device_pos"]["matchedComponentGuids"], [demo_device])
        self.assertEqual(by_id["demo_nest_pos"]["actualCount"], 1)

    def test_missing_families_are_absent_not_failed_profiles(self):
        coverage = build_connector_coverage_from_geometry(
            {
                "components": [{"guid": "11111111-1111-4111-8111-111111111111", "name": "Generic Nest"}],
                "connectors": [],
            }
        )
        self.assertEqual(coverage["profiles"], [])
        self.assertEqual(coverage["summary"]["matchedProfiles"], 0)


if __name__ == "__main__":
    unittest.main()
