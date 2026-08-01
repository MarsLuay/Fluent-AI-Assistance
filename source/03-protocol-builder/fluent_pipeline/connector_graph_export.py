"""Build a connector snap graph from imported ZEIA geometry and/or DataStore Snap files.

Committed ``public/models/fluent/connector-graph.json`` is not used.
Authoritative edges come from:

- full ZEIA ``Connectors/*.xcon`` Snap walk (preferred — matches host rebuild),
- ``worktable_geometry`` when detailed XML parse already mined connectors, or
- per-machine host install Snap rebuilds via ``source/tools/simulator/build_connector_graph.py``
  into gitignored ``public/models/fluent/local/``.

Large ZEIA imports often skip detailed ``worktable_geometry`` (entry-count limit).
Package graphs must still walk on-disk ``Connectors/`` so Snap edges are not weaker
than a host rebuild from the same DataStore.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .runner import write_json
from .worktable_datastore import (
    discover_worktable_datastore,
    resolve_worktable_datastore,
)

CONNECTOR_GRAPH_SCHEMA_VERSION = 1
CONNECTOR_GRAPH_KIND = "fluent-connector-graph"
CONNECTOR_GRAPH_FILENAME = "connector_graph.json"
_WORKTABLE_REL = Path("SystemSpecific") / "Worktable"


def build_connector_graph_from_geometry(
    geometry: Mapping[str, Any] | None,
    *,
    source: str = "worktable_geometry",
) -> dict[str, Any] | None:
    """Convert ZEIA ``worktable_geometry`` connectors/sites into a fluent-connector-graph."""
    geometry = geometry if isinstance(geometry, Mapping) else {}
    connectors_in = [item for item in (geometry.get("connectors") or []) if isinstance(item, Mapping)]
    sites_in = [item for item in (geometry.get("sites") or []) if isinstance(item, Mapping)]
    components_in = [item for item in (geometry.get("components") or []) if isinstance(item, Mapping)]
    if not connectors_in and not sites_in:
        return None

    component_names: dict[str, str] = {}
    site_owner: dict[str, str] = {}
    for component in components_in:
        guid = _norm_guid(component.get("guid"))
        name = str(component.get("name") or component.get("object_name") or "").strip()
        if guid:
            if name:
                component_names[guid] = name
            for site_guid in component.get("site_guids") or []:
                site_key = _norm_guid(site_guid)
                if site_key and site_key not in site_owner:
                    site_owner[site_key] = guid

    return _assemble_connector_graph(
        connectors_in=connectors_in,
        sites_in=sites_in,
        component_names=component_names,
        site_owner=site_owner,
        source=source,
        install_path="",
        install_source_type="zeia",
        include_all_connectors=True,
        note=(
            "Derived from imported ZEIA worktable_geometry. Prefer full Connectors/*.xcon "
            "Snap walk when the DataStore is present (large exports skip detailed geometry)."
        ),
        verification_geometry=geometry,
    )


def build_connector_graph_from_datastore(
    datastore_root: Path | str | None,
    *,
    geometry: Mapping[str, Any] | None = None,
    source: str = "zeia_datastore",
    max_xml_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any] | None:
    """Walk ``Connectors/*.xcon`` under a ZEIA/install DataStore — full Snap edges."""
    root = resolve_worktable_datastore(datastore_root)
    if root is None:
        return None
    connectors_dir = root / _WORKTABLE_REL / "Connectors"
    if not connectors_dir.is_dir():
        return None

    from .worktable_geometry import parse_component, parse_connector, parse_site

    geometry = geometry if isinstance(geometry, Mapping) else {}
    component_names: dict[str, str] = {}
    site_owner: dict[str, str] = {}
    for component in geometry.get("components") or []:
        if not isinstance(component, Mapping):
            continue
        guid = _norm_guid(component.get("guid"))
        name = str(component.get("name") or component.get("object_name") or "").strip()
        if guid and name:
            component_names[guid] = name
        if not guid:
            continue
        for site_guid in component.get("site_guids") or []:
            site_key = _norm_guid(site_guid)
            if site_key and site_key not in site_owner:
                site_owner[site_key] = guid

    connectors_in: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in sorted(connectors_dir.glob("*.xcon")):
        try:
            connectors_in.append(parse_connector(path, max_xml_bytes=max_xml_bytes))
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path), "error": str(exc)})

    if not connectors_in:
        return None

    needed_sites = {
        guid
        for item in connectors_in
        for guid in [_norm_guid(item.get("site_guid"))]
        if guid
    }
    remaining = {guid for guid in needed_sites if guid not in site_owner}
    components_dir = root / _WORKTABLE_REL / "Components"
    if remaining and components_dir.is_dir():
        for path in sorted(components_dir.glob("*.xcmp")):
            if not remaining:
                break
            try:
                component = parse_component(path, max_xml_bytes=max_xml_bytes)
            except Exception:
                continue
            guid = _norm_guid(component.get("guid"))
            name = str(component.get("name") or component.get("object_name") or "").strip()
            if guid and name:
                component_names.setdefault(guid, name)
            if not guid:
                continue
            for site_guid in component.get("site_guids") or []:
                site_key = _norm_guid(site_guid)
                if not site_key:
                    continue
                if site_key in remaining or site_key not in site_owner:
                    site_owner.setdefault(site_key, guid)
                remaining.discard(site_key)

    sites_dir = root / _WORKTABLE_REL / "Sites"
    sites_in: list[dict[str, Any]] = []
    for site in geometry.get("sites") or []:
        if isinstance(site, Mapping) and _norm_guid(site.get("guid")) in needed_sites:
            sites_in.append(dict(site))
    have = {_norm_guid(item.get("guid")) for item in sites_in}
    if sites_dir.is_dir():
        for site_guid in sorted(needed_sites - have):
            path = sites_dir / f"{site_guid}.xsit"
            if not path.is_file():
                matches = list(sites_dir.glob(f"{site_guid}.xsit"))
                path = matches[0] if matches else path
            if not path.is_file():
                continue
            try:
                sites_in.append(parse_site(path, max_xml_bytes=max_xml_bytes))
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": str(path), "error": str(exc)})

    for connector in connectors_in:
        child = _norm_guid(connector.get("component_guid"))
        name = str(connector.get("component_name") or connector.get("name") or "").strip()
        if child and name and child not in component_names and name.casefold() != child:
            component_names.setdefault(child, name)

    graph = _assemble_connector_graph(
        connectors_in=connectors_in,
        sites_in=sites_in,
        component_names=component_names,
        site_owner=site_owner,
        source=source,
        install_path=str(root),
        install_source_type="zeia",
        include_all_connectors=True,
        note=(
            "Full Snap edges from ZEIA/install Connectors/*.xcon (include_all). "
            "Matches host rebuild scope; not limited to detailed worktable_geometry."
        ),
        verification_geometry=None,
    )
    if graph is not None and errors:
        graph["parseErrors"] = errors[:50]
        graph["summary"]["parseErrorCount"] = len(errors)
    return graph


def build_connector_graph_for_package(
    geometry: Mapping[str, Any] | None = None,
    *,
    context_root: Path | str | None = None,
    datastore_root: Path | str | None = None,
    source: str = "worktable_geometry",
) -> dict[str, Any] | None:
    """Prefer the fuller of geometry-derived and DataStore Snap graphs."""
    geometry_graph = build_connector_graph_from_geometry(geometry, source=source)
    root = resolve_worktable_datastore(datastore_root)
    if root is None and context_root is not None:
        root = discover_worktable_datastore(context_root)
    datastore_graph = (
        build_connector_graph_from_datastore(root, geometry=geometry, source="zeia_datastore")
        if root is not None
        else None
    )
    return select_richer_connector_graph(geometry_graph, datastore_graph)


def select_richer_connector_graph(
    left: Mapping[str, Any] | None,
    right: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Keep the graph with more Snap connector edges (ties → left)."""
    if left is None:
        return dict(right) if isinstance(right, Mapping) else None
    if right is None:
        return dict(left)
    left_count = int((left.get("summary") or {}).get("connectorCount") or 0)
    right_count = int((right.get("summary") or {}).get("connectorCount") or 0)
    if right_count > left_count:
        return dict(right)
    return dict(left)


def write_connector_graph(
    destination: Path,
    geometry: Mapping[str, Any] | None,
    *,
    source: str = "worktable_geometry",
    context_root: Path | str | None = None,
    datastore_root: Path | str | None = None,
) -> Path | None:
    """Write ``connector_graph.json`` from geometry and/or ZEIA DataStore Snap walk."""
    graph = build_connector_graph_for_package(
        geometry,
        context_root=context_root or Path(destination).parent,
        datastore_root=datastore_root,
        source=source,
    )
    if not graph:
        return None
    if int(graph.get("summary", {}).get("connectorCount") or 0) <= 0 and not graph.get("sites"):
        return None
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, graph)
    return destination


def write_connector_graph_for_context(
    context_root: Path,
    geometry: Mapping[str, Any] | None,
) -> Path | None:
    """Write the graph next to ``manifest.json`` under a project context root."""
    return write_connector_graph(
        Path(context_root) / CONNECTOR_GRAPH_FILENAME,
        geometry,
        context_root=context_root,
        source="worktable_geometry",
    )


def _assemble_connector_graph(
    *,
    connectors_in: list[Mapping[str, Any]],
    sites_in: list[Mapping[str, Any]],
    component_names: dict[str, str],
    site_owner: dict[str, str],
    source: str,
    install_path: str,
    install_source_type: str,
    include_all_connectors: bool,
    note: str,
    verification_geometry: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not connectors_in and not sites_in:
        return None

    sites_by_guid: dict[str, dict[str, Any]] = {}
    for site in sites_in:
        site_guid = _norm_guid(site.get("guid"))
        if not site_guid:
            continue
        parent_guid = site_owner.get(site_guid)
        dimension = site.get("dimension_mm") if isinstance(site.get("dimension_mm"), Mapping) else {}
        sites_by_guid[site_guid] = {
            "siteGuid": site_guid,
            "parentComponentGuid": parent_guid,
            "parentComponentName": component_names.get(parent_guid or "", parent_guid),
            "locationGroupName": site.get("location_group_name") or site.get("name"),
            "typeName": site.get("type_name"),
            "dimensionsMm": _vec3(dimension),
            "compatibleChildGuids": [],
            "connectors": [],
            "connectorGuids": [
                guid
                for guid in (_norm_guid(value) for value in (site.get("connector_guids") or []))
                if guid
            ],
        }

    connector_rows: list[dict[str, Any]] = []
    child_connectors_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for connector in connectors_in:
        connector_guid = _norm_guid(connector.get("guid"))
        child_guid = _norm_guid(connector.get("component_guid"))
        site_guid = _norm_guid(connector.get("site_guid"))
        if not connector_guid:
            continue
        if child_guid and connector.get("component_name"):
            component_names.setdefault(child_guid, str(connector.get("component_name")))
        position = connector.get("position_in_parent_mm")
        row = {
            "guid": connector_guid,
            "childComponentGuid": child_guid,
            "siteGuid": site_guid,
            "name": connector.get("name") or connector.get("object_name"),
            "description": connector.get("description"),
            "isDefault": bool(connector.get("is_default")),
            "positionMm": _vec3(position) if isinstance(position, Mapping) else None,
            "orientation": connector.get("orientation_matrix"),
            "sourcePath": connector.get("path") or connector.get("extracted_path"),
        }
        connector_rows.append(row)
        if child_guid:
            child_connectors_by_component[child_guid].append(row)
        if site_guid:
            site_entry = sites_by_guid.setdefault(
                site_guid,
                {
                    "siteGuid": site_guid,
                    "parentComponentGuid": site_owner.get(site_guid),
                    "parentComponentName": component_names.get(
                        site_owner.get(site_guid) or "", site_owner.get(site_guid)
                    ),
                    "locationGroupName": connector.get("site_name"),
                    "typeName": None,
                    "dimensionsMm": None,
                    "compatibleChildGuids": [],
                    "connectors": [],
                    "connectorGuids": [],
                },
            )
            site_entry["connectors"].append(row)
            if child_guid and child_guid not in site_entry["compatibleChildGuids"]:
                site_entry["compatibleChildGuids"].append(child_guid)

    snap_anchors_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for site_guid, site_entry in sites_by_guid.items():
        site_entry["compatibleChildGuids"] = sorted(site_entry.get("compatibleChildGuids") or [])
        parent_guid = site_entry.get("parentComponentGuid") or site_owner.get(site_guid)
        if parent_guid:
            site_entry["parentComponentGuid"] = parent_guid
            site_entry["parentComponentName"] = component_names.get(parent_guid, parent_guid)
            snap_anchors_by_component[parent_guid].append(_site_to_snap_anchor(site_entry))

    for parent_guid in list(snap_anchors_by_component):
        snap_anchors_by_component[parent_guid] = sorted(
            snap_anchors_by_component[parent_guid],
            key=lambda row: str(row.get("siteGuid") or ""),
        )

    connector_rows.sort(
        key=lambda row: (
            str(row.get("childComponentGuid") or ""),
            str(row.get("siteGuid") or ""),
            str(row.get("guid") or ""),
        )
    )

    if verification_geometry is not None:
        from .connector_coverage_export import build_connector_coverage_from_geometry

        verification = list(
            build_connector_coverage_from_geometry(verification_geometry, source=source).get("profiles") or []
        )
    else:
        from .connector_coverage_export import build_profiles_from_component_counts

        per_component_counts: dict[str, int] = defaultdict(int)
        for row in connector_rows:
            site = str(row.get("siteGuid") or "")
            parent = site_owner.get(site)
            key = parent or str(row.get("childComponentGuid") or "")
            if key:
                per_component_counts[key] += 1
        verification = build_profiles_from_component_counts(
            component_names,
            per_component_counts,
            minimum_count=1,
            source=source,
        )

    return {
        "schemaVersion": CONNECTOR_GRAPH_SCHEMA_VERSION,
        "kind": CONNECTOR_GRAPH_KIND,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "installPath": install_path,
            "installSourceType": install_source_type,
            "registryPath": None,
            "connectorsDir": source if not install_path else str(Path(install_path) / _WORKTABLE_REL / "Connectors"),
            "includeAllConnectors": include_all_connectors,
        },
        "summary": {
            "connectorCount": len(connector_rows),
            "siteCount": len(sites_by_guid),
            "componentCount": len(component_names),
            "compatibilityCheckCount": 0,
            "verifiedChecks": 0,
        },
        "verification": verification,
        "connectors": connector_rows,
        "sites": sorted(sites_by_guid.values(), key=lambda row: str(row.get("siteGuid") or "")),
        "snapAnchorsByComponent": {
            guid: anchors for guid, anchors in sorted(snap_anchors_by_component.items())
        },
        "childConnectorsByComponent": {
            guid: rows for guid, rows in sorted(child_connectors_by_component.items())
        },
        "compatibilityChecks": [],
        "note": note,
    }


def _site_to_snap_anchor(site_entry: Mapping[str, Any]) -> dict[str, Any]:
    connectors = list(site_entry.get("connectors") or [])
    default_connector = next((row for row in connectors if row.get("isDefault")), None)
    if default_connector is None and connectors:
        default_connector = connectors[0]
    anchor: dict[str, Any] = {
        "siteGuid": site_entry.get("siteGuid"),
        "locationGroupName": site_entry.get("locationGroupName"),
        "typeName": site_entry.get("typeName"),
        "dimensionsMm": site_entry.get("dimensionsMm"),
        "compatibleChildGuids": list(site_entry.get("compatibleChildGuids") or []),
        "connectorCount": len(connectors),
    }
    if default_connector:
        anchor["snapPoint"] = {
            "connectorGuid": default_connector.get("guid"),
            "positionMm": default_connector.get("positionMm"),
            "orientation": default_connector.get("orientation"),
            "childComponentGuid": default_connector.get("childComponentGuid"),
        }
    return anchor


def _norm_guid(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return text if len(text) == 36 and text.count("-") == 4 else (text if text else "")


def _vec3(value: Mapping[str, Any] | None) -> list[float] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return [round(float(value.get("x")), 3), round(float(value.get("y")), 3), round(float(value.get("z")), 3)]
    except (TypeError, ValueError):
        return None
