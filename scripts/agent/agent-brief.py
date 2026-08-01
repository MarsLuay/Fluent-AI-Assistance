#!/usr/bin/env python3
"""Print a short, mode-scoped brief for AI agents (CLI wrapper over fluent_pipeline)."""

from __future__ import annotations

import argparse
import json
import sys

from fluent_pipeline.agent_brief import (
    AGENT_BRIEF_MODES,
    render_agent_brief,
    resolve_agent_brief_mode,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a short AI agent brief for Fluent AI-Assistance.",
    )
    parser.add_argument(
        "--mode",
        choices=sorted({m for m in AGENT_BRIEF_MODES if m != "script"} | {"script"}),
        default="status",
        help="which checklist to print (default: status); ignored when --intent is set",
    )
    parser.add_argument(
        "--intent",
        default="",
        help="free-text user request; maps to a mode via keyword rules (wins over --mode)",
    )
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="print JSON mode resolution only (no checklist body)",
    )
    args = parser.parse_args(argv)
    if args.intent.strip():
        resolution = resolve_agent_brief_mode(args.intent)
        if args.resolve_only:
            print(json.dumps({"ok": True, **resolution}, indent=2))
            return 0
        print(f"# resolved mode={resolution['mode']} ({resolution['reason']})", file=sys.stderr)
        print(render_agent_brief(str(resolution["mode"])))
        return 0
    if args.resolve_only:
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": args.mode if args.mode != "script" else "new-script",
                    "matched_keyword": None,
                    "confidence": "low",
                    "reason": "no intent; using --mode",
                },
                indent=2,
            )
        )
        return 0
    print(render_agent_brief(args.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
