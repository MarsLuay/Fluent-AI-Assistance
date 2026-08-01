"""Mine source-backed contracts for external FluentControl commands."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .xml_compat import ET


_VARIABLE_TOKEN = re.compile(r"~([^~]+)~")
_EXPRESSION_NAME = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def inspect_external_command(
    manifest: dict[str, Any],
    *,
    context_root: Path,
    command_name: str,
    module_name: str | None = None,
    source_script: str | None = None,
) -> dict[str, Any]:
    """Return matching source usages plus declarations and assignment chains."""
    matches: list[dict[str, Any]] = []
    scripts = manifest.get("scripts") or []
    for script in scripts:
        if not isinstance(script, dict):
            continue
        object_name = str(script.get("object_name") or script.get("name") or "")
        if source_script and object_name.casefold() != source_script.casefold():
            continue
        path = _script_path(script, context_root)
        if path is None:
            continue
        matches.extend(
            _inspect_script(
                path,
                object_name=object_name,
                command_name=command_name,
                module_name=module_name,
            )
        )
    return {
        "schema_version": "tecan.external_command_contract.v1",
        "command_name": command_name,
        "module_name": module_name,
        "source_script": source_script,
        "match_count": len(matches),
        "matches": matches,
    }


def render_external_command_contract_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# External Command Source Contract",
        "",
        f"- Command: `{report.get('command_name')}`",
        f"- Module: `{report.get('module_name') or 'any'}`",
        f"- Source script filter: `{report.get('source_script') or 'any'}`",
        f"- Matching usages: `{report.get('match_count', 0)}`",
        "",
    ]
    for index, match in enumerate(report.get("matches") or [], 1):
        lines.extend(
            [
                f"## {index}. {match.get('source_script')}",
                "",
                f"- Source XSCR: `{match.get('source_path')}`",
                f"- Execution settings: `{match.get('execution_settings')}`",
                f"- Execution time: `{match.get('execution_time')}`",
                f"- Disabled: `{match.get('disabled')}`",
                f"- Line number: `{match.get('line_number')}`",
                f"- Referenced variables: `{', '.join(match.get('referenced_variables') or []) or 'none'}`",
            ]
        )
        companion = match.get("following_companion")
        if companion:
            lines.extend(
                [
                    f"- Following companion: `{companion.get('name')}`",
                    f"- Companion settings: `{companion.get('execution_settings')}`",
                    f"- Companion execution time: `{companion.get('execution_time')}`",
                ]
            )
        lines.extend(["", "### Variable Contract", ""])
        declarations = match.get("variable_declarations") or []
        if not declarations:
            lines.append("- No variable placeholders are used by this command.")
        for declaration in declarations:
            lines.append(
                "- "
                f"`{declaration.get('name')}`: {declaration.get('type')}, "
                f"scope {declaration.get('scope')}, "
                f"default `{declaration.get('default')}`"
            )
            for assignment in declaration.get("assignments") or []:
                condition = assignment.get("condition")
                suffix = f" when `{condition}`" if condition else ""
                lines.append(f"  - set to `{assignment.get('value')}`{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_external_command_contract(
    report: dict[str, Any],
    *,
    json_path: Path | None = None,
    markdown_path: Path | None = None,
) -> None:
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            render_external_command_contract_markdown(report),
            encoding="utf-8",
        )


def _inspect_script(
    path: Path,
    *,
    object_name: str,
    command_name: str,
    module_name: str | None,
) -> list[dict[str, Any]]:
    root = ET.parse(path).getroot()
    parents = {child: parent for parent in root.iter() for child in parent}
    declarations = _declarations(root)
    assignments = _assignments(root, parents)
    macros = [
        elem
        for elem in root.iter()
        if _local_name(elem.tag) in {"LegacyDriverMacro", "ApplicationDriverMacro"}
    ]
    results: list[dict[str, Any]] = []
    for macro_index, macro in enumerate(macros):
        if str(macro.get("Name") or "").casefold() != command_name.casefold():
            continue
        if module_name and str(macro.get("ModuleName") or "").casefold() != module_name.casefold():
            continue
        settings = _child_text(macro, "ExecutionSettings")
        direct_variables = _variable_tokens(settings)
        dependency_variables = _dependency_closure(direct_variables, assignments, declarations)
        companion = _following_companion(macros, macro_index, macro.get("ModuleName"))
        results.append(
            {
                "source_script": object_name,
                "source_path": str(path),
                "command_kind": _local_name(macro.tag),
                "name": macro.get("Name") or "",
                "module_name": macro.get("ModuleName") or "",
                "execution_settings": settings,
                "execution_time": macro.get("ExecutionTime") or "",
                "disabled": str(macro.get("IsDisabledForExecution") or "false").casefold() == "true",
                "line_number": macro.get("LineNumber") or "",
                "referenced_variables": direct_variables,
                "dependency_variables": dependency_variables,
                "variable_declarations": [
                    {
                        **declarations.get(name, {"name": name, "type": "unknown", "scope": "unknown", "default": ""}),
                        "assignments": assignments.get(name, []),
                    }
                    for name in dependency_variables
                ],
                "following_companion": companion,
            }
        )
    return results


def _script_path(script: dict[str, Any], context_root: Path) -> Path | None:
    raw = script.get("resolved_path") or script.get("path") or script.get("extracted_path")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = context_root / path
    return path if path.is_file() else None


def _declarations(root: ET.Element) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for elem in root.iter():
        declared_type = next(
            (
                str(value)
                for key, value in elem.attrib.items()
                if _local_name(key) == "type"
            ),
            "",
        )
        if (
            _local_name(elem.tag) != "VariableDefinitionHelper"
            and not declared_type.endswith("VariableDefinitionHelper")
        ):
            continue
        name = _child_text(elem, "Name")
        if not name:
            continue
        values = [
            str(child.text or "")
            for child in elem.iter()
            if _local_name(child.tag) == "string"
        ]
        result[name] = {
            "name": name,
            "type": _child_text(elem, "TypeName"),
            "scope": _child_text(elem, "Scope"),
            "default": values[0] if values else "",
        }
    return result


def _assignments(
    root: ET.Element,
    parents: dict[ET.Element, ET.Element],
) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for elem in root.iter():
        if _local_name(elem.tag) != "SetVariableStatement":
            continue
        name = _child_text(elem, "Name")
        if not name:
            continue
        condition = ""
        parent = parents.get(elem)
        while parent is not None:
            if _local_name(parent.tag) == "ConditionalGroup":
                condition = _child_text(parent, "Condition")
                break
            parent = parents.get(parent)
        result.setdefault(name, []).append(
            {
                "value": _child_text(elem, "Value"),
                "condition": condition,
                "line_number": _child_text(elem, "LineNumber"),
            }
        )
    return result


def _dependency_closure(
    initial: Iterable[str],
    assignments: dict[str, list[dict[str, str]]],
    declarations: dict[str, dict[str, Any]],
) -> list[str]:
    ordered: list[str] = []
    pending = list(initial)
    while pending:
        name = pending.pop(0)
        if name in ordered:
            continue
        ordered.append(name)
        for assignment in assignments.get(name, []):
            expression = f"{assignment.get('value', '')} {assignment.get('condition', '')}"
            for candidate in _EXPRESSION_NAME.findall(expression):
                if candidate in declarations and candidate not in ordered:
                    pending.append(candidate)
    return ordered


def _following_companion(
    macros: list[ET.Element],
    index: int,
    module_name: str | None,
) -> dict[str, Any] | None:
    if index + 1 >= len(macros):
        return None
    candidate = macros[index + 1]
    if str(candidate.get("ModuleName") or "").casefold() != str(module_name or "").casefold():
        return None
    return {
        "name": candidate.get("Name") or "",
        "module_name": candidate.get("ModuleName") or "",
        "execution_settings": _child_text(candidate, "ExecutionSettings"),
        "execution_time": candidate.get("ExecutionTime") or "",
        "disabled": str(candidate.get("IsDisabledForExecution") or "false").casefold() == "true",
        "line_number": candidate.get("LineNumber") or "",
    }


def _variable_tokens(value: str) -> list[str]:
    return list(dict.fromkeys(match.strip() for match in _VARIABLE_TOKEN.findall(value) if match.strip()))


def _child_text(elem: ET.Element, name: str) -> str:
    for child in elem:
        if _local_name(child.tag) == name:
            return str(child.text or "")
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
