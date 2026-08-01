"""Tests for agent brief + shared bootstrap status (MCP/CLI)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from fluent_pipeline.agent_brief import render_agent_brief
from fluent_pipeline.bootstrap_status import build_bootstrap_status
from fluent_pipeline.cli.commands.doctor import _cmd_bootstrap_status
from fluent_pipeline.mcp_gateway import ProtocolBuilderGateway
from fluent_pipeline.runner import PipelineError


class AgentBriefTests(unittest.TestCase):
    def test_render_new_script_mentions_bootstrap(self) -> None:
        text = render_agent_brief("new-script")
        self.assertIn("fluent_bootstrap_status", text)
        self.assertIn("bootstrap-status", text)
        self.assertIn("ready-to-import", text)

    def test_intent_maps_repair_keywords(self) -> None:
        from fluent_pipeline.agent_brief import resolve_agent_brief_mode

        resolved = resolve_agent_brief_mode("Script won't open; read diagnosis.md")
        self.assertEqual(resolved["mode"], "repair")
        self.assertIsNotNone(resolved["matched_keyword"])

    def test_intent_maps_new_script_zeia(self) -> None:
        from fluent_pipeline.agent_brief import resolve_agent_brief_mode

        resolved = resolve_agent_brief_mode("Use this ZEIA to make a new script")
        self.assertEqual(resolved["mode"], "new-script")

    def test_gateway_agent_brief_intent_overrides_mode(self) -> None:
        payload = ProtocolBuilderGateway().agent_brief("status", intent="launch the simulator UI")
        self.assertEqual(payload["mode"], "simulator")
        self.assertEqual(payload["resolution"]["mode"], "simulator")
        self.assertIn("simulator", payload["brief"].casefold())

    def test_gateway_resolve_brief_mode(self) -> None:
        payload = ProtocolBuilderGateway().resolve_brief_mode("wire MCP after install.ps1")
        self.assertEqual(payload["mode"], "install")
        self.assertTrue(payload["ok"])

    def test_bootstrap_status_requires_confirm_for_install(self) -> None:
        with self.assertRaisesRegex(PipelineError, "confirm_install"):
            build_bootstrap_status(install_missing=True, confirm_install=False)

    def test_bootstrap_status_next_step_when_healthy_without_projects(self) -> None:
        checks = [
            {"name": "fluentcoder root", "ok": True, "detail": "ok"},
            {"name": "shared repo venv python", "ok": True, "detail": "ok"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / "_shared" / "temp_files"
            with mock.patch(
                "fluent_pipeline.cli.commands.doctor.collect_doctor_checks",
                return_value=checks,
            ), mock.patch(
                "fluent_pipeline.bootstrap_status.list_projects",
                return_value=[],
            ), mock.patch(
                "fluent_pipeline.bootstrap_status.SHARED_TEMP_DIR",
                shared,
            ):
                payload = build_bootstrap_status(write_report=True)
                gateway_payload = ProtocolBuilderGateway().bootstrap_status(write_report=False)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["next_step"]["action"], "import_project")
            self.assertEqual(payload["next_step"]["tool"], "fluent_import_project")
            self.assertEqual(payload["next_step"]["cli"], "import-project <path-to-user-zeia>")
            self.assertIn("fluent_generate_protocol", payload["next_step"]["blocked_tools"])
            self.assertIn("fluent_import_project", payload["next_step"]["allowed_tools"])
            self.assertEqual(gateway_payload["next_step"]["action"], "import_project")
            self.assertTrue((shared / "logs" / "doctor.md").is_file())

    def test_bootstrap_status_next_step_when_doctor_fails(self) -> None:
        checks = [{"name": "shared repo venv python", "ok": False, "detail": "missing"}]
        with mock.patch(
            "fluent_pipeline.cli.commands.doctor.collect_doctor_checks",
            return_value=checks,
        ), mock.patch(
            "fluent_pipeline.bootstrap_status.list_projects",
            return_value=[{"name": "demo"}],
        ):
            payload = build_bootstrap_status(write_report=False)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["next_step"]["action"], "fix_doctor")
        self.assertTrue(payload["next_step"]["arguments"]["install_missing"])
        self.assertIn("--install-missing", payload["next_step"]["cli"])
        self.assertIn("fluent_generate_protocol", payload["next_step"]["blocked_tools"])

    def test_bootstrap_blocks_generate_until_inspected(self) -> None:
        checks = [{"name": "fluentcoder root", "ok": True, "detail": "ok"}]
        projects = [{"name": "demo"}]
        with mock.patch(
            "fluent_pipeline.cli.commands.doctor.collect_doctor_checks",
            return_value=checks,
        ), mock.patch(
            "fluent_pipeline.bootstrap_status.list_projects",
            return_value=projects,
        ):
            before = build_bootstrap_status(write_report=False, inspected=False)
            after = build_bootstrap_status(write_report=False, inspected=True)
        self.assertEqual(before["next_step"]["action"], "inspect_project")
        self.assertIn("fluent_generate_protocol", before["next_step"]["blocked_tools"])
        self.assertIn("fluent_inspect_project", before["next_step"]["allowed_tools"])
        self.assertEqual(before["next_step"]["unlock_generate_after"], ["fluent_inspect_project"])
        self.assertEqual(after["next_step"]["action"], "choose_workflow")
        self.assertNotIn("fluent_generate_protocol", after["next_step"]["blocked_tools"])
        self.assertIn("fluent_generate_protocol", after["next_step"]["allowed_tools"])
        self.assertEqual(after["next_step"]["unlock_generate_after"], [])

    def test_brief_and_bootstrap_resource_mirrors(self) -> None:
        from fluent_pipeline import mcp_server

        brief = json.loads(mcp_server.brief_resource("repair"))
        self.assertTrue(brief["ok"])
        self.assertEqual(brief["mode"], "repair")
        self.assertIn("diagnosis.md", brief["brief"])

        bad = json.loads(mcp_server.brief_resource("not-a-mode"))
        self.assertFalse(bad["ok"])

        with mock.patch(
            "fluent_pipeline.cli.commands.doctor.collect_doctor_checks",
            return_value=[{"name": "x", "ok": True, "detail": "ok"}],
        ), mock.patch(
            "fluent_pipeline.bootstrap_status.list_projects",
            return_value=[],
        ):
            bootstrap = json.loads(mcp_server.bootstrap_resource())
        self.assertIn("next_step", bootstrap)
        self.assertEqual(bootstrap["next_step"]["action"], "import_project")

    def test_gateway_agent_brief_rejects_unknown_mode(self) -> None:
        with self.assertRaisesRegex(PipelineError, "unknown agent brief mode"):
            ProtocolBuilderGateway().agent_brief("not-a-mode")

    def test_cli_bootstrap_status_prints_json_payload(self) -> None:
        fake = {
            "ok": True,
            "doctor_ok": True,
            "doctor_checks": [{"name": "fluentcoder root", "ok": True, "detail": "ok"}],
            "doctor_report": None,
            "project_count": 0,
            "projects": [],
            "next_step": {
                "action": "import_project",
                "tool": "fluent_import_project",
                "cli": "import-project <path-to-user-zeia>",
                "arguments": {"archive": "<path-to-user-zeia>"},
                "brief_mode": "new-script",
                "reason": "test",
            },
            "brief": "brief",
        }
        args = argparse.Namespace(
            install_missing=False,
            confirm_install=False,
            no_report=True,
            inspected=False,
        )
        buf = StringIO()
        err = StringIO()
        with mock.patch(
            "fluent_pipeline.bootstrap_status.build_bootstrap_status",
            return_value=fake,
        ), mock.patch.object(sys, "stdout", buf), mock.patch.object(sys, "stderr", err):
            rc = _cmd_bootstrap_status(args)
        self.assertEqual(rc, 0)
        printed = json.loads(buf.getvalue())
        self.assertEqual(printed["next_step"]["action"], "import_project")
        self.assertIn("import-project", err.getvalue())


if __name__ == "__main__":
    unittest.main()
