"""Tests for build_fluent_registry.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tecan_tools import build_fluent_registry as registry

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_READY = PROJECT_ROOT / "source/03-protocol-builder/ready-to-import"
EXTRACTED_ROOT = next(
    iter(
        sorted(
            path
            for path in _READY.glob("*/temp_files/extracted/DataStore")
            if path.is_dir()
        )
    ),
    _READY / "_missing" / "temp_files" / "extracted" / "DataStore",
)
MANIFEST_PATH = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent/local/manifest.json"
ALIASES_PATH = PROJECT_ROOT / "source/03-protocol-builder/config/aliases/labware_aliases.yaml"
PROCEDURAL_PATH = PROJECT_ROOT / "source/04-protocol-simulator/src/data/labwareCatalog.ts"


class BuildFluentRegistryTests(unittest.TestCase):
    def test_resolve_datastore_root_from_extracted_zeia(self) -> None:
        if not EXTRACTED_ROOT.exists():
            self.skipTest("extracted ZEIA fixture not available")
        root, source_type = registry.resolve_datastore_root(EXTRACTED_ROOT.parent)
        self.assertEqual(source_type, "zeia")
        self.assertTrue((root / "SystemSpecific/Worktable/Components").exists())

    def test_parse_xcmp_extracts_mesh_and_site_refs(self) -> None:
        if not EXTRACTED_ROOT.exists():
            self.skipTest("extracted ZEIA fixture not available")
        sample = next((EXTRACTED_ROOT / "SystemSpecific/Worktable/Components").glob("*.xcmp"))
        parsed = registry.parse_xcmp_file(sample, "zeia")
        self.assertTrue(parsed["componentGuid"])
        self.assertTrue(parsed["componentName"])
        self.assertIn("sourcePath", parsed)
        self.assertIsInstance(parsed["siteIds"], list)
        self.assertIsInstance(parsed["meshRefs"], list)

    def test_load_mesh_manifest_indexes_by_guid(self) -> None:
        if not MANIFEST_PATH.exists():
            self.skipTest("mesh manifest not available")
        manifest = registry.load_mesh_manifest(MANIFEST_PATH)
        self.assertGreater(len(manifest), 0)
        sample = next(iter(manifest.values()))
        self.assertIn("bounds", sample)
        self.assertIn("vertexCount", sample)
        self.assertIn(sample["sourceType"], {"host-db", "zeia"})

    def test_build_registry_from_extracted_fixture(self) -> None:
        if not EXTRACTED_ROOT.exists() or not MANIFEST_PATH.exists():
            self.skipTest("fixture assets not available")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "registry.json"
            payload = registry.build_registry(
                install_path=EXTRACTED_ROOT,
                manifest_path=MANIFEST_PATH,
                aliases_path=ALIASES_PATH if ALIASES_PATH.exists() else None,
                procedural_catalog_path=PROCEDURAL_PATH if PROCEDURAL_PATH.exists() else None,
                hardware_manifest_path=None,
                texture_manifest_path=None,
                refresh_index=False,
                include_all_connectors=False,
            )
            output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            self.assertEqual(payload["kind"], registry.REGISTRY_KIND)
            self.assertGreater(payload["summary"]["entryCount"], 0)
            mesh_rows = [row for row in payload["entries"] if row.get("meshGuid")]
            self.assertGreater(len(mesh_rows), 0)
            joined = [row for row in payload["entries"] if row.get("meshGuid") and row.get("componentGuid")]
            self.assertGreater(len(joined), 0)
            procedural = [row for row in payload["entries"] if row.get("sourceType") == "procedural"]
            self.assertGreater(len(procedural), 0)

    def test_registry_entry_shape(self) -> None:
        row = registry.build_entry(
            mesh_guid="003b5ecc-6d56-4f44-abca-4a0494c36522",
            component_guid="11111111-1111-1111-1111-111111111111",
            object_name="Carousel Left Std",
            component_name="Carousel Left Std",
            renderer="Carousel Left Std",
            source_path="SystemSpecific/Worktable/Meshes/003b5ecc-6d56-4f44-abca-4a0494c36522.xmsh",
            source_type="host-db",
            dimensions={"xMm": 10.0, "yMm": 20.0, "zMm": 30.0},
            bounds={"min": [0, 0, 0], "max": [1, 1, 1], "size": [1, 1, 1]},
            bounds_mm={"min": [0, 0, 0], "max": [1000, 1000, 1000], "size": [1000, 1000, 1000]},
            vertex_count=72,
            triangle_count=44,
            asset_path="/models/fluent/local/003b5ecc-6d56-4f44-abca-4a0494c36522.glb",
            site_ids=["22222222-2222-2222-2222-222222222222"],
            connector_ids=["33333333-3333-3333-3333-333333333333"],
            texture_ids=["Carousel Left Std"],
            textures=[],
            aliases=["Carousel Left Std[001]"],
            snap_anchors=[],
            child_connectors=[],
        )
        required = {
            "meshGuid",
            "componentGuid",
            "objectName",
            "componentName",
            "renderer",
            "sourcePath",
            "sourceType",
            "dimensions",
            "bounds",
            "boundsMm",
            "vertexCount",
            "triangleCount",
            "assetPath",
            "siteIds",
            "connectorIds",
            "textureIds",
            "textures",
            "aliases",
            "snapAnchors",
            "childConnectors",
        }
        self.assertEqual(required, set(row.keys()))

    def test_parse_barcode_plate_texture_bindings(self) -> None:
        if not EXTRACTED_ROOT.exists():
            self.skipTest("extracted ZEIA fixture not available")
        barcode = EXTRACTED_ROOT / "SystemSpecific/Worktable/Components/45595a03-3046-4be5-a39d-666c8f050eb3.xcmp"
        if not barcode.exists():
            self.skipTest("barcode plate component fixture not available")
        parsed = registry.parse_xcmp_file(barcode, "zeia")
        self.assertIn("Barcodeplate_Top", parsed["textureIds"])
        bindings = parsed.get("textureBindings") or []
        self.assertTrue(any(row.get("textureId") == "Barcodeplate_Top" for row in bindings))


if __name__ == "__main__":
    unittest.main()
