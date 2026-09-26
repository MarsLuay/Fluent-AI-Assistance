"""Focused deterministic tests for target-bound promotion plans."""

from __future__ import annotations

import copy
import unittest

from fluent_pipeline.deployment_plan import build_deployment_plan, target_drift_diagnostics
from fluent_pipeline.target_datastore import build_target_datastore_profile


def _profile(*, guid: str = "a" * 36, content: str = "one", family: str = "FluentControl") -> dict:
    return {
        "schema_version": "tecan.target_datastore.v1",
        "status": "bound",
        "target_unbound": False,
        "target_profile_id": "fixture",
        "software": {"family": family, "version": "3.8", "build": "3.8.16"},
        "objects": {"userspecific": [{
            "guid": guid,
            "object_name": "Method",
            "object_subfolder_path": "Scripts",
            "type_id": "Script",
            "kind": "script",
            "relative_path": guid + ".xscr",
            "content_fingerprint": content,
        }], "systemspecific": []},
        "state_classification": {},
        "provenance": {"source": "fixture"},
        "target_roots": {"userspecific_provided": True, "systemspecific_provided": False},
        "unknowns": [],
    }


class DeploymentPlanTests(unittest.TestCase):
    def test_same_target_is_deterministic_and_reuses_exact_object(self) -> None:
        source = _profile()
        target = copy.deepcopy(source)
        first = build_deployment_plan(source, target)
        second = build_deployment_plan(target, source)
        self.assertEqual(first["status"], "ready_for_same_target_dropin")
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["actions"][0]["action"], "reuse_target")

    def test_same_name_different_content_requires_review(self) -> None:
        plan = build_deployment_plan(_profile(content="source"), _profile(content="target"), mode="cross_target_import")
        self.assertEqual(plan["status"], "needs_review")
        self.assertIn("target_object_conflict", {row["code"] for row in plan["findings"]})

    def test_missing_target_object_is_import_dependency(self) -> None:
        target = _profile()
        target["objects"]["userspecific"] = []
        plan = build_deployment_plan(_profile(), target, mode="cross_target_import")
        self.assertEqual(plan["status"], "ready_for_import")
        self.assertEqual(plan["actions"][0]["action"], "import_dependency")

    def test_unbound_target_and_family_mismatch_fail_closed(self) -> None:
        source = _profile(family="FluentControl")
        unbound = build_target_datastore_profile()
        blocked = build_deployment_plan(source, unbound)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("target_profile_required", {row["code"] for row in blocked["findings"]})

        mismatch = build_deployment_plan(source, _profile(family="vControl"), mode="cross_target_import")
        self.assertEqual(mismatch["status"], "blocked")
        self.assertIn("source_target_software_family_mismatch", {row["code"] for row in mismatch["findings"]})

    def test_unknown_family_cannot_determine_and_match_stays_ready(self) -> None:
        source = _profile(family="FluentControl")
        target = _profile(family="FluentControl")
        matched = build_deployment_plan(source, target, mode="cross_target_import")
        self.assertNotIn("cannot_determine", matched["status"])
        self.assertEqual(matched["software_families"], {"source": "FluentControl", "target": "FluentControl"})

        unknown = dict(source)
        unknown["software"] = {"family": "", "version": "3.8", "build": "3.8.16"}
        plan = build_deployment_plan(unknown, target, mode="cross_target_import")
        self.assertEqual(plan["status"], "cannot_determine")
        self.assertIn("source_target_software_family_unknown", {row["code"] for row in plan["findings"]})
        self.assertNotEqual(plan["status"], "ready_for_import")

    def test_drift_and_external_relocation_are_explicit(self) -> None:
        source = _profile()
        target = _profile()
        plan = build_deployment_plan(
            source,
            target,
            external_files=[{"source_path": "media/a.png", "target_path": "media/b.png"}],
        )
        changed = _profile(guid="b" * 36)
        self.assertEqual(target_drift_diagnostics(plan, changed)[0]["code"], "target_profile_drift")
        self.assertIn("relocate_external_file", {row["action"] for row in plan["actions"]})


if __name__ == "__main__":
    unittest.main()
