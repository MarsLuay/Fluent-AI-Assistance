"""Unit tests for MCP install smoke validators."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_smoke_module():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "mcp"
        / "smoke_mcp.py"
    )
    spec = importlib.util.spec_from_file_location("smoke_mcp_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SmokeMcpValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.smoke = _load_smoke_module()

    def test_validate_passes_with_full_surface(self) -> None:
        tools = sorted(self.smoke.REQUIRED_TOOLS)
        next_step = {
            "action": "inspect_project",
            "tool": "fluent_inspect_project",
            "cli": "project-info",
            "arguments": {},
            "brief_mode": "new-script",
            "reason": "ok",
            "allowed_tools": ["fluent_inspect_project"],
            "blocked_tools": ["fluent_generate_protocol"],
            "unlock_generate_after": ["fluent_inspect_project"],
        }
        failures = self.smoke.validate_smoke_result(
            tool_names=tools,
            status_ok=True,
            bootstrap={"ok": True, "next_step": next_step},
        )
        self.assertEqual(failures, [])

    def test_validate_fails_when_bootstrap_tool_missing(self) -> None:
        tools = sorted(self.smoke.REQUIRED_TOOLS - {"fluent_bootstrap_status"})
        failures = self.smoke.validate_smoke_result(
            tool_names=tools,
            status_ok=True,
            bootstrap={"ok": True, "next_step": {"action": "x", "tool": "y"}},
        )
        self.assertTrue(any("fluent_bootstrap_status" in item for item in failures))

    def test_validate_fails_when_next_step_absent(self) -> None:
        tools = sorted(self.smoke.REQUIRED_TOOLS)
        failures = self.smoke.validate_smoke_result(
            tool_names=tools,
            status_ok=True,
            bootstrap={"ok": True},
        )
        self.assertTrue(any("next_step" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
