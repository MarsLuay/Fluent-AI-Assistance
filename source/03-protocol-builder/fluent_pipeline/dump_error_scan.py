"""Scan recent FluentControl crash dumps for Script Editor error strings."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .fluent_log_parser import diagnose_fluent_log_text


DUMP_ERROR_RULES: tuple[dict[str, str], ...] = (
    {
        "id": "fluent_log.if_else_branches_mismatched",
        "needle": "Mismatching If-Else branches",
        "message": "Mismatching If-Else branches",
    },
    {
        "id": "fluent_log.gripper_fingers_not_mounted",
        "needle": "Unable to start transfer because no gripper fingers are mounted",
        "message": "Unable to start transfer because no gripper fingers are mounted. Please mount fingers to the arm.",
    },
    {
        "id": "fluent_log.vb_script_compile_failed",
        "needle": "Unable to load and compile VB script",
        "message": "Unable to load and compile VB script",
    },
    {
        "id": "fluent_log.resolvex_a200_command_unknown",
        "needle": "ResolvexA200_Run",
        "message": 'Command "ResolvexA200_Run" is unknown',
    },
    {
        "id": "fluent_log.invalid_labware_selection",
        "needle": "Select a valid labware",
        "message": "Select a valid labware.",
    },
)


def scan_fluent_dump_errors(
    dump_root: Path,
    *,
    since_days: int,
    max_files: int = 6,
) -> dict[str, Any]:
    """Find known Script Editor error strings without copying multi-GB dump files."""
    cutoff = datetime.now() - timedelta(days=since_days)
    files = sorted(
        (
            path
            for path in dump_root.glob("*.dmp")
            if datetime.fromtimestamp(path.stat().st_mtime) >= cutoff
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:max_files]
    matches: dict[str, list[Path]] = {rule["id"]: [] for rule in DUMP_ERROR_RULES}
    for path in files:
        found = _scan_dump_file(path)
        for rule in DUMP_ERROR_RULES:
            if rule["id"] in found:
                matches[rule["id"]].append(path)

    findings: list[dict[str, Any]] = []
    for rule in DUMP_ERROR_RULES:
        paths = matches[rule["id"]]
        if not paths:
            continue
        diagnostic = next(
            item
            for item in diagnose_fluent_log_text(rule["message"], source="VisionX crash dump")
            if item["id"] == rule["id"]
        )
        findings.append(
            {
                "id": rule["id"],
                "title": diagnostic["title"],
                "severity": diagnostic["severity"],
                "category": diagnostic["category"],
                "actual_error": rule["message"],
                "likely_cause": diagnostic["likely_workflow_defect"],
                "suggested_fix": diagnostic["suggested_fix"],
                "files": [str(path) for path in paths],
            }
        )
    return {
        "schema_version": "tecan.script_editor_dump_scan.v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dump_root": str(dump_root),
        "scanned_files": [str(path) for path in files],
        "findings": findings,
    }


def _scan_dump_file(path: Path) -> set[str]:
    needles = {
        rule["id"]: tuple(
            variant.lower()
            for variant in (
                rule["needle"].encode("utf-8"),
                rule["needle"].encode("utf-16-le"),
            )
        )
        for rule in DUMP_ERROR_RULES
    }
    overlap = max(len(variant) for variants in needles.values() for variant in variants)
    found: set[str] = set()
    carry = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            data = (carry + chunk).lower()
            for rule_id, variants in needles.items():
                if rule_id not in found and any(variant in data for variant in variants):
                    found.add(rule_id)
            if len(found) == len(needles):
                break
            carry = data[-overlap:]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_root", type=Path)
    parser.add_argument("--since-days", type=int, default=1)
    parser.add_argument("--max-files", type=int, default=6)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = scan_fluent_dump_errors(args.dump_root, since_days=args.since_days, max_files=args.max_files)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
