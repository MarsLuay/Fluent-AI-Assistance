"""Compact inspect + project_query token caps."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fluent_pipeline.application_services import (
    ProjectInspectionRequest,
    inspect_project,
)
from fluent_pipeline.project_context import (
    PROJECT_QUERY_MAX_LIMIT,
    clamp_project_query_limit,
    compact_project_match,
    inspection_payload,
    query_project,
)


def _synthetic_context(*, object_count: int = 30) -> SimpleNamespace:
    objects = [
        {
            "kind": "labware",
            "object_name": f"StubLabware{i:03d}",
            "entry": f"Objects/StubLabware{i:03d}.xcmp",
            "dependencies": {"scripts": [f"dep-{j}" for j in range(40)]},
            "extracted_fields": {"noise": list(range(50))},
        }
        for i in range(object_count)
    ]
    scripts = [
        {
            "kind": "script",
            "object_name": "StubScript",
            "entry": "Scripts/StubScript.xscr",
            "dependencies": {"objects": ["a"] * 20},
        }
    ]
    root = Path(tempfile.mkdtemp())
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "project_report.md").write_text("# stub\n", encoding="utf-8")
    return SimpleNamespace(
        name="stub-project",
        root=root,
        manifest={
            "name": "stub-project",
            "scripts": scripts,
            "objects": objects,
            "workspaces": [{"object_name": "StubWs"}],
            "snapshot_evidence": [],
            "entry_count": object_count + 2,
            "source_archive": "/tmp/stub.zeia",
            "imported_at": "2026-01-01T00:00:00+00:00",
            "full_zeia_export": {
                "required": True,
                "status": "accepted",
                "accepted": True,
                "summary": "stub",
                "warnings": [{"code": "w1"}, {"code": "w2"}],
            },
        },
    )


class ProjectQueryCompactTests(unittest.TestCase):
    def test_inspection_payload_omits_full_manifest(self) -> None:
        ctx = _synthetic_context()
        payload = inspection_payload(ctx)
        raw = json.dumps(payload)
        self.assertNotIn("manifest", payload)
        self.assertIn("manifest_path", payload)
        self.assertIn("summary", payload)
        self.assertNotIn("StubLabware000", raw)
        self.assertLess(len(raw), 5000)
        self.assertEqual(payload["summary"]["object_count"], 30)
        self.assertEqual(payload["summary"]["full_zeia_export"]["warning_count"], 2)
        self.assertNotIn("warnings", payload["summary"]["full_zeia_export"])

    def test_inspect_project_to_dict_is_compact(self) -> None:
        ctx = _synthetic_context()
        with mock.patch(
            "fluent_pipeline.application_services.load_project",
            return_value=ctx,
        ):
            result = inspect_project(ProjectInspectionRequest(context_name="stub-project"))
        payload = result.to_dict()
        self.assertLess(len(json.dumps(payload)), 5000)
        self.assertEqual(payload["report_path"], str(ctx.root / "project_report.md"))

    def test_query_respects_limit_and_compacts_matches(self) -> None:
        ctx = _synthetic_context(object_count=40)
        payload = query_project(ctx, "StubLabware", kind="labware", limit=5)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["match_count"], 5)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["limit"], 5)
        match = payload["matches"][0]
        self.assertNotIn("dependencies", match)
        self.assertNotIn("extracted_fields", match)
        self.assertIn("object_name", match)
        self.assertLess(len(json.dumps(payload)), 8000)

    def test_clamp_limit_caps_at_max(self) -> None:
        self.assertEqual(clamp_project_query_limit(999), PROJECT_QUERY_MAX_LIMIT)
        self.assertEqual(clamp_project_query_limit(None), 20)

    def test_compact_match_strips_heavy_fields(self) -> None:
        compact = compact_project_match(
            {
                "kind": "script",
                "object_name": "A",
                "dependencies": {"x": [1, 2, 3]},
                "summary": "x" * 400,
            }
        )
        self.assertNotIn("dependencies", compact)
        self.assertTrue(compact["summary"].endswith("..."))
        self.assertLessEqual(len(compact["summary"]), 240)


if __name__ == "__main__":
    unittest.main()
