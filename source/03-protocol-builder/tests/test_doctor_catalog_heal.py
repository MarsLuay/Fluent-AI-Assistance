"""Doctor auto-heals empty global fluentcoder catalog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fluent_pipeline.cli.commands import doctor as doctor_cmd


class EnsureGlobalCatalogTests(unittest.TestCase):
    def test_skips_when_catalog_already_ok(self) -> None:
        with mock.patch.object(doctor_cmd, "_catalog_info_ok", return_value=True):
            result = doctor_cmd.ensure_global_catalog_index()
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "already_populated")

    def test_copies_project_catalog_when_global_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_db = root / "project-catalog.db"
            project_db.write_bytes(b"stub")
            global_db = root / "global-catalog.db"
            ctx = SimpleNamespace(name="stub")
            info_calls = {"n": 0}

            def catalog_info_ok() -> bool:
                info_calls["n"] += 1
                # First probe empty; after copy, treat as populated.
                return info_calls["n"] > 1

            with (
                mock.patch.object(doctor_cmd, "_catalog_info_ok", side_effect=catalog_info_ok),
                mock.patch.object(doctor_cmd, "_global_catalog_db_path", return_value=global_db),
                mock.patch.object(
                    doctor_cmd,
                    "_project_catalog_refresh_sources",
                    return_value=[("stub", root / "ds")],
                ),
                mock.patch.object(doctor_cmd, "load_project", return_value=ctx),
                mock.patch.object(doctor_cmd, "ensure_project_catalog", return_value=project_db),
                mock.patch.object(doctor_cmd, "_copy_catalog_db") as copy_db,
                mock.patch.object(doctor_cmd, "_fc_install_with_components", return_value=None),
                mock.patch.object(doctor_cmd, "run_fluentcoder") as run_fc,
            ):
                result = doctor_cmd.ensure_global_catalog_index()

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "copied_project_catalog")
            copy_db.assert_called_once_with(project_db, global_db)
            run_fc.assert_not_called()

    def test_reports_unavailable_when_no_sources(self) -> None:
        with (
            mock.patch.object(doctor_cmd, "_catalog_info_ok", return_value=False),
            mock.patch.object(
                doctor_cmd,
                "_project_catalog_refresh_sources",
                return_value=[],
            ),
            mock.patch.object(doctor_cmd, "_fc_install_with_components", return_value=None),
        ):
            result = doctor_cmd.ensure_global_catalog_index()

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "unavailable")
        self.assertIn("DataStore", result["detail"])


if __name__ == "__main__":
    unittest.main()
