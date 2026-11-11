"""Tests for ZEIA-derived labware label/catalog contracts."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import fluent_pipeline.labware_contracts as labware_contracts
from tecan_common import native_acceleration
from fluent_pipeline.labware_contracts import (
    LABEL_CATALOG_MISMATCH_CODE,
    ir_label_catalog_issues,
    preferred_label_catalogs_from_geometry,
    preferred_label_catalogs_from_labware_catalog,
)


class PreferredLabelCatalogTests(unittest.TestCase):
    def test_label_key_normalization_uses_native_ascii_and_unicode_fallback(self):
        values = [" AdapterA200 ", "Straße", "İmage", None, 0, ""]
        expected = [str(value or "").strip().casefold() for value in values]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [labware_contracts._norm(value) for value in values]
        self.assertEqual(disabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                labware_contracts,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                enabled = [labware_contracts._norm(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(enabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                labware_contracts,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback = [labware_contracts._norm(value) for value in values]
        self.assertEqual(fallback, expected)

    def test_geometry_unanimous_label_catalog(self):
        preferred = preferred_label_catalogs_from_geometry(
            {
                "workspaces": [
                    {
                        "placements": [
                            {"label": "AdapterA200", "catalog": "Adapter A200_ElutionRack"},
                            {"label": "AdapterA200[001]", "catalog": "Adapter A200_ElutionRack"},
                            {"label": "DemoPlate", "catalog": "96 Well Flat"},
                        ]
                    }
                ]
            }
        )
        self.assertEqual(preferred["adaptera200"], "Adapter A200_ElutionRack")
        self.assertEqual(preferred["demoplate"], "96 Well Flat")

    def test_geometry_conflict_skips_label(self):
        preferred = preferred_label_catalogs_from_geometry(
            {
                "workspaces": [
                    {
                        "placements": [
                            {"label": "AdapterA200", "catalog": "Adapter A200_ElutionRack"},
                            {"label": "AdapterA200", "catalog": "Adapter A200"},
                        ]
                    }
                ]
            }
        )
        self.assertNotIn("adaptera200", preferred)

    def test_labware_catalog_aliases_supply_preferred_type(self):
        preferred = preferred_label_catalogs_from_labware_catalog(
            {
                "entries": [
                    {
                        "name": "Adapter A200_ElutionRack",
                        "aliases": ["AdapterA200", "Adapter A200_ElutionRack"],
                    }
                ]
            }
        )
        self.assertEqual(preferred["adaptera200"], "Adapter A200_ElutionRack")

    def test_ir_issues_require_preferred_map(self):
        ir = {
            "labware": [
                {"label": "AdapterA200", "catalog": "Adapter A200"},
            ]
        }
        self.assertEqual(ir_label_catalog_issues(ir), [])
        issues = ir_label_catalog_issues(
            ir,
            {"adaptera200": "Adapter A200_ElutionRack"},
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], LABEL_CATALOG_MISMATCH_CODE)
        self.assertEqual(issues[0]["expected"], "Adapter A200_ElutionRack")

    def test_legacy_mismatch_message_uses_preferred_map_only(self):
        from fluent_pipeline.labware_contracts import (
            A200_ADAPTER_REQUIRED_CATALOG,
            a200_adapter_mismatch_message,
        )

        self.assertEqual(A200_ADAPTER_REQUIRED_CATALOG, "")
        no_map = a200_adapter_mismatch_message(catalog="Adapter A200")
        self.assertNotIn("ElutionRack", no_map)
        self.assertIn("no preferred ZEIA catalog", no_map)

        with_map = a200_adapter_mismatch_message(
            catalog="Adapter A200",
            preferred_label_catalogs={"adaptera200": "Demo_Adapter_A200_Variant"},
        )
        self.assertIn("Demo_Adapter_A200_Variant", with_map)
        self.assertNotIn("ElutionRack", with_map)

        explicit = a200_adapter_mismatch_message(
            catalog="Adapter A200",
            expected="ZEIA_Exact_Catalog",
        )
        self.assertIn("ZEIA_Exact_Catalog", explicit)
        self.assertNotIn("ElutionRack", explicit)


if __name__ == "__main__":
    unittest.main()
