"""Default worktable selection must follow Script→WorktableWorkspace / recipe pins."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from fluent_pipeline.generation_workflow import _default_worktable
from fluent_pipeline.worktable_diff import (
    _merge_manifest_source,
    _select_workspace_from_script_refs,
    _source_geometry_workspaces,
)


def _script(name: str, *, wt_name: str = "", wt_guid: str = "") -> dict:
    refs = []
    if wt_name or wt_guid:
        refs.append(
            {
                "type_id": "WorktableWorkspace",
                "object_name": wt_name,
                "guid": wt_guid,
            }
        )
    return {"object_name": name, "references": refs, "dependencies": {}}


class DefaultWorktableTests(unittest.TestCase):
    def test_prefers_recipe_match_over_first_script(self) -> None:
        scripts = [
            _script("ScriptA", wt_name="WT_A", wt_guid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            _script("ScriptB", wt_name="WT_B", wt_guid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ]
        context = SimpleNamespace(
            manifest={
                "workspaces": [
                    {"object_name": "WT_A", "guids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]},
                    {"object_name": "WT_B", "guids": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]},
                ]
            }
        )
        worktable = _default_worktable(
            context,
            scripts,
            preferred={"name": "WT_B", "guid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
        )
        self.assertEqual(worktable["name"], "WT_B")
        self.assertEqual(worktable["guid"], "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    def test_uses_script_consensus_not_workspaces_zero(self) -> None:
        scripts = [
            _script("ScriptA", wt_name="Pinned", wt_guid="cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
            _script("ScriptB", wt_name="Pinned", wt_guid="cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        ]
        context = SimpleNamespace(
            manifest={
                "workspaces": [
                    {"object_name": "OtherFirst", "guids": ["dddddddd-dddd-4ddd-8ddd-dddddddddddd"]},
                    {"object_name": "Pinned", "guids": ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"]},
                ]
            }
        )
        worktable = _default_worktable(context, scripts)
        self.assertEqual(worktable["name"], "Pinned")
        self.assertEqual(worktable["guid"], "cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    def test_named_ref_survives_duplicate_workspace_guid_dependency(self) -> None:
        scripts = [
            {
                "object_name": "Hello",
                "references": [
                    {
                        "type_id": "WorktableWorkspace",
                        "object_name": "Pinned",
                        "guid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    }
                ],
                "dependencies": {
                    "workspace_guids": ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"],
                },
            }
        ]
        worktable = _default_worktable(None, scripts)
        self.assertEqual(worktable["name"], "Pinned")
        self.assertEqual(worktable["guid"], "cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    def test_refuses_ambiguous_workspaces_zero(self) -> None:
        context = SimpleNamespace(
            manifest={
                "workspaces": [
                    {"object_name": "First", "guids": ["11111111-1111-4111-8111-111111111111"]},
                    {"object_name": "Second", "guids": ["22222222-2222-4222-8222-222222222222"]},
                ]
            }
        )
        worktable = _default_worktable(context, [])
        self.assertEqual(worktable, {"name": "", "guid": "", "auto_place": False})

    def test_single_manifest_workspace_ok(self) -> None:
        context = SimpleNamespace(
            manifest={
                "workspaces": [
                    {"object_name": "Only", "guids": ["33333333-3333-4333-8333-333333333333"]},
                ]
            }
        )
        worktable = _default_worktable(context, [])
        self.assertEqual(worktable["name"], "Only")
        self.assertEqual(worktable["guid"], "33333333-3333-4333-8333-333333333333")


class WorktableDiffSelectionTests(unittest.TestCase):
    def test_merge_manifest_uses_script_ref_not_first_workspace(self) -> None:
        manifest = {
            "workspaces": [
                {
                    "object_name": "OtherFirst",
                    "guids": ["dddddddd-dddd-4ddd-8ddd-dddddddddddd"],
                    "extracted_path": "extracted/OtherFirst.xwsp",
                },
                {
                    "object_name": "Pinned",
                    "guids": ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"],
                    "extracted_path": "extracted/Pinned.xwsp",
                },
            ],
            "scripts": [
                {
                    "object_name": "DemoScript",
                    "dependencies": {"workspace_guids": ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"]},
                }
            ],
        }
        context = {
            "name": "",
            "worktable": {},
            "labware_by_label": {},
            "labware_catalogs": set(),
            "liquid_classes": set(),
            "carriers": set(),
            "device_aliases": set(),
            "worklist_paths": set(),
            "worktable_geometry": {},
        }
        _merge_manifest_source(context, manifest, {}, requested_worktable={})
        self.assertEqual(context["worktable"]["name"], "Pinned")
        self.assertEqual(context["worktable"]["guid"], "cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    def test_geometry_workspaces_refuse_order_fallback(self) -> None:
        geometry = {
            "workspaces": [
                {"name": "A", "guid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
                {"name": "B", "guid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
            ]
        }
        selected = _source_geometry_workspaces({}, {}, geometry, requested_worktable={})
        self.assertEqual(selected, [])

    def test_select_workspace_from_script_refs(self) -> None:
        workspaces = [
            {"object_name": "A", "guids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]},
            {"object_name": "B", "guids": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]},
        ]
        manifest = {
            "scripts": [
                {"dependencies": {"workspace_guids": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]}},
            ]
        }
        selected = _select_workspace_from_script_refs(manifest, workspaces)
        assert selected is not None
        self.assertEqual(selected["object_name"], "B")


if __name__ == "__main__":
    unittest.main()
