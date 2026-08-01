"""Path configuration for the fluentcoder wrapper."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
SOURCE_ROOT = PROJECT_DIR.parent
REPO_ROOT = SOURCE_ROOT.parent if SOURCE_ROOT.name == "source" else SOURCE_ROOT
TECAN_AI_DIR = REPO_ROOT
READY_TO_IMPORT_DIR = TECAN_AI_DIR / "ready-to-import"
# All project workflow state lives in ready-to-import/<project>/temp_files/.
# Shared process state stays under ready-to-import/_shared/temp_files/.
TEMP_FILES_DIRNAME = "temp_files"
SHARED_TEMP_DIR = READY_TO_IMPORT_DIR / "_shared" / TEMP_FILES_DIRNAME
CACHE_DIR = SHARED_TEMP_DIR / "cache"
PACKAGE_STAGING_DIR = SHARED_TEMP_DIR / "package-staging"
FAILED_PACKAGES_DIR = SHARED_TEMP_DIR / "failed-packages"
DEFAULT_FLUENTCODER_ROOT = PROJECT_DIR / "libs" / "fluentcoder"
# Use one shared repo-level virtual environment for fluent_pipeline,
# FluentCoder, MCP, tests, and local tools.
DEFAULT_FLUENTCODER_PYTHON = (
    REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt"
    else REPO_ROOT / ".venv" / "bin" / "python"
)
PROJECTS_DIR = READY_TO_IMPORT_DIR
COLLECTIONS_DIR = READY_TO_IMPORT_DIR
ACTIVE_CONTEXT_FILE = SHARED_TEMP_DIR / ".active_context"
LOGS_DIR = SHARED_TEMP_DIR / "logs"
# Shared tooling scratch (indexes, reports, setuptools staging, api_v2 outputs).
SHARED_BUILD_DIR = SHARED_TEMP_DIR / "build"
# Shared, content-addressed cache for the fluentcoder catalog index. Keyed on a
# hash of the catalog source files so byte-identical worktable inputs reuse a
# prior build across re-imports and differently named contexts instead of
# triggering the multi-minute rebuild.
CATALOG_CACHE_DIR = CACHE_DIR / "catalog"
# Shared, content-addressed cache for per-source-ZEIA reference-resolution
# records (datastore node descriptions and script metadata). Keyed on the base
# ZEIA's content fingerprint so repeated `generate` runs against the same
# immutable base skip the multi-minute reference-resolution scan during
# packaging. Lives next to the catalog cache.
ZEIA_REFERENCE_CACHE_DIR = CACHE_DIR / "zeia-references"


def fluentcoder_root() -> Path:
    """Return the fluentcoder repo root, honoring FLUENTCODER_ROOT if present."""
    raw = os.environ.get("FLUENTCODER_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_FLUENTCODER_ROOT.resolve()


def fluentcoder_python(root: Path | None = None) -> Path:
    """Return the shared repo-level Python executable for fluentcoder commands."""
    raw = os.environ.get("FLUENTCODER_PYTHON")
    if raw:
        return Path(raw).expanduser()

    # Do not resolve this path. On macOS/Linux, venv Python executables are
    # often symlinks to the base interpreter, and resolving the symlink makes
    # subprocess calls behave like they are outside the virtual environment.
    return DEFAULT_FLUENTCODER_PYTHON


def ensure_logs_dir() -> Path:
    """Create the workspace log directory when tooling needs to write a log file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def workspace_log_path(name: str) -> Path:
    """Return a log path under ``ready-to-import/_shared/temp_files/logs/``."""
    return ensure_logs_dir() / name


def _safe_log_label(value: str) -> str:
    keep = []
    for char in value.lower().strip():
        if char.isalnum():
            keep.append(char)
        elif char in {" ", "_", "-"}:
            keep.append("-")
    label = "".join(keep).strip("-")
    while "--" in label:
        label = label.replace("--", "-")
    return label[:80] or "generation"


def workflow_event_log_path(label: str) -> Path:
    """Default JSONL event-log path for a generation run."""
    return workspace_log_path(f"{_safe_log_label(label)}.events.jsonl")


def resolve_user_path(value: str | Path, *, base: Path | None = None) -> Path:
    """Resolve a CLI path relative to the caller's current working directory."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return path.resolve()


def obsidian_vault_root(*, start: Path | None = None) -> Path | None:
    """Best-effort Obsidian vault root (directory containing ``.obsidian`` or ``Home.md``)."""
    seeds = [start, TECAN_AI_DIR, TECAN_AI_DIR.parent, Path.cwd()]
    seen: set[str] = set()
    for seed in seeds:
        if seed is None:
            continue
        path = seed.expanduser().resolve()
        for candidate in [path, *path.parents]:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if (candidate / ".obsidian").exists() or (candidate / "Home.md").exists():
                return candidate
    return None


def discover_vault_root_zeia(*, start: Path | None = None) -> Path | None:
    """Return the newest ``*.zeia`` in the Obsidian vault root, if any."""
    vault = obsidian_vault_root(start=start) or (start or Path.cwd()).resolve()
    zeias = sorted(vault.glob("*.zeia"), key=lambda item: item.stat().st_mtime, reverse=True)
    return zeias[0] if zeias else None
