"""Composite identity helpers for imported project contexts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import __version__ as IMPORTER_VERSION
from .provenance import policy_profile_sha256s, sha256_path
from tecan_common.command_registry import command_registry_sha256


def build_import_options(
    snapshot_archives: Sequence[Path],
    *,
    extra_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the import options that should influence cache reuse."""
    options: dict[str, Any] = {
        "snapshot_archive_names": [snapshot.name for snapshot in snapshot_archives],
    }
    if extra_options:
        options.update(extra_options)
    return options


def sha256_json(payload: Any) -> str:
    """Return the SHA-256 digest of a canonical JSON serialization."""
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    )
    return digest.hexdigest()


def build_source_import_identity(
    source_archive: Path,
    snapshot_archives: Sequence[Path],
    *,
    manifest_schema_version: int,
    extra_import_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the composite cache identity for an imported ZEIA project."""
    import_options = build_import_options(snapshot_archives, extra_options=extra_import_options)
    return {
        "source_archive_sha256": sha256_path(source_archive),
        "snapshot_archive_sha256s": [sha256_path(snapshot) for snapshot in snapshot_archives],
        "importer_version": IMPORTER_VERSION,
        "manifest_schema_version": manifest_schema_version,
        "command_registry_sha256": command_registry_sha256(),
        "policy_profile_sha256s": policy_profile_sha256s(),
        "import_options_sha256": sha256_json(import_options),
    }
