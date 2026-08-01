import unittest
import json

from fluent_pipeline.worktable_diff import (
    diff_worktable_requirements,
    render_worktable_changes_markdown,
    render_worktable_patch_json,
    worktable_patch_from_diff,
)


SOURCE_IR = {
    "protocol": {"name": "Source setup"},
    "worktable": {"name": "Assay_Worktable_v3", "guid": "source-guid"},
    "labware": [
        {
            "label": "SourcePlate",
            "catalog": "96 Well Flat",
            "location": "Site",
            "position": 12,
            "deck_location": "Site 12",
        },
        {
            "label": "OldTips",
            "catalog": "MCA96, 100ul, Box",
            "location": "Site",
            "position": 3,
            "deck_location": "Site 3",
        },
    ],
    "liquid_classes": [{"name": "Water Free Single"}],
    "worklists": [{"name": "old_worklist", "source": "Worklists/old.gwl"}],
    "dependencies": [{"kind": "carrier", "name": "Plate Carrier 1"}],
    "steps": [
        {"operation": "aspirate", "parameters": {"device_alias": "MCA384"}},
    ],
}


PROTOCOL_IR = {
    "protocol": {"name": "New assay"},
    "worktable": {"name": "Assay_Worktable_v3", "guid": "source-guid", "auto_place": False},
    "labware": [
        {
            "label": "SourcePlate",
            "catalog": "96 Well Flat",
            "location": "Site",
            "position": 14,
            "deck_location": "Site 14",
            "role": "source",
        },
        {
            "label": "DestinationPlate",
            "catalog": "96 Well Flat",
            "location": "Site",
            "position": 16,
            "deck_location": "Site 16",
            "role": "destination",
        },
        {
            "label": "MCA384TipBox",
            "catalog": "MCA384, 50ul, Box",
            "location": "Site",
            "position": 3,
            "deck_location": "Site 3",
            "role": "tips",
        },
    ],
    "liquid_classes": [{"name": "Water Free Single"}, {"name": "DMSO Single"}],
    "worklists": [{"name": "new_worklist", "source": "Worklists/new.gwl"}],
    "dependencies": [
        {"kind": "carrier", "name": "Plate Carrier 1"},
        {"kind": "carrier", "name": "MCA Tip Carrier"},
    ],
    "steps": [
        {"operation": "pick_up_tips", "target_labware": "MCA384TipBox"},
        {
            "operation": "aspirate",
            "target_labware": "SourcePlate",
            "liquid_class": "Water Free Single",
            "parameters": {"device_alias": "MCA384"},
        },
        {
            "operation": "dispense",
            "target_labware": "DestinationPlate",
            "liquid_class": "DMSO Single",
        },
    ],
}


class WorktableDiffTests(unittest.TestCase):
    def test_diff_reports_worktable_requirements(self):
        diff = diff_worktable_requirements(
            PROTOCOL_IR,
            source_manifest={
                "name": "source-project",
                "workspaces": [
                    {
                        "object_name": "Assay_Worktable_v3",
                        "guids": ["source-guid"],
                        "extracted_path": "Worktables/assay.xwsp",
                    }
                ],
                "labware_names": ["SourcePlate"],
                "liquid_classes": ["Water Free Single"],
                "rack_types": ["Plate Carrier 1"],
                "worklist_paths": ["Worklists/old.gwl"],
                "scripts": [{"dependencies": {"device_aliases": ["MCA384"]}}],
            },
            source_ir=SOURCE_IR,
        )

        self.assertEqual(diff["source"]["worktable"]["name"], "Assay_Worktable_v3")
        self.assertEqual([item["label"] for item in diff["missing_labware"]], ["DestinationPlate", "MCA384TipBox"])
        self.assertEqual(diff["changed_deck_positions"][0]["label"], "SourcePlate")
        self.assertEqual(diff["changed_deck_positions"][0]["source_deck_location"], "Site 12")
        self.assertEqual(diff["changed_deck_positions"][0]["required_deck_location"], "Site 14")
        self.assertIn(
            {"name": "DMSO Single", "status": "missing"},
            diff["required_liquid_classes"],
        )
        self.assertIn(
            {"name": "MCA Tip Carrier", "status": "missing"},
            diff["required_carriers"],
        )
        self.assertIn(
            {"name": "MCA384", "status": "available"},
            diff["device_aliases"],
        )
        self.assertIn(
            {"name": "Worklists/new.gwl", "status": "missing"},
            diff["worklist_paths"],
        )
        self.assertEqual(diff["summary"]["overall_severity"], "blocking")

        markdown = render_worktable_changes_markdown(diff)
        self.assertIn("## Missing Labware", markdown)
        self.assertIn("## Changed Deck Positions", markdown)
        self.assertIn("## Required Liquid Classes", markdown)
        self.assertIn("## Required Tip Boxes", markdown)
        self.assertIn("## Required Carriers", markdown)
        self.assertIn("## Device Aliases", markdown)
        self.assertIn("## Worklist Paths", markdown)
        self.assertIn("## Automatic FluentControl Setup Steps", markdown)
        self.assertIn("## Manual FluentControl Setup Steps", markdown)
        self.assertIn("Move `SourcePlate` from `Site 12` to `Site 14`.", markdown)

        patch = worktable_patch_from_diff(diff)
        self.assertEqual(patch["kind"], "worktable_patch")
        self.assertEqual(patch["schema_version"], "tecan.worktable_patch.v1")
        self.assertEqual(patch["summary"]["overall_severity"], "blocking")
        self.assertEqual(
            patch["summary"]["severity_counts"],
            {"safe": 4, "needs_review": 1, "blocking": 6},
        )
        self.assertIn(
            {
                "id": "labware.add_labware.destinationplate",
                "category": "labware",
                "action": "add_labware",
                "severity": "blocking",
                "status": "missing",
                "target": {
                    "label": "DestinationPlate",
                    "catalog": "96 Well Flat",
                    "deck_location": "Site 16",
                    "role": "destination",
                },
                "reason": "not found in source ZEIA context",
                "diff_path": "/missing_labware/0",
            },
            patch["operations"],
        )
        self.assertIn(
            {
                "id": "labware.move_labware.sourceplate",
                "category": "labware",
                "action": "move_labware",
                "severity": "needs_review",
                "status": "changed",
                "source": {
                    "label": "SourcePlate",
                    "catalog": "96 Well Flat",
                    "deck_location": "Site 12",
                },
                "target": {
                    "label": "SourcePlate",
                    "catalog": "96 Well Flat",
                    "deck_location": "Site 14",
                },
                "reason": "required deck position differs from the source ZEIA context",
                "diff_path": "/changed_deck_positions/0",
            },
            patch["operations"],
        )

        rendered_patch = json.loads(render_worktable_patch_json(diff))
        self.assertEqual(rendered_patch["summary"]["overall_severity"], "blocking")
        self.assertEqual(rendered_patch["manual_setup_steps"][0]["severity"], "safe")

    def test_diff_uses_real_workspace_geometry_for_source_positions(self):
        diff = diff_worktable_requirements(
            {
                "protocol": {"name": "Pinned assay"},
                "worktable": {"name": "Pinned Worktable", "guid": "workspace-guid"},
                "labware": [
                    {
                        "label": "PinnedCarrier[001]",
                        "catalog": "5 Nest Hotel",
                        "deck_location": "Protocol Site 1",
                    }
                ],
            },
            source_manifest={
                "name": "geometry-source",
                "workspaces": [
                    {
                        "object_name": "Wrong Worktable",
                        "workspace_guid": "wrong-workspace-guid",
                        "extracted_path": "DataStore/SystemSpecific/Worktable/Workspaces/wrong-workspace-guid.xwsp",
                    },
                    {
                        "object_name": "Pinned Worktable",
                        "workspace_guid": "workspace-guid",
                        "extracted_path": "DataStore/SystemSpecific/Worktable/Workspaces/workspace-guid.xwsp",
                    },
                ],
                "worktable_geometry": {
                    "schema_version": "tecan.worktable_geometry.v1",
                    "workspace_count": 2,
                    "component_count": 1,
                    "site_count": 1,
                    "connector_count": 1,
                    "pin_sites": [{"pin_name": "WorktablePin_MiddleFront"}],
                    "workspaces": [
                        {
                            "guid": "wrong-workspace-guid",
                            "name": "Wrong Worktable",
                            "placements": [
                                {
                                    "label": "OtherCarrier[001]",
                                    "catalog": "Other Carrier",
                                    "deck_location": "OtherPin via connector other-connector at (0, 0, 0) mm",
                                }
                            ],
                        },
                        {
                            "guid": "workspace-guid",
                            "name": "Pinned Worktable",
                            "placements": [
                                {
                                    "label": "PinnedCarrier[001]",
                                    "catalog": "5 Nest Hotel",
                                    "site_name": "WorktablePin_MiddleFront",
                                    "pin_name": "WorktablePin_MiddleFront",
                                    "site_path": [0],
                                    "deck_location": (
                                        "WorktablePin_MiddleFront via connector connector-guid "
                                        "at (12.5, -16.5, 3) mm"
                                    ),
                                    "connector_guid": "connector-guid",
                                    "connector_site_guid": "site-pin-guid",
                                    "connector_site_name": "WorktablePin_MiddleFront",
                                    "connector_component_guid": "carrier-guid",
                                    "connector_position_in_parent_mm": {"x": 12.5, "y": -16.5, "z": 3.0},
                                    "connector_orientation_euler_deg": {
                                        "phi": 90.0,
                                        "theta": 0.0,
                                        "psi": 180.0,
                                    },
                                }
                            ],
                        },
                    ],
                },
            },
        )

        self.assertEqual(diff["source"]["worktable"]["guid"], "workspace-guid")
        self.assertEqual(diff["missing_labware"], [])
        self.assertEqual(len(diff["changed_deck_positions"]), 1)
        changed = diff["changed_deck_positions"][0]
        self.assertEqual(changed["label"], "PinnedCarrier[001]")
        self.assertEqual(
            changed["source_deck_location"],
            "WorktablePin_MiddleFront via connector connector-guid at (12.5, -16.5, 3) mm",
        )
        self.assertEqual(changed["source_geometry"]["pin_name"], "WorktablePin_MiddleFront")
        self.assertEqual(changed["source_geometry"]["connector_guid"], "connector-guid")
        self.assertEqual(changed["source_geometry"]["connector_position_in_parent_mm"]["x"], 12.5)

        markdown = render_worktable_changes_markdown(diff)
        self.assertIn("Source workspace geometry", markdown)
        self.assertIn("WorktablePin_MiddleFront", markdown)
        self.assertIn("Source Connector offset", markdown)
        self.assertIn("Phi=90.0, Theta=0.0, Psi=180.0", markdown)

        patch = worktable_patch_from_diff(diff)
        move_ops = [item for item in patch["operations"] if item["action"] == "move_labware"]
        self.assertEqual(len(move_ops), 1)
        self.assertEqual(move_ops[0]["source"]["geometry"]["connector_guid"], "connector-guid")
        self.assertEqual(move_ops[0]["source"]["geometry"]["connector_position_in_parent_mm"]["y"], -16.5)

    def test_diff_resolves_configured_aliases(self):
        diff = diff_worktable_requirements(
            {
                "protocol": {"name": "Alias assay"},
                "labware": [
                    {"label": "MCA384TipBox", "catalog": "MCA384, 50ul, Box", "role": "tips"},
                ],
                "liquid_classes": [{"name": "Water Free Single"}],
                "steps": [
                    {
                        "operation": "aspirate",
                        "target_labware": "MCA384TipBox",
                        "liquid_class": "Water Free Single",
                        "parameters": {"device_alias": "MCA384"},
                    }
                ],
            },
            source_manifest={
                "labware_names": ["MCA384TipBox[001]"],
                "liquid_classes": ["Water Free Single[001]"],
                "scripts": [{"dependencies": {"device_aliases": ["MCA384[001]"]}}],
            },
        )

        self.assertEqual(diff["missing_labware"], [])
        self.assertIn({"name": "Water Free Single", "status": "available"}, diff["required_liquid_classes"])
        self.assertIn({"name": "MCA384", "status": "available"}, diff["device_aliases"])
        self.assertEqual(diff["required_tip_boxes"][0]["status"], "available")

    def test_diff_resolves_dynamic_loop_labware_labels_by_base(self):
        diff = diff_worktable_requirements(
            {
                "protocol": {"name": "Dynamic tubes"},
                "steps": [
                    {
                        "operation": "loop_over_wells",
                        "target_labware": "SampleSourceTube[LoopAddSourceTubes]",
                    },
                    {
                        "operation": "loop_over_wells",
                        "target_labware": "SampleDestTube1[LoopAddDestTubes]",
                    },
                    {
                        "operation": "loop_over_wells",
                        "target_labware": "SampleDestTube2[LoopAddDestTubes]",
                    },
                    {
                        "operation": "loop_over_wells",
                        "target_labware": "UnmatchedTube[LoopIndex]",
                    },
                ],
            },
            source_manifest={
                "labware_names": [
                    "SampleSourceTube[001]",
                    "SampleDestTube1[NumSourceTubes]",
                    "SampleDestTube2[008]",
                ],
                "scripts": [],
            },
        )

        self.assertEqual(
            [item["label"] for item in diff["missing_labware"]],
            ["UnmatchedTube[LoopIndex]"],
        )

    def test_diff_does_not_template_match_numbered_labware_labels(self):
        diff = diff_worktable_requirements(
            {
                "protocol": {"name": "Numbered tubes"},
                "steps": [{"operation": "aspirate", "target_labware": "Tube[001]"}],
            },
            source_manifest={"labware_names": ["Tube[002]"], "scripts": []},
        )

        self.assertEqual([item["label"] for item in diff["missing_labware"]], ["Tube[001]"])


if __name__ == "__main__":
    unittest.main()
