"""Non-persistent project archive inspection helpers.

This module lets UI/adapters preview ZEIA contents using the same manifest
inspection path as persistent project import, without creating a project context
under ``ready-to-import/<project>/temp_files/``.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import zipfile
from typing import Any

from tecan_common.zeia_limits import validate_zeia_archive_limits

from .import_identity import build_source_import_identity
from .project_context import (
    PROJECT_MANIFEST_SCHEMA_VERSION,
    build_manifest,
    sanitize_project_name,
    safe_extract_archive,
)
from .runner import PipelineError


def inspect_zeia_archive(archive: str | Path) -> dict[str, Any]:
    """Inspect a ZEIA archive and return a project-style manifest.

    The returned manifest is intended for read-only preview flows such as the
    interactive prompt builder. It reuses the normal import manifest builder but
    extracts into a temporary directory and does not write persistent project
    context files.
    """
    archive_path = Path(archive).expanduser().resolve()
    if not archive_path.exists():
        raise PipelineError(f"project archive not found: {archive_path}")
    if not zipfile.is_zipfile(archive_path):
        raise PipelineError(f"not a readable .zeia/zip archive: {archive_path}")

    project_name = sanitize_project_name(archive_path.stem, "preview")
    source_import_identity = build_source_import_identity(
        archive_path,
        [],
        manifest_schema_version=PROJECT_MANIFEST_SCHEMA_VERSION,
    )

    with tempfile.TemporaryDirectory(prefix="tecan-zeia-preview-") as temp_dir:
        root = Path(temp_dir) / project_name
        extracted_dir = root / "extracted"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive_path) as zf:
                infos = validate_zeia_archive_limits(zf)
                entries = [info.filename for info in infos]
                safe_extract_archive(zf, extracted_dir)
        except zipfile.BadZipFile as exc:
            raise PipelineError(
                f"ZEIA archive failed safety or format validation: {archive_path} ({exc})"
            ) from exc

        manifest = build_manifest(
            project_name=project_name,
            archive=archive_path,
            copied_archive=archive_path,
            root=root,
            extracted_dir=extracted_dir,
            entries=entries,
            snapshot_archives=[],
            source_import_identity=source_import_identity,
        )

    manifest["preview_only"] = True
    return manifest
