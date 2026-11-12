"""Reader for FluentControl `.xscr` script XML."""

from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
import re

from tecan_common import native_acceleration
from tecan_common.xml_compat import ET
from tecan_common.xml_helpers import (
    all_text_values,
    child_text,
    elements_by_name,
    first_attribute,
    first_text,
    grouped_texts_excluding,
    local_name,
    records_by_name,
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


def _ordered_counts(values: list[str]) -> dict[str, int]:
    native = native_acceleration.ordered_string_counts_required(values)
    if native is not None:
        return native
    return dict(Counter(values).most_common())


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
    command_counts = _ordered_counts([command["type"] for command in commands])
    family_counts = _ordered_counts([command["family"] for command in commands])
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
        "command_counts": command_counts,
        "family_counts": family_counts,
        "commands": commands,
        "dependencies": dependencies,
        "comments": comments,
        "warnings": _warnings(commands),
        "source_metadata": {"unknown_fields": _unknown_fields(root)},
    }


def _script_version(root: ET.Element) -> str:
    return first_attribute(root, "Script", "version")


def _references(root: ET.Element) -> list[dict[str, str]]:
    names = ("Guid", "TypeId", "ObjectName", "ObjectSubfolderPath")
    refs: list[dict[str, str]] = []
    for guid, type_id, object_name, object_path in records_by_name(
        root, "Reference", names, direct=True
    ):
        ref = {
            "guid": guid,
            "type_id": type_id,
            "object_name": object_name,
            "object_subfolder_path": object_path,
            "object_path": object_path,
        }
        if any(ref.values()):
            refs.append(ref)
    return refs


def _variable_definition_elements(root: ET.Element) -> list[ET.Element]:
    native = native_acceleration.xml_variable_definition_elements(root, ET.Element)
    if native is not None:
        return native
    elements: list[ET.Element] = []
    for element in root.iter():
        direct_names = {local_name(child.tag) for child in list(element)}
        type_hint = " ".join(str(value) for value in element.attrib.values())
        if (
            local_name(element.tag) in {"anyType", "VariableDefinitionHelper"}
            or "VariableDefinitionHelper" in type_hint
        ) and {"Name", "TypeName"}.issubset(direct_names):
            elements.append(element)
    return elements


def _variable_declarations(root: ET.Element) -> list[dict[str, str]]:
    candidates: list[tuple[ET.Element, str, str, str]] = []
    for el in _variable_definition_elements(root):
        name = child_text(el, "Name")
        variable_type = child_text(el, "TypeName")
        scope = child_text(el, "Scope")
        if name:
            candidates.append((el, name, scope, variable_type))

    keys = [(name, scope, variable_type) for _, name, scope, variable_type in candidates]
    unique_indices = native_acceleration.unique_ascii_tuple_indices_required(keys)
    if unique_indices is None:
        unique_indices = []
        seen: set[tuple[str, str, str]] = set()
        for index, key in enumerate(keys):
            if key not in seen:
                seen.add(key)
                unique_indices.append(index)

    variables: list[dict[str, str]] = []
    for index in unique_indices:
        el, name, scope, variable_type = candidates[index]
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
    names = ("Name", "QueryPrompt", "MinimumText", "MaximumText", "LineNumber")
    return [
        dict(zip(("name", "prompt", "minimum", "maximum", "line"), values))
        for values in records_by_name(root, "QueryVariableStatement", names)
    ]


def _set_variables(root: ET.Element) -> list[dict[str, str]]:
    names = ("Name", "Value", "LineNumber")
    return [
        dict(zip(("name", "value", "line"), values))
        for values in records_by_name(root, "SetVariableStatement", names)
    ]


def _commands(root: ET.Element) -> list[dict[str, Any]]:
    native_records = native_acceleration.xscr_command_records(
        root, INTERESTING_FIELDS, ET.Element
    )
    if native_records is not None:
        commands: list[dict[str, Any]] = []
        for type_name, fields in native_records:
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
    commands: list[dict[str, Any]] = []
    for el in elements_by_name(root, {"Object"}):
        if "Type" not in el.attrib:
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
    for child in elements_by_name(el, INTERESTING_FIELDS):
        name = local_name(child.tag)
        value = (child.text or "").strip()
        if not value:
            continue
        if name in fields:
            if value not in fields[name].split(" | "):
                fields[name] += f" | {value}"
        else:
            fields[name] = value
    return fields


def _sorted_unique_strings(*groups: list[str]) -> list[str]:
    native = native_acceleration.sorted_unique_strings_required(groups)
    if native is not None:
        return native
    return sorted({value for group in groups for value in group})


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
    liquid_classes = _sorted_unique_strings(
        grouped.get("LiquidClassName", []),
        grouped.get("LiquidClassNameBySelection", []),
    )
    external = _sorted_unique_strings(
        grouped.get("ScriptName", []),
        grouped.get("MethodName", []),
        grouped.get("ApplicationName", []),
        grouped.get("FileName", []),
        grouped.get("Path", []),
        grouped.get("WorklistName", []),
        grouped.get("SubRoutine", []),
    )
    locations = grouped.get("Location", [])
    pin_matches = native_acceleration.ascii_case_insensitive_substring_match_values_required(
        locations, "pin"
    )
    if pin_matches is None:
        worktable_pin_locations = _sorted_unique_strings(
            [value for value in locations if "pin" in _casefold_text(str(value))]
        )
    else:
        worktable_pin_locations = _sorted_unique_strings(
            [value for value, matched in zip(locations, pin_matches) if matched]
        )
    return {
        "workspace_guids": grouped.get("BaseWorkspaceName", []),
        "labware_names": _sorted_unique_strings(
            grouped.get("LabwareName", []),
            grouped.get("LabwareLabel", []),
            grouped.get("LabwareLable", []),
        ),
        "rack_labels": _sorted_unique_strings(grouped.get("RackLabel", [])),
        "rack_types": _sorted_unique_strings(
            grouped.get("RackType", []), grouped.get("LabwareType", [])
        ),
        "liquid_classes": liquid_classes,
        "device_aliases": _sorted_unique_strings(grouped.get("DeviceAlias", [])),
        "available_ids": _sorted_unique_strings(grouped.get("AvailableID", [])),
        "external_or_worklist_refs": external,
        "subroutine_refs": _sorted_unique_strings(grouped.get("SubRoutine", [])),
        "barcode_refs": _sorted_unique_strings(grouped.get("Barcode", [])),
        "custom_asset_refs": _sorted_unique_strings(
            _asset_refs(grouped.get("CustomDetailImageFilePath", [])),
            _asset_refs_from_text(_all_text_values(root)),
        ),
        "pin_refs": _sorted_unique_strings(
            grouped.get("PinNumber", []), _pin_refs(_all_text_values(root))
        ),
        "worktable_pin_locations": worktable_pin_locations,
        "touchtools_titles": _sorted_unique_strings(grouped.get("RUPScreenTitle", [])),
    }


def _all_text_values(root: ET.Element) -> list[str]:
    return all_text_values(root)


def _lower_text(value: str) -> str:
    if type(value) is str and value.strip() == value:
        native = native_acceleration.ascii_lower_required(value)
        if native is not None:
            return native
    return value.lower()


def _casefold_text(value: str) -> str:
    if type(value) is str and value.strip() == value:
        native = native_acceleration.ascii_casefold_trim_required(value)
        if native is not None:
            return native
    return value.casefold()


def _casefold_equals(value: str, expected: str) -> bool:
    native = native_acceleration.ascii_casefold_equals_required(value, expected)
    if native is not None:
        return native
    return value.casefold() == expected


def _startup_variables(root: ET.Element) -> list[dict[str, Any]]:
    variables: list[dict[str, Any]] = []
    for element in _variable_definition_elements(root):
        name = child_text(element, "Name")
        if not name:
            continue
        native_values = native_acceleration.xml_container_child_texts(
            element, "Values", ET.Element
        )
        values = (
            native_values
            if native_values is not None
            else [
                (child.text or "").strip()
                for values_node in list(element)
                if local_name(values_node.tag) == "Values"
                for child in list(values_node)
                if (child.text or "").strip()
            ]
        )
        query = _casefold_equals(child_text(element, "QueryOnStartup"), "true")
        prompt = child_text(element, "QueryOnStartupString")
        variables.append(
            {
                "name": name,
                "scope": child_text(element, "Scope"),
                "type": child_text(element, "TypeName"),
                "query_on_startup": query,
                "prompt": prompt,
                "read_only": _casefold_equals(child_text(element, "ReadOnly"), "true"),
                "default_values": values,
                "manual_review_required": query or bool(prompt),
            }
        )
    return variables


def _operator_prompts(root: ET.Element) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    statement_names = {"RUPVariableStatement", "RUPWorktableStatement", "RUPStandardStatement"}
    for element in elements_by_name(root, statement_names):
        statement_name = local_name(element.tag)
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
    names = ("VariableName", "DisplayText", "DisplayType", "AllowedValues", "IsEnabled")
    items: list[dict[str, str]] = []
    for name, display_text, display_type, allowed_values, enabled in records_by_name(
        statement, "RupVariableItem", names, direct=True
    ):
        item = {
            "name": name,
            "display_text": display_text,
            "display_type": display_type,
            "allowed_values": allowed_values,
            "enabled": enabled,
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
    native = grouped_texts_excluding(root, known, limit=20)
    if native is not None:
        return native
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
    native = native_acceleration.entry_object_guid(source_name)
    if native is not None:
        return native
    stem = PurePosixPath(str(source_name or "").replace("\\", "/")).stem
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        stem,
        re.IGNORECASE,
    ):
        return ""
    guid = _lower_text(stem)
    return "" if guid == "00000000-0000-0000-0000-000000000000" else guid


def _asset_refs_from_text(values: list[str]) -> list[str]:
    native = native_acceleration.asset_reference_candidates(values)
    if native is not None:
        return _asset_refs(native)
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
    native = native_acceleration.pin_reference_candidates(values)
    if native is not None:
        return native
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
    raw_types = [command["raw_type"] for command in commands]
    search_groups = (
        ("subroutine",),
        ("executevbscript", "executeapplication"),
        ("worklist",),
        ("touchtools", ".rup."),
    )
    matches = native_acceleration.ascii_case_insensitive_substring_match_groups(
        raw_types, search_groups
    )
    if matches is None:
        lowered_types = [_lower_text(raw_type) for raw_type in raw_types]
        matches = [
            any(needle in value for value in lowered_types for needle in group)
            for group in search_groups
        ]
    if matches[0]:
        warnings.append(
            "Contains subroutine calls; inspect referenced scripts before editing."
        )
    if matches[1]:
        warnings.append("Contains external application or VB script calls.")
    if matches[2]:
        warnings.append("Contains worklist import/load/execute commands.")
    if matches[3]:
        warnings.append("Contains TouchTools/RUP UI workflow commands.")
    field_values = [
        value
        for command in commands
        for value in command.get("fields", {}).values()
    ]
    pin_match = None
    if all(type(value) is str and value.isascii() for value in field_values):
        pin_match = native_acceleration.ascii_case_insensitive_substring_match_groups(
            field_values, (("pin",),)
        )
    contains_pin = (
        pin_match[0]
        if pin_match is not None
        else any(
            "pin" in _lower_text(str(value))
            for command in commands
            for value in command.get("fields", {}).values()
        )
    )
    if contains_pin:
        warnings.append("Contains pin-controlled or pin-located hardware references.")
    unknown = [command for command in commands if command["family"] == "Other"]
    if unknown:
        warnings.append(
            f"Contains {len(unknown)} commands outside the current family classifier."
        )
    return warnings


def _asset_refs(values: list[str]) -> list[str]:
    native = native_acceleration.asset_reference_names(values)
    if native is not None:
        return native
    refs = []
    for value in values:
        name = Path(str(value).replace("\\", "/")).name
        if name and name not in refs:
            refs.append(name)
    return refs
