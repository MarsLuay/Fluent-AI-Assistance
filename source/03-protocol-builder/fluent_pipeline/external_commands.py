"""Mine source-backed contracts for external FluentControl commands."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .xml_compat import ET
from fluentcoder.ir.driver_recovery import parse_driver_recovery_policy


_VARIABLE_TOKEN = re.compile(r"~([^~]+)~")
_EXPRESSION_NAME = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")

EXTERNAL_COMMAND_CONTRACT_SCHEMA = "tecan.external_command_contract.v1"
DRIVER_USAGE_CONTRACT_SCHEMA = "tecan.driver_usage_contract.v1"


class DriverCommandContractAmbiguityError(ValueError):
    """Raised when multiple incompatible source-backed contracts match."""

    def __init__(
        self,
        message: str,
        *,
        macro_name: str,
        module_name: str | None,
        contract_ids: Sequence[str],
    ) -> None:
        super().__init__(message)
        self.macro_name = macro_name
        self.module_name = module_name
        self.contract_ids = list(contract_ids)


class DriverCommandContractNotFoundError(ValueError):
    """Raised when no source-backed contract matches the requested macro."""

    def __init__(
        self,
        message: str,
        *,
        macro_name: str,
        module_name: str | None,
    ) -> None:
        super().__init__(message)
        self.macro_name = macro_name
        self.module_name = module_name


def inspect_external_command(
    manifest: dict[str, Any],
    *,
    context_root: Path,
    command_name: str,
    module_name: str | None = None,
    source_script: str | None = None,
) -> dict[str, Any]:
    """Return matching source usages plus declarations and assignment chains."""
    matches = [
        contract
        for contract in build_driver_usage_contracts(
            manifest,
            context_root=context_root,
            command_name=command_name,
            module_name=module_name,
            source_script=source_script,
        )
    ]
    return {
        "schema_version": EXTERNAL_COMMAND_CONTRACT_SCHEMA,
        "command_name": command_name,
        "module_name": module_name,
        "source_script": source_script,
        "match_count": len(matches),
        "matches": matches,
    }


def build_driver_usage_contracts(
    manifest: Mapping[str, Any] | None,
    *,
    context_root: Path | str | None = None,
    command_name: str | None = None,
    module_name: str | None = None,
    source_script: str | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic per-usage driver contracts from imported XSCR scripts."""
    root = Path(context_root).expanduser() if context_root else None
    matches: list[dict[str, Any]] = []
    scripts = (manifest or {}).get("scripts") or [] if isinstance(manifest, Mapping) else []
    for script in scripts:
        if not isinstance(script, Mapping):
            continue
        object_name = str(script.get("object_name") or script.get("name") or "")
        if source_script and object_name.casefold() != source_script.casefold():
            continue
        path = _script_path(dict(script), root) if root is not None else _script_path(dict(script), None)
        if path is None:
            continue
        matches.extend(
            _inspect_script(
                path,
                object_name=object_name,
                command_name=command_name,
                module_name=module_name,
                context_root=root,
            )
        )
    matches.sort(key=_contract_sort_key)
    return matches


def build_usage_contract_from_macro(
    *,
    macro: ET.Element,
    macros: Sequence[ET.Element],
    macro_index: int,
    object_name: str,
    source_path: str,
    declarations: Mapping[str, dict[str, Any]],
    assignments: Mapping[str, list[dict[str, str]]],
) -> dict[str, Any]:
    """Build one reusable per-usage contract from a parsed macro element."""
    settings = _child_text(macro, "ExecutionSettings")
    direct_variables = _variable_tokens(settings)
    dependency_variables = _dependency_closure(direct_variables, assignments, declarations)
    companion = _following_companion(list(macros), macro_index, macro.get("ModuleName"))
    command_kind = _local_name(macro.tag)
    recovery_policy = parse_driver_recovery_policy(macro)
    contract: dict[str, Any] = {
        "schema_version": DRIVER_USAGE_CONTRACT_SCHEMA,
        "source_script": object_name,
        "source_path": source_path,
        "command_kind": command_kind,
        "name": macro.get("Name") or "",
        "macro_name": macro.get("Name") or "",
        "module_name": macro.get("ModuleName") or "",
        "execution_settings": settings,
        "execution_time": macro.get("ExecutionTime") or "",
        "disabled": str(macro.get("IsDisabledForExecution") or "false").casefold() == "true",
        "line_number": macro.get("LineNumber") or "",
        "command_index": macro_index,
        "recovery_policy": recovery_policy.as_dict() if recovery_policy is not None else None,
        "referenced_variables": direct_variables,
        "dependency_variables": dependency_variables,
        "variable_declarations": [
            {
                **declarations.get(
                    name,
                    {"name": name, "type": "unknown", "scope": "unknown", "default": ""},
                ),
                "assignments": list(assignments.get(name, [])),
            }
            for name in dependency_variables
        ],
        "following_companion": companion,
    }
    contract["contract_id"] = fingerprint_driver_usage_contract(contract)
    return contract


def fingerprint_driver_usage_contract(contract: Mapping[str, Any]) -> str:
    """Stable fingerprint from source semantics (never absolute developer paths)."""
    companion = contract.get("following_companion") or {}
    payload = {
        "command_kind": str(contract.get("command_kind") or ""),
        "disabled": bool(contract.get("disabled")),
        "execution_settings": str(contract.get("execution_settings") or ""),
        "execution_time": str(contract.get("execution_time") or ""),
        "recovery_policy": contract.get("recovery_policy"),
        "following_companion": {
            "execution_settings": str(companion.get("execution_settings") or ""),
            "execution_time": str(companion.get("execution_time") or ""),
            "name": str(companion.get("name") or ""),
            "module_name": str(companion.get("module_name") or ""),
        }
        if companion
        else None,
        "line_number": str(contract.get("line_number") or ""),
        "macro_name": str(contract.get("macro_name") or contract.get("name") or ""),
        "module_name": str(contract.get("module_name") or ""),
        "referenced_variables": list(contract.get("referenced_variables") or []),
        "dependency_variables": list(contract.get("dependency_variables") or []),
        "source_script": str(contract.get("source_script") or ""),
        "variable_defaults": [
            {
                "name": str(item.get("name") or ""),
                "type": str(item.get("type") or ""),
                "scope": str(item.get("scope") or ""),
                "default": str(item.get("default") or ""),
                "assignments": [
                    {
                        "value": str(assignment.get("value") or ""),
                        "condition": str(assignment.get("condition") or ""),
                    }
                    for assignment in (item.get("assignments") or [])
                ],
            }
            for item in (contract.get("variable_declarations") or [])
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"dcc_{digest[:16]}"


def semantic_contract_key(contract: Mapping[str, Any]) -> tuple[Any, ...]:
    """Key used to decide whether two usages are compatible (not merely same macro)."""
    companion = contract.get("following_companion") or {}
    return (
        str(contract.get("execution_settings") or ""),
        str(contract.get("execution_time") or ""),
        bool(contract.get("disabled")),
        tuple(contract.get("referenced_variables") or []),
        tuple(contract.get("dependency_variables") or []),
        str(companion.get("name") or ""),
        str(companion.get("execution_settings") or ""),
        str(companion.get("execution_time") or ""),
        json.dumps(contract.get("variable_declarations") or [], sort_keys=True, default=str),
    )


def select_driver_usage_contract(
    contracts: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    *,
    macro_name: str,
    module_name: str | None = None,
    contract_id: str | None = None,
    source_script: str | None = None,
    command_kind: str | None = None,
) -> dict[str, Any]:
    """Select one exact source-backed usage contract or fail deterministically."""
    usages = _normalize_contract_sequence(contracts)
    if contract_id:
        for usage in usages:
            if str(usage.get("contract_id") or "") == contract_id:
                return dict(usage)
        raise DriverCommandContractNotFoundError(
            f"No driver usage contract with id {contract_id!r}",
            macro_name=macro_name,
            module_name=module_name,
        )

    matches = [
        usage
        for usage in usages
        if str(usage.get("macro_name") or usage.get("name") or "").casefold() == macro_name.casefold()
        and (
            module_name is None
            or str(usage.get("module_name") or "").casefold() == module_name.casefold()
        )
        and (
            source_script is None
            or str(usage.get("source_script") or "").casefold() == source_script.casefold()
        )
        and (
            command_kind is None
            or str(usage.get("command_kind") or "").casefold() == command_kind.casefold()
        )
    ]
    if not matches:
        raise DriverCommandContractNotFoundError(
            f"No source-backed driver usage contract for {macro_name!r}"
            + (f" / {module_name!r}" if module_name else ""),
            macro_name=macro_name,
            module_name=module_name,
        )

    unique_by_semantics: dict[tuple[Any, ...], dict[str, Any]] = {}
    for match in matches:
        unique_by_semantics.setdefault(semantic_contract_key(match), match)
    unique = sorted(unique_by_semantics.values(), key=_contract_sort_key)
    if len(unique) == 1:
        return dict(unique[0])

    contract_ids = [str(item.get("contract_id") or "") for item in unique]
    raise DriverCommandContractAmbiguityError(
        "Ambiguous driver usage contracts for "
        f"{macro_name!r}/{module_name or '*'}: {', '.join(contract_ids)}. "
        "Pass contract_id or source_script to disambiguate.",
        macro_name=macro_name,
        module_name=module_name,
        contract_ids=contract_ids,
    )


def ambiguity_groups_for_contracts(
    contracts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Group macro/module pairs that have incompatible source-backed contracts."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for contract in contracts:
        if str(contract.get("source_kind") or "script_usage") == "datastore_inventory":
            continue
        key = (
            str(contract.get("macro_name") or contract.get("name") or ""),
            str(contract.get("module_name") or ""),
        )
        if not key[0]:
            continue
        grouped.setdefault(key, []).append(dict(contract))

    groups: list[dict[str, Any]] = []
    for (macro_name, module_name), usages in sorted(
        grouped.items(),
        key=lambda item: (item[0][0].casefold(), item[0][1].casefold()),
    ):
        unique_keys = {semantic_contract_key(item) for item in usages}
        if len(unique_keys) <= 1:
            continue
        ordered = sorted(usages, key=_contract_sort_key)
        groups.append(
            {
                "macro_name": macro_name,
                "module_name": module_name or None,
                "contract_ids": [str(item.get("contract_id") or "") for item in ordered],
                "usage_count": len(ordered),
                "reason": "incompatible_source_contracts",
            }
        )
    return groups


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
                f"- Contract ID: `{match.get('contract_id')}`",
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


def _normalize_contract_sequence(
    contracts: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if contracts is None:
        return []
    if isinstance(contracts, Mapping):
        if "usages" in contracts:
            raw = contracts.get("usages") or []
        elif "matches" in contracts:
            raw = contracts.get("matches") or []
        elif "entries" in contracts:
            raw = contracts.get("entries") or []
        else:
            raw = [contracts]
    else:
        raw = list(contracts)
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _contract_sort_key(contract: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(contract.get("macro_name") or contract.get("name") or "").casefold(),
        str(contract.get("module_name") or "").casefold(),
        str(contract.get("source_script") or "").casefold(),
        str(contract.get("line_number") or ""),
        int(contract.get("command_index") or 0),
        str(contract.get("contract_id") or ""),
    )


def _inspect_script(
    path: Path,
    *,
    object_name: str,
    command_name: str | None,
    module_name: str | None,
    context_root: Path | None,
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
    stable_path = _stable_source_path(path, context_root)
    results: list[dict[str, Any]] = []
    for macro_index, macro in enumerate(macros):
        macro_name = str(macro.get("Name") or "")
        if command_name and macro_name.casefold() != command_name.casefold():
            continue
        if module_name and str(macro.get("ModuleName") or "").casefold() != module_name.casefold():
            continue
        results.append(
            build_usage_contract_from_macro(
                macro=macro,
                macros=macros,
                macro_index=macro_index,
                object_name=object_name,
                source_path=stable_path,
                declarations=declarations,
                assignments=assignments,
            )
        )
    return results


def _stable_source_path(path: Path, context_root: Path | None) -> str:
    if context_root is not None:
        try:
            return path.resolve().relative_to(Path(context_root).resolve()).as_posix()
        except ValueError:
            pass
    return path.name


def _script_path(script: dict[str, Any], context_root: Path | None) -> Path | None:
    raw = script.get("resolved_path") or script.get("path") or script.get("extracted_path")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute() and context_root is not None:
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
