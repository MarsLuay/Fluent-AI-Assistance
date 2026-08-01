"""Archive comparison helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .archive import inspect_archive


def compare_archives(left: str | Path, right: str | Path, *, script_limit: int | None = None) -> dict[str, Any]:
    left_report = inspect_archive(left, script_limit=script_limit, object_limit=0)
    right_report = inspect_archive(right, script_limit=script_limit, object_limit=0)
    left_names = set(left_report["script_names"])
    right_names = set(right_report["script_names"])
    return {
        "kind": "zeia_compare",
        "left": str(left),
        "right": str(right),
        "left_entry_count": left_report["entry_count"],
        "right_entry_count": right_report["entry_count"],
        "left_extension_counts": left_report["extension_counts"],
        "right_extension_counts": right_report["extension_counts"],
        "script_names_added": sorted(right_names - left_names),
        "script_names_removed": sorted(left_names - right_names),
        "script_names_common_count": len(left_names & right_names),
        "left_family_counts": left_report["family_counts"],
        "right_family_counts": right_report["family_counts"],
        "left_warning_counts": left_report["warning_counts"],
        "right_warning_counts": right_report["warning_counts"],
    }
