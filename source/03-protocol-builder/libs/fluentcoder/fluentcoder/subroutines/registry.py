"""Registry of FluentControl subroutine .xscr files indexed by script path."""

from __future__ import annotations

from .. import xml_compat as ET
from pathlib import Path
from typing import Optional, Union

from ..catalog.xcmp import _find, _text


def normalize_subroutine_path(path: str) -> str:
    """Normalize a subroutine path for lookup (FluentControl uses backslashes)."""
    text = path.strip().strip('"').strip("'")
    text = text.replace("/", "\\")
    while "\\\\" in text:
        text = text.replace("\\\\", "\\")
    return text


def subroutine_path_from_xscr(path: Union[Path, str]) -> str:
    """Read ``ObjectSubfolderPath\\ObjectName`` from a subroutine .xscr file."""
    tree = ET.parse(str(path))
    root = tree.getroot()
    payload = _find(root, "Payload")
    if payload is None:
        raise ValueError(f"No Payload in {path}")
    name = _text(_find(payload, "ObjectName")) or Path(path).stem
    subfolder = _text(_find(payload, "ObjectSubfolderPath")) or ""
    if subfolder:
        return normalize_subroutine_path(f"{subfolder}\\{name}")
    return normalize_subroutine_path(name)


class SubroutineRegistry:
    """Index of subroutine .xscr files by FluentControl script path."""

    def __init__(self) -> None:
        self._by_path: dict[str, Path] = {}

    def register(self, xscr_path: Union[Path, str]) -> str:
        """Register a subroutine .xscr; returns its indexed path key."""
        path = Path(xscr_path)
        key = subroutine_path_from_xscr(path)
        self._by_path[key] = path.resolve()
        return key

    def register_directory(
        self,
        directory: Union[Path, str],
        *,
        pattern: str = "*.xscr",
    ) -> list[str]:
        """Register all matching .xscr files under ``directory``."""
        root = Path(directory)
        keys: list[str] = []
        for xscr in sorted(root.rglob(pattern)):
            if xscr.is_file():
                keys.append(self.register(xscr))
        return keys

    def resolve(self, path: str) -> Optional[Path]:
        """Return the .xscr path for a FluentControl subroutine path, or None."""
        key = normalize_subroutine_path(path)
        return self._by_path.get(key)

    def normalize_path(self, path: str) -> str:
        return normalize_subroutine_path(path)

    def __contains__(self, path: str) -> bool:
        return self.resolve(path) is not None

    def paths(self) -> list[str]:
        return sorted(self._by_path)


def build_subroutine_registry(
    *,
    subroutine_dirs: list[Path] | None = None,
    subroutine_xscr: list[Path] | None = None,
) -> SubroutineRegistry | None:
    """Build a registry from CLI-style directory and file path lists."""
    dirs = [path for path in (subroutine_dirs or []) if path is not None]
    xscr_paths = [path for path in (subroutine_xscr or []) if path is not None]
    if not dirs and not xscr_paths:
        return None
    registry = SubroutineRegistry()
    for directory in dirs:
        registry.register_directory(directory)
    for xscr_path in xscr_paths:
        registry.register(xscr_path)
    return registry if registry.paths() else None
