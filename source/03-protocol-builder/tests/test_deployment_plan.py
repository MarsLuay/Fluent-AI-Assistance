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

    def test_runtime_and_internal_metadata_are_not_deployable(self) -> None:
        source = _profile()
        source["objects"]["userspecific"][0]["state_classification"] = "instrument_local_runtime_state"
        source["objects"]["systemspecific"] = [{
            "guid": "b" * 36,
            "object_name": "Internal",
            "kind": "metadata",
            "type_id": "metadata",
            "relative_path": "SVNRoot/SystemSW_1.0/DataBase.svn",
            "content_fingerprint": "metadata",
        }]
        plan = build_deployment_plan(source, _profile(), mode="cross_target_import")
        actions = {row["action"] for row in plan["actions"]}
        findings = {row["code"] for row in plan["findings"]}
        self.assertIn("exclude_source_object", actions)
        self.assertIn("internal_svn_metadata_not_deployable", findings)
        self.assertEqual(plan["status"], "blocked")

    def test_active_recovery_and_whole_database_replacement_fail_closed(self) -> None:
        source = _profile()
        source["state_classification"] = {"active_recovery": True}
        plan = build_deployment_plan(source, _profile(), proposed_database_replacement=True)
        findings = {row["code"] for row in plan["findings"]}
        self.assertIn("active_method_recovery_requires_operator_resolution", findings)
        self.assertIn("whole_database_replacement_refused", findings)
        self.assertEqual(plan["status"], "blocked")

    def test_disposable_tip_conflict_prefers_verified_target_or_reports_loss(self) -> None:
        source = _profile()
        source["objects"]["userspecific"][0].update({
            "kind": "disposable_tip_labware",
            "object_name": "Tip Definition",
            "custom_attributes_fingerprint": "old",
        })
        target = copy.deepcopy(source)
        target["objects"]["userspecific"][0].update({
            "guid": "b" * 36,
            "custom_attributes_fingerprint": "current",
            "vendor_verified": True,
        })
        preferred = build_deployment_plan(source, target, mode="cross_target_import")
        self.assertEqual(preferred["actions"][0]["action"], "reuse_target")
        self.assertIn("tip_definition_execution_attributes_would_be_lost", {row["code"] for row in preferred["findings"]})

        target["objects"]["userspecific"][0]["vendor_verified"] = False
        review = build_deployment_plan(source, target, mode="cross_target_import")
        self.assertEqual(review["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
