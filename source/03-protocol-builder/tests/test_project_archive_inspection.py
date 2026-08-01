from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch
import zipfile

import pytest

from fluent_pipeline.project_archive_inspection import inspect_zeia_archive
from fluent_pipeline.runner import PipelineError


def test_preview_rejects_archive_limit_failures_with_clear_message() -> None:
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "limited.zeia"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("safe.txt", "content")

        with patch(
            "fluent_pipeline.project_archive_inspection.validate_zeia_archive_limits",
            side_effect=zipfile.BadZipFile("ZEIA archive exceeds safe entry count limit"),
        ), pytest.raises(PipelineError, match="failed safety or format validation"):
            inspect_zeia_archive(archive)


def test_preview_rejects_path_traversal_entries() -> None:
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "unsafe.zeia"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escaped.txt", "content")

        with pytest.raises(PipelineError, match="unsafe archive entry path"):
            inspect_zeia_archive(archive)


def test_preview_uses_only_a_temporary_project_root() -> None:
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "preview.zeia"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("DataStore/UserSpecific/demo.xscr", "<Script />")

        with patch(
            "fluent_pipeline.project_archive_inspection.build_manifest",
            return_value={"full_zeia_export": {"status": "pass"}},
        ) as build_manifest:
            manifest = inspect_zeia_archive(archive)

        root = build_manifest.call_args.kwargs["root"]
        assert manifest["preview_only"] is True
        assert root.parent.name.startswith("tecan-zeia-preview-")
        assert not root.exists()
