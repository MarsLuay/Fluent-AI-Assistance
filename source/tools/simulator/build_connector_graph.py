#!/usr/bin/env python3
"""Build structured connector snap metadata from host/ZEIA Snap (.xcon) edges.

No committed ``connector-graph.json`` under ``public/models/fluent/``.
Regenerate per machine into ``public/models/fluent/local/`` from:

- host FluentControl VisionX Database, or
- an extracted full ZEIA ``DataStore`` (same Connectors/Sites layout).

Package imports may also ship ``source/connector_graph.json`` from a full ZEIA
``Connectors/*.xcon`` Snap walk (``fluent_pipeline.connector_graph_export``), not
only connectors already present in detailed ``worktable_geometry``.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[3]
DEFAULT_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
DEFAULT_FLUENT_MODELS = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent"
DEFAULT_LOCAL_MODELS = DEFAULT_FLUENT_MODELS / "local"
DEFAULT_REGISTRY = DEFAULT_LOCAL_MODELS / "registry.json"
DEFAULT_OUTPUT = DEFAULT_LOCAL_MODELS / "connector-graph.json"

from fluentcoder.catalog.xcmp import load_xsit
from fluentcoder.catalog.xcon import load_xcon

try:
    from tecan_tools import build_fluent_registry as registry_builder
except ImportError:  # running as a flat module on PYTHONPATH=source/tools
    import build_fluent_registry as registry_builder  # type: ignore

GRAPH_SCHEMA_VERSION = 1
GRAPH_KIND = "fluent-connector-graph"

# Coverage profiles are mined from the current install/ZEIA component connector
# counts (see fluent_pipeline.connector_coverage_export). Never assume Resolvex /
# A200 / CapHolder families exist in product source.
try:
    from fluent_pipeline.connector_coverage_export import (  # type: ignore
        CONNECTOR_COUNT_PROFILES as _PIPELINE_CONNECTOR_COUNT_PROFILES,
        build_profiles_from_component_counts as _build_profiles_from_component_counts,
    )

    CONNECTOR_COUNT_PROFILES: tuple[dict[str, Any], ...] = tuple(_PIPELINE_CONNECTOR_COUNT_PROFILES)

    def build_profiles_from_component_counts(
        component_names: dict[str, str],
        per_component_counts: dict[str, int],
        *,
        minimum_count: int = 1,
        source: str = "install",
    ) -> list[dict[str, Any]]:
        return _build_profiles_from_component_counts(
            component_names,
            per_component_counts,
            minimum_count=minimum_count,
            source=source,
        )

except ImportError:
    CONNECTOR_COUNT_PROFILES = ()

    def build_profiles_from_component_counts(
        component_names: dict[str, str],
        per_component_counts: dict[str, int],
        *,
        minimum_count: int = 1,
        source: str = "install",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for guid in sorted(set(component_names) | set(per_component_counts)):
            actual = int(per_component_counts.get(guid) or 0)
            if actual <= 0:
                continue
            name = str(component_names.get(guid) or "").strip() or guid
            slug = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or f"component_{guid[:8]}"
            rows.append(
                {
                    "id": slug[:80],
                    "componentGuid": guid,
                    "componentName": name,
                    "namePatterns": [name.casefold()],
                    "matchedComponentGuids": [guid],
                    "matchedComponentNames": [name],
                    "expectedCount": minimum_count,
                    "minimumCount": minimum_count,
                    "actualCount": actual,
                    "matches": actual >= minimum_count,
                    "source": source,
                }
            )
        rows.sort(key=lambda row: (str(row.get("componentName") or ""), str(row.get("id") or "")))
        return rows


GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)


def name_matches_any(name: str | None, patterns: list[str] | tuple[str, ...] | None) -> bool:
    text = (name or "").casefold()
    if not text or not patterns:
        return False
    return any(pattern.casefold() in text for pattern in patterns)


def component_guids_matching_names(
    component_names: dict[str, str],
    patterns: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Return install/registry component GUIDs whose names match any pattern."""
    if not patterns:
        return []
    matched: list[str] = []
    for guid, name in component_names.items():
        if name_matches_any(name, patterns):
            normalized = normalize_guid(guid)
            if normalized and normalized not in matched:
                matched.append(normalized)
    matched.sort()
    return matched


# Deprecated: never pin host GUIDs here. Coverage resolves from install/ZEIA names
# into context ``connector_coverage.json`` (see fluent_pipeline.connector_coverage_export).
KNOWN_CONNECTOR_COUNTS: dict[str, tuple[str, int]] = {}


def main() -> int:
    args = parse_args()
    output_path = Path(args.out)
    refuse_committed_stub_output(output_path, force=bool(args.force_stub_overwrite))
    graph = build_connector_graph(
        install_path=Path(args.install),
        registry_path=Path(args.registry) if args.registry else None,
        refresh_index=args.refresh_index,
        include_all_connectors=not args.site_connectors_only,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    summary = graph.get("summary", {})
    print(
        "Connector graph complete: "
        f"{summary.get('connectorCount', 0)} connectors, "
        f"{summary.get('siteCount', 0)} sites, "
        f"{summary.get('compatibilityCheckCount', 0)} checks -> {output_path}"
    )
    return 0


def refuse_committed_stub_output(output_path: Path, *, force: bool = False) -> None:
    """Refuse writing host-derived graphs into the tracked fluent models root.

    Rebuilds belong under ``public/models/fluent/local/`` (gitignored). There is
    no committed stub file — do not recreate ``connector-graph.json`` beside
    meshes. Pass ``force=True`` only for intentional exceptions.
    """
    if force:
        return
    resolved = output_path.resolve()
    forbidden = (DEFAULT_FLUENT_MODELS / "connector-graph.json").resolve()
    if resolved == forbidden:
        raise SystemExit(
            f"Refusing to write {forbidden} (tracked models root). "
            f"Use {DEFAULT_LOCAL_MODELS / 'connector-graph.json'} "
            "(per-machine local rebuild) or pass --force-stub-overwrite."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install",
        default=str(DEFAULT_INSTALL),
        help="Host VisionX Database or extracted ZEIA DataStore root (Snap/.xcon source).",
    )
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--refresh-index", action="store_true")
    parser.add_argument(
        "--site-connectors-only",
        action="store_true",
        help="Index only connectors referenced by .xsit files (faster, fewer edges).",
    )
    parser.add_argument(
        "--force-stub-overwrite",
        action="store_true",
        help="Allow writing under the tracked models root (not for normal rebuilds).",
    )
    return parser.parse_args()


def build_connector_graph(
    *,
    install_path: Path,
    registry_path: Path | None,
    refresh_index: bool,
    include_all_connectors: bool,
) -> dict[str, Any]:
    datastore_root, source_type = registry_builder.resolve_datastore_root(install_path)
    connectors_dir = datastore_root / "SystemSpecific" / "Worktable" / "Connectors"
    sites_dir = datastore_root / "SystemSpecific" / "Worktable" / "Sites"

    catalog_rows = registry_builder.load_catalog_rows(
        datastore_root,
        refresh_index=refresh_index,
        include_all_connectors=include_all_connectors,
    )
    component_names = {
        normalize_guid(row["guid"]): row.get("name")
        for row in catalog_rows.get("components", [])
        if row.get("guid")
    }
    registry: dict[str, Any] | None = None
    if registry_path and registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        for entry in registry.get("entries", []):
            guid = normalize_guid(entry.get("componentGuid"))
            if guid and entry.get("componentName"):
                component_names[guid] = entry.get("componentName")

    connector_rows = load_connector_rows(datastore_root, catalog_rows, connectors_dir)
    needed_site_guids = {
        normalize_guid(row.get("siteGuid"))
        for row in connector_rows
        if normalize_guid(row.get("siteGuid"))
    }
    site_owner = build_owned_site_owner_map(
        catalog_components=catalog_rows.get("components", []),
        datastore_root=datastore_root,
        needed_site_guids=needed_site_guids,
    )

    sites_by_guid: dict[str, dict[str, Any]] = {}
    child_connectors_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    snap_anchors_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in connector_rows:
        child_guid = normalize_guid(row["childComponentGuid"])
        site_guid = normalize_guid(row["siteGuid"])
        parent_guid = site_owner.get(site_guid)
        child_connectors_by_component[child_guid].append(row)
        site_entry = sites_by_guid.setdefault(
            site_guid,
            {
                "siteGuid": site_guid,
                "parentComponentGuid": parent_guid,
                "parentComponentName": component_names.get(parent_guid or "", parent_guid),
                "compatibleChildGuids": [],
                "connectors": [],
            },
        )
        site_entry["connectors"].append(row)
        if child_guid and child_guid not in site_entry["compatibleChildGuids"]:
            site_entry["compatibleChildGuids"].append(child_guid)

    for site_guid, site_entry in sites_by_guid.items():
        site_meta = load_site_metadata(sites_dir, site_guid)
        site_entry.update(site_meta)
        site_entry["compatibleChildGuids"] = sorted(site_entry["compatibleChildGuids"])
        parent_guid = site_entry.get("parentComponentGuid")
        if parent_guid:
            snap_anchors_by_component[parent_guid].append(site_entry_to_snap_anchor(site_entry))

    for parent_guid in snap_anchors_by_component:
        snap_anchors_by_component[parent_guid] = sorted(
            snap_anchors_by_component[parent_guid],
            key=lambda row: row.get("siteGuid") or "",
        )

    per_component_counts = {
        guid: len(rows) for guid, rows in child_connectors_by_component.items()
    }
    verification = build_count_verification(per_component_counts, component_names)
    compatibility_checks = build_compatibility_checks(
        connector_rows,
        component_names,
        site_owner,
        snap_anchors_by_component,
        sites_dir=sites_dir,
    )

    return {
        "schemaVersion": GRAPH_SCHEMA_VERSION,
        "kind": GRAPH_KIND,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "installPath": str(datastore_root),
            "installSourceType": source_type,
            "productAuthority": False,
            "localRebuildOnly": True,
            "note": "Per-machine rebuild under models/fluent/local/ — not product law; do not commit.",
            "registryPath": str(registry_path) if registry_path else None,
            "connectorsDir": str(connectors_dir),
            "includeAllConnectors": include_all_connectors,
        },
        "summary": {
            "connectorCount": len(connector_rows),
            "siteCount": len(sites_by_guid),
            "componentCount": len(component_names),
            "compatibilityCheckCount": len(compatibility_checks),
            "verifiedChecks": sum(1 for row in compatibility_checks if row.get("verified")),
        },
        "verification": verification,
        "connectors": connector_rows,
        "sites": sorted(sites_by_guid.values(), key=lambda row: row.get("siteGuid") or ""),
        "snapAnchorsByComponent": {
            guid: anchors for guid, anchors in sorted(snap_anchors_by_component.items())
        },
        "childConnectorsByComponent": {
            guid: rows for guid, rows in sorted(child_connectors_by_component.items())
        },
        "compatibilityChecks": compatibility_checks,
    }


def load_connector_rows(
    datastore_root: Path,
    catalog_rows: dict[str, Any],
    connectors_dir: Path,
) -> list[dict[str, Any]]:
    from fluentcoder.catalog.paths import index_db_path_default, install_path_key

    install_key = install_path_key(datastore_root)
    db_path = index_db_path_default(datastore_root)
    rows: list[dict[str, Any]] = []
    if not db_path.exists():
        return rows

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        db_rows = conn.execute(
            """
            SELECT guid, component_guid, site_guid, file_path
            FROM connectors
            WHERE install_key = ?
            """,
            (install_key,),
        ).fetchall()

    for db_row in db_rows:
        connector_guid = normalize_guid(db_row["guid"])
        child_guid = normalize_guid(db_row["component_guid"])
        site_guid = normalize_guid(db_row["site_guid"])
        rel_path = db_row["file_path"] or f"SystemSpecific/Worktable/Connectors/{connector_guid}.xcon"
        xcon_path = datastore_root / rel_path
        if not xcon_path.exists():
            xcon_path = connectors_dir / f"{connector_guid}.xcon"
        row = {
            "guid": connector_guid,
            "childComponentGuid": child_guid,
            "siteGuid": site_guid,
            "sourcePath": rel_path,
            "isDefault": False,
            "positionMm": None,
            "orientation": None,
        }
        if xcon_path.exists():
            try:
                parsed = load_xcon(xcon_path)
                row.update(
                    {
                        "name": parsed.name,
                        "isDefault": parsed.is_default,
                        "positionMm": vec3_to_list(parsed.position_mm),
                        "orientation": matrix_to_list(parsed.orientation),
                        "description": parsed.description,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                row["parseError"] = str(exc)
        rows.append(row)
    rows.sort(key=lambda item: (item.get("childComponentGuid") or "", item.get("siteGuid") or "", item.get("guid") or ""))
    return rows


def build_owned_site_owner_map(
    *,
    catalog_components: list[dict[str, Any]],
    datastore_root: Path,
    needed_site_guids: set[str] | None = None,
) -> dict[str, str]:
    """Map worktable site GUIDs to owning components using .xcmp site references."""
    owner: dict[str, str] = {}
    remaining = set(needed_site_guids or [])
    components_dir = datastore_root / "SystemSpecific" / "Worktable" / "Components"
    candidates = [
        row
        for row in catalog_components
        if normalize_guid(row.get("guid")) and row.get("site_count") not in (None, 0, "0")
    ]
    candidates.sort(key=lambda row: int(row.get("site_count") or 0), reverse=True)

    for row in candidates:
        if needed_site_guids is not None and not remaining:
            break
        component_guid = normalize_guid(row.get("guid"))
        rel_path = row.get("file_path")
        xcmp_path = datastore_root / rel_path if rel_path else components_dir / f"{component_guid}.xcmp"
        if not xcmp_path.exists():
            continue
        try:
            parsed = registry_builder.parse_xcmp_file(xcmp_path, "host-db")
        except Exception:
            continue
        for site_id in parsed.get("siteIds", []):
            site_guid = normalize_guid(site_id)
            if not site_guid:
                continue
            if needed_site_guids is not None and site_guid not in remaining:
                continue
            owner[site_guid] = component_guid
            remaining.discard(site_guid)

    return owner


def build_site_owner_map(sites_by_component: dict[str, list[str]]) -> dict[str, str]:
    owner: dict[str, str] = {}
    for component_guid, site_ids in sites_by_component.items():
        for site_guid in site_ids:
            owner[normalize_guid(site_guid)] = normalize_guid(component_guid)
    return owner


def load_site_metadata(sites_dir: Path, site_guid: str) -> dict[str, Any]:
    path = sites_dir / f"{site_guid}.xsit"
    if not path.exists():
        return {
            "locationGroupName": None,
            "typeName": None,
            "dimensionsMm": None,
        }
    try:
        site = load_xsit(path)
        return {
            "locationGroupName": site.location_group_name,
            "typeName": site.type_name,
            "dimensionsMm": parse_xsit_dimensions(path),
            "connectorGuids": list(site.connector_guids),
        }
    except Exception:
        return {
            "locationGroupName": None,
            "typeName": None,
            "dimensionsMm": parse_xsit_dimensions(path),
        }


def parse_xsit_dimensions(path: Path) -> list[float] | None:
    from tecan_common import xml_compat as ET

    try:
        root = ET.parse(path).getroot()
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] != "Dimension":
                continue
            coords = {}
            for child in node:
                name = child.tag.rsplit("}", 1)[-1]
                if name in {"X", "Y", "Z"} and child.text:
                    coords[name] = float(child.text)
            if {"X", "Y", "Z"}.issubset(coords):
                return [round(coords["X"], 3), round(coords["Y"], 3), round(coords["Z"], 3)]
    except Exception:
        return None
    return None


def site_entry_to_snap_anchor(site_entry: dict[str, Any]) -> dict[str, Any]:
    default_connector = next(
        (row for row in site_entry.get("connectors", []) if row.get("isDefault")),
        site_entry.get("connectors", [None])[0] if site_entry.get("connectors") else None,
    )
    anchor = {
        "siteGuid": site_entry.get("siteGuid"),
        "locationGroupName": site_entry.get("locationGroupName"),
        "typeName": site_entry.get("typeName"),
        "dimensionsMm": site_entry.get("dimensionsMm"),
        "compatibleChildGuids": site_entry.get("compatibleChildGuids") or [],
        "connectorCount": len(site_entry.get("connectors") or []),
    }
    if default_connector:
        anchor["snapPoint"] = {
            "connectorGuid": default_connector.get("guid"),
            "positionMm": default_connector.get("positionMm"),
            "orientation": default_connector.get("orientation"),
            "childComponentGuid": default_connector.get("childComponentGuid"),
        }
    return anchor


def build_count_verification(
    per_component_counts: dict[str, int],
    component_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Report connector coverage mined from this install/ZEIA component set.

    One row per component that has connectors. No assumed Resolvex/A200/CapHolder
    (or other) family list — presence comes from geometry/registry counts only.
    """
    return build_profiles_from_component_counts(
        component_names,
        per_component_counts,
        minimum_count=1,
        source="install",
    )


def build_compatibility_checks(
    connector_rows: list[dict[str, Any]],
    component_names: dict[str, str],
    site_owner: dict[str, str],
    snap_anchors_by_component: dict[str, list[dict[str, Any]]],
    *,
    sites_dir: Path,
) -> list[dict[str, Any]]:
    """Soft compatibility checks are not product law.

    Vendor/family substring checks (Resolvex A200, CapHolder, Falcon tube runner,
    filter→SBS nest, …) invent lab vocabulary and drift across installs. Prefer
    package ``connector_coverage.json`` / geometry Snap edges from full ZEIA.
    Host rebuilds still emit connector rows; soft family checks stay empty.
    """
    del connector_rows, component_names, site_owner, snap_anchors_by_component, sites_dir
    return []


def build_pair_check(
    *,
    id: str,
    description: str,
    child_guid: str | None,
    parent_guids: list[str] | None,
    parent_name_patterns: list[str],
    connector_rows: list[dict[str, Any]],
    site_owner: dict[str, str],
    component_names: dict[str, str],
    child_name_patterns: list[str] | None = None,
    child_name_exclude_patterns: list[str] | None = None,
    snap_anchors_by_component: dict[str, list[dict[str, Any]]] | None = None,
    parent_site_type_patterns: list[str] | None = None,
    parent_site_location_patterns: list[str] | None = None,
    site_meta_cache: dict[str, dict[str, Any]] | None = None,
    sites_dir: Path | None = None,
) -> dict[str, Any]:
    examples: list[dict[str, Any]] = []
    parent_guid_set = {normalize_guid(guid) for guid in (parent_guids or []) if guid}
    # Also resolve parents by name so snap-anchor walks work without pinned GUIDs.
    parent_guid_set.update(
        component_guids_matching_names(component_names, parent_name_patterns)
    )
    child_pattern = [pattern.lower() for pattern in (child_name_patterns or [])]
    child_exclude = [pattern.lower() for pattern in (child_name_exclude_patterns or [])]
    parent_pattern = [pattern.lower() for pattern in parent_name_patterns]
    site_type_pattern = [pattern.lower() for pattern in (parent_site_type_patterns or [])]
    site_location_pattern = [pattern.lower() for pattern in (parent_site_location_patterns or [])]

    def parent_matches(parent: str | None, site_guid: str) -> bool:
        if parent_guid_set and parent in parent_guid_set:
            return True
        site_info: dict[str, Any] = {}
        if site_meta_cache is not None and sites_dir is not None and site_guid:
            if site_guid not in site_meta_cache:
                site_meta_cache[site_guid] = load_site_metadata(sites_dir, site_guid)
            site_info = site_meta_cache[site_guid]
        type_name = (site_info.get("typeName") or "").lower()
        location_name = (site_info.get("locationGroupName") or "").lower()
        # Prefer ZEIA/install xsit TypeName over hardcoded location tokens.
        if site_type_pattern and any(pattern in type_name for pattern in site_type_pattern):
            return True
        if site_location_pattern and any(pattern in location_name for pattern in site_location_pattern):
            return True
        parent_name = (component_names.get(parent or "") or "").lower()
        return any(pattern in parent_name for pattern in parent_pattern)

    def child_matches(child: str | None) -> bool:
        if child_guid and child == child_guid:
            return True
        if child_pattern:
            child_name = (component_names.get(child or "") or "").lower()
            if child_exclude and any(pattern in child_name for pattern in child_exclude):
                return False
            return any(pattern in child_name for pattern in child_pattern)
        return bool(child_guid) and child == child_guid

    for row in connector_rows:
        child = normalize_guid(row.get("childComponentGuid"))
        site_guid = normalize_guid(row.get("siteGuid"))
        parent = site_owner.get(site_guid)
        if not child_matches(child):
            continue
        if not parent_matches(parent, site_guid):
            continue
        examples.append(
            {
                "connectorGuid": row.get("guid"),
                "childComponentGuid": child,
                "childComponentName": component_names.get(child),
                "parentComponentGuid": parent,
                "parentComponentName": component_names.get(parent or ""),
                "siteGuid": site_guid,
                "positionMm": row.get("positionMm"),
            }
        )

    if snap_anchors_by_component and parent_guid_set:
        for parent in sorted(parent_guid_set):
            for anchor in snap_anchors_by_component.get(parent, []):
                for child in anchor.get("compatibleChildGuids") or []:
                    if not child_matches(child):
                        continue
                    examples.append(
                        {
                            "connectorGuid": anchor.get("snapPoint", {}).get("connectorGuid"),
                            "childComponentGuid": child,
                            "childComponentName": component_names.get(child),
                            "parentComponentGuid": parent,
                            "parentComponentName": component_names.get(parent),
                            "siteGuid": anchor.get("siteGuid"),
                            "positionMm": anchor.get("snapPoint", {}).get("positionMm"),
                            "source": "snap-anchor",
                        }
                    )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for example in examples:
        key = (example.get("connectorGuid"), example.get("childComponentGuid"), example.get("siteGuid"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(example)

    return {
        "id": id,
        "description": description,
        "verified": len(deduped) > 0,
        "exampleCount": len(deduped),
        "examples": deduped[:12],
    }


def enrich_registry_entries(registry: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    snap_by_component = graph.get("snapAnchorsByComponent") or {}
    child_by_component = graph.get("childConnectorsByComponent") or {}
    for entry in registry.get("entries", []):
        guid = normalize_guid(entry.get("componentGuid"))
        if not guid:
            continue
        entry["snapAnchors"] = snap_by_component.get(guid, [])
        entry["childConnectors"] = child_by_component.get(guid, [])
    registry["connectorGraphPath"] = graph.get("sources", {}).get("registryPath")
    registry["compatibilityChecks"] = graph.get("compatibilityChecks", [])
    registry["connectorVerification"] = graph.get("verification", [])
    summary = registry.setdefault("summary", {})
    summary["entriesWithSnapAnchors"] = sum(1 for row in registry.get("entries", []) if row.get("snapAnchors"))
    summary["entriesWithChildConnectors"] = sum(1 for row in registry.get("entries", []) if row.get("childConnectors"))
    return registry


def vec3_to_list(value: tuple[float, float, float] | None) -> list[float] | None:
    if value is None:
        return None
    return [round(value[0], 4), round(value[1], 4), round(value[2], 4)]


def matrix_to_list(
    value: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None,
) -> list[list[float]] | None:
    if value is None:
        return None
    return [[round(cell, 6) for cell in row] for row in value]


def normalize_guid(value: str | None) -> str:
    match = GUID_RE.search(value or "")
    return match.group(0).lower() if match else ""


if __name__ == "__main__":
    raise SystemExit(main())
