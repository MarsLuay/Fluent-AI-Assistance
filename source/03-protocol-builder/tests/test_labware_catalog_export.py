"""Tests for ZEIA-derived labware catalog export."""

from __future__ import annotations

import unittest
from pathlib import Path

from fluent_pipeline.aliases import resolve_alias
from fluent_pipeline.labware_catalog_export import (
    alias_maps_from_labware_catalog,
    build_labware_catalog_from_geometry,
    strip_fluent_instance_suffix,
    write_labware_catalog,
)


class LabwareCatalogExportTests(unittest.TestCase):
    def test_strip_instance_suffix(self):
        self.assertEqual(strip_fluent_instance_suffix("Runner[001]"), "Runner")
        self.assertEqual(strip_fluent_instance_suffix("FilterDWP[platecount]"), "FilterDWP")
        self.assertEqual(strip_fluent_instance_suffix("Plain"), "Plain")

    def test_build_catalog_from_geometry_uses_exact_component_names(self):
        geometry = {
            "components": [
                {
                    "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "name": "Custom Tube Runner_SiteLab",
                    "object_name": "Custom Tube Runner_SiteLab",
                    "dimension_mm": {"x": 49.0, "y": 398.58, "z": 70.5},
                    "arrangements": [
                        {
                            "sites_in_x": 10,
                            "sites_in_y": 1,
                            "site_spacing_mm": {"x": 37.0, "y": 0.0, "z": 0.0},
                        }
                    ],
                }
            ],
            "workspaces": [
                {
                    "placements": [
                        {
                            "label": "Custom Tube Runner_SiteLab[001]",
                            "catalog": "Custom Tube Runner_SiteLab",
                            "component_guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "component_name": "Custom Tube Runner_SiteLab",
                        }
                    ]
                }
            ],
        }
        catalog = build_labware_catalog_from_geometry(geometry)
        self.assertEqual(catalog["schema_version"], "tecan.labware_catalog.v1")
        self.assertEqual(catalog["entry_count"], 1)
        entry = catalog["entries"][0]
        self.assertEqual(entry["name"], "Custom Tube Runner_SiteLab")
        self.assertIn("Custom Tube Runner_SiteLab[001]", entry["aliases"])
        self.assertEqual(entry["cols"], 10)
        self.assertEqual(entry["physical_width_mm"], 49.0)

    def test_catalog_exports_mesh_guids_separate_from_component_guid(self):
        component_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        mesh_guid = "11111111-2222-3333-4444-555555555555"
        geometry = {
            "components": [
                {
                    "guid": component_guid,
                    "name": "96 Well Flat",
                    "mesh_guids": [mesh_guid],
                    "mesh_names": ["30053126_Plate"],
                    "dimension_mm": {"x": 127.8, "y": 85.5, "z": 14.4},
                }
            ],
            "workspaces": [],
        }
        catalog = build_labware_catalog_from_geometry(geometry)
        entry = catalog["entries"][0]
        self.assertEqual(entry["guid"], component_guid)
        self.assertEqual(entry["mesh_guid"], mesh_guid)
        self.assertEqual(entry["mesh_guids"], [mesh_guid])
        self.assertEqual(entry["mesh_names"], ["30053126_Plate"])
        self.assertNotEqual(entry["guid"], entry["mesh_guid"])

    def test_write_catalog_and_alias_overlay(self):
        geometry = {
            "components": [
                {
                    "guid": "11111111-2222-3333-4444-555555555555",
                    "name": "SitePlate",
                    "dimension_mm": {"x": 127.8, "y": 85.5, "z": 14.0},
                }
            ],
            "workspaces": [
                {
                    "placements": [
                        {
                            "label": "SitePlate[002]",
                            "catalog": "SitePlate",
                            "component_guid": "11111111-2222-3333-4444-555555555555",
                        }
                    ]
                }
            ],
        }
        with self.temporary_directory() as tmp:
            path = write_labware_catalog(Path(tmp) / "labware_catalog.json", geometry)
            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.is_file())
            catalog = build_labware_catalog_from_geometry(geometry)
            maps = alias_maps_from_labware_catalog(catalog)
            self.assertEqual(maps["labware_aliases"]["SitePlate[002]"], "SitePlate")
            resolved = resolve_alias("SitePlate[002]", "labware", maps)
            self.assertEqual(resolved, "SitePlate")

    def temporary_directory(self):
        import tempfile

        return tempfile.TemporaryDirectory()

    def test_generic_instance_strip_without_yaml(self):
        self.assertEqual(resolve_alias("DiTiWaste[009]", "labware", {"labware_aliases": {}, "catalog_aliases": {}}), "DiTiWaste")

    def test_catalog_exports_pipettable_grip_site_templates_and_compat(self):
        site_guid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        child_guid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        geometry = {
            "components": [
                {
                    "guid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "name": "Carrier Nest",
                    "dimension_mm": {"x": 127.8, "y": 85.5, "z": 20.0},
                    "arrangements": [
                        {
                            "sites_in_x": 1,
                            "sites_in_y": 1,
                            "site_spacing_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
                            "site_template_identifiers": {"0": site_guid},
                            "allowed_grip_modes": {"0": ["Narrow", "Wide"]},
                        }
                    ],
                    "pipettable": {
                        "cols": 12,
                        "rows": 8,
                        "pitch_x_mm": 9.0,
                        "pitch_y_mm": -9.0,
                        "well_diameter_mm": 6.96,
                        "well_depth_mm": 10.9,
                        "well_shape": "round",
                        "max_volume_ul": 392.47,
                    },
                    "custom_attributes": {"Force": "20"},
                    "compatible_component_names": ["96 Well Flat"],
                    "compatible_component_guids": [child_guid],
                }
            ],
            "sites": [
                {
                    "guid": site_guid,
                    "location_group_name": "Nest",
                    "type_name": "NestPlatform",
                    "site_kind": "nest",
                }
            ],
            "workspaces": [],
        }
        catalog = build_labware_catalog_from_geometry(geometry)
        entry = catalog["entries"][0]
        self.assertEqual(entry["rows"], 8)
        self.assertEqual(entry["cols"], 12)
        self.assertEqual(entry["pitch_x_mm"], 9.0)
        self.assertEqual(entry["pitch_y_mm"], 9.0)
        self.assertEqual(entry["well_diameter_mm"], 6.96)
        self.assertEqual(entry["well_depth_mm"], 10.9)
        self.assertEqual(entry["well_shape"], "round")
        self.assertEqual(entry["max_volume_ul"], 392.47)
        self.assertEqual(entry["pipettable"]["cols"], 12)
        self.assertEqual(entry["grip"]["allowed_modes"]["0"], ["Narrow", "Wide"])
        self.assertEqual(entry["grip"]["force"], "20")
        self.assertEqual(entry["site_templates"][0]["guid"], site_guid)
        self.assertEqual(entry["site_templates"][0]["location_group_name"], "Nest")
        self.assertEqual(entry["compatible_component_guids"], [child_guid])
        self.assertEqual(entry["compatible_component_names"], ["96 Well Flat"])
        self.assertTrue(any(row.get("guid") == child_guid for row in entry["compatible_components"]))
        self.assertTrue(any(row.get("name") == "96 Well Flat" for row in entry["compatible_components"]))


if __name__ == "__main__":
    unittest.main()
