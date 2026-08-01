"""Sync generated readiness-gate docs and simulator data from the registry."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from fluent_pipeline.readiness_gates import (
    optional_diagnostic_gate_count,
    readiness_gate,
    readiness_gates,
    render_readiness_gate_registry_markdown,
    render_readiness_gate_registry_typescript,
    required_offline_gate_count,
)

ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = ROOT.parent / "04-protocol-simulator"


SUMMARY_MARKERS = {
    "README.md": ("docs/READINESS_GATES.md", "fluent_pipeline/data/readiness_gate_registry.json", ""),
    "AGENTS.md": ("docs/READINESS_GATES.md", "fluent_pipeline/data/readiness_gate_registry.json", "   "),
    "docs/CODEX_WORKFLOW.md": ("READINESS_GATES.md", "../fluent_pipeline/data/readiness_gate_registry.json", "    "),
    "docs/PROTOCOL_BUILDER_GUIDE.md": ("READINESS_GATES.md", "../fluent_pipeline/data/readiness_gate_registry.json", ""),
}

SUMMARY_BEGIN = "<!-- BEGIN GENERATED: readiness-gate-summary -->"
SUMMARY_END = "<!-- END GENERATED: readiness-gate-summary -->"


def _readiness_summary_block(table_link: str, registry_path: str, indent: str) -> str:
    fluent_gate = readiness_gate("fluent_context_check")
    lines = [
        SUMMARY_BEGIN,
        f"Readiness registry summary (generated from `{registry_path}`):",
        f"- Required offline ready-to-import gates: `{required_offline_gate_count()}`",
        f"- Optional diagnostics: `{optional_diagnostic_gate_count()}` (`{fluent_gate.gate_label}`)",
        f"- Current active entries: `{len(readiness_gates())}`",
        "- Stable IDs are the contract; gate numbers are display labels only.",
        f"- Authoritative table: [Readiness Gate Registry]({table_link})",
        SUMMARY_END,
    ]
    if not indent:
        return "\n".join(lines)
    return "\n".join(f"{indent}{line}" for line in lines)


def _replace_marker_block(text: str, begin: str, end: str, replacement: str, *, path: Path) -> str:
    pattern = re.compile(
        rf"^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"Could not find generated block markers in {path}.")
    return text[: match.start()] + replacement + text[match.end() :]


def _write_or_check(path: Path, content: str, *, check: bool) -> bool:
    normalized = content if content.endswith("\n") else f"{content}\n"
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == normalized:
        return False
    if check:
        print(f"out of date: {path}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")
    print(f"updated: {path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated files are out of date")
    args = parser.parse_args()

    changed = False

    docs_registry = ROOT / "docs" / "READINESS_GATES.md"
    changed |= _write_or_check(
        docs_registry,
        render_readiness_gate_registry_markdown(),
        check=args.check,
    )

    simulator_registry = SIMULATOR_ROOT / "src" / "data" / "readinessGateRegistry.ts"
    changed |= _write_or_check(
        simulator_registry,
        render_readiness_gate_registry_typescript(),
        check=args.check,
    )

    for relative_path, (table_link, registry_path, indent) in SUMMARY_MARKERS.items():
        path = ROOT / relative_path
        replacement = _readiness_summary_block(table_link, registry_path, indent)
        updated = _replace_marker_block(
            path.read_text(encoding="utf-8"),
            SUMMARY_BEGIN,
            SUMMARY_END,
            replacement,
            path=path,
        )
        changed |= _write_or_check(path, updated, check=args.check)

    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
