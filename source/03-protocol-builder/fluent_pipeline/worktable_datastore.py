"""Locate ZEIA/install worktable DataStore roots for geometry exports."""

from __future__ import annotations

from pathlib import Path

_WORKTABLE_REL = Path("SystemSpecific") / "Worktable"


def resolve_worktable_datastore(path: Path | str | None) -> Path | None:
    """Return DataStore/worktable root when ``SystemSpecific/Worktable`` is present.

    Accepts a DataStore directory or a parent that contains ``DataStore/``.
    A worktable is recognized when ``Worktable`` exists as a directory and/or
    exposes ``Components`` or ``Connectors`` (labware vs connector exporters).
    """
    if path is None:
        return None
    candidate = Path(path).expanduser()
    if not candidate.exists():
        return None

    def _looks_like_worktable_root(root: Path) -> bool:
        worktable = root / _WORKTABLE_REL
        if not worktable.exists():
            return False
        if worktable.is_dir():
            return True
        return (worktable / "Components").is_dir() or (worktable / "Connectors").is_dir()

    if _looks_like_worktable_root(candidate):
        return candidate
    datastore = candidate / "DataStore"
    if _looks_like_worktable_root(datastore):
        return datastore
    if candidate.name.casefold() == "datastore" and _looks_like_worktable_root(candidate):
        return candidate
    return None


def discover_worktable_datastore(context_root: Path | str | None) -> Path | None:
    """Find ZEIA/install worktable root under a project context or extract tree."""
    if context_root is None:
        return None
    root = Path(context_root).expanduser()
    for candidate in (
        root,
        root / "extracted",
        root / "extracted" / "DataStore",
        root / "DataStore",
    ):
        resolved = resolve_worktable_datastore(candidate)
        if resolved is not None:
            return resolved
    for search_root in (root / "extracted", root):
        if not search_root.is_dir():
            continue
        for marker in (
            search_root.glob("**/SystemSpecific/Worktable/Components"),
            search_root.glob("**/SystemSpecific/Worktable/Connectors"),
        ):
            for path in marker:
                if path.is_dir():
                    # Components|Connectors -> Worktable -> SystemSpecific -> datastore root
                    return path.parent.parent.parent
    return None
