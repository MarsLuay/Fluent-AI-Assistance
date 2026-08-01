from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CODE_ROOTS = (
    REPO_ROOT / "scripts",
    REPO_ROOT / "source",
)
SELF = Path(__file__).resolve()
APPROVED_SYS_PATH_INSERT_FILES: set[Path] = {SELF}
APPROVED_PYTHONPATH_FILES: set[Path] = {SELF}
SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
}
DOMAIN_MODULE_ROOTS = (
    REPO_ROOT / "source" / "03-protocol-builder" / "fluent_pipeline" / "ir",
    REPO_ROOT / "source" / "03-protocol-builder" / "fluent_pipeline" / "gates",
)
FORBIDDEN_DOMAIN_IMPORT_PREFIXES = (
    "fluent_pipeline.cli",
    "fluent_pipeline.mcp_",
    "fluent_pipeline.adapters",
    "fluent_pipeline.infrastructure",
)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in CODE_ROOTS:
        for path in root.rglob("*.py"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def test_no_unapproved_sys_path_insert_usage() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if path in APPROVED_SYS_PATH_INSERT_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if "sys.path.insert" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, "Unexpected sys.path.insert usage:\n" + "\n".join(offenders)


def test_no_unapproved_pythonpath_mutation() -> None:
    offenders: list[str] = []
    markers = ('"PYTHONPATH"', "'PYTHONPATH'")
    for path in _python_files():
        if path in APPROVED_PYTHONPATH_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in markers):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, "Unexpected PYTHONPATH mutation/reference in code:\n" + "\n".join(offenders)


def test_domain_modules_do_not_import_adapters_or_infrastructure() -> None:
    """Keep focused domain seams independent from delivery adapters."""
    offenders: list[str] = []
    for root in DOMAIN_MODULE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            imports.update(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            forbidden = sorted(
                imported
                for imported in imports
                if imported.startswith(FORBIDDEN_DOMAIN_IMPORT_PREFIXES)
            )
            if forbidden:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: {', '.join(forbidden)}"
                )
    assert not offenders, "Domain modules import adapters/infrastructure:\n" + "\n".join(offenders)
