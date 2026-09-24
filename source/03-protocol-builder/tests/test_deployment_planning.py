"""Regression tests for the shared target-aware planning service and CLI."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.application_services import DeploymentPlanRequest, plan_deployment
from fluent_pipeline.cli.commands.deployment import _cmd_plan_deployment
from fluent_pipeline.cli.parser import _build_parser
from fluent_pipeline.deployment_plan import render_deployment_plan_markdown
from fluent_pipeline.target_datastore import build_target_datastore_profile, write_target_datastore_profile


def _profile(*, guid: str = "a" * 36, content: str = "one") -> dict:
    return {
        "schema_version": "tecan.target_datastore.v1",
        "status": "bound",
        "target_unbound": False,
        "target_profile_id": "fixture",
        "software": {"family": "FluentControl", "version": "3.8", "build": "3.8.16"},
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


class DeploymentPlanningTests(unittest.TestCase):
    def test_shared_service_is_canonical_and_reports_fingerprints(self) -> None:
        result = plan_deployment(DeploymentPlanRequest(source_profile=_profile(), target_profile=_profile()))

        self.assertTrue(result.ok)
        self.assertEqual(result.plan["status"], "ready_for_same_target_dropin")
        self.assertEqual(result.plan["source_fingerprint"], result.plan["target_fingerprint"])
        self.assertIn("target fingerprint", render_deployment_plan_markdown(result.plan).lower())

    def test_target_drift_invalidates_plan(self) -> None:
        result = plan_deployment(
            DeploymentPlanRequest(
                source_profile=_profile(),
                target_profile=_profile(),
                current_target_profile=_profile(guid="b" * 36),
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.plan["status"], "blocked")
        self.assertEqual(result.drift[0]["code"], "target_profile_drift")
        self.assertIn("target_profile_drift", {item["code"] for item in result.plan["findings"]})

    def test_unknown_target_is_explicit_and_non_mutating(self) -> None:
        result = plan_deployment(DeploymentPlanRequest(source_profile=_profile()))

        self.assertFalse(result.ok)
        self.assertEqual(result.plan["status"], "blocked")
        self.assertTrue(result.plan["destructive_target_mutation"] is False)
        self.assertTrue(result.plan["runtime_state_excluded"] is True)

    def test_cli_registers_one_shared_planning_command(self) -> None:
        args = _build_parser().parse_args(["plan-deployment", "--source-profile", "source.json", "--no-target"])
        self.assertEqual(args.func.__name__, "_cmd_plan_deployment")

    def test_cli_consumes_explicit_profiles_and_writes_canonical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.json"
            target_path = root / "target.json"
            report_path = root / "plan.md"
            json_path = root / "plan.json"
            write_target_datastore_profile(source_path, _profile())
            write_target_datastore_profile(target_path, _profile())
            args = _build_parser().parse_args([
                "plan-deployment",
                "--source-profile", str(source_path),
                "--target-profile", str(target_path),
                "--json-out", str(json_path),
                "--report", str(report_path),
                "--json",
            ])

            rc = _cmd_plan_deployment(args)

            self.assertEqual(rc, 0)
            self.assertTrue(report_path.exists())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "tecan.deployment_plan.v1")
            self.assertEqual(payload["status"], "ready_for_same_target_dropin")


if __name__ == "__main__":
    unittest.main()
