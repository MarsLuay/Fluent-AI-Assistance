"""Geometry extraction for ZEIA-exported FluentControl worktable XML."""

from __future__ import annotations

import re
from . import xml_compat as ET
from pathlib import Path
from typing import Any, Mapping


GEOMETRY_SCHEMA_VERSION = "tecan.worktable_geometry.v1"
ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def build_worktable_geometry(
    manifest: dict[str, Any],
    *,
    max_xml_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any]:
    """Return parsed worktable geometry from a project or collection manifest."""
    objects = [item for item in manifest.get("objects") or [] if isinstance(item, dict)]
    components: dict[str, dict[str, Any]] = {}
    sites: dict[str, dict[str, Any]] = {}
    connectors: dict[str, dict[str, Any]] = {}
    workspaces: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for item in objects:
        path = _object_path(manifest, item)
        if path is None or not path.exists():
            continue
        kind = str(item.get("kind") or "").lower()
        try:
            if kind == "component" or path.suffix.lower() == ".xcmp":
                component = parse_component(path, max_xml_bytes=max_xml_bytes)
                component.update(_source_fields(item))
                components[component["guid"]] = component
            elif kind == "site" or path.suffix.lower() == ".xsit":
                site = parse_site(path, max_xml_bytes=max_xml_bytes)
                site.update(_source_fields(item))
                sites[site["guid"]] = site
            elif kind == "connector" or path.suffix.lower() == ".xcon":
                connector = parse_connector(path, max_xml_bytes=max_xml_bytes)
                connector.update(_source_fields(item))
                connectors[connector["guid"]] = connector
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})

    for item in objects:
        path = _object_path(manifest, item)
        if path is None or not path.exists():
            continue
        kind = str(item.get("kind") or "").lower()
        if kind != "workspace" and path.suffix.lower() != ".xwsp":
            continue
        try:
            workspace = parse_workspace(
                path,
                components=components,
                sites=sites,
                connectors=connectors,
                max_xml_bytes=max_xml_bytes,
            )
            workspace.update(_source_fields(item))
            workspaces.append(workspace)
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})

    _decorate_connectors(connectors, components=components, sites=sites)
    _decorate_compatible_components(components, workspaces=workspaces)

    pin_sites = [
        site
        for site in sites.values()
        if site.get("site_kind") in {"pin", "cap_nest", "nest"}
        or site.get("pin_name")
        or _looks_like_pin_or_nest_cap_site(
            site.get("pin_name") or site.get("location_group_name") or site.get("type_name")
        )
    ]
    nest_cap_sites = [site for site in pin_sites if site.get("site_kind") == "cap_nest"]

    return _clean(
        {
            "schema_version": GEOMETRY_SCHEMA_VERSION,
            "component_count": len(components),
            "site_count": len(sites),
            "connector_count": len(connectors),
            "workspace_count": len(workspaces),
            "components": sorted(components.values(), key=lambda item: (item.get("name") or "", item.get("guid") or "")),
            "sites": sorted(sites.values(), key=lambda item: (item.get("pin_name") or item.get("location_group_name") or "", item.get("guid") or "")),
            "connectors": sorted(connectors.values(), key=lambda item: (item.get("site_name") or "", item.get("component_name") or "", item.get("guid") or "")),
            "workspaces": sorted(workspaces, key=lambda item: (item.get("name") or "", item.get("guid") or "")),
            "pin_sites": sorted(pin_sites, key=lambda item: item.get("pin_name") or item.get("guid") or ""),
            "nest_cap_sites": sorted(
                nest_cap_sites, key=lambda item: item.get("pin_name") or item.get("guid") or ""
            ),
            "errors": errors,
        }
    )


def parse_connector(path: Path | str, *, max_xml_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    path = Path(path)
    root = _parse_xml(path, max_xml_bytes=max_xml_bytes)
    payload = _find(root, "Payload")
    template = _find(_find(payload, "PayloadData"), "ConnectorTemplate")
    if template is None:
        raise ValueError("no ConnectorTemplate payload")
    object_name = _child_text(payload, "ObjectName") or path.stem
    description = _child_text(template, "Description") or _first_text(root, "Description")
    return _clean(
        {
            "kind": "connector",
            "guid": _child_text(template, "GUID") or path.stem,
            "name": object_name,
            "object_name": object_name,
            "description": description,
            "component_guid": _child_text(template, "ComponentGuid"),
            "site_guid": _child_text(template, "SiteGuid"),
            "position_in_parent_mm": _vec_dict(_find(template, "PositionInParent")),
            "orientation_matrix": _matrix(_find(template, "Orientation")),
            "orientation_euler_deg": _orientation_from_text(description or object_name),
            "is_default": _bool_text(_child_text(template, "IsDefaultConnector")),
            "path": str(path),
        }
    )


def parse_site(path: Path | str, *, max_xml_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    path = Path(path)
    root = _parse_xml(path, max_xml_bytes=max_xml_bytes)
    payload = _find(root, "Payload")
    template = _find(_find(payload, "PayloadData"), "SiteTemplate")
    if template is None:
        raise ValueError("no SiteTemplate payload")
    guid = _child_text(template, "GUID") or path.stem
    location_group = _child_text(template, "LocationGroupName")
    type_name = _child_text(template, "TypeName")
    object_name = _child_text(payload, "ObjectName") or guid
    connector_guids = _references(payload, "WorktableConnector")
    site_kind = classify_site_kind(location_group, type_name, object_name)
    pin_name = _pin_name(location_group, type_name, object_name, site_kind=site_kind)
    return _clean(
        {
            "kind": "site",
            "guid": guid,
            "name": location_group or type_name or object_name,
            "object_name": object_name,
            "location_group_name": location_group,
            "type_name": type_name,
            "site_kind": site_kind,
            "pin_name": pin_name,
            "connector_guids": connector_guids,
            "dimension_mm": _vec_dict(_find(template, "Dimension")),
            "orientation_matrix": _matrix(_find(template, "Orientation")),
            "path": str(path),
        }
    )


def parse_component(path: Path | str, *, max_xml_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    path = Path(path)
    root = _parse_xml(path, max_xml_bytes=max_xml_bytes)
    payload = _find(root, "Payload")
    template = _find(_find(payload, "PayloadData"), "CarrierOrLabwareTemplate")
    if template is None:
        raise ValueError("no CarrierOrLabwareTemplate payload")
    object_name = _child_text(payload, "ObjectName") or path.stem
    guid = _child_text(template, "GUID") or path.stem
    mesh_guids, mesh_names = _mesh_references(payload)
    compatible_names, compatible_guids = _compatible_components(payload, template, object_name=object_name, guid=guid)
    return _clean(
        {
            "kind": "component",
            "guid": guid,
            "name": object_name,
            "object_name": object_name,
            "functional_group": _child_text(template, "FunctionalGroup"),
            "footprint": _child_text(template, "FootPrint"),
            "renderer": _child_text(template, "Renderer"),
            "dimension_mm": _vec_dict(_find(template, "Dimension")),
            "site_guids": _references(payload, "WorktableSite"),
            "connector_guids": _references(payload, "WorktableConnector"),
            "mesh_guids": mesh_guids,
            "mesh_names": mesh_names,
            "arrangements": _component_arrangements(template),
            "pipettable": _component_pipettable(template),
            "custom_attributes": _component_custom_attributes(template),
            "sub_component_names": _sub_component_names(template, object_name=object_name),
            "compatible_component_names": compatible_names,
            "compatible_component_guids": compatible_guids,
            "path": str(path),
        }
    )


def parse_workspace(
    path: Path | str,
    *,
    components: dict[str, dict[str, Any]],
    sites: dict[str, dict[str, Any]],
    connectors: dict[str, dict[str, Any]],
    max_xml_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any]:
    path = Path(path)
    root = _parse_xml(path, max_xml_bytes=max_xml_bytes)
    payload = _find(root, "Payload")
    if payload is None:
        raise ValueError("no Payload element")
    name = _child_text(payload, "ObjectName") or path.stem
    placements, available_sites = _workspace_tree(root, components=components, sites=sites, connectors=connectors)
    base_guid, base_name = _base_worktable(payload)
    connector_guids = sorted({value for item in placements for value in [item.get("connector_guid")] if value})
    pin_sites = sorted({item.get("pin_name") for item in available_sites if item.get("pin_name")})
    location_names = sorted({item.get("site_name") for item in available_sites if item.get("site_name")})
    return _clean(
        {
            "kind": "workspace",
            "guid": path.stem,
            "name": name,
            "object_name": name,
            "base_worktable_guid": base_guid,
            "base_worktable_name": base_name,
            "placement_count": len(placements),
            "available_site_count": len(available_sites),
            "placements": placements,
            "available_sites": available_sites,
            "connector_guids": connector_guids,
            "pin_sites": pin_sites,
            "location_names": location_names,
            "path": str(path),
        }
    )


def workspace_labware_records(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert workspace placements into source labware records for diffs."""
    records: list[dict[str, Any]] = []
    for placement in workspace.get("placements") or []:
        label = placement.get("label") or placement.get("name")
        if not label:
            continue
        records.append(
            _clean(
                {
                    "label": label,
                    "catalog": placement.get("catalog") or placement.get("component_name"),
                    "location": placement.get("site_name") or placement.get("pin_name"),
                    "position": placement.get("position_label"),
                    "deck_location": placement.get("deck_location"),
                    "role": "workspace",
                    "workspace": workspace.get("name"),
                    "workspace_guid": workspace.get("guid"),
                    "geometry": {
                        "site_path": placement.get("site_path"),
                        "site_index": placement.get("site_index"),
                        "base_site_guid": placement.get("base_site_guid"),
                        "base_site_name": placement.get("base_site_name"),
                        "pin_name": placement.get("pin_name"),
                        "connector_guid": placement.get("connector_guid"),
                        "connector_site_guid": placement.get("connector_site_guid"),
                        "connector_site_name": placement.get("connector_site_name"),
                        "connector_component_guid": placement.get("connector_component_guid"),
                        "connector_position_in_parent_mm": placement.get("connector_position_in_parent_mm"),
                        "connector_orientation_matrix": placement.get("connector_orientation_matrix"),
                        "connector_orientation_euler_deg": placement.get("connector_orientation_euler_deg"),
                        "origin_mm": placement.get("origin_mm"),
                        "orientation_matrix": placement.get("orientation_matrix"),
                    },
                }
            )
        )
    return records


def _workspace_tree(
    root: ET.Element,
    *,
    components: dict[str, dict[str, Any]],
    sites: dict[str, dict[str, Any]],
    connectors: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    placements: list[dict[str, Any]] = []
    available: list[dict[str, Any]] = []

    def label_from_labware_name(elem: ET.Element | None) -> str:
        if elem is None:
            return ""
        values: dict[str, str] = {}
        for kv in list(elem):
            if not isinstance(kv.tag, str) or not _local_name(kv.tag).startswith("KeyValueOfstringstring"):
                continue
            key = _text(_find(kv, "Key"))
            value = _text(_find(kv, "Value"))
            if key and value:
                values[key] = value
        return values.get("initial") or values.get("default") or next(iter(values.values()), "")

    def connected_component(value_node: ET.Element) -> ET.Element | None:
        connected = _direct_child(value_node, "ConnectedComponent")
        if connected is not None and _is_nil(connected):
            return None
        return connected

    def walk_sites(container: ET.Element | None, site_path: tuple[int, ...]) -> None:
        if container is None:
            return
        for node in list(container):
            if not isinstance(node.tag, str) or not _local_name(node.tag).startswith("KeyValueOfintSite"):
                continue
            key = _int_text(_find(node, "Key"))
            value_node = _find(node, "Value")
            if key is None or value_node is None:
                continue
            current_path = site_path + (key,)
            component_node = connected_component(value_node)
            effective_component = component_node if component_node is not None else value_node
            site_record = _workspace_site_record(value_node, effective_component, current_path, sites, connectors)
            available.append(site_record)

            label = label_from_labware_name(_direct_child(effective_component, "LabwareName"))
            if label:
                placements.append(
                    _workspace_placement_record(
                        label,
                        value_node,
                        effective_component,
                        current_path,
                        site_record,
                        components,
                        connectors,
                        sites,
                    )
                )

            nested_sites = _direct_child(effective_component, "Sites")
            if nested_sites is not None:
                walk_sites(nested_sites, current_path)
            arrangements = _direct_child(effective_component, "Arrangements")
            if arrangements is not None:
                for arrangement in list(arrangements):
                    if isinstance(arrangement.tag, str) and _local_name(arrangement.tag) == "Arrangement":
                        walk_sites(_find(arrangement, "Sites"), current_path)

    payload = _find(root, "Payload")
    payload_data = _find(payload, "PayloadData") if payload is not None else None
    worktables = _find(payload_data, "Worktables")
    if worktables is None:
        return placements, available
    for kv in list(worktables):
        if not isinstance(kv.tag, str):
            continue
        frame = _find(_find(kv, "Value"), "Frame")
        arrangements = _find(frame, "Arrangements")
        if arrangements is None:
            continue
        for arrangement in list(arrangements):
            if isinstance(arrangement.tag, str) and _local_name(arrangement.tag) == "Arrangement":
                walk_sites(_find(arrangement, "Sites"), tuple())
    return [_clean(item) for item in placements], [_clean(item) for item in available]


def _workspace_site_record(
    value_node: ET.Element,
    component_node: ET.Element,
    site_path: tuple[int, ...],
    sites: dict[str, dict[str, Any]],
    connectors: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    base_site_guid = _child_text(value_node, "BaseTemplateGuid")
    site = sites.get(base_site_guid or "", {})
    connector_guid = _child_text(value_node, "ConnectorTemplateGuid")
    connector = connectors.get(connector_guid or "", {})
    base_location = _child_text(component_node, "BaseLocationIdentifier")
    site_name = site.get("location_group_name") or site.get("type_name") or _non_guid(base_location)
    pin_name = site.get("pin_name")
    return _clean(
        {
            "site_path": list(site_path),
            "site_index": site_path[-1] if site_path else None,
            "base_site_guid": base_site_guid,
            "base_site_name": site_name,
            "site_name": site_name,
            "pin_name": pin_name,
            "connector_guid": connector_guid if connector_guid != ZERO_GUID else "",
            "connector_position_in_parent_mm": connector.get("position_in_parent_mm"),
            "connector_orientation_matrix": connector.get("orientation_matrix"),
            "origin_mm": _vec_dict(_find(_find(value_node, "Adjustment"), "origin")),
            "orientation_matrix": _matrix(_find(_find(value_node, "Adjustment"), "orientation")),
        }
    )


def _workspace_placement_record(
    label: str,
    value_node: ET.Element,
    component_node: ET.Element,
    site_path: tuple[int, ...],
    site_record: dict[str, Any],
    components: dict[str, dict[str, Any]],
    connectors: dict[str, dict[str, Any]],
    sites: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    template_guid = _child_text(component_node, "CarrierOrLabwareTemplateGUID")
    component = components.get(template_guid or "", {})
    connector_guid = _child_text(component_node, "BaseLocationConnectorIdentifier") or site_record.get("connector_guid")
    connector = connectors.get(connector_guid or "", {})
    connector_site = sites.get(str(connector.get("site_guid") or ""), {})
    base_site = sites.get(str(site_record.get("base_site_guid") or ""), {})
    component_name = component.get("name") or _label_base(label)
    site_name = (
        connector_site.get("pin_name")
        or connector_site.get("location_group_name")
        or site_record.get("pin_name")
        or site_record.get("site_name")
    )
    pin_name = connector_site.get("pin_name") or site_record.get("pin_name")
    position_label = _position_label(connector, site_path)
    deck_location = _deck_location_label(site_name, position_label, connector, pin_name=pin_name)
    return _clean(
        {
            "label": label,
            "catalog": component_name,
            "component_guid": template_guid,
            "component_name": component_name,
            "site_path": list(site_path),
            "site_index": site_path[-1] if site_path else None,
            "position_label": position_label,
            "deck_location": deck_location,
            "base_site_guid": site_record.get("base_site_guid"),
            "base_site_name": base_site.get("location_group_name") or site_record.get("base_site_name"),
            "pin_name": pin_name,
            "site_name": site_name,
            "connector_guid": connector_guid if connector_guid != ZERO_GUID else "",
            "connector_site_guid": connector.get("site_guid"),
            "connector_site_name": connector_site.get("pin_name") or connector_site.get("location_group_name"),
            "connector_component_guid": connector.get("component_guid"),
            "connector_component_name": connector.get("component_name"),
            "connector_position_in_parent_mm": connector.get("position_in_parent_mm"),
            "connector_orientation_matrix": connector.get("orientation_matrix"),
            "connector_orientation_euler_deg": connector.get("orientation_euler_deg"),
            "origin_mm": _vec_dict(_find(_find(value_node, "Adjustment"), "origin")),
            "orientation_matrix": _matrix(_find(_find(value_node, "Adjustment"), "orientation")),
        }
    )


def _component_arrangements(template: ET.Element) -> list[dict[str, Any]]:
    arrangements: list[dict[str, Any]] = []
    arrs = _direct_child(template, "Arrangements")
    if arrs is None:
        return arrangements
    for index, arrangement in enumerate(_direct_children(arrs, "ArrangementTemplate"), start=1):
        site_offsets = _indexed_vectors(_find(arrangement, "SiteOffsets"))
        site_templates = _indexed_values(_find(arrangement, "SiteTemplateIdentifiers"))
        arrangements.append(
            _clean(
                {
                    "index": index,
                    "sites_in_x": _int_text(_find(arrangement, "SitesInX")),
                    "sites_in_y": _int_text(_find(arrangement, "SitesInY")),
                    "sites_in_z": _int_text(_find(arrangement, "SitesInZ")),
                    "site_spacing_mm": {
                        "x": _float_text(_find(arrangement, "SiteSpacingInX")),
                        "y": _float_text(_find(arrangement, "SiteSpacingInY")),
                        "z": _float_text(_find(arrangement, "SiteSpacingInZ")),
                    },
                    "position_in_parent_mm": _vec_dict(_find(arrangement, "PositionInParent")),
                    "site_offsets_mm": site_offsets,
                    "site_template_identifiers": site_templates,
                    "allowed_grip_modes": _allowed_grip_modes(_direct_child(arrangement, "AllowedGripModes")),
                }
            )
        )
    return arrangements


def _component_pipettable(template: ET.Element | None) -> dict[str, Any] | None:
    pip = _direct_child(template, "Pipettable")
    if pip is None:
        return None
    cols = _int_text(_find(pip, "XNumberOfWells"))
    rows = _int_text(_find(pip, "YNumberOfWells"))
    if not cols or not rows:
        return None
    cavity_shapes = _pipettable_cavity_shapes(pip)
    primary = _primary_cavity(cavity_shapes)
    z_heights = _indexed_float_values(_find(pip, "ZHeights"))
    well_depth = None
    if cavity_shapes:
        depths = [shape.get("height_mm") for shape in cavity_shapes if shape.get("height_mm") is not None]
        if depths:
            well_depth = sum(float(value) for value in depths)
    if well_depth is None:
        for key in ("ZStart", "ZTravel", "ZDispense"):
            if key in z_heights:
                well_depth = max(well_depth or 0.0, float(z_heights[key]))
    diameter = None
    if primary:
        for key in ("diameter_top_mm", "diameter_mm", "diameter_bottom_mm", "length_mm", "width_mm"):
            value = primary.get(key)
            if value is not None and float(value) > 0:
                diameter = float(value)
                break
        if diameter is None:
            for key in ("area_top_mm2", "area_bottom_mm2"):
                area = primary.get(key)
                if area is not None and float(area) > 0:
                    diameter = float(area) ** 0.5
                    break
    return _clean(
        {
            "cols": cols,
            "rows": rows,
            "pitch_x_mm": _float_text(_find(pip, "XSpacing")),
            "pitch_y_mm": _float_text(_find(pip, "YSpacing")),
            "first_well_mm": _vec_dict(_find(pip, "PositionOfFirstWell")),
            "well_diameter_mm": diameter,
            "well_depth_mm": well_depth if well_depth and well_depth > 0 else None,
            "well_shape": _cavity_shape_kind(primary),
            "max_volume_ul": _cavity_volume_ul(cavity_shapes),
            "z_heights": z_heights or None,
            "cavity_shapes": cavity_shapes or None,
        }
    )


def _pipettable_cavity_shapes(pip: ET.Element) -> list[dict[str, Any]]:
    shapes: list[dict[str, Any]] = []
    for shape_el in _findall_local(pip, "CavityShape"):
        diameter = _float_text(_find(shape_el, "Diameter"))
        diameter_top = _float_text(_find(shape_el, "DiameterTop"))
        diameter_bottom = _float_text(_find(shape_el, "DiameterBottom"))
        xsi_type = (
            shape_el.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}type")
            or shape_el.attrib.get("type")
            or "Unknown"
        )
        shapes.append(
            _clean(
                {
                    "shape": str(xsi_type).split(":")[-1],
                    "height_mm": _float_text(_find(shape_el, "Height")),
                    "diameter_mm": diameter,
                    "diameter_top_mm": diameter_top if diameter_top is not None else diameter,
                    "diameter_bottom_mm": diameter_bottom if diameter_bottom is not None else diameter,
                    "length_mm": _float_text(_find(shape_el, "Length")),
                    "width_mm": _float_text(_find(shape_el, "Width")),
                    "area_top_mm2": _float_text(_find(shape_el, "AreaTop")),
                    "area_bottom_mm2": _float_text(_find(shape_el, "AreaBottom")),
                    "width_top_mm": _float_text(_find(shape_el, "WidthTop")),
                    "width_base_mm": _float_text(_find(shape_el, "WidthBase")),
                }
            )
        )
    return shapes


def _primary_cavity(shapes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not shapes:
        return None
    return max(
        shapes,
        key=lambda shape: (
            float(_cavity_volume_ul([shape]) or 0.0),
            float(shape.get("height_mm") or 0.0),
        ),
    )


def _cavity_shape_kind(shape: Mapping[str, Any] | None) -> str | None:
    if not shape:
        return None
    text = str(shape.get("shape") or "").casefold()
    if any(token in text for token in ("cylinder", "cone")):
        return "round"
    if any(token in text for token in ("cuboid", "pyramid", "trapezoid")):
        return "square"
    if any(shape.get(key) for key in ("diameter_mm", "diameter_top_mm", "diameter_bottom_mm")):
        return "round"
    if any(shape.get(key) for key in ("length_mm", "width_mm", "area_top_mm2")):
        return "square"
    return None


def _cavity_volume_ul(shapes: list[dict[str, Any]]) -> float | None:
    import math

    total = 0.0
    found = False
    for shape in shapes:
        height = shape.get("height_mm")
        if height is None or float(height) <= 0:
            continue
        h = float(height)
        text = str(shape.get("shape") or "").casefold()
        top = shape.get("diameter_top_mm")
        bottom = shape.get("diameter_bottom_mm")
        diameter = shape.get("diameter_mm")
        if top is None and diameter is not None:
            top = diameter
        if bottom is None and diameter is not None:
            bottom = diameter
        if top is not None and bottom is not None and float(top) > 0 and float(bottom) > 0:
            r1 = float(bottom) / 2.0
            r2 = float(top) / 2.0
            total += (math.pi * h / 3.0) * (r1 * r1 + r1 * r2 + r2 * r2)
            found = True
            continue
        length = shape.get("length_mm")
        width = shape.get("width_mm") or shape.get("width_top_mm") or shape.get("width_base_mm")
        if length is not None and width is not None and float(length) > 0 and float(width) > 0:
            total += float(length) * float(width) * h
            found = True
            continue
        area = shape.get("area_top_mm2") or shape.get("area_bottom_mm2")
        if area is not None and float(area) > 0 and "cylinder" not in text:
            total += float(area) * h
            found = True
    return total if found else None


def _allowed_grip_modes(elem: ET.Element | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if elem is None:
        return out
    for site_kv in list(elem):
        if not isinstance(site_kv.tag, str):
            continue
        site_idx = _int_text(_find(site_kv, "Key"))
        if site_idx is None:
            continue
        cgas: list[str] = []
        value_el = _find(site_kv, "Value")
        for cga_kv in list(value_el if value_el is not None else []):
            if not isinstance(cga_kv.tag, str):
                continue
            cga = _text(_find(cga_kv, "Key"))
            if cga and cga not in cgas:
                cgas.append(cga)
        if cgas:
            out[str(site_idx)] = cgas
    return out


def _component_custom_attributes(template: ET.Element | None) -> dict[str, str]:
    out: dict[str, str] = {}
    custom = _direct_child(template, "CustomAttributes")
    if custom is None:
        return out
    for kv in list(custom):
        if not isinstance(kv.tag, str):
            continue
        key = _text(_find(kv, "Key"))
        value_el = _find(kv, "Value")
        string_content = _text(_find(value_el, "StringContent"))
        if not key or string_content is None:
            continue
        try:
            inner = ET.fromstring(string_content)
            txt = _text(inner)
            out[key] = txt if txt is not None else string_content
        except Exception:
            out[key] = string_content
    return out


def _sub_component_names(template: ET.Element | None, *, object_name: str) -> list[str]:
    names: list[str] = []
    if template is None:
        return names
    for name_el in _findall_local(template, "ObjectName"):
        text = _text(name_el)
        if not text or text == object_name or _looks_like_guid(text):
            continue
        if text not in names:
            names.append(text)
    return names


def _compatible_components(
    payload: ET.Element | None,
    template: ET.Element | None,
    *,
    object_name: str,
    guid: str,
) -> tuple[list[str], list[str]]:
    names: list[str] = []
    guids: list[str] = []

    def add_name(value: str) -> None:
        text = str(value or "").strip()
        if not text or text == object_name or _looks_like_guid(text):
            return
        if text not in names:
            names.append(text)

    def add_guid(value: str) -> None:
        text = str(value or "").strip()
        if not text or text == guid or text == ZERO_GUID:
            return
        if text not in guids:
            guids.append(text)

    for ref in _direct_children(payload, "Reference"):
        type_id = _child_text(ref, "TypeId")
        if type_id not in {"WorktableComponent", "CompatibleComponent", "CompatibleLabware"}:
            continue
        add_guid(_child_text(ref, "Guid"))
        add_name(_child_text(ref, "ObjectName"))

    for local in ("CompatibleComponents", "CompatibleLabware", "CompatibleLabwares"):
        block = _direct_child(template, local)
        if block is None:
            block = _find(template, local)
        if block is None:
            continue
        for node in list(block):
            if not isinstance(node.tag, str):
                continue
            add_guid(_text(_find(node, "Guid")) or _text(_find(node, "Value")) or _text(node))
            add_name(_text(_find(node, "ObjectName")) or _text(_find(node, "Key")))
            value_el = _find(node, "Value")
            if value_el is not None:
                add_guid(_text(_find(value_el, "Guid")) or _text(value_el))
                add_name(_text(_find(value_el, "ObjectName")))

    for name in _sub_component_names(template, object_name=object_name):
        add_name(name)
    return names, guids


def _decorate_compatible_components(
    components: dict[str, dict[str, Any]],
    *,
    workspaces: list[dict[str, Any]],
) -> None:
    """Fold workspace occupancy into parent compatible-component lists."""
    for workspace in workspaces:
        for placement in workspace.get("placements") or []:
            if not isinstance(placement, Mapping):
                continue
            parent_guid = str(
                placement.get("connector_component_guid") or placement.get("parent_component_guid") or ""
            ).strip()
            child_guid = str(placement.get("component_guid") or "").strip()
            child_name = str(
                placement.get("catalog") or placement.get("component_name") or placement.get("name") or ""
            ).strip()
            if not parent_guid or parent_guid not in components:
                continue
            parent = components[parent_guid]
            if child_guid and child_guid != parent_guid:
                guids = list(parent.get("compatible_component_guids") or [])
                if child_guid not in guids:
                    guids.append(child_guid)
                    parent["compatible_component_guids"] = guids
            if child_name and child_name != parent.get("name"):
                names = list(parent.get("compatible_component_names") or [])
                if child_name not in names:
                    names.append(child_name)
                    parent["compatible_component_names"] = names


def _indexed_float_values(elem: ET.Element | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if elem is None:
        return out
    for kv in list(elem):
        if not isinstance(kv.tag, str):
            continue
        key = _text(_find(kv, "Key"))
        value = _float_text(_find(kv, "Value"))
        if key and value is not None:
            out[key] = value
    return out


def _findall_local(elem: ET.Element | None, local_name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [
        child
        for child in elem.iter()
        if isinstance(child.tag, str) and _local_name(child.tag) == local_name
    ]


def _decorate_connectors(
    connectors: dict[str, dict[str, Any]],
    *,
    components: dict[str, dict[str, Any]],
    sites: dict[str, dict[str, Any]],
) -> None:
    for connector in connectors.values():
        component = components.get(str(connector.get("component_guid") or ""))
        site = sites.get(str(connector.get("site_guid") or ""))
        if component is not None:
            connector["component_name"] = component.get("name")
        if site is not None:
            connector["site_name"] = site.get("pin_name") or site.get("location_group_name") or site.get("type_name")
            connector["pin_name"] = site.get("pin_name")


def _base_worktable(payload: ET.Element) -> tuple[str, str]:
    candidates: list[tuple[str, str]] = []
    for ref in _direct_children(payload, "Reference"):
        if _child_text(ref, "TypeId") != "WorktableComponent":
            continue
        guid = _child_text(ref, "Guid")
        name = _child_text(ref, "ObjectName")
        if guid and name and not _looks_like_guid(name):
            candidates.append((guid, name))
    if len(candidates) == 1:
        return candidates[0]
    for guid, name in candidates:
        if "base" in name.casefold() or "worktable" in name.casefold():
            return guid, name
    return "", ""


def _object_path(manifest: dict[str, Any], item: dict[str, Any]) -> Path | None:
    raw = str(item.get("extracted_path") or "")
    if not raw:
        raw = str(item.get("context_extracted_path") or item.get("entry") or "")
    if not raw:
        return None
    path = Path(raw.replace("\\", "/"))
    if path.is_absolute():
        return path
    root = Path(str(item.get("context_root") or manifest.get("root") or "")).expanduser()
    if root:
        candidate = root / path
        if candidate.exists():
            return candidate
    extracted_dir = Path(str(manifest.get("extracted_dir") or "")).expanduser()
    if extracted_dir and path.parts and path.parts[0] != "extracted":
        candidate = extracted_dir / path
        if candidate.exists():
            return candidate
    return root / path if root else path


def _source_fields(item: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "entry": item.get("entry"),
        "extracted_path": item.get("extracted_path"),
        "source_context": item.get("source_context"),
        "context_extracted_path": item.get("context_extracted_path"),
    }
    return {key: value for key, value in fields.items() if value}


def _parse_xml(path: Path, *, max_xml_bytes: int = 4 * 1024 * 1024) -> ET.Element:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return ET.fromstring(data.decode(encoding), max_bytes=max_xml_bytes)
        except UnicodeDecodeError:
            continue
    return ET.fromstring(data.decode("latin-1", errors="replace"), max_bytes=max_xml_bytes)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find(elem: ET.Element | None, local_name: str) -> ET.Element | None:
    if elem is None:
        return None
    for child in elem.iter():
        if isinstance(child.tag, str) and _local_name(child.tag) == local_name:
            return child
    return None


def _direct_child(elem: ET.Element | None, local_name: str) -> ET.Element | None:
    if elem is None:
        return None
    for child in list(elem):
        if isinstance(child.tag, str) and _local_name(child.tag) == local_name:
            return child
    return None


def _direct_children(elem: ET.Element | None, local_name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [child for child in list(elem) if isinstance(child.tag, str) and _local_name(child.tag) == local_name]


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _child_text(elem: ET.Element | None, local_name: str) -> str:
    return _text(_direct_child(elem, local_name))


def _first_text(root: ET.Element, local_name: str) -> str:
    return _text(_find(root, local_name))


def _references(payload: ET.Element | None, type_id: str) -> list[str]:
    out: list[str] = []
    for ref in _direct_children(payload, "Reference"):
        if _child_text(ref, "TypeId") != type_id:
            continue
        guid = _child_text(ref, "Guid")
        if guid and guid not in out:
            out.append(guid)
    return out


def _mesh_references(payload: ET.Element | None) -> tuple[list[str], list[str]]:
    """Return (mesh_guids, mesh_names) from WorktableMesh references on a component."""
    guids: list[str] = []
    names: list[str] = []
    for ref in _direct_children(payload, "Reference"):
        if _child_text(ref, "TypeId") != "WorktableMesh":
            continue
        guid = _child_text(ref, "Guid")
        name = _child_text(ref, "ObjectName")
        if guid and guid not in guids:
            guids.append(guid)
        if name and name not in names and name.casefold() != (guid or "").casefold():
            names.append(name)
    return guids, names


def _vec_dict(elem: ET.Element | None) -> dict[str, float]:
    if elem is None:
        return {}
    values: dict[str, float] = {}
    for child in list(elem):
        if not isinstance(child.tag, str):
            continue
        name = _local_name(child.tag).lower()
        if name not in {"x", "y", "z"}:
            continue
        value = _float_text(child)
        if value is not None:
            values[name] = value
    return values if set(values) == {"x", "y", "z"} else {}


def _matrix(elem: ET.Element | None) -> list[list[float]]:
    mat = _find(elem, "Mat")
    rows: list[list[float]] = []
    for row in _direct_children(mat, "ArrayOfdouble"):
        values = [_float_text(child) for child in list(row) if isinstance(child.tag, str) and _local_name(child.tag) == "double"]
        if all(value is not None for value in values) and values:
            rows.append([float(value) for value in values if value is not None])
    return rows if rows else []


def _indexed_vectors(elem: ET.Element | None) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if elem is None:
        return out
    for kv in list(elem):
        if not isinstance(kv.tag, str):
            continue
        key = _int_text(_find(kv, "Key"))
        value = _vec_dict(_find(kv, "Value"))
        if key is not None and value:
            out[str(key)] = value
    return out


def _indexed_values(elem: ET.Element | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if elem is None:
        return out
    for kv in list(elem):
        if not isinstance(kv.tag, str):
            continue
        key = _int_text(_find(kv, "Key"))
        value = _text(_find(kv, "Value"))
        if key is not None and value and value != ZERO_GUID:
            out[str(key)] = value
    return out


def _int_text(elem: ET.Element | None) -> int | None:
    text = _text(elem)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _float_text(elem: ET.Element | None) -> float | None:
    text = _text(elem)
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return 0.0 if value == -0.0 else value


def _bool_text(text: str) -> bool | None:
    if not text:
        return None
    if text.casefold() == "true":
        return True
    if text.casefold() == "false":
        return False
    return None


def _is_nil(elem: ET.Element) -> bool:
    return any(key.endswith("}nil") or key == "i:nil" for key in elem.attrib) and any(
        str(value).casefold() == "true" for value in elem.attrib.values()
    )


def _orientation_from_text(text: str) -> dict[str, float]:
    match = re.search(
        r"Phi\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*Theta\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*Psi\s*=\s*(-?\d+(?:\.\d+)?)",
        text or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return {}
    return {"phi": float(match.group(1)), "theta": float(match.group(2)), "psi": float(match.group(3))}


def _pin_name(
    location_group: str,
    type_name: str,
    object_name: str,
    *,
    site_kind: str = "",
) -> str:
    kind = site_kind or classify_site_kind(location_group, type_name, object_name)
    if not kind:
        return ""
    # Prefer LocationGroupName (deck instance / Cap_nest_* label), then TypeName, then ObjectName.
    for value in (location_group, type_name, object_name):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def classify_site_kind(location_group: Any = "", type_name: Any = "", object_name: Any = "") -> str:
    """Classify xsit sites for pin_sites / nest-cap mining.

    Mines WorktablePin* **and** CapHolder / *_Cap_nest_* / NestPlatform families from
    TypeName + LocationGroupName — not only names containing the substring ``pin``.
    """
    parts = [str(location_group or ""), str(type_name or ""), str(object_name or "")]
    blob = " ".join(part for part in parts if part).strip()
    if not blob:
        return ""
    lower = blob.casefold()
    compact = re.sub(r"[^a-z0-9]+", "", lower)

    if "pin" in lower:
        return "pin"
    if "capnest" in compact or "capholder" in compact:
        return "cap_nest"
    if "nestplatform" in compact or "nestbase" in compact or "regripnest" in compact:
        return "nest"
    # Nest site templates (7mm Nest, 61mm Nest, Landscape Nest, …) via TypeName/LocationGroup.
    if re.search(r"(^|[^a-z0-9])nest([^a-z0-9]|$)", lower):
        return "nest"
    return ""


def _looks_like_pin_or_nest_cap_site(value: Any) -> bool:
    return bool(classify_site_kind(value, "", ""))


def _looks_like_pin_name(value: Any) -> bool:
    """Backward-compatible alias — now includes nest/cap TypeName families."""
    return _looks_like_pin_or_nest_cap_site(value)


def _looks_like_guid(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text or ""))


def _non_guid(text: str) -> str:
    text = str(text or "").strip()
    return "" if not text or text == ZERO_GUID or _looks_like_guid(text) else text


def _label_base(label: str) -> str:
    return re.sub(r"\[[^\]]+\]$", "", str(label or "")).strip()


def _position_label(connector: dict[str, Any], site_path: tuple[int, ...]) -> str:
    position = connector.get("position_in_parent_mm") or {}
    if isinstance(position, dict) and position:
        return _format_vec(position)
    return ".".join(str(index + 1) for index in site_path) if site_path else ""


def _deck_location_label(site_name: Any, position_label: str, connector: dict[str, Any], *, pin_name: Any = "") -> str:
    site = str(pin_name or site_name or "").strip()
    connector_guid = str(connector.get("guid") or "").strip()
    if site and position_label and connector_guid:
        return f"{site} via connector {connector_guid} at {position_label}"
    if site and position_label:
        return f"{site} at {position_label}"
    if site:
        return site
    if position_label:
        return position_label
    return connector_guid


def _format_vec(vec: dict[str, Any]) -> str:
    def fmt(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:g}"

    return f"({fmt(vec.get('x'))}, {fmt(vec.get('y'))}, {fmt(vec.get('z'))}) mm"


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = _clean(item)
            if cleaned not in ("", None, [], {}):
                out[key] = cleaned
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            cleaned = _clean(item)
            if cleaned not in ("", None, [], {}):
                out.append(cleaned)
        return out
    return value
