"""Worktable and labware diffing for generated Tecan protocols."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .aliases import alias_candidates, load_alias_maps, resolve_alias
from .worktable_geometry import build_worktable_geometry, workspace_labware_records


WORKTABLE_PATCH_SCHEMA_VERSION = "tecan.worktable_patch.v1"
PATCH_SEVERITIES = ("safe", "needs_review", "blocking")


def diff_worktable_requirements(
    protocol_ir: dict[str, Any],
    *,
    source_manifest: dict[str, Any] | None = None,
    source_ir: dict[str, Any] | None = None,
    source_irs: list[dict[str, Any]] | None = None,
    alias_maps: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Compare source ZEIA context/worktable data with protocol IR requirements."""
    aliases = alias_maps if alias_maps is not None else load_alias_maps()
    requirements = _requirements_from_ir(protocol_ir, alias_maps=aliases)
    source = _source_context(
        source_manifest=source_manifest,
        source_ir=source_ir,
        source_irs=source_irs or [],
        alias_maps=aliases,
        requested_worktable=requirements["worktable"],
    )

    missing_labware = _missing_labware(source, requirements, aliases)
    changed_positions = _changed_deck_positions(source, requirements, aliases)
    liquid_classes = _status_records(
        requirements["liquid_classes"],
        source["liquid_classes"],
        alias_kind="liquid_class",
        alias_maps=aliases,
    )
    tip_boxes = _required_tip_boxes(source, requirements, aliases)
    carriers = _status_records(requirements["carriers"], source["carriers"], alias_kind="catalog", alias_maps=aliases)
    device_aliases = _status_records(
        requirements["device_aliases"],
        source["device_aliases"],
        alias_kind="device_alias",
        alias_maps=aliases,
    )
    worklist_paths = _status_records(requirements["worklist_paths"], source["worklist_paths"])

    diff = {
        "kind": "worktable_diff",
        "source": {
            "name": source.get("name") or "",
            "worktable": source.get("worktable") or {},
            "worktable_geometry": _geometry_summary(source.get("worktable_geometry") or {}),
            "labware_count": len(source["labware_by_label"]),
            "liquid_class_count": len(source["liquid_classes"]),
            "carrier_count": len(source["carriers"]),
            "device_alias_count": len(source["device_aliases"]),
            "worklist_count": len(source["worklist_paths"]),
        },
        "protocol": {
            "name": (protocol_ir.get("protocol") or {}).get("name") or protocol_ir.get("id") or "",
            "worktable": requirements["worktable"],
            "labware_count": len(requirements["labware"]),
            "liquid_class_count": len(requirements["liquid_classes"]),
            "carrier_count": len(requirements["carriers"]),
            "device_alias_count": len(requirements["device_aliases"]),
            "worklist_count": len(requirements["worklist_paths"]),
        },
        "missing_labware": missing_labware,
        "changed_deck_positions": changed_positions,
        "required_liquid_classes": liquid_classes,
        "required_tip_boxes": tip_boxes,
        "required_carriers": carriers,
        "device_aliases": device_aliases,
        "worklist_paths": worklist_paths,
        "manual_setup_steps": [],
        "warnings": _diff_warnings(source, requirements),
    }
    diff["automatic_setup_steps"] = _automatic_setup_steps(diff)
    diff["manual_setup_steps"] = _manual_setup_steps(diff)
    diff["summary"] = worktable_patch_from_diff(diff)["summary"]
    return diff


def render_worktable_changes_markdown(diff: dict[str, Any]) -> str:
    lines = [
        "# Worktable Changes",
        "",
        "This report compares the source ZEIA worktable/context with the new protocol IR requirements.",
        "",
        "## Source Worktable",
        "",
    ]
    source = diff.get("source") or {}
    source_worktable = source.get("worktable") or {}
    if source_worktable:
        _append_item_details(
            lines,
            source_worktable.get("name") or source.get("name") or "source worktable",
            {
                "GUID": source_worktable.get("guid"),
                "Path": source_worktable.get("path"),
            },
        )
    else:
        lines.append("- No source worktable metadata was available.")
    source_geometry = source.get("worktable_geometry") or {}
    lines.extend(
        [
            f"- Source labware entries: `{source.get('labware_count', 0)}`",
            f"- Source liquid classes: `{source.get('liquid_class_count', 0)}`",
            f"- Source carriers: `{source.get('carrier_count', 0)}`",
            f"- Source device aliases: `{source.get('device_alias_count', 0)}`",
            f"- Source worklists: `{source.get('worklist_count', 0)}`",
        ]
    )
    if source_geometry:
        lines.append(
            f"- Source workspace geometry: `{source_geometry.get('workspace_count', 0)}` workspaces, "
            f"`{source_geometry.get('connector_count', 0)}` connectors, `{source_geometry.get('pin_count', 0)}` pins"
        )
        if source_geometry.get("pin_names"):
            lines.append(f"- Source worktable pins: `{', '.join(source_geometry['pin_names'][:12])}`")
    lines.extend(["", "## Protocol Requirements", ""])
    protocol = diff.get("protocol") or {}
    req_worktable = protocol.get("worktable") or {}
    if req_worktable:
        _append_item_details(
            lines,
            req_worktable.get("name") or protocol.get("name") or "protocol worktable",
            {
                "GUID": req_worktable.get("guid"),
                "Auto-place labware": req_worktable.get("auto_place"),
            },
        )
    else:
        lines.append("- No protocol worktable was specified.")
    lines.extend(
        [
            f"- Required labware entries: `{protocol.get('labware_count', 0)}`",
            f"- Required liquid classes: `{protocol.get('liquid_class_count', 0)}`",
            f"- Required carriers: `{protocol.get('carrier_count', 0)}`",
            f"- Required device aliases: `{protocol.get('device_alias_count', 0)}`",
            f"- Required worklists: `{protocol.get('worklist_count', 0)}`",
            "",
        ]
    )

    _append_labware_section(lines, "Missing Labware", diff.get("missing_labware") or [])
    _append_position_section(lines, diff.get("changed_deck_positions") or [])
    _append_status_section(lines, "Required Liquid Classes", diff.get("required_liquid_classes") or [])
    _append_labware_section(lines, "Required Tip Boxes", diff.get("required_tip_boxes") or [])
    _append_status_section(lines, "Required Carriers", diff.get("required_carriers") or [])
    _append_status_section(lines, "Device Aliases", diff.get("device_aliases") or [])
    _append_status_section(lines, "Worklist Paths", diff.get("worklist_paths") or [])

    lines.extend(["## Automatic FluentControl Setup Steps", ""])
    for index, step in enumerate(diff.get("automatic_setup_steps") or [], start=1):
        lines.append(f"{index}. {step}")
    lines.append("")

    lines.extend(["## Manual FluentControl Setup Steps", ""])
    for index, step in enumerate(diff.get("manual_setup_steps") or [], start=1):
        lines.append(f"{index}. {step}")
    if not diff.get("manual_setup_steps"):
        lines.append("1. No manual worktable setup changes were detected from the available metadata.")
    lines.append("")

    if diff.get("warnings"):
        lines.extend(["## Safety Notes", ""])
        for warning in diff["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def worktable_patch_from_diff(diff: dict[str, Any]) -> dict[str, Any]:
    """Return a machine-readable worktable patch derived from a worktable diff."""
    operations: list[dict[str, Any]] = []
    _append_worktable_patch_operation(operations, diff)

    for index, item in enumerate(diff.get("missing_labware") or [], start=1):
        operations.append(
            _patch_operation(
                "labware",
                "add_labware",
                _severity_from_status(item.get("status")),
                index=index,
                status=item.get("status"),
                target=_labware_target(item),
                reason=item.get("reason") or "required labware was not found in the source context",
                diff_path=f"/missing_labware/{index - 1}",
            )
        )

    for index, item in enumerate(diff.get("changed_deck_positions") or [], start=1):
        operations.append(
            _patch_operation(
                "labware",
                "move_labware",
                "needs_review",
                index=index,
                status=item.get("status") or "changed",
                source={
                    "label": item.get("label"),
                    "catalog": item.get("catalog"),
                    "deck_location": item.get("source_deck_location"),
                    "geometry": item.get("source_geometry"),
                },
                target={
                    "label": item.get("label"),
                    "catalog": item.get("catalog"),
                    "deck_location": item.get("required_deck_location"),
                },
                reason="required deck position differs from the source ZEIA context",
                diff_path=f"/changed_deck_positions/{index - 1}",
            )
        )

    _append_status_patch_operations(
        operations,
        "liquid_class",
        "require_liquid_class",
        diff.get("required_liquid_classes") or [],
        "/required_liquid_classes",
    )
    _append_labware_patch_operations(
        operations,
        "tip_box",
        "require_tip_box",
        diff.get("required_tip_boxes") or [],
        "/required_tip_boxes",
    )
    _append_status_patch_operations(
        operations,
        "carrier",
        "require_carrier",
        diff.get("required_carriers") or [],
        "/required_carriers",
    )
    _append_status_patch_operations(
        operations,
        "device_alias",
        "require_device_alias",
        diff.get("device_aliases") or [],
        "/device_aliases",
    )
    _append_status_patch_operations(
        operations,
        "worklist",
        "require_worklist",
        diff.get("worklist_paths") or [],
        "/worklist_paths",
    )

    warnings = [
        {
            "id": f"warning.{index}",
            "severity": "needs_review",
            "message": warning,
            "diff_path": f"/warnings/{index - 1}",
        }
        for index, warning in enumerate(diff.get("warnings") or [], start=1)
    ]
    manual_steps = [
        {
            "index": index,
            "severity": _manual_step_severity(step),
            "text": step,
        }
        for index, step in enumerate(diff.get("manual_setup_steps") or [], start=1)
    ]
    summary = _patch_summary(operations, warnings=warnings, manual_steps=manual_steps)

    return {
        "kind": "worktable_patch",
        "schema_version": WORKTABLE_PATCH_SCHEMA_VERSION,
        "source": diff.get("source") or {},
        "protocol": diff.get("protocol") or {},
        "summary": summary,
        "operations": operations,
        "warnings": warnings,
        "manual_setup_steps": manual_steps,
    }


def render_worktable_patch_json(diff: dict[str, Any]) -> str:
    return json.dumps(worktable_patch_from_diff(diff), indent=2, sort_keys=True) + "\n"


def _source_context(
    *,
    source_manifest: dict[str, Any] | None,
    source_ir: dict[str, Any] | None,
    source_irs: list[dict[str, Any]],
    alias_maps: dict[str, dict[str, str]],
    requested_worktable: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = _empty_source_context()
    if source_manifest:
        _merge_manifest_source(context, source_manifest, alias_maps, requested_worktable=requested_worktable or {})
    if source_ir:
        _merge_ir_source(context, source_ir, alias_maps)
    for item in source_irs:
        _merge_ir_source(context, item, alias_maps)
    return context


def _empty_source_context() -> dict[str, Any]:
    return {
        "name": "",
        "worktable": {},
        "labware_by_label": {},
        "labware_catalogs": set(),
        "liquid_classes": set(),
        "carriers": set(),
        "device_aliases": set(),
        "worklist_paths": set(),
        "worktable_geometry": {},
    }


def _merge_manifest_source(
    context: dict[str, Any],
    manifest: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
    *,
    requested_worktable: dict[str, Any],
) -> None:
    context["name"] = context["name"] or manifest.get("name") or ""
    workspaces = manifest.get("workspaces") or []
    if workspaces and not context["worktable"]:
        workspace = _select_manifest_workspace(workspaces, requested_worktable)
        if workspace is None:
            workspace = _select_workspace_from_script_refs(manifest, workspaces)
        if workspace is None and len(workspaces) == 1:
            workspace = workspaces[0]
        if workspace is not None:
            context["worktable"] = {
                "name": workspace.get("object_name") or _first(workspace.get("names")),
                "guid": _workspace_guid(workspace),
                "path": workspace.get("extracted_path") or workspace.get("entry"),
            }

    _merge_worktable_geometry_source(context, manifest, alias_maps, requested_worktable=requested_worktable)

    for label in manifest.get("labware_names") or []:
        _add_source_labware(context, {"label": label}, alias_maps)
    for catalog in manifest.get("rack_types") or []:
        _add_available(context["carriers"], catalog, "catalog", alias_maps)
    for name in manifest.get("liquid_classes") or []:
        _add_available(context["liquid_classes"], name, "liquid_class", alias_maps)

    for script in manifest.get("scripts") or []:
        deps = script.get("dependencies") or {}
        for label in deps.get("labware_names") or []:
            _add_source_labware(context, {"label": label}, alias_maps)
        for label in deps.get("rack_labels") or []:
            _add_source_labware(context, {"label": label}, alias_maps)
        for carrier in deps.get("rack_types") or []:
            _add_available(context["carriers"], carrier, "catalog", alias_maps)
        for name in deps.get("liquid_classes") or []:
            _add_available(context["liquid_classes"], name, "liquid_class", alias_maps)
        for alias in deps.get("device_aliases") or []:
            _add_available(context["device_aliases"], alias, "device_alias", alias_maps)
        for path in deps.get("external_or_worklist_refs") or []:
            if _looks_like_worklist(path):
                context["worklist_paths"].add(_norm_path(path))

    for path in manifest.get("worklist_paths") or []:
        context["worklist_paths"].add(_norm_path(path))


def _merge_worktable_geometry_source(
    context: dict[str, Any],
    manifest: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
    *,
    requested_worktable: dict[str, Any],
) -> None:
    geometry = _manifest_geometry(manifest)
    if not geometry:
        return
    context["worktable_geometry"] = geometry
    for workspace in _source_geometry_workspaces(context, manifest, geometry, requested_worktable=requested_worktable):
        if not context["worktable"]:
            context["worktable"] = {
                "name": workspace.get("name") or "",
                "guid": workspace.get("guid") or "",
                "path": workspace.get("extracted_path") or workspace.get("path") or "",
            }
        for record in workspace_labware_records(workspace):
            _add_source_labware(context, record, alias_maps)
            if record.get("catalog"):
                _add_available(context["carriers"], record["catalog"], "catalog", alias_maps)


def _manifest_geometry(manifest: dict[str, Any]) -> dict[str, Any]:
    geometry = manifest.get("worktable_geometry")
    if isinstance(geometry, dict) and geometry.get("schema_version"):
        return geometry
    try:
        geometry = build_worktable_geometry(manifest)
    except Exception:
        return {}
    return geometry if isinstance(geometry, dict) else {}


def _source_geometry_workspaces(
    context: dict[str, Any],
    manifest: dict[str, Any],
    geometry: dict[str, Any],
    *,
    requested_worktable: dict[str, Any],
) -> list[dict[str, Any]]:
    workspaces = [item for item in geometry.get("workspaces") or [] if isinstance(item, dict)]
    if not workspaces:
        return []
    by_guid = {_norm(item.get("guid")): item for item in workspaces if item.get("guid")}
    by_name = {_norm(item.get("name")): item for item in workspaces if item.get("name")}

    for table in (requested_worktable or {}, context.get("worktable") or {}):
        for key in (_norm(table.get("guid")), _norm(table.get("name"))):
            if key and key in by_guid:
                return [by_guid[key]]
            if key and key in by_name:
                return [by_name[key]]

    current = context.get("worktable") or {}
    for key in (_norm(current.get("guid")), _norm(current.get("name"))):
        if key and key in by_guid:
            return [by_guid[key]]
        if key and key in by_name:
            return [by_name[key]]

    for guid in _workspace_refs_from_manifest(manifest):
        key = _norm(guid)
        if key in by_guid:
            return [by_guid[key]]
    if len(workspaces) == 1:
        return workspaces
    # Multiple workspaces with no script/recipe pin — refuse order-based pick.
    return []


def _workspace_refs_from_manifest(manifest: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for script in manifest.get("scripts") or []:
        deps = script.get("dependencies") or {}
        for value in deps.get("workspace_guids") or []:
            text = str(value or "").strip()
            if text and text not in refs:
                refs.append(text)
    return refs


def _select_manifest_workspace(
    workspaces: list[dict[str, Any]],
    requested_worktable: dict[str, Any],
) -> dict[str, Any] | None:
    if not requested_worktable:
        return None
    requested_keys = {
        _norm(requested_worktable.get("guid")),
        _norm(requested_worktable.get("name")),
    }
    requested_keys.discard("")
    if not requested_keys:
        return None
    for workspace in workspaces:
        candidates = {
            _norm(_workspace_guid(workspace)),
            _norm(workspace.get("object_name")),
            *(_norm(name) for name in workspace.get("names") or []),
        }
        if requested_keys & candidates:
            return workspace
    return None


def _select_workspace_from_script_refs(
    manifest: dict[str, Any],
    workspaces: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Prefer Script→WorktableWorkspace GUIDs over archive workspaces[0] order."""
    refs = _workspace_refs_from_manifest(manifest)
    if not refs:
        return None
    by_guid = {_norm(_workspace_guid(workspace)): workspace for workspace in workspaces}
    matched = [by_guid[key] for key in (_norm(guid) for guid in refs) if key in by_guid]
    if not matched:
        return None
    # Consensus: first unique GUID that scripts reference (refs already de-duped).
    if len({_norm(_workspace_guid(item)) for item in matched}) == 1:
        return matched[0]
    return matched[0]


def _workspace_guid(workspace: dict[str, Any]) -> str:
    if workspace.get("workspace_guid"):
        return str(workspace["workspace_guid"])
    workspace_path_stem = ""
    for key in ("extracted_path", "entry", "path"):
        value = str(workspace.get(key) or "").replace("\\", "/")
        if value.lower().endswith(".xwsp"):
            workspace_path_stem = Path(value).stem
            if _looks_like_guid(workspace_path_stem):
                return workspace_path_stem
    return _first(workspace.get("guids")) or workspace_path_stem


def _looks_like_guid(value: Any) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            str(value or ""),
        )
    )


def _geometry_summary(geometry: dict[str, Any]) -> dict[str, Any]:
    if not geometry:
        return {}
    pin_names = [
        str(site.get("pin_name"))
        for site in geometry.get("pin_sites") or []
        if isinstance(site, dict) and site.get("pin_name")
    ]
    return _clean_json(
        {
            "schema_version": geometry.get("schema_version"),
            "workspace_count": geometry.get("workspace_count"),
            "component_count": geometry.get("component_count"),
            "site_count": geometry.get("site_count"),
            "connector_count": geometry.get("connector_count"),
            "pin_count": len(pin_names),
            "pin_names": pin_names[:20],
        }
    )


def _merge_ir_source(
    context: dict[str, Any],
    ir: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
) -> None:
    worktable = ir.get("worktable") or {}
    if worktable and not context["worktable"]:
        context["worktable"] = {
            "name": worktable.get("name") or "",
            "guid": worktable.get("guid") or "",
            "path": (ir.get("source") or {}).get("path") or "",
        }
    for item in ir.get("labware") or []:
        _add_source_labware(context, item, alias_maps)
    for item in ir.get("liquid_classes") or []:
        if item.get("name"):
            _add_available(context["liquid_classes"], item["name"], "liquid_class", alias_maps)
    for item in ir.get("worklists") or []:
        path = item.get("source") or item.get("path") or item.get("name")
        if path:
            context["worklist_paths"].add(_norm_path(path))
    for dep in ir.get("dependencies") or []:
        kind = str(dep.get("kind") or "").lower()
        name = dep.get("name")
        if not name:
            continue
        if kind == "liquid_class":
            _add_available(context["liquid_classes"], name, "liquid_class", alias_maps)
        elif kind in {"carrier", "rack_type"}:
            _add_available(context["carriers"], name, "catalog", alias_maps)
        elif kind in {"device_alias", "device"}:
            _add_available(context["device_aliases"], name, "device_alias", alias_maps)
        elif kind == "worklist":
            context["worklist_paths"].add(_norm_path(name))
    for step in ir.get("steps") or []:
        params = step.get("parameters") or {}
        for key in ("device_alias", "DeviceAlias"):
            if params.get(key):
                _add_available(context["device_aliases"], params[key], "device_alias", alias_maps)
        for key in ("rack_type", "RackType", "forced_rack_type", "carrier", "carrier_type"):
            if params.get(key):
                _add_available(context["carriers"], params[key], "catalog", alias_maps)
        for key in ("WorklistName", "FileName", "Path", "worklist", "worklist_path"):
            if params.get(key):
                context["worklist_paths"].add(_norm_path(params[key]))


def _add_source_labware(
    context: dict[str, Any],
    item: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
) -> None:
    label = item.get("label") or item.get("name") or item.get("target_labware")
    catalog = item.get("catalog") or item.get("labware_type") or item.get("rack_type")
    record = {
        "label": label or "",
        "catalog": catalog or "",
        "deck_location": _deck_location(item),
        "role": item.get("role") or "",
        "workspace": item.get("workspace") or "",
        "workspace_guid": item.get("workspace_guid") or "",
        "geometry": item.get("geometry") or {},
    }
    if label:
        for candidate in alias_candidates(label, "labware", alias_maps):
            key = _norm(candidate)
            existing = context["labware_by_label"].get(key)
            context["labware_by_label"][key] = _merge_source_labware_record(existing, record)
    if catalog:
        _add_available(context["labware_catalogs"], catalog, "catalog", alias_maps)


def _merge_source_labware_record(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    if existing is None:
        return incoming
    existing_geometry = bool(existing.get("geometry"))
    incoming_geometry = bool(incoming.get("geometry"))
    if incoming_geometry and not existing_geometry:
        return _fill_missing_source_fields(incoming, existing)
    if existing_geometry and not incoming_geometry:
        return _fill_missing_source_fields(existing, incoming)
    if incoming.get("deck_location") and not existing.get("deck_location"):
        return _fill_missing_source_fields(incoming, existing)
    return _fill_missing_source_fields(existing, incoming)


def _fill_missing_source_fields(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    for key, value in secondary.items():
        if not _has_value(merged.get(key)) and _has_value(value):
            merged[key] = value
    return merged


def _add_available(
    values: set[str],
    value: Any,
    alias_kind: str,
    alias_maps: dict[str, dict[str, str]],
) -> None:
    for candidate in alias_candidates(value, alias_kind, alias_maps):
        values.add(_norm(candidate))


def _requirements_from_ir(ir: dict[str, Any], *, alias_maps: dict[str, dict[str, str]]) -> dict[str, Any]:
    requirements = {
        "worktable": ir.get("worktable") or {},
        "labware": [],
        "liquid_classes": {},
        "tip_boxes": [],
        "carriers": {},
        "device_aliases": {},
        "worklist_paths": {},
    }

    by_label: dict[str, dict[str, Any]] = {}
    for item in ir.get("labware") or []:
        record = {
            "label": item.get("label") or "",
            "catalog": item.get("catalog") or item.get("labware_type") or "",
            "python_class": item.get("python_class") or "",
            "deck_location": _deck_location(item),
            "role": item.get("role") or "",
            "source_path": item.get("source_path") or "",
        }
        if record["label"]:
            _add_required_labware_label(by_label, record["label"], record, alias_maps)
            requirements["labware"].append(record)
        _collect_carrier_from_item(requirements["carriers"], item, alias_maps)

    for step in ir.get("steps") or []:
        target = step.get("target_labware") or step.get("source_labware") or step.get("destination_labware")
        if target and not _required_labware_label_exists(by_label, target, alias_maps):
            record = {
                "label": target,
                "catalog": "",
                "python_class": "",
                "deck_location": "",
                "role": _role_from_operation(step.get("operation")),
                "source_path": step.get("source_path") or step.get("compiled_path") or "",
            }
            _add_required_labware_label(by_label, target, record, alias_maps)
            requirements["labware"].append(record)
        if step.get("liquid_class"):
            _add_required(requirements["liquid_classes"], step["liquid_class"], "liquid_class", alias_maps)
        params = step.get("parameters") or {}
        for key in ("device_alias", "DeviceAlias"):
            if params.get(key):
                _add_required(requirements["device_aliases"], params[key], "device_alias", alias_maps)
        for key in ("rack_type", "RackType", "forced_rack_type", "carrier", "carrier_type"):
            if params.get(key):
                _add_required(requirements["carriers"], params[key], "catalog", alias_maps)
        for key in ("WorklistName", "FileName", "Path", "worklist", "worklist_path"):
            if params.get(key):
                _add_required_path(requirements["worklist_paths"], params[key])

    for item in ir.get("liquid_classes") or []:
        if item.get("name"):
            _add_required(requirements["liquid_classes"], item["name"], "liquid_class", alias_maps)
    for item in ir.get("worklists") or []:
        path = item.get("source") or item.get("path") or item.get("name")
        if path:
            _add_required_path(requirements["worklist_paths"], path)
    for dep in ir.get("dependencies") or []:
        kind = str(dep.get("kind") or "").lower()
        name = dep.get("name")
        if not name:
            continue
        if kind == "liquid_class":
            _add_required(requirements["liquid_classes"], name, "liquid_class", alias_maps)
        elif kind in {"carrier", "rack_type"}:
            _add_required(requirements["carriers"], name, "catalog", alias_maps)
        elif kind in {"device_alias", "device"}:
            _add_required(requirements["device_aliases"], name, "device_alias", alias_maps)
        elif kind == "worklist":
            _add_required_path(requirements["worklist_paths"], name)

    for item in requirements["labware"]:
        if _is_tip_box(item):
            requirements["tip_boxes"].append(item)
    for step in ir.get("steps") or []:
        if str(step.get("operation") or "") in {"pick_up_tips", "mca384_get_tips", "liha_get_tips"}:
            target = step.get("target_labware") or ""
            if target:
                item = _required_labware_record(by_label, target, alias_maps) or {"label": target, "catalog": "", "deck_location": ""}
                if not any(
                    _norm(existing.get("label")) in {_norm(candidate) for candidate in alias_candidates(target, "labware", alias_maps)}
                    for existing in requirements["tip_boxes"]
                ):
                    requirements["tip_boxes"].append(item)

    return requirements


def _missing_labware(
    source: dict[str, Any],
    requirements: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    source_has_inventory = bool(source["labware_by_label"] or source["labware_catalogs"])
    out = []
    for item in requirements["labware"]:
        label_match = _source_labware_record(source, item.get("label"), alias_maps) is not None
        catalog_match = bool(
            not item.get("label")
            and any(_norm(candidate) in source["labware_catalogs"] for candidate in alias_candidates(item.get("catalog"), "catalog", alias_maps))
        )
        if label_match or catalog_match:
            continue
        resolved_label = resolve_alias(item.get("label"), "labware", alias_maps)
        resolved_catalog = resolve_alias(item.get("catalog"), "catalog", alias_maps)
        out.append(
            _clean_json(
                {
                    **item,
                    "resolved_label": resolved_label if resolved_label != item.get("label") else "",
                    "resolved_catalog": resolved_catalog if resolved_catalog != item.get("catalog") else "",
                    "status": "missing" if source_has_inventory else "unverified",
                    "reason": "not found in source ZEIA context" if source_has_inventory else "no source labware inventory available",
                }
            )
        )
    return out


def _changed_deck_positions(
    source: dict[str, Any],
    requirements: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    out = []
    for item in requirements["labware"]:
        source_item = _source_labware_record(source, item.get("label"), alias_maps)
        if source_item is None:
            continue
        resolved_label = resolve_alias(item.get("label"), "labware", alias_maps)
        source_location = source_item.get("deck_location") or ""
        required_location = item.get("deck_location") or ""
        if source_location and required_location and _norm(source_location) != _norm(required_location):
            out.append(
                _clean_json(
                    {
                        "label": item.get("label"),
                        "resolved_label": resolved_label if resolved_label != item.get("label") else "",
                        "catalog": item.get("catalog") or source_item.get("catalog") or "",
                        "source_deck_location": source_location,
                        "required_deck_location": required_location,
                        "source_geometry": source_item.get("geometry") or {},
                        "status": "changed",
                    }
                )
            )
    return out


def _required_tip_boxes(
    source: dict[str, Any],
    requirements: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    source_has_inventory = bool(source["labware_by_label"] or source["labware_catalogs"])
    out = []
    for item in requirements["tip_boxes"]:
        matched = _source_has_labware(source, item, alias_maps)
        resolved_label = resolve_alias(item.get("label"), "labware", alias_maps)
        resolved_catalog = resolve_alias(item.get("catalog"), "catalog", alias_maps)
        if matched:
            status = "available"
        elif source_has_inventory:
            status = "missing"
        else:
            status = "unverified"
        out.append(
            _clean_json(
                {
                    **item,
                    "resolved_label": resolved_label if resolved_label != item.get("label") else "",
                    "resolved_catalog": resolved_catalog if resolved_catalog != item.get("catalog") else "",
                    "status": status,
                }
            )
        )
    return out


def _status_records(
    required: dict[str, str],
    available: set[str],
    *,
    alias_kind: str | None = None,
    alias_maps: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    out = []
    for normalized, name in sorted(required.items(), key=lambda item: item[1].casefold()):
        if not name:
            continue
        candidates = alias_candidates(name, alias_kind, alias_maps) if alias_kind and alias_maps is not None else [name]
        if not available:
            status = "unverified"
        elif normalized in available or any(_norm(candidate) in available for candidate in candidates):
            status = "available"
        else:
            status = "missing"
        resolved = resolve_alias(name, alias_kind, alias_maps) if alias_kind and alias_maps is not None else name
        record = {"name": name, "status": status}
        if resolved != name:
            record["resolved_name"] = resolved
        out.append(record)
    return out


def _automatic_setup_steps(diff: dict[str, Any]) -> list[str]:
    return [
        "Open FluentControl.",
        "Import **`generated_project.zeia`** when the bundle ships a generated project archive.",
    ]


def _manual_setup_steps(diff: dict[str, Any]) -> list[str]:
    protocol = diff.get("protocol") or {}
    worktable = protocol.get("worktable") or {}
    steps = [f"Load worktable: `{worktable.get('name') or 'selected source worktable'}`."]

    for item in diff.get("missing_labware") or []:
        text = f"Add labware `{item.get('label') or 'required labware'}`"
        if item.get("catalog"):
            text += f" using type `{item['catalog']}`"
        if item.get("deck_location"):
            text += f" at carrier position `{item['deck_location']}`"
        steps.append(text + ".")
    for item in diff.get("changed_deck_positions") or []:
        steps.append(
            f"Move `{item.get('label')}` from `{item.get('source_deck_location')}` "
            f"to `{item.get('required_deck_location')}`."
        )
    for record in diff.get("required_liquid_classes") or []:
        if record.get("status") != "available":
            steps.append(f"Confirm liquid class `{record.get('name')}` exists and is selected.")
    for item in diff.get("required_tip_boxes") or []:
        if item.get("status") != "available":
            location = f" at `{item.get('deck_location')}`" if item.get("deck_location") else ""
            steps.append(f"Load required tip box `{item.get('label')}`{location}.")
    for record in diff.get("required_carriers") or []:
        if record.get("status") != "available":
            steps.append(f"Confirm required carrier `{record.get('name')}` is available on the deck.")
    for record in diff.get("device_aliases") or []:
        if record.get("status") != "available":
            steps.append(f"Confirm device alias `{record.get('name')}` resolves in FluentControl.")
    for record in diff.get("worklist_paths") or []:
        if record.get("status") != "available":
            steps.append(f"Confirm worklist path `{record.get('name')}` exists and is reachable.")
    steps.append("Run the optional FluentControl import/load diagnostic or manually open the generated script in Script Editor.")
    steps.append("Simulate before real instrument use.")
    return steps


def _diff_warnings(source: dict[str, Any], requirements: dict[str, Any]) -> list[str]:
    warnings = [
        "The worklist generator does not verify deck layout, labware availability, liquid classes, instrument state, or tip strategy.",
    ]
    if not source["labware_by_label"] and requirements["labware"]:
        warnings.append("Source ZEIA context did not expose exact labware placements; labware differences may require manual inspection.")
    if not source["device_aliases"] and requirements["device_aliases"]:
        warnings.append("Source ZEIA context did not expose device aliases; device aliases are marked unverified.")
    return warnings


def _append_labware_section(lines: list[str], title: str, items: list[dict[str, Any]]) -> None:
    lines.extend([f"## {title}", ""])
    if not items:
        lines.append("- None detected from available metadata.")
        lines.append("")
        return
    for item in items:
        _append_item_details(
            lines,
            item.get("label") or item.get("name") or "required labware",
            {
                "Status": item.get("status"),
                "Resolved label": item.get("resolved_label"),
                "Catalog / FluentControl type": item.get("catalog"),
                "Resolved catalog": item.get("resolved_catalog"),
                "Python class": item.get("python_class"),
                "Deck location": item.get("deck_location"),
                "Role": item.get("role"),
                "Workspace": item.get("workspace"),
                "Workspace GUID": item.get("workspace_guid"),
                **_geometry_detail_fields(item.get("geometry") or {}),
                "Reason": item.get("reason"),
                "Source path": item.get("source_path"),
            },
        )
    lines.append("")


def _append_position_section(lines: list[str], items: list[dict[str, Any]]) -> None:
    lines.extend(["## Changed Deck Positions", ""])
    if not items:
        lines.append("- None detected from available metadata.")
        lines.append("")
        return
    for item in items:
        _append_item_details(
            lines,
            item.get("label") or "labware",
            {
                "Status": item.get("status"),
                "Catalog / FluentControl type": item.get("catalog"),
                "Source deck position": item.get("source_deck_location"),
                "Required deck position": item.get("required_deck_location"),
                **_geometry_detail_fields(item.get("source_geometry") or {}, prefix="Source "),
            },
        )
    lines.append("")


def _append_status_section(lines: list[str], title: str, items: list[dict[str, str]]) -> None:
    lines.extend([f"## {title}", ""])
    if not items:
        lines.append("- None required by protocol IR.")
        lines.append("")
        return
    for item in items:
        lines.append(f"- `{item.get('name')}`")
        lines.append(f"  - Status: `{item.get('status')}`")
        if item.get("resolved_name"):
            lines.append(f"  - Resolved name: `{item.get('resolved_name')}`")
    lines.append("")


def _append_worktable_patch_operation(operations: list[dict[str, Any]], diff: dict[str, Any]) -> None:
    source = diff.get("source") or {}
    protocol = diff.get("protocol") or {}
    source_worktable = source.get("worktable") or {}
    protocol_worktable = protocol.get("worktable") or {}
    if not protocol_worktable:
        operations.append(
            _patch_operation(
                "worktable",
                "verify_worktable",
                "needs_review",
                index=1,
                status="unverified",
                source=source_worktable,
                target={},
                reason="protocol IR does not specify a worktable",
                diff_path="/protocol/worktable",
            )
        )
        return

    if not source_worktable:
        severity = "needs_review"
        status = "unverified"
        action = "verify_worktable"
        reason = "source ZEIA context did not expose worktable metadata"
    elif _same_worktable(source_worktable, protocol_worktable):
        severity = "safe"
        status = "available"
        action = "use_worktable"
        reason = "protocol worktable matches the source ZEIA context"
    else:
        severity = "needs_review"
        status = "changed"
        action = "change_worktable"
        reason = "protocol worktable differs from the source ZEIA context"

    operations.append(
        _patch_operation(
            "worktable",
            action,
            severity,
            index=1,
            status=status,
            source=source_worktable,
            target=protocol_worktable,
            reason=reason,
            diff_path="/protocol/worktable",
        )
    )


def _append_status_patch_operations(
    operations: list[dict[str, Any]],
    category: str,
    action: str,
    items: list[dict[str, str]],
    base_path: str,
) -> None:
    for index, item in enumerate(items, start=1):
        operations.append(
            _patch_operation(
                category,
                action,
                _severity_from_status(item.get("status")),
                index=index,
                status=item.get("status"),
                target={"name": item.get("name"), "resolved_name": item.get("resolved_name")},
                reason=_status_reason(category, item.get("status")),
                diff_path=f"{base_path}/{index - 1}",
            )
        )


def _append_labware_patch_operations(
    operations: list[dict[str, Any]],
    category: str,
    action: str,
    items: list[dict[str, Any]],
    base_path: str,
) -> None:
    for index, item in enumerate(items, start=1):
        operations.append(
            _patch_operation(
                category,
                action,
                _severity_from_status(item.get("status")),
                index=index,
                status=item.get("status"),
                target=_labware_target(item),
                reason=_status_reason(category, item.get("status")),
                diff_path=f"{base_path}/{index - 1}",
            )
        )


def _patch_operation(
    category: str,
    action: str,
    severity: str,
    *,
    index: int,
    status: Any = "",
    source: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    reason: Any = "",
    diff_path: str = "",
) -> dict[str, Any]:
    target = target or {}
    source = source or {}
    label = target.get("label") or target.get("name") or source.get("label") or source.get("name") or str(index)
    return _clean_json(
        {
            "id": f"{category}.{action}.{_slug(label)}",
            "category": category,
            "action": action,
            "severity": severity if severity in PATCH_SEVERITIES else "needs_review",
            "status": status,
            "source": source,
            "target": target,
            "reason": reason,
            "diff_path": diff_path,
        }
    )


def _labware_target(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": item.get("label") or item.get("name"),
        "resolved_label": item.get("resolved_label"),
        "catalog": item.get("catalog"),
        "resolved_catalog": item.get("resolved_catalog"),
        "python_class": item.get("python_class"),
        "deck_location": item.get("deck_location"),
        "role": item.get("role"),
        "source_path": item.get("source_path"),
        "geometry": item.get("geometry"),
    }


def _severity_from_status(status: Any) -> str:
    normalized = _norm(status)
    if normalized == "available":
        return "safe"
    if normalized == "missing":
        return "blocking"
    return "needs_review"


def _status_reason(category: str, status: Any) -> str:
    normalized = _norm(status)
    label = category.replace("_", " ")
    if normalized == "available":
        return f"required {label} is present in the source context"
    if normalized == "missing":
        return f"required {label} is missing from the source context"
    return f"required {label} could not be verified from the source context"


def _manual_step_severity(step: Any) -> str:
    text = str(step or "").casefold()
    if any(
        token in text
        for token in (
            "add labware",
            "load required tip box",
            "confirm liquid class",
            "confirm required carrier",
            "confirm device alias",
            "confirm worklist path",
        )
    ):
        return "blocking"
    if any(token in text for token in ("move `", "run fluentcontrol context check", "simulate before")):
        return "needs_review"
    return "safe"


def _patch_summary(
    operations: list[dict[str, Any]],
    *,
    warnings: list[dict[str, Any]],
    manual_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    severity_counts = {severity: 0 for severity in PATCH_SEVERITIES}
    for operation in operations:
        severity = operation.get("severity")
        if severity in severity_counts:
            severity_counts[severity] += 1

    warning_needs_review = any(warning.get("severity") == "needs_review" for warning in warnings)
    if severity_counts["blocking"]:
        overall = "blocking"
    elif severity_counts["needs_review"] or warning_needs_review:
        overall = "needs_review"
    else:
        overall = "safe"

    return {
        "operation_count": len(operations),
        "severity_counts": severity_counts,
        "overall_severity": overall,
        "has_blocking": bool(severity_counts["blocking"]),
        "has_needs_review": bool(severity_counts["needs_review"] or warning_needs_review),
        "warning_count": len(warnings),
        "manual_step_count": len(manual_steps),
    }


def _same_worktable(source: dict[str, Any], protocol: dict[str, Any]) -> bool:
    source_name = _norm(source.get("name"))
    protocol_name = _norm(protocol.get("name"))
    source_guid = _norm(source.get("guid"))
    protocol_guid = _norm(protocol.get("guid"))
    if source_guid and protocol_guid:
        return source_guid == protocol_guid
    if source_name and protocol_name:
        return source_name == protocol_name
    return False


def _slug(value: Any) -> str:
    chars = []
    previous_dash = False
    for char in str(value or "").casefold():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-") or "item"


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            nested = _clean_json(item)
            if _has_value(nested) and nested not in ({}, []):
                cleaned[key] = nested
        return cleaned
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    return value


def _append_item_details(lines: list[str], name: str, fields: dict[str, Any]) -> None:
    lines.append(f"- `{name}`")
    for label, value in fields.items():
        if _has_value(value):
            lines.append(f"  - {label}: `{value}`")


def _geometry_detail_fields(geometry: dict[str, Any], *, prefix: str = "") -> dict[str, Any]:
    if not geometry:
        return {}
    return {
        f"{prefix}Workspace pin/site": geometry.get("pin_name") or geometry.get("connector_site_name") or geometry.get("base_site_name"),
        f"{prefix}Connector": geometry.get("connector_guid"),
        f"{prefix}Connector offset": _format_geometry_vec(geometry.get("connector_position_in_parent_mm")),
        f"{prefix}Connector rotation": _format_geometry_rotation(
            geometry.get("connector_orientation_euler_deg"),
            geometry.get("connector_orientation_matrix"),
        ),
        f"{prefix}Site path": ".".join(str(index + 1) for index in geometry.get("site_path") or [] if isinstance(index, int)),
    }


def _format_geometry_vec(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if not {"x", "y", "z"}.issubset(value):
        return ""

    def fmt(raw: Any) -> str:
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return str(raw)
        if number.is_integer():
            return str(int(number))
        return f"{number:g}"

    return f"({fmt(value.get('x'))}, {fmt(value.get('y'))}, {fmt(value.get('z'))}) mm"


def _format_geometry_rotation(euler: Any, matrix: Any) -> str:
    if isinstance(euler, dict) and {"phi", "theta", "psi"}.issubset(euler):
        return f"Phi={euler.get('phi')}, Theta={euler.get('theta')}, Psi={euler.get('psi')}"
    if isinstance(matrix, list) and matrix:
        return json.dumps(matrix, separators=(",", ":"))
    return ""


def _source_has_labware(
    source: dict[str, Any],
    item: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
) -> bool:
    return bool(
        _source_labware_record(source, item.get("label"), alias_maps) is not None
        or (
            not item.get("label")
            and any(_norm(candidate) in source["labware_catalogs"] for candidate in alias_candidates(item.get("catalog"), "catalog", alias_maps))
        )
    )


def _source_labware_record(
    source: dict[str, Any],
    label: Any,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    required_numeric = _numeric_instance_pattern(label)
    for candidate in alias_candidates(label, "labware", alias_maps):
        record = source["labware_by_label"].get(_norm(candidate))
        if record is None:
            continue
        if required_numeric is not None:
            source_numeric = _numeric_instance_pattern(record.get("label"))
            # Tube[001] must not resolve to Tube[002] via bare-type alias/strip.
            if source_numeric is not None and source_numeric["inner"] != required_numeric["inner"]:
                continue
        return record
    return _source_dynamic_labware_record(source, label, alias_maps)


def _numeric_instance_pattern(label: Any) -> dict[str, str] | None:
    pattern = _bracket_label_pattern(label)
    if pattern is None:
        return None
    if not re.fullmatch(r"\d+", pattern["inner"]):
        return None
    return pattern


def _source_dynamic_labware_record(
    source: dict[str, Any],
    label: Any,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    pattern = _dynamic_label_pattern(label)
    if pattern is None:
        return None
    required_base = _norm(pattern["base"])
    for record in source["labware_by_label"].values():
        source_label = record.get("label")
        for candidate in alias_candidates(source_label, "labware", alias_maps):
            source_pattern = _bracket_label_pattern(candidate)
            if source_pattern is None:
                continue
            if _norm(source_pattern["base"]) == required_base:
                return record
    return None


def _required_labware_record(
    by_label: dict[str, dict[str, Any]],
    label: Any,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    for candidate in alias_candidates(label, "labware", alias_maps):
        record = by_label.get(_norm(candidate))
        if record is not None:
            return record
    return None


def _required_labware_label_exists(
    by_label: dict[str, dict[str, Any]],
    label: Any,
    alias_maps: dict[str, dict[str, str]],
) -> bool:
    return _required_labware_record(by_label, label, alias_maps) is not None


def _add_required_labware_label(
    by_label: dict[str, dict[str, Any]],
    label: Any,
    record: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
) -> None:
    for candidate in alias_candidates(label, "labware", alias_maps):
        by_label[_norm(candidate)] = record


def _collect_carrier_from_item(
    carriers: dict[str, str],
    item: dict[str, Any],
    alias_maps: dict[str, dict[str, str]],
) -> None:
    for key in ("carrier", "carrier_type", "rack_type", "forced_rack_type"):
        if item.get(key):
            _add_required(carriers, item[key], "catalog", alias_maps)


def _add_required(
    values: dict[str, str],
    value: Any,
    alias_kind: str,
    alias_maps: dict[str, dict[str, str]],
) -> None:
    text = str(value or "").strip()
    if text:
        resolved = resolve_alias(text, alias_kind, alias_maps)
        values.setdefault(_norm(resolved or text), text)


def _add_required_path(values: dict[str, str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        values.setdefault(_norm_path(text), text)


def _role_from_operation(operation: Any) -> str:
    op = str(operation or "")
    if "aspirate" in op:
        return "source"
    if "dispense" in op:
        return "destination"
    if "tip" in op:
        return "tips"
    return ""


def _is_tip_box(item: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("label", "catalog", "python_class", "role")
    ).casefold()
    return any(token in haystack for token in ("tip", "diti", "mca100box", "mca384", "mca96"))


def _dynamic_label_pattern(label: Any) -> dict[str, str] | None:
    pattern = _bracket_label_pattern(label)
    if pattern is None:
        return None
    inner = pattern["inner"].strip()
    if re.fullmatch(r"\d+", inner):
        return None
    if not re.search(r"[A-Za-z_]", inner):
        return None
    return pattern


def _bracket_label_pattern(label: Any) -> dict[str, str] | None:
    text = str(label or "").strip()
    match = re.fullmatch(r"(.+?)\[([^\]]+)\]", text)
    if not match:
        return None
    base = match.group(1).strip()
    inner = match.group(2).strip()
    if not base or not inner:
        return None
    return {"base": base, "inner": inner}


def _deck_location(item: dict[str, Any]) -> str:
    if item.get("deck_location"):
        return str(item["deck_location"])
    location = item.get("location")
    position = item.get("position")
    if _has_value(location) and _has_value(position):
        return f"{location} {position}"
    if _has_value(location):
        return str(location)
    if _has_value(position):
        return str(position)
    return ""


def _looks_like_worklist(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and (text.endswith(".gwl") or "worklist" in text))


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _norm_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).as_posix().casefold()


def _first(values: Any) -> str:
    if isinstance(values, list) and values:
        return str(values[0])
    return ""


def _has_value(value: Any) -> bool:
    return value is not None and value != ""
