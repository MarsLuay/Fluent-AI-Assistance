#!/usr/bin/env python3
"""Build scripts_inventory.json from a FluentControl UserSpecific dump or live DB."""

from __future__ import annotations

import argparse
from pathlib import Path

from fluent_pipeline.fluentcontrol_inventory import (
    build_scripts_inventory,
    report_missing_system_dependencies,
    write_scripts_inventory,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--userspecific",
        type=Path,
        default=None,
        help="UserSpecific directory (default: TECAN_VISIONX_USERSPECIFIC or live DB)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output scripts_inventory.json path",
    )
    parser.add_argument(
        "--systemspecific",
        type=Path,
        default=None,
        help="Optional SystemSpecific dir for a companion target_prereq stub (empty payload)",
    )
    args = parser.parse_args()
    inventory = build_scripts_inventory(args.userspecific)
    write_scripts_inventory(args.out, inventory)
    print(f"Wrote {args.out} scripts={inventory.get('script_count')} collisions={len(inventory.get('collisions') or {})}")
    if args.systemspecific is not None:
        report_path = args.out.with_name("target_prereq_report_empty_payload.json")
        report = report_missing_system_dependencies(b"", systemspecific_dir=args.systemspecific)
        report_path.write_text(__import__("json").dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
