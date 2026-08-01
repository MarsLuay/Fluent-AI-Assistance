"""Helpers for recording reproducible generation provenance."""

from __future__ import annotations

import hashlib
from importlib import metadata as importlib_metadata
import platform
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from . import __version__ as PROTOCOL_BUILDER_VERSION
from .checksums import checksum_backend_name
from .command_registry import command_registry_sha256
from .config import PROJECT_DIR, REPO_ROOT
from .readiness_gates import readiness_gate_registry_version


_HASH_CHUNK = 1024 * 1024
_DISTRIBUTION_PYPROJECTS = {
    "tecan-protocol-builder": PROJECT_DIR / "pyproject.toml",
    "fluentcoder": PROJECT_DIR / "libs" / "fluentcoder" / "pyproject.toml",
    "tecan-common": REPO_ROOT / "source" / "00-shared" / "pyproject.toml",
    "tecan-project-reader": REPO_ROOT / "source" / "01-project-reader" / "pyproject.toml",
}


def sha256_path(path: Path | None) -> str | None:
    """Return the SHA-256 digest for ``path`` when it is a readable file."""
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def environment_provenance(*, simulation_backend: str) -> dict[str, Any]:
    """Return a stable execution-environment summary for generation manifests."""
    return {
        "repository_commit": repository_commit(),
        "python_version": sys.version.replace("\n", " "),
        "protocol_builder_version": distribution_version(
            "tecan-protocol-builder",
            fallback_value=PROTOCOL_BUILDER_VERSION,
        ),
        "fluentcoder_version": distribution_version("fluentcoder"),
        "tecan_common_version": distribution_version("tecan-common"),
        "reader_version": distribution_version("tecan-project-reader"),
        "command_registry_sha256": command_registry_sha256(),
        "readiness_registry_version": readiness_gate_registry_version(),
        "policy_profile_sha256s": policy_profile_sha256s(),
        "checksum_backend": checksum_backend_name() or "unavailable",
        "operating_system": platform.platform(),
        "simulation_backend": simulation_backend,
    }


def distribution_version(
    distribution: str,
    *,
    fallback_value: str | None = None,
) -> str | None:
    """Return the installed or source-tree version for ``distribution``."""
    try:
        return importlib_metadata.version(distribution)
    except importlib_metadata.PackageNotFoundError:
        pass

    pyproject = _DISTRIBUTION_PYPROJECTS.get(distribution)
    if pyproject is not None:
        version = _pyproject_version(pyproject)
        if version:
            return version
    return fallback_value


def policy_profile_sha256s() -> dict[str, str]:
    """Return digests for repository policy profile files used by generation."""
    policies_root = PROJECT_DIR / "fluent_pipeline" / "policies"
    out: dict[str, str] = {}
    if not policies_root.is_dir():
        return out
    for path in sorted(policies_root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest = sha256_path(path)
        if digest:
            out[path.relative_to(PROJECT_DIR).as_posix()] = digest
    return out


def repository_commit() -> str | None:
    """Return the current repository commit hash when Git metadata is available."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _pyproject_version(path: Path) -> str | None:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = payload.get("project", {}).get("version")
    return str(value).strip() if value not in (None, "") else None
