import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fluent_pipeline.config as config
from fluent_pipeline.cli.parser import _build_parser
from fluent_pipeline.cli.requests import project_import_request_from_cli


class ExplicitZeiaInputTests(unittest.TestCase):
    def _parse_import(self, archive: str):
        return _build_parser().parse_args(["import-project", archive])

    def test_obsidian_discovery_helpers_are_not_public_runtime_api(self):
        self.assertFalse(hasattr(config, "obsidian_vault_root"))
        self.assertFalse(hasattr(config, "discover_vault_root_zeia"))

    def test_explicit_zeia_wins_inside_unrelated_obsidian_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "unrelated-vault"
            vault.mkdir()
            (vault / ".obsidian").mkdir()
            explicit = vault / "explicit.zeia"
            newer = vault / "newer-but-unrequested.zeia"
            explicit.write_bytes(b"explicit")
            newer.write_bytes(b"newer")

            args = self._parse_import("explicit.zeia")
            with mock.patch("fluent_pipeline.config.Path.cwd", return_value=vault):
                request = project_import_request_from_cli(args)

            self.assertEqual(request.archive, explicit.resolve())
            self.assertNotEqual(request.archive, newer.resolve())

    def test_explicit_zeia_resolution_is_identical_without_obsidian(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp) / "plain-directory"
            working_dir.mkdir()
            explicit = working_dir / "explicit.zeia"
            explicit.write_bytes(b"explicit")

            args = self._parse_import("explicit.zeia")
            with mock.patch("fluent_pipeline.config.Path.cwd", return_value=working_dir):
                request = project_import_request_from_cli(args)

            self.assertEqual(request.archive, explicit.resolve())

    def test_runtime_package_contains_no_obsidian_discovery_path(self):
        package_root = Path(config.__file__).resolve().parent
        forbidden = (
            "obsidian_vault_root",
            "discover_vault_root_zeia",
            '".obsidian"',
            "'.obsidian'",
            '"Home.md"',
            "'Home.md'",
        )

        violations = []
        for path in package_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(package_root)}: {token}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
