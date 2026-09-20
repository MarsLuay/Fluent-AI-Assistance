"""Fail when tracked artifacts embed a developer-specific home path."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

ARTIFACT_ROOTS = (
    REPO_ROOT / "source" / "03-protocol-builder" / "ready-to-import",
    REPO_ROOT / "source" / "03-protocol-builder" / "AGENTS.details",
    REPO_ROOT / "source" / "04-protocol-simulator" / "AGENTS.details",
)

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "build",
    "dist",
}

# Tests/docs that intentionally mention developer-home path shapes.
ALLOWLIST = {
    Path("source/03-protocol-builder/tests/test_determinism.py"),
    Path("source/03-protocol-builder/tests/test_repository_hygiene.py"),
    Path("source/tools/simulator/extract_fluent_textures.py"),
}

DEVELOPER_HOME_PATTERNS = (
    re.compile(r"/Users/mars/"),
    re.compile(r"/Users/[^/\s\"']+/ObsidianNotes"),
    re.compile(r"(?i)[A-Za-z]:\\Users\\[^\\]+\\"),
    re.compile(r"(?i)[A-Za-z]:/Users/[^/]+/"),
    re.compile(r"/home/[^/\s\"']+/"),
)

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".log",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.suffix == "" and path.name in {".gitignore", ".gitattributes"}


def _iter_artifact_files() -> list[Path]:
    files: list[Path] = []
    for root in ARTIFACT_ROOTS:
        if not root.exists():
            continue
        if root.is_file():
            files.append(root)
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
                continue
            files.append(path)
    return files


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    names = [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]
    return [REPO_ROOT / name for name in names]


def _offending_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    hits: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in DEVELOPER_HOME_PATTERNS:
            if pattern.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line_no}: {line.strip()}")
                break
    return hits


def test_tracked_artifacts_do_not_embed_developer_home_paths() -> None:
    allow = {path.as_posix() for path in ALLOWLIST}
    offenders: list[str] = []
    seen: set[Path] = set()
    candidates = _iter_artifact_files() + [
        path for path in _tracked_files() if _is_text_file(path)
    ]
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in allow:
            continue
        offenders.extend(_offending_lines(path))
    assert not offenders, "Developer-home paths in tracked/generated artifacts:\n" + "\n".join(offenders)


def test_agent_state_diagnostics_are_not_tracked() -> None:
    tracked = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _tracked_files()
        if path.relative_to(REPO_ROOT).as_posix().startswith(".agent-state/diagnostics/")
    ]
    assert not tracked, "Ephemeral diagnostics must not stay tracked:\n" + "\n".join(tracked)
