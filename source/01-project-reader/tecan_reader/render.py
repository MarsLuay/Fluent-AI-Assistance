"""JSON and Markdown rendering for reports."""

from __future__ import annotations

import json
from typing import Any

from .common import compact_list, to_jsonable


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(to_jsonable(report), indent=2, ensure_ascii=False)


def render_markdown(report: dict[str, Any]) -> str:
    kind = report.get("kind", "report")
    if kind == "xscr":
        return _render_xscr(report)
    if kind == "zeia":
        return _render_zeia(report)
    if kind == "zeia_compare":
        return _render_compare(report)
    if kind in {"project_index_build", "project_index_summary"}:
        return _render_project_index_summary(report)
    if kind == "project_index_search":
        return _render_project_index_search(report)
    if kind in {"script_pattern_mining", "script_pattern_summary"}:
        return _render_script_pattern_summary(report)
    if kind == "script_pattern_search":
        return _render_script_pattern_search(report)
    if kind == "script_pattern_types":
        return _render_script_pattern_types(report)
    if kind == "directory":
        return _render_directory(report)
    if kind == "gwl":
        return _render_gwl(report)
    return _render_object(report)


def _render_xscr(report: dict[str, Any]) -> str:
    lines = [
        f"# XSCR Report: {report.get('object_name') or report.get('source')}",
        "",
        f"- Source: `{report.get('source')}`",
        f"- Script version: `{report.get('script_version') or 'unknown'}`",
        f"- Command count: `{report.get('command_count')}`",
    ]
    refs = report.get("references", [])
    if refs:
        lines.append("")
        lines.append("## References")
        for ref in refs[:20]:
            lines.append(f"- {ref.get('type_id') or 'Reference'}: `{ref.get('object_name')}` (`{ref.get('guid')}`)")
    _append_dict(lines, "Command Families", report.get("family_counts", {}))
    _append_dict(lines, "Command Types", report.get("command_counts", {}), limit=30)
    _append_list(lines, "Variables", [f"{v.get('name')} ({v.get('type') or 'unknown'})" for v in report.get("variables", [])])
    _append_list(lines, "Query Prompts", [f"{p.get('name')}: {p.get('prompt')} [{p.get('minimum')}..{p.get('maximum')}]" for p in report.get("query_prompts", [])])
    deps = report.get("dependencies", {})
    if deps:
        lines.append("")
        lines.append("## Dependencies And Mentions")
        for key, values in deps.items():
            if values:
                lines.append(f"- {key}: " + ", ".join(f"`{v}`" for v in compact_list(values, limit=20)))
    _append_list(lines, "Comments", report.get("comments", []), limit=12)
    _append_list(lines, "Warnings", report.get("warnings", []))
    commands = report.get("commands", [])
    if commands:
        lines.append("")
        lines.append("## First Commands")
        for command in commands[:40]:
            label = command.get("name") or command.get("fields", {}).get("Comment") or ""
            line = command.get("line") or "?"
            lines.append(f"- #{command.get('index')} line {line}: `{command.get('type')}` ({command.get('family')}) {label}")
    return "\n".join(lines) + "\n"


def _render_zeia(report: dict[str, Any]) -> str:
    lines = [
        f"# ZEIA Report: {report.get('source')}",
        "",
        f"- Entries: `{report.get('entry_count')}`",
        f"- Scripts: `{report.get('script_count_total')}` total, `{report.get('script_count_summarized')}` summarized",
        f"- XML objects summarized: `{report.get('object_count_summarized')}`",
        f"- GWL files summarized: `{report.get('gwl_count_summarized')}`",
    ]
    _append_dict(lines, "File Extensions", report.get("extension_counts", {}), limit=40)
    _append_dict(lines, "Command Families", report.get("family_counts", {}), limit=30)
    _append_dict(lines, "Command Types", report.get("command_counts", {}), limit=30)
    _append_dict(lines, "Warning Counts", report.get("warning_counts", {}), limit=20)
    _append_list(lines, "Script Names", report.get("script_names", []), limit=80)
    scripts = report.get("scripts", [])
    if scripts:
        lines.append("")
        lines.append("## Script Summaries")
        for script in scripts[:40]:
            families = ", ".join(f"{k}={v}" for k, v in list(script.get("family_counts", {}).items())[:5])
            lines.append(f"- `{script.get('object_name')}`: {script.get('command_count')} commands ({families})")
    errors = report.get("errors", [])
    if errors:
        lines.append("")
        lines.append("## Parse Errors")
        for error in errors[:20]:
            lines.append(f"- `{error.get('entry')}`: {error.get('error')}")
    return "\n".join(lines) + "\n"


def _render_compare(report: dict[str, Any]) -> str:
    lines = [
        "# ZEIA Compare Report",
        "",
        f"- Left: `{report.get('left')}`",
        f"- Right: `{report.get('right')}`",
        f"- Entries: `{report.get('left_entry_count')}` -> `{report.get('right_entry_count')}`",
        f"- Common script names: `{report.get('script_names_common_count')}`",
    ]
    _append_list(lines, "Scripts Added", report.get("script_names_added", []), limit=100)
    _append_list(lines, "Scripts Removed", report.get("script_names_removed", []), limit=100)
    _append_dict(lines, "Left Command Families", report.get("left_family_counts", {}))
    _append_dict(lines, "Right Command Families", report.get("right_family_counts", {}))
    return "\n".join(lines) + "\n"


def _render_project_index_summary(report: dict[str, Any]) -> str:
    title = "Tecan Project Index Build" if report.get("kind") == "project_index_build" else "Tecan Project Index"
    lines = [
        f"# {title}",
        "",
        f"- Database: `{report.get('database')}`",
        f"- Schema version: `{report.get('schema_version')}`",
        f"- ZEIA files: `{report.get('zeia_file_count')}`",
        f"- Scripts: `{report.get('script_count')}`",
        f"- Commands: `{report.get('command_count')}`",
        f"- Command sequences: `{report.get('command_sequence_count')}`",
        f"- Catalog objects: `{report.get('catalog_object_count')}`",
        f"- Worklists: `{report.get('worklist_count')}`",
    ]
    _append_list(lines, "Indexed Files", report.get("indexed_files", []), limit=100)
    _append_dict(lines, "Entity Counts", report.get("entity_counts", {}), limit=40)
    _append_dict(lines, "Command Family Counts", report.get("command_family_counts", {}), limit=30)
    files = report.get("files", [])
    if files:
        lines.append("")
        lines.append("## ZEIA Files")
        for file_item in files[:100]:
            lines.append(
                f"- `{file_item.get('file_name')}`: "
                f"{file_item.get('script_count_summarized')} scripts, "
                f"{file_item.get('object_count_summarized')} objects, "
                f"{file_item.get('gwl_count_summarized')} worklists"
            )
            lines.append(f"  - Path: `{file_item.get('path')}`")
        if len(files) > 100:
            lines.append(f"- ... {len(files) - 100} more")
    return "\n".join(lines) + "\n"


def _render_project_index_search(report: dict[str, Any]) -> str:
    lines = [
        "# Tecan Project Index Search",
        "",
        f"- Database: `{report.get('database')}`",
        f"- Query: `{report.get('query')}`",
        f"- Kind filter: `{report.get('kind_filter') or 'any'}`",
        f"- Results: `{report.get('result_count')}`",
    ]
    results = report.get("results", [])
    if results:
        lines.append("")
        lines.append("## Results")
        for idx, result in enumerate(results, start=1):
            title = result.get("name") or result.get("value") or result.get("source_path")
            lines.append(f"{idx}. `{result.get('kind')}` `{title}`")
            if result.get("value"):
                lines.append(f"   - Detail: `{result.get('value')}`")
            if result.get("script"):
                lines.append(f"   - Script: `{result.get('script')}`")
            if result.get("command_index") is not None:
                lines.append(f"   - Command: `{result.get('command_index')}`")
            lines.append(f"   - ZEIA: `{result.get('zeia_file')}`")
            lines.append(f"   - Path: `{result.get('source_path')}`")
    return "\n".join(lines) + "\n"


def _render_script_pattern_summary(report: dict[str, Any]) -> str:
    title = "Tecan Script Pattern Mining" if report.get("kind") == "script_pattern_mining" else "Tecan Script Patterns"
    lines = [
        f"# {title}",
        "",
        f"- Database: `{report.get('database')}`",
        f"- Pattern schema version: `{report.get('pattern_schema_version')}`",
        f"- Patterns: `{report.get('pattern_count')}`",
        f"- Steps: `{report.get('step_count')}`",
    ]
    if report.get("kind") == "script_pattern_mining":
        lines.extend(
            [
                f"- Mined this run: `{report.get('mined_pattern_count')}`",
                f"- Replaced existing: `{report.get('replaced_existing')}`",
            ]
        )
    _append_dict(lines, "Pattern Type Counts", report.get("pattern_type_counts", {}), limit=40)
    sources = report.get("top_sources", [])
    if sources:
        lines.append("")
        lines.append("## Top Source Scripts")
        for source in sources:
            lines.append(f"- `{source.get('source_script')}`: {source.get('pattern_count')} patterns")
            lines.append(f"  - ZEIA: `{source.get('zeia_file')}`")
    return "\n".join(lines) + "\n"


def _render_script_pattern_search(report: dict[str, Any]) -> str:
    lines = [
        "# Tecan Script Pattern Search",
        "",
        f"- Database: `{report.get('database')}`",
        f"- Query: `{report.get('query')}`",
        f"- Pattern type filter: `{report.get('pattern_type_filter') or 'any'}`",
        f"- Source script filter: `{report.get('source_script_filter') or 'any'}`",
        f"- Results: `{report.get('result_count')}`",
    ]
    results = report.get("results", [])
    if results:
        lines.append("")
        lines.append("## Patterns")
        for idx, pattern in enumerate(results, start=1):
            lines.append(f"{idx}. Pattern: `{pattern.get('name')}`")
            lines.append(f"   - Type: `{pattern.get('pattern_type')}` ({pattern.get('label')})")
            lines.append(f"   - Source script: `{pattern.get('source_script')}`")
            lines.append(f"   - Commands: `{pattern.get('start_command_index')}` to `{pattern.get('end_command_index')}`")
            lines.append(f"   - ZEIA: `{pattern.get('zeia_file')}`")
            lines.append(f"   - Path: `{pattern.get('source_path')}`")
            _append_pattern_specs(lines, pattern.get("specifications", {}))
            safety_notes = pattern.get("safety_notes", [])
            if safety_notes:
                lines.append("   - Safety notes:")
                for note in safety_notes:
                    lines.append(f"     - {note}")
            steps = pattern.get("steps", [])
            if steps:
                lines.append("   - Steps:")
                for step in steps:
                    lines.append(
                        f"     {step.get('step_number')}. {step.get('summary')} "
                        f"(`{step.get('command_name')}`, command `{step.get('command_index')}`)"
                    )
                    lines.append(f"        - Path: `{pattern.get('source_path')}`")
    return "\n".join(lines) + "\n"


def _render_script_pattern_types(report: dict[str, Any]) -> str:
    lines = ["# Tecan Script Pattern Types", ""]
    for pattern_type in report.get("pattern_types", []):
        lines.append(f"- `{pattern_type.get('pattern_type')}`: {pattern_type.get('label')}")
        lines.append(f"  - {pattern_type.get('description')}")
    return "\n".join(lines) + "\n"


def _append_pattern_specs(lines: list[str], specs: dict[str, Any]) -> None:
    if not specs:
        return
    lines.append("   - Specifications:")
    for key, values in specs.items():
        if not values:
            continue
        rendered = ", ".join(f"`{value}`" for value in values[:12])
        if len(values) > 12:
            rendered += f", ... {len(values) - 12} more"
        lines.append(f"     - {key}: {rendered}")


def _render_directory(report: dict[str, Any]) -> str:
    lines = [
        f"# Directory Report: {report.get('source')}",
        "",
        f"- Files found: `{report.get('file_count')}`",
    ]
    _append_dict(lines, "File Extensions", report.get("extension_counts", {}))
    _append_dict(lines, "Command Families", report.get("family_counts", {}), limit=30)
    _append_dict(lines, "Command Types", report.get("command_counts", {}), limit=30)
    _append_dict(lines, "Warning Counts", report.get("warning_counts", {}), limit=20)
    _append_list(lines, "Script Names", report.get("script_names", []), limit=100)
    scripts = [item for item in report.get("items", []) if item.get("kind") == "xscr"]
    if scripts:
        lines.append("")
        lines.append("## Script Summaries")
        for script in scripts[:80]:
            families = ", ".join(f"{k}={v}" for k, v in list(script.get("family_counts", {}).items())[:5])
            lines.append(f"- `{script.get('object_name')}`: {script.get('command_count')} commands ({families})")
    _append_list(lines, "Files", [item.get("source", "") for item in report.get("items", [])], limit=100)
    return "\n".join(lines) + "\n"


def _render_gwl(report: dict[str, Any]) -> str:
    lines = [
        f"# GWL Report: {report.get('source')}",
        "",
        f"- Lines: `{report.get('line_count')}`",
        f"- Transfer pairs estimate: `{report.get('transfer_pairs_estimate')}`",
    ]
    _append_dict(lines, "Record Counts", report.get("record_counts", {}))
    examples = report.get("pipette_examples", [])
    if examples:
        lines.append("")
        lines.append("## Pipette Examples")
        for item in examples:
            lines.append(
                f"- line {item.get('line')}: `{item.get('operation')}` {item.get('rack_label')} "
                f"pos {item.get('position')} vol {item.get('volume')} lc `{item.get('liquid_class')}`"
            )
    return "\n".join(lines) + "\n"


def _render_object(report: dict[str, Any]) -> str:
    lines = [
        f"# XML Object Report: {report.get('object_name') or report.get('source')}",
        "",
        f"- Kind: `{report.get('kind')}`",
        f"- Source: `{report.get('source')}`",
        f"- Type ID: `{report.get('type_id')}`",
        f"- Functional group: `{report.get('functional_group')}`",
        f"- Footprint: `{report.get('footprint')}`",
    ]
    _append_list(lines, "Names", report.get("names", []))
    _append_list(lines, "GUIDs", report.get("guids", []))
    return "\n".join(lines) + "\n"


def _append_dict(lines: list[str], title: str, values: dict[str, Any], *, limit: int = 20) -> None:
    if not values:
        return
    lines.append("")
    lines.append(f"## {title}")
    for idx, (key, value) in enumerate(values.items()):
        if idx >= limit:
            lines.append(f"- ... {len(values) - limit} more")
            break
        lines.append(f"- `{key}`: {value}")


def _append_list(lines: list[str], title: str, values: list[str], *, limit: int = 20) -> None:
    if not values:
        return
    lines.append("")
    lines.append(f"## {title}")
    for value in values[:limit]:
        lines.append(f"- {value}")
    if len(values) > limit:
        lines.append(f"- ... {len(values) - limit} more")
