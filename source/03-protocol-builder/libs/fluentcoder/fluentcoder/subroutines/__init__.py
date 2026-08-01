"""Subroutine registry for resolving FluentControl .xscr bodies by script path."""

from .registry import (
    SubroutineRegistry,
    build_subroutine_registry,
    normalize_subroutine_path,
    subroutine_path_from_xscr,
)

__all__ = [
    "SubroutineRegistry",
    "build_subroutine_registry",
    "normalize_subroutine_path",
    "subroutine_path_from_xscr",
]
