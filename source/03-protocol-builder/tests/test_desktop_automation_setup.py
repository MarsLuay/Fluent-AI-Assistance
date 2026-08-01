from __future__ import annotations

import unittest
from unittest import mock

from fluent_pipeline import bootstrap


class DesktopAutomationSetupTests(unittest.TestCase):
    def test_vendored_requirements_match_installer_manifest(self):
        expected = "\n".join(bootstrap.DESKTOP_AUTOMATION_REQUIREMENTS) + "\n"
        actual = bootstrap.DESKTOP_AUTOMATION_REQUIREMENTS_FILE.read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_vendored_constraints_match_installer_lock(self):
        expected = "\n".join(bootstrap.DESKTOP_AUTOMATION_CONSTRAINTS) + "\n"
        actual = bootstrap.DESKTOP_AUTOMATION_CONSTRAINTS_FILE.read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_installer_prefers_wheelhouse_when_wheels_exist(self):
        commands = []

        def fake_run(command, *, cwd):
            commands.append(command)

        class FakeWheelhouse:
            def mkdir(self, *, parents, exist_ok):
                self.mkdir_args = {"parents": parents, "exist_ok": exist_ok}

            def glob(self, pattern):
                self.glob_pattern = pattern
                return iter(["cached.whl"])

            def __str__(self):
                return "fake-wheelhouse"

        wheelhouse = FakeWheelhouse()
        with mock.patch.object(bootstrap, "ensure_desktop_automation_manifests"):
            with mock.patch.object(bootstrap, "DESKTOP_AUTOMATION_WHEELHOUSE", wheelhouse):
                with mock.patch.object(bootstrap, "_desktop_automation_wheelhouse_is_current", return_value=True):
                    with mock.patch.object(bootstrap, "_run_setup_command", side_effect=fake_run):
                        bootstrap.install_desktop_automation_dependencies(bootstrap.Path("python"))

        self.assertEqual(len(commands), 1)
        self.assertIn("--no-index", commands[0])
        self.assertIn("--find-links", commands[0])

    def test_bootstrap_finishes_with_pip_check(self):
        commands = []

        def fake_run(command, *, cwd):
            commands.append(command)

        with mock.patch.object(bootstrap, "_run_setup_command", side_effect=fake_run):
            bootstrap.bootstrap_workspace(bootstrap.Path("python"), include_desktop_automation=False)

        self.assertTrue(commands)
        self.assertEqual(commands[-1], ["python", "-m", "pip", "check"])

    def test_main_supports_skip_desktop_automation(self):
        with mock.patch.object(bootstrap, "bootstrap_workspace") as bootstrap_workspace:
            exit_code = bootstrap.main(["--skip-desktop-automation"])

        bootstrap_workspace.assert_called_once_with(include_desktop_automation=False)
        self.assertEqual(exit_code, 0)
