"""Tests for extract_fluent_meshes mesh GUID list filtering."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tecan_tools import extract_fluent_meshes as extract


class ExtractMeshGuidFilterTests(unittest.TestCase):
    def test_catalog_pinlist_loads_mesh_guids(self) -> None:
        mesh_a = "11111111-1111-4111-8111-111111111111"
        mesh_b = "22222222-2222-4222-8222-222222222222"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labware_catalog.json"
            path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {"mesh_guid": mesh_a},
                            {"mesh_guids": [mesh_b]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(extract.mesh_guids_from_catalog_or_pinlist(path), {mesh_a, mesh_b})

    def test_filter_source_meshes_by_stem_guid(self) -> None:
        keep = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        drop = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        meshes = [
            extract.SourceMesh(path=f"DataStore/SystemSpecific/Worktable/Meshes/{keep}.xmsh", text="<x/>"),
            extract.SourceMesh(path=f"DataStore/SystemSpecific/Worktable/Meshes/{drop}.xmsh", text="<x/>"),
        ]
        selected = extract.filter_source_meshes(meshes, {keep})
        self.assertEqual([item.path for item in selected], [meshes[0].path])

    def test_resolve_filter_none_without_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract.resolve_mesh_guid_filter(
                cli_guids=[],
                from_paths=[],
                output_dir=Path(tmp),
                only_listed=False,
            )
        self.assertIsNone(result)

    def test_resolve_filter_from_cli_guid(self) -> None:
        guid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        with tempfile.TemporaryDirectory() as tmp:
            result = extract.resolve_mesh_guid_filter(
                cli_guids=[guid],
                from_paths=[],
                output_dir=Path(tmp),
                only_listed=False,
            )
        self.assertEqual(result, {guid})

    def test_only_listed_requires_guids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                extract.resolve_mesh_guid_filter(
                    cli_guids=[],
                    from_paths=[],
                    output_dir=Path(tmp),
                    only_listed=True,
                )

    def test_refuse_tracked_fluent_root(self) -> None:
        with self.assertRaises(SystemExit):
            extract.refuse_tracked_fluent_mesh_root(extract.DEFAULT_FLUENT_MODELS)

    def test_portable_source_label_drops_absolute_foreign_path(self) -> None:
        label = extract.portable_source_label(Path("/tmp/FullExport.zeia"))
        self.assertEqual(label, "FullExport.zeia")
        self.assertNotIn("/tmp", label)


if __name__ == "__main__":
    unittest.main()
