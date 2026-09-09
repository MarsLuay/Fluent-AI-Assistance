import argparse
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import fluent_pipeline.config as config
from fluent_pipeline.application_services import ProjectImportRequest, import_project
from fluent_pipeline.cli.requests import project_import_request_from_cli
from fluent_pipeline.mcp_requests import project_import_request_from_mcp


class ExplicitZeiaInputTests(unittest.TestCase):
    def test_config_exposes_no_obsidian_auto_discovery_helpers(self):
        self.assertFalse(hasattr(config, "obsidian_vault_root"))
        self.assertFalse(hasattr(config, "discover_vault_root_zeia"))

    def test_runtime_package_contains_no_obsidian_auto_discovery(self):
        package_root = Path(config.__file__).resolve().parent
        for path in package_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("obsidian_vault_root", source, str(path))
            self.assertNotIn("discover_vault_root_zeia", source, str(path))
            self.assertNotIn('".obsidian"', source, str(path))

    def test_cli_explicit_archive_wins_inside_unrelated_obsidian_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requested = root / "requested.zeia"
            requested.write_bytes(b"requested")

            vault = root / "unrelated-vault"
            (vault / ".obsidian").mkdir(parents=True)
            wrong = vault / "newer-wrong.zeia"
            wrong.write_bytes(b"wrong")
            os.utime(wrong, (requested.stat().st_mtime + 60, requested.stat().st_mtime + 60))

            inside_vault = vault / "notes"
            inside_vault.mkdir()
            plain_directory = root / "plain"
            plain_directory.mkdir()

            args = argparse.Namespace(
                archive=str(requested),
                name="demo",
                force=False,
                snapshot=[],
                activate=False,
            )

            with mock.patch.object(Path, "cwd", return_value=inside_vault):
                from_vault = project_import_request_from_cli(args)
            with mock.patch.object(Path, "cwd", return_value=plain_directory):
                without_vault = project_import_request_from_cli(args)

            self.assertEqual(from_vault.archive, requested.resolve())
            self.assertEqual(without_vault.archive, requested.resolve())
            self.assertNotEqual(from_vault.archive, wrong.resolve())

    def test_mcp_preserves_explicit_archive(self):
        archive = Path("explicit/project.zeia")
        request = project_import_request_from_mcp(
            archive,
            name="demo",
            activate=False,
            snapshots=None,
            force=False,
        )
        self.assertEqual(request.archive, archive)

    def test_python_service_forwards_explicit_archive_unchanged(self):
        archive = Path("explicit/project.zeia")
        context = SimpleNamespace(name="demo", root=Path("projects/demo"), manifest={"scripts": []})

        with mock.patch(
            "fluent_pipeline.application_services.import_project_context",
            return_value=context,
        ) as import_context:
            import_project(ProjectImportRequest(archive=archive, name="demo"))

        import_context.assert_called_once_with(
            archive,
            name="demo",
            force=False,
            snapshot_archives=[],
        )


if __name__ == "__main__":
    unittest.main()
