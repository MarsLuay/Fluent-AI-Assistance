"""Tests for merge_fluent_mesh_libraries.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tecan_tools import merge_fluent_mesh_libraries as merge

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIM_DIR = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent/local"
HOST_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
BACKUP_MANIFEST = SIM_DIR / "manifest.sim.bak.json"
BASELINE_MANIFEST = BACKUP_MANIFEST if BACKUP_MANIFEST.exists() else SIM_DIR / "manifest.json"


class MergePreservePinlistUnitTests(unittest.TestCase):
    """No baked host GUID pinlist — preserve set comes from install/ZEIA sources."""

    def test_default_preserve_pinlist_is_empty(self) -> None:
        self.assertEqual(merge.DEFAULT_PRESERVE_SIM_GUIDS, set())
        self.assertFalse(hasattr(merge, "PRESERVE_SIM_NAMES"))

    def test_mesh_guids_from_labware_catalog(self) -> None:
        mesh_a = "11111111-1111-4111-8111-111111111111"
        mesh_b = "22222222-2222-4222-8222-222222222222"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labware_catalog.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "tecan.labware_catalog.v1",
                        "entries": [
                            {"name": "Plate", "guid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "mesh_guid": mesh_a},
                            {
                                "name": "Runner",
                                "guid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                                "mesh_guids": [mesh_b, mesh_a],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            guids = merge.mesh_guids_from_preserve_source(path)
        self.assertEqual(guids, {mesh_a, mesh_b})

    def test_mesh_guids_from_xcmp_worktable_mesh_refs(self) -> None:
        mesh_guid = "33333333-3333-4333-8333-333333333333"
        xml = f"""<?xml version="1.0"?>
<root>
  <Payload>
    <ObjectName>Demo</ObjectName>
    <Reference>
      <TypeId>WorktableMesh</TypeId>
      <Guid>{mesh_guid}</Guid>
      <ObjectName>DemoMesh</ObjectName>
    </Reference>
    <Reference>
      <TypeId>WorktableSite</TypeId>
      <Guid>44444444-4444-4444-8444-444444444444</Guid>
    </Reference>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>55555555-5555-4555-8555-555555555555</GUID>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</root>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.xcmp"
            path.write_text(xml, encoding="utf-8")
            guids = merge.mesh_guids_from_xcmp(path)
        self.assertEqual(guids, {mesh_guid})

    def test_resolve_preserve_includes_cli_and_catalog(self) -> None:
        cli = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        catalog_mesh = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "labware_catalog.json"
            catalog.write_text(
                json.dumps({"entries": [{"name": "X", "mesh_guid": catalog_mesh}]}),
                encoding="utf-8",
            )
            sim_dir = root / "fluent"
            (sim_dir / "local").mkdir(parents=True)
            guids = merge.resolve_preserve_sim_guids(
                cli_guids=[cli],
                preserve_from=[str(catalog)],
                host_install=None,
                sim_dir=sim_dir,
            )
        self.assertEqual(guids, {cli, catalog_mesh})

    def test_plan_merge_preserve_flag_uses_pinlist_not_baked_names(self) -> None:
        pin = "11111111-1111-4111-8111-111111111111"
        other = "22222222-2222-4222-8222-222222222222"
        sim_library = {
            pin: merge.MeshRecord(guid=pin, name="Sim Only Pinned", source_path="a"),
            other: merge.MeshRecord(guid=other, name="Sim Only Other", source_path="b"),
        }
        host_library: dict[str, merge.MeshRecord] = {}
        actions = merge.plan_merge(sim_library, host_library, {pin})
        by_guid = {action.guid: action for action in actions}
        self.assertEqual(by_guid[pin].action, "preserve_sim_only")
        self.assertTrue(by_guid[pin].preserve)
        self.assertEqual(by_guid[other].action, "preserve_sim_only")
        self.assertFalse(by_guid[other].preserve)


class MergeFluentMeshLibrariesHostTests(unittest.TestCase):
    def test_dry_run_does_not_modify_manifest(self) -> None:
        if not BASELINE_MANIFEST.exists():
            self.skipTest("simulator manifest not available")
        if not (HOST_INSTALL / "SystemSpecific/Worktable/Meshes").exists():
            self.skipTest("host install not available")

        before = (SIM_DIR / "manifest.json").read_text(encoding="utf-8")
        preserve = merge.resolve_preserve_sim_guids(
            cli_guids=[],
            preserve_from=[],
            host_install=HOST_INSTALL,
            sim_dir=SIM_DIR,
        )
        report = merge.merge_libraries(
            sim_dir=SIM_DIR,
            host_install=HOST_INSTALL,
            preserve_sim_guids=preserve,
            apply=False,
            force_host_overlap=False,
            sim_manifest_path=BASELINE_MANIFEST,
        )
        after = (SIM_DIR / "manifest.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertFalse(report["applied"])
        self.assertGreaterEqual(len(preserve), 0)


if __name__ == "__main__":
    unittest.main()
