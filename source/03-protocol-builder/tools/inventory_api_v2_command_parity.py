"""Inventory command-registry entries missing dedicated typed API V2 parity."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from fluent_pipeline.api_v2.commands import API_V2_COMMAND_TYPES, XSCR_COMMAND_ID_TO_API_V2
from fluent_pipeline.command_registry import load_command_registry


def build_inventory() -> dict[str, Any]:
    commands = load_command_registry().get("commands", {})
    typed_ids = set(XSCR_COMMAND_ID_TO_API_V2)
    mapped_gaps: list[dict[str, str]] = []
    approved_passthrough: list[dict[str, str]] = []
    for command_id, raw_entry in sorted(commands.items()):
        if not isinstance(raw_entry, dict):
            continue
        row = {
            "command_id": command_id,
            "operation": str(raw_entry.get("operation") or ""),
            "family": str(raw_entry.get("family") or ""),
            "fluentcontrol_name": str(raw_entry.get("fluentcontrol_name") or ""),
        }
        if raw_entry.get("operation") and command_id not in typed_ids:
            mapped_gaps.append(row)
            continue
        status = str(raw_entry.get("support_status") or "").strip()
        if (status == "approved_passthrough" or raw_entry.get("approved_passthrough")) and command_id not in typed_ids:
            approved_passthrough.append(row)
    return {
        "typed_command_ids": sorted(typed_ids),
        "api_v2_command_types": sorted(API_V2_COMMAND_TYPES),
        "mapped_gaps": mapped_gaps,
        "approved_passthrough_without_typed_model": approved_passthrough,
    }


def render_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# Typed API V2 Command Parity Inventory",
        "",
        "Source comparison:",
        "",
        "- Typed command IDs: `fluent_pipeline.api_v2.commands.XSCR_COMMAND_ID_TO_API_V2`",
        "- Registry commands: `source_command_registry_path()` via `fluent_pipeline.command_registry`",
        "",
        f"- Dedicated typed API V2 command families: `{len(inventory['api_v2_command_types'])}`",
        f"- Typed compiled command IDs: `{len(inventory['typed_command_ids'])}`",
        f"- Mapped registry command IDs without typed parity: `{len(inventory['mapped_gaps'])}`",
        f"- Approved passthrough command IDs without typed parity: `{len(inventory['approved_passthrough_without_typed_model'])}`",
        "",
        "## Mapped Gaps",
        "",
        "| Command ID | Operation | Family | FluentControl name |",
        "| --- | --- | --- | --- |",
    ]
    for row in inventory["mapped_gaps"]:
        lines.append(
            "| {command_id} | {operation} | {family} | {fluentcontrol_name} |".format(
                **{key: _cell(value) for key, value in row.items()}
            )
        )
    lines.extend(
        [
            "",
            "## Approved Passthrough Without Dedicated Model",
            "",
            "These are intentionally accepted through `GenericCommand`/approved raw XML today.",
            "",
            "| Command ID | Family | FluentControl name |",
            "| --- | --- | --- |",
        ]
    )
    for row in inventory["approved_passthrough_without_typed_model"]:
        lines.append(
            "| {command_id} | {family} | {fluentcontrol_name} |".format(
                **{key: _cell(value) for key, value in row.items()}
            )
        )
    lines.extend(
        [
            "",
            "## Regenerate",
            "",
            "```powershell",
            r"..\..\..\.venv\Scripts\python.exe -m tools.inventory_api_v2_command_parity --write-doc",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _cell(value: Any) -> str:
    text = str(value or "").replace("|", "\\|").strip()
    return text or " "


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-doc",
        action="store_true",
        help="Write docs/api_v2/typed-command-parity-inventory.md instead of printing.",
    )
    args = parser.parse_args()
    markdown = render_markdown(build_inventory())
    if not args.write_doc:
        print(markdown)
        return
    out = ROOT / "docs" / "api_v2" / "typed-command-parity-inventory.md"
    out.write_text(markdown, encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
