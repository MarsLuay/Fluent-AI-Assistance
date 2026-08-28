"""Reader for FluentControl `.xscr` script XML."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from tecan_common.xml_compat import ET
from tecan_common.xml_helpers import (
    child_text,
    first_text,
    local_name,
    texts_by_name,
    unique_texts,
)

from .common import (
    command_family,
    command_short_name,
    parse_xml_text,
    read_text,
)

INTERESTING_FIELDS = {
    "Name",
    "Comment",
    "LineNumber",
    "Condition",
    "LoopVariable",
    "NumberOfLoops",
    "Value",
    "QueryPrompt",
    "MinimumText",
    "MaximumText",
    "LabwareName",
    "LabwareLable",
    "LabwareLabel",
    "LabwareType",
    "RackLabel",
    "RackType",
    "Location",
    "Position",
    "LiquidClassName",
    "LiquidClassNameBySelection",
    "Volume",
    "DeviceAlias",
    "AvailableID",
    "ScriptName",
    "MethodName",
    "ApplicationName",
    "FileName",
    "Path",
    "WorklistName",
    "SubRoutine",
    "Barcode",
    "CustomDetailImageFilePath",
    "PinNumber",
    "RUPScreenTitle",
}


def inspect_xscr(path: str | Path, *, source_name: str | None = None) -> dict[str, Any]:
    text = read_text(path)
    root = parse_xml_text(text)
    return inspect_xscr_text(text, source_name=source_name or str(path), root=root)


def inspect_xscr_text(
    text: str,
    *,
    source_name: str,
    root: ET.Element | None = None,
) -> dict[str, Any]:
    root = root or parse_xml_text(text)
    object_name = first_text(root, "ObjectName")
    checksum = first_text(root, "Checksum")
    script_version = _script_version(root)
    references = _references(root)
    variables = _variable_declarations(root)
    prompts = _query_prompts(root)
    set_variables = _set_variables(root)
    commands = _commands(root)
    command_counts = Counter(command["type"] for command in commands)
    family_counts = Counter(command["family"] for command in commands)
    dependencies = _dependencies(root)
    comments = _comments(root)

    return {
        "kind": "xscr",
        "source": source_name,
        "object_name": object_name,
        "script_version": script_version,
        "checksum": checksum,
        "references": references,
        "variables": variables,
        "query_prompts": prompts,
        "set_variables": set_variables,
        "command_count": len(commands),
        "command_counts": dict(command_counts.most_common()),
        "family_counts": dict(family_counts.most_common()),
        "commands": commands,
        "dependencies": dependencies,
        "comments": comments,
        "warnings": _warnings(commands),
    }


def _script_version(root: ET.Element) -> str:
    for el in root.iter():
        if local_name(el.tag) == "Script":
            return el.attrib.get("version", "")
    return ""


def _references(root: ET.Element) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for el in root.iter():
        if local_name(el.tag) != "Reference":
            continue
        ref = {
            "guid": child_text(el, "Guid"),
            "type_id": child_text(el, "TypeId"),
            "object_name": child_text(el, "ObjectName"),
        }
        if any(ref.values()):
            refs.append(ref)
    return refs


def _variable_declarations(root: ET.Element) -> list[dict[str, str]]:
    variables: list[dict[str, str]] = []
    for el in root.iter():
        if local_name(el.tag) != "anyType":
            continue
        type_hint = " ".join(str(v) for v in el.attrib.values())
        if "VariableDefinitionHelper" not in type_hint:
            continue
        var = {
            "name": first_text(el, "Name"),
            "type": first_text(el, "TypeName"),
            "scope": first_text(el, "Scope"),
            "query_on_startup": first_text(el, "QueryOnStartup"),
            "read_only": first_text(el, "ReadOnly"),
        }
        if var["name"]:
            variables.append(var)
    return variables


def _query_prompts(root: ET.Element) -> list[dict[str, str]]:
    prompts: list[dict[str, str]] = []
    for el in root.iter():
        if local_name(el.tag) != "QueryVariableStatement":
            continue
        prompts.append(
            {
                "name": first_text(el, "Name"),
                "prompt": first_text(el, "QueryPrompt"),
                "minimum": first_text(el, "MinimumText"),
                "maximum": first_text(el, "MaximumText"),
                "line": first_text(el, "LineNumber"),
            }
        )
    return prompts


def _set_variables(root: ET.Element) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for el in root.iter():
        if local_name(el.tag) != "SetVariableStatement":
            continue
        values.append(
            {
                "name": first_text(el, "Name"),
                "value": first_text(el, "Value"),
                "line": first_text(el, "LineNumber"),
            }
        )
    return values


def _commands(root: ET.Element) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for el in root.iter():
        if local_name(el.tag) != "Object" or "Type" not in el.attrib:
            continue
        type_name = el.attrib["Type"]
        fields = _interesting_fields(el)
        commands.append(
            {
                "index": len(commands) + 1,
                "type": command_short_name(type_name),
                "raw_type": type_name,
                "family": command_family(type_name),
                "line": fields.get("LineNumber", ""),
                "name": fields.get("Name", ""),
                "fields": fields,
            }
        )
    return commands


def _interesting_fields(el: ET.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for child in el.iter():
        name = local_name(child.tag)
        if name not in INTERESTING_FIELDS:
            continue
        value = (child.text or "").strip()
        if not value:
            continue
        if name in fields:
            if value not in fields[name].split(" | "):
                fields[name] += f" | {value}"
        else:
            fields[name] = value
    return fields


def _dependencies(root: ET.Element) -> dict[str, list[str]]:
    grouped = texts_by_name(
        root,
        {
            "BaseWorkspaceName",
            "LabwareName",
            "RackLabel",
            "RackType",
            "LiquidClassName",
            "LiquidClassNameBySelection",
            "DeviceAlias",
            "ScriptName",
            "MethodName",
            "ApplicationName",
            "FileName",
            "Path",
            "WorklistName",
            "SubRoutine",
            "Barcode",
            "CustomDetailImageFilePath",
            "PinNumber",
            "Location",
            "RUPScreenTitle",
        },
    )
    liquid_classes = sorted(
        set(grouped.get("LiquidClassName", []))
        | set(grouped.get("LiquidClassNameBySelection", []))
    )
    external = sorted(
        set(grouped.get("ScriptName", []))
        | set(grouped.get("MethodName", []))
        | set(grouped.get("ApplicationName", []))
        | set(grouped.get("FileName", []))
        | set(grouped.get("Path", []))
        | set(grouped.get("WorklistName", []))
        | set(grouped.get("SubRoutine", []))
    )
    return {
        "workspace_guids": grouped.get("BaseWorkspaceName", []),
        "labware_names": sorted(set(grouped.get("LabwareName", []))),
        "rack_labels": sorted(set(grouped.get("RackLabel", []))),
        "rack_types": sorted(set(grouped.get("RackType", []))),
        "liquid_classes": liquid_classes,
        "device_aliases": sorted(set(grouped.get("DeviceAlias", []))),
        "external_or_worklist_refs": external,
        "subroutine_refs": sorted(set(grouped.get("SubRoutine", []))),
        "barcode_refs": sorted(set(grouped.get("Barcode", []))),
        "custom_asset_refs": sorted(
            set(_asset_refs(grouped.get("CustomDetailImageFilePath", [])))
        ),
        "pin_refs": sorted(set(grouped.get("PinNumber", []))),
        "worktable_pin_locations": sorted(
            value
            for value in set(grouped.get("Location", []))
            if "pin" in str(value).casefold()
        ),
        "touchtools_titles": sorted(set(grouped.get("RUPScreenTitle", []))),
    }


def _comments(root: ET.Element) -> list[str]:
    return unique_texts(root, {"Comment"}, limit=30)


def _warnings(commands: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    raw_types = [command["raw_type"].lower() for command in commands]
    if any("subroutine" in t for t in raw_types):
        warnings.append(
            "Contains subroutine calls; inspect referenced scripts before editing."
        )
    if any("executevbscript" in t or "executeapplication" in t for t in raw_types):
        warnings.append("Contains external application or VB script calls.")
    if any("worklist" in t for t in raw_types):
        warnings.append("Contains worklist import/load/execute commands.")
    if any("touchtools" in t or ".rup." in t for t in raw_types):
        warnings.append("Contains TouchTools/RUP UI workflow commands.")
    if any(
        "pin" in str(value).lower()
        for command in commands
        for value in command.get("fields", {}).values()
    ):
        warnings.append("Contains pin-controlled or pin-located hardware references.")
    unknown = [command for command in commands if command["family"] == "Other"]
    if unknown:
        warnings.append(
            f"Contains {len(unknown)} commands outside the current family classifier."
        )
    return warnings


def _asset_refs(values: list[str]) -> list[str]:
    refs = []
    for value in values:
        name = Path(str(value).replace("\\", "/")).name
        if name and name not in refs:
            refs.append(name)
    return refs
