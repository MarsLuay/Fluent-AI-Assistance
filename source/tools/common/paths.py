"""Shared path discovery for ``source/tools`` scripts (nesting-safe)."""

from __future__ import annotations

from pathlib import Path


def tools_root() -> Path:
    """Return ``source/tools`` regardless of which subpackage this module lives in."""
    return Path(__file__).resolve().parents[1]


def source_root() -> Path:
    return tools_root().parent


def repo_root() -> Path:
    return source_root().parent
