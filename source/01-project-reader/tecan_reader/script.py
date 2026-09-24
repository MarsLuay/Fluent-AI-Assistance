"""Reader for FluentControl `.xscr` script XML."""

from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
import re

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
    root = root if root is not None else parse_xml_text(text)
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
    script_guid = _entry_object_guid(source_name)
    startup_variables = _startup_variables(root)
    operator_prompts = _operator_prompts(root)

    return {
        "kind": "xscr",
        "source": source_name,
        "entry": source_name,
        "object_name": object_name,
        "guid": script_guid,
        "script_guid": script_guid,
        "guids": [script_guid] if script_guid else [],
        "folder": first_text(root, "ObjectSubfolderPath"),
        "object_path": first_text(root, "ObjectSubfolderPath"),
        "object_subfolder_path": first_text(root, "ObjectSubfolderPath"),
        "script_version": script_version,
        "checksum": checksum,
        "base_worktable_name": first_text(root, "BaseWorktableName"),
        "base_worktable_guid": first_text(root, "BaseWorktableGuid"),
        "references": references,
        "variables": variables,
        "startup_variables": startup_variables,
        "query_prompts": prompts,
        "operator_prompts": operator_prompts,
        "set_variables": set_variables,
        "command_count": len(commands),
        "command_counts": dict(command_counts.most_common()),
        "family_counts": dict(family_counts.most_common()),
        "commands": commands,
        "dependencies": dependencies,
        "comments": comments,
        "warnings": _warnings(commands),
        "source_metadata": {"unknown_fields": _unknown_fields(root)},
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
            "object_subfolder_path": child_text(el, "ObjectSubfolderPath"),
            "object_path": child_text(el, "ObjectSubfolderPath"),
        }
        if any(ref.values()):
            refs.append(ref)
    return refs


def _variable_declarations(root: ET.Element) -> list[dict[str, str]]:
    variables: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for el in root.iter():
        direct_names = {local_name(child.tag) for child in list(el)}
        type_hint = " ".join(str(v) for v in el.attrib.values())
        if (
            local_name(el.tag) not in {"anyType", "VariableDefinitionHelper"}
            and "VariableDefinitionHelper" not in type_hint
        ):
            continue
        if not {"Name", "TypeName"}.issubset(direct_names):
            continue
        name = child_text(el, "Name")
        variable_type = child_text(el, "TypeName")
        scope = child_text(el, "Scope")
        key = (name, scope, variable_type)
        if not name or key in seen:
            continue
        seen.add(key)
        variables.append(
            {
                "name": name,
                "type": variable_type,
                "scope": scope,
                "query_on_startup": child_text(el, "QueryOnStartup"),
                "read_only": child_text(el, "ReadOnly"),
            }
        )
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
            "LabwareLabel",
            "LabwareLable",
            "LabwareType",
            "RackLabel",
            "RackType",
            "LiquidClassName",
            "LiquidClassNameBySelection",
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
        "labware_names": sorted(
            set(grouped.get("LabwareName", []))
            | set(grouped.get("LabwareLabel", []))
            | set(grouped.get("LabwareLable", []))
        ),
        "rack_labels": sorted(set(grouped.get("RackLabel", []))),
        "rack_types": sorted(
            set(grouped.get("RackType", [])) | set(grouped.get("LabwareType", []))
        ),
        "liquid_classes": liquid_classes,
        "device_aliases": sorted(set(grouped.get("DeviceAlias", []))),
        "available_ids": sorted(set(grouped.get("AvailableID", []))),
        "external_or_worklist_refs": external,
        "subroutine_refs": sorted(set(grouped.get("SubRoutine", []))),
        "barcode_refs": sorted(set(grouped.get("Barcode", []))),
        "custom_asset_refs": sorted(
            set(_asset_refs(grouped.get("CustomDetailImageFilePath", [])))
            | set(_asset_refs_from_text(_all_text_values(root)))
        ),
        "pin_refs": sorted(
            set(grouped.get("PinNumber", [])) | set(_pin_refs(_all_text_values(root)))
        ),
        "worktable_pin_locations": sorted(
            value
            for value in set(grouped.get("Location", []))
            if "pin" in str(value).casefold()
        ),
        "touchtools_titles": sorted(set(grouped.get("RUPScreenTitle", []))),
    }


def _all_text_values(root: ET.Element) -> list[str]:
    values: list[str] = []
    for element in root.iter():
        value = (element.text or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _startup_variables(root: ET.Element) -> list[dict[str, Any]]:
    variables: list[dict[str, Any]] = []
    for element in root.iter():
        direct_names = {local_name(child.tag) for child in list(element)}
        type_hint = " ".join(str(value) for value in element.attrib.values())
        if (
            local_name(element.tag) not in {"anyType", "VariableDefinitionHelper"}
            and "VariableDefinitionHelper" not in type_hint
        ):
            continue
        if not {"Name", "TypeName"}.issubset(direct_names):
            continue
        name = child_text(element, "Name")
        if not name:
            continue
        values = [
            (child.text or "").strip()
            for values_node in list(element)
            if local_name(values_node.tag) == "Values"
            for child in list(values_node)
            if (child.text or "").strip()
        ]
        query = child_text(element, "QueryOnStartup").casefold() == "true"
        prompt = child_text(element, "QueryOnStartupString")
        variables.append(
            {
                "name": name,
                "scope": child_text(element, "Scope"),
                "type": child_text(element, "TypeName"),
                "query_on_startup": query,
                "prompt": prompt,
                "read_only": child_text(element, "ReadOnly").casefold() == "true",
                "default_values": values,
                "manual_review_required": query or bool(prompt),
            }
        )
    return variables


def _operator_prompts(root: ET.Element) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    for element in root.iter():
        statement_name = local_name(element.tag)
        if statement_name not in {"RUPVariableStatement", "RUPWorktableStatement", "RUPStandardStatement"}:
            continue
        prompt = {
            "kind": statement_name,
            "title": first_text(element, "RUPScreenTitle"),
            "line_number": first_text(element, "LineNumber"),
            "instructions": first_text(element, "Instructions"),
            "display_and_wait": first_text(element, "RUPDisplayAndWait"),
            "auto_close": first_text(element, "RUPAutoClose"),
            "timeout": first_text(element, "RUPTimeOut"),
            "variables": _rup_variable_items(element),
        }
        if prompt["title"] or prompt["instructions"] or prompt["variables"]:
            prompts.append(prompt)
    return prompts


def _rup_variable_items(statement: ET.Element) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for element in statement.iter():
        if local_name(element.tag) != "RupVariableItem":
            continue
        item = {
            "name": child_text(element, "VariableName"),
            "display_text": child_text(element, "DisplayText"),
            "display_type": child_text(element, "DisplayType"),
            "allowed_values": child_text(element, "AllowedValues"),
            "enabled": child_text(element, "IsEnabled"),
        }
        if any(item.values()):
            items.append(item)
    return items


def _unknown_fields(root: ET.Element) -> dict[str, list[str]]:
    known = INTERESTING_FIELDS | {
        "VxData", "Payload", "PayloadData", "Script", "Properties", "Commands",
        "Object", "Reference", "Guid", "GUID", "ObjectName", "ObjectSubfolderPath",
        "Checksum", "TypeName", "Scope", "QueryOnStartup", "QueryOnStartupString",
        "ReadOnly", "Values", "VariableDefinitionHelper", "QueryVariableStatement",
        "SetVariableStatement", "VariableDeclarations", "ScriptGroup", "Objects",
        "RUPVariableStatement", "RUPWorktableStatement", "RUPStandardStatement",
        "RupVariableItem", "VariableDatas", "VariableDataModel", "Variables",
        "Instructions", "RUPDisplayAndWait", "RUPAutoClose", "RUPTimeOut",
        "VariableName", "DisplayText", "DisplayType", "AllowedValues", "IsEnabled",
    }
    values: dict[str, list[str]] = {}
    for element in root.iter():
        name = local_name(element.tag)
        value = (element.text or "").strip()
        if not value or name in known:
            continue
        bucket = values.setdefault(name, [])
        if value not in bucket and len(bucket) < 20:
            bucket.append(value)
    return {name: values[name] for name in sorted(values)}


def _entry_object_guid(source_name: str) -> str:
    stem = PurePosixPath(str(source_name or "").replace("\\", "/")).stem
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        stem,
        re.IGNORECASE,
    ):
        return ""
    guid = stem.lower()
    return "" if guid == "00000000-0000-0000-0000-000000000000" else guid


def _asset_refs_from_text(values: list[str]) -> list[str]:
    return _asset_refs(
        [
            value
            for value in values
            if re.search(
                r"\.(?:bmp|gif|jpe?g|png|tiff?)(?:$|[\\s\"'])",
                value,
                re.IGNORECASE,
            )
        ]
    )


def _pin_refs(values: list[str]) -> list[str]:
    return sorted({
        match
        for value in values
        for match in re.findall(
            r"\b(?:GIO\d+_Pin\d+|Worktable_[A-Za-z0-9_]*Pin[A-Za-z0-9_]*|WorktablePin_[A-Za-z0-9_]+)\b",
            value,
        )
    })


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
