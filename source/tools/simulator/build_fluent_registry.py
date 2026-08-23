#!/usr/bin/env python3
"""Build a Fluent asset registry from the host worktable database and mesh manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from tecan_common import xml_compat as ET

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[3]
DEFAULT_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
DEFAULT_MANIFEST = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent/local/manifest.json"
DEFAULT_LOCAL_MODELS = DEFAULT_MANIFEST.parent
DEFAULT_FLUENT_MODELS = DEFAULT_LOCAL_MODELS.parent
DEFAULT_OUTPUT = DEFAULT_LOCAL_MODELS / "registry.json"
DEFAULT_TEXTURE_MANIFEST = DEFAULT_LOCAL_MODELS / "textures/manifest.json"
# Legacy fallback if an older rebuild still wrote beside fluent/textures/
LEGACY_TEXTURE_MANIFEST = DEFAULT_FLUENT_MODELS / "textures/manifest.json"
DEFAULT_CONNECTOR_GRAPH = DEFAULT_LOCAL_MODELS / "connector-graph.json"
FLUENT_MESH_ASSET_PREFIX = "/models/fluent/local"
FLUENT_TEXTURE_ASSET_PREFIX = "/models/fluent/local/textures"
DEFAULT_ALIASES = PROJECT_ROOT / "source/03-protocol-builder/config/aliases/labware_aliases.yaml"
DEFAULT_PROCEDURAL_CATALOG = PROJECT_ROOT / "source/04-protocol-simulator/src/data/labwareCatalog.ts"

GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
HOST_INSTALL_MARKERS = (
    r"programdata\tecan\visionx\database",
    r"program files\tecan",
)

REGISTRY_SCHEMA_VERSION = 1
REGISTRY_KIND = "fluent-asset-registry"


def resolve_texture_manifest_path(explicit: Path | None) -> Path | None:
    """Prefer local/textures rebuild; fall back to legacy fluent/textures if present."""
    if explicit is not None:
        return explicit
    if DEFAULT_TEXTURE_MANIFEST.is_file():
        return DEFAULT_TEXTURE_MANIFEST
    if LEGACY_TEXTURE_MANIFEST.is_file():
        return LEGACY_TEXTURE_MANIFEST
    return DEFAULT_TEXTURE_MANIFEST


def portable_manifest_label(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return path.name


def main() -> int:
    args = parse_args()
    texture_arg = str(args.texture_manifest or "").strip()
    texture_explicit = Path(texture_arg) if texture_arg and texture_arg != str(DEFAULT_TEXTURE_MANIFEST) else None
    texture_manifest_path = resolve_texture_manifest_path(texture_explicit)
    registry = build_registry(
        install_path=Path(args.install),
        manifest_path=Path(args.manifest) if args.manifest else None,
        aliases_path=Path(args.aliases) if args.aliases else None,
        procedural_catalog_path=Path(args.procedural_catalog) if args.procedural_catalog else None,
        hardware_manifest_path=Path(args.hardware_manifest) if args.hardware_manifest else None,
        texture_manifest_path=texture_manifest_path,
        refresh_index=args.refresh_index,
        include_all_connectors=args.include_all_connectors,
    )
    # Prefer portable repo-relative texture path in written registry (no host abs paths).
    if isinstance(registry.get("sources"), dict):
        registry["sources"]["textureManifestPath"] = portable_manifest_label(texture_manifest_path)
    output_path = Path(args.out)
    refuse_committed_stub_outputs(
        registry_path=output_path,
        connector_graph_path=Path(args.connector_graph_out) if args.with_connectors else None,
        force=bool(getattr(args, "force_stub_overwrite", False)),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    summary = registry.get("summary", {})
    print(
        "Fluent asset registry complete: "
        f"{summary.get('entryCount', 0)} entries "
        f"({summary.get('meshEntries', 0)} mesh, "
        f"{summary.get('componentOnlyEntries', 0)} component-only, "
        f"{summary.get('proceduralEntries', 0)} procedural) -> {output_path}"
    )
    if args.with_connectors:
        connector_graph = build_connector_graph_bundle(
            install_path=Path(args.install),
            registry=registry,
            registry_path=output_path,
            refresh_index=args.refresh_index,
            connector_graph_path=Path(args.connector_graph_out),
        )
        connector_graph_path = Path(args.connector_graph_out)
        connector_graph_path.parent.mkdir(parents=True, exist_ok=True)
        connector_graph_path.write_text(json.dumps(connector_graph, indent=2) + "\n", encoding="utf-8")
        registry = enrich_registry_with_connectors(registry, connector_graph)
        output_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        print(
            "Connector metadata merged: "
            f"{connector_graph.get('summary', {}).get('connectorCount', 0)} connectors, "
            f"{summary.get('entriesWithSnapAnchors', registry.get('summary', {}).get('entriesWithSnapAnchors', 0))} entries with snap anchors -> {connector_graph_path}"
        )
    return 0


def refuse_committed_stub_outputs(
    *,
    registry_path: Path,
    connector_graph_path: Path | None,
    force: bool = False,
) -> None:
    """Refuse writing host-derived catalogs into the tracked fluent models root."""
    if force:
        return
    registry_forbidden = (DEFAULT_FLUENT_MODELS / "registry.json").resolve()
    graph_forbidden = (DEFAULT_FLUENT_MODELS / "connector-graph.json").resolve()
    if registry_path.resolve() == registry_forbidden:
        raise SystemExit(
            f"Refusing to write {registry_forbidden} (tracked models root). "
            f"Use {DEFAULT_LOCAL_MODELS / 'registry.json'} or pass --force-stub-overwrite."
        )
    if connector_graph_path is not None and connector_graph_path.resolve() == graph_forbidden:
        raise SystemExit(
            f"Refusing to write {graph_forbidden} (tracked models root). "
            f"Use {DEFAULT_LOCAL_MODELS / 'connector-graph.json'} or pass --force-stub-overwrite."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install",
        default=str(resolve_install_default()),
        help="FluentControl install/DataStore root (host worktable database).",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Fluent mesh manifest.json with bounds and triangle counts.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help="Output registry.json path.",
    )
    parser.add_argument(
        "--aliases",
        default=str(DEFAULT_ALIASES),
        help="Labware alias YAML used to populate entry aliases.",
    )
    parser.add_argument(
        "--procedural-catalog",
        default=str(DEFAULT_PROCEDURAL_CATALOG),
        help="Simulator labware catalog TypeScript for procedural asset entries.",
    )
    parser.add_argument(
        "--hardware-manifest",
        default="",
        help="Optional hardware_manifest.json for texture/asset cross-refs.",
    )
    parser.add_argument(
        "--texture-manifest",
        default=str(DEFAULT_TEXTURE_MANIFEST),
        help="Decoded fluent texture manifest.json (from extract_fluent_textures.py).",
    )
    parser.add_argument(
        "--refresh-index",
        action="store_true",
        help="Rebuild the fluentcoder install_index.db before indexing.",
    )
    parser.add_argument(
        "--include-all-connectors",
        action="store_true",
        help="Index every Connectors/*.xcon file (slow on full installs).",
    )
    parser.add_argument(
        "--with-connectors",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Build connector snap metadata and merge snapAnchors/childConnectors into registry entries.",
    )
    parser.add_argument(
        "--connector-graph-out",
        default=str(DEFAULT_CONNECTOR_GRAPH),
        help="Output connector-graph.json path.",
    )
    parser.add_argument(
        "--force-stub-overwrite",
        action="store_true",
        help="Allow writing under the tracked models root (not for normal rebuilds).",
    )
    return parser.parse_args()


def build_connector_graph_bundle(
    *,
    install_path: Path,
    registry: dict[str, Any],
    registry_path: Path,
    refresh_index: bool,
    connector_graph_path: Path,
) -> dict[str, Any]:
    from tecan_tools import build_connector_graph as connector_graph

    return connector_graph.build_connector_graph(
        install_path=install_path,
        registry_path=registry_path,
        refresh_index=refresh_index,
        include_all_connectors=True,
    )


def enrich_registry_with_connectors(registry: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    from tecan_tools import build_connector_graph as connector_graph

    enriched = connector_graph.enrich_registry_entries(registry, graph)
    enriched["sources"]["connectorGraphPath"] = graph.get("sources", {}).get("installPath")
    return enriched


def resolve_install_default() -> Path:
    env = os.environ.get("FLUENTCODER_FC_INSTALL")
    return Path(env) if env else DEFAULT_INSTALL




def _build_component_entries(
    *,
    parsed_components: list[dict[str, Any]],
    components_by_guid: dict[str, Any],
    sites_by_component: dict[str, list[str]],
    connectors_by_component: dict[str, list[str]],
    hardware_textures: dict[str, list[str]],
    texture_catalog: dict[str, Any],
    alias_map: dict[str, list[str]],
    mesh_manifest: dict[str, Any],
    install_source_type: str,
    entries: dict[str, dict[str, Any]],
    mesh_component_pairs: set[tuple[str, str | None]],
) -> None:
    for component in parsed_components:
        component_guid = component["componentGuid"]
        catalog = components_by_guid.get(component_guid, {})
        component_name = component["componentName"] or catalog.get("name") or component_guid
        dimensions = component["dimensions"] or dimensions_from_catalog(catalog)
        site_ids = sorted(
            set(component["siteIds"])
            | set(sites_by_component.get(component_guid, []))
        )
        connector_ids = sorted(connectors_by_component.get(component_guid, []))
        texture_ids = sorted(
            set(component["textureIds"])
            | set(hardware_textures.get(component_guid, []))
        )
        textures = resolve_texture_bindings(component.get("textureBindings") or [], texture_catalog)
        aliases = sorted(
            set(alias_map.get(component_name, []))
            | set(alias_map.get(component["renderer"], []))
        )

        mesh_refs = component["meshRefs"]
        if mesh_refs:
            for mesh_ref in mesh_refs:
                mesh_guid = mesh_ref["guid"]
                mesh_component_pairs.add((mesh_guid, component_guid))
                mesh_row = mesh_manifest.get(mesh_guid, {})
                entry_key = f"mesh:{mesh_guid}:component:{component_guid}"
                entries[entry_key] = build_entry(
                    mesh_guid=mesh_guid,
                    component_guid=component_guid,
                    object_name=mesh_ref.get("name") or mesh_row.get("name") or mesh_guid,
                    component_name=component_name,
                    renderer=component["renderer"],
                    source_path=mesh_row.get("sourcePath") or mesh_ref.get("sourcePath") or component["sourcePath"],
                    source_type=mesh_row.get("sourceType") or install_source_type,
                    dimensions=dimensions,
                    bounds=mesh_row.get("bounds"),
                    bounds_mm=mesh_row.get("boundsMm"),
                    vertex_count=mesh_row.get("vertexCount"),
                    triangle_count=mesh_row.get("triangleCount"),
                    asset_path=mesh_row.get("assetPath"),
                    site_ids=site_ids,
                    connector_ids=connector_ids,
                    texture_ids=texture_ids,
                    textures=textures,
                    aliases=aliases,
                )
        else:
            entry_key = f"component:{component_guid}"
            entries[entry_key] = build_entry(
                mesh_guid=None,
                component_guid=component_guid,
                object_name=component_name,
                component_name=component_name,
                renderer=component["renderer"],
                source_path=component["sourcePath"],
                source_type=install_source_type,
                dimensions=dimensions,
                bounds=None,
                bounds_mm=None,
                vertex_count=None,
                triangle_count=None,
                asset_path=None,
                site_ids=site_ids,
                connector_ids=connector_ids,
                texture_ids=texture_ids,
                textures=textures,
                aliases=aliases,
            )


def _build_manifest_mesh_entries(
    *,
    mesh_manifest: dict[str, Any],
    mesh_component_pairs: set[tuple[str, str | None]],
    alias_map: dict[str, list[str]],
    entries: dict[str, dict[str, Any]],
) -> None:
    for mesh_guid, mesh_row in mesh_manifest.items():
        if any(pair[0] == mesh_guid for pair in mesh_component_pairs):
            continue
        entry_key = f"mesh:{mesh_guid}"
        entries[entry_key] = build_entry(
            mesh_guid=mesh_guid,
            component_guid=None,
            object_name=mesh_row.get("name") or mesh_guid,
            component_name=None,
            renderer=None,
            source_path=mesh_row.get("sourcePath"),
            source_type=mesh_row.get("sourceType") or classify_source_path(mesh_row.get("sourcePath"), mesh_row.get("archivePath")),
            dimensions=None,
            bounds=mesh_row.get("bounds"),
            bounds_mm=mesh_row.get("boundsMm"),
            vertex_count=mesh_row.get("vertexCount"),
            triangle_count=mesh_row.get("triangleCount"),
            asset_path=mesh_row.get("assetPath"),
            site_ids=[],
            connector_ids=[],
            texture_ids=[],
            textures=[],
            aliases=sorted(alias_map.get(mesh_row.get("name", ""), [])),
        )


def _build_directory_mesh_entries(
    *,
    meshes_dir: Path,
    datastore_root: Path,
    mesh_manifest: dict[str, Any],
    mesh_component_pairs: set[tuple[str, str | None]],
    install_source_type: str,
    entries: dict[str, dict[str, Any]],
) -> None:
    for mesh_path in sorted(meshes_dir.glob("*.xmsh")):
        mesh_guid = normalize_guid(mesh_path.stem)
        if not mesh_guid or mesh_guid in mesh_manifest or any(pair[0] == mesh_guid for pair in mesh_component_pairs):
            continue
        relative = mesh_path.relative_to(datastore_root).as_posix()
        entry_key = f"mesh:{mesh_guid}"
        entries[entry_key] = build_entry(
            mesh_guid=mesh_guid,
            component_guid=None,
            object_name=mesh_path.stem,
            component_name=None,
            renderer=None,
            source_path=relative,
            source_type=install_source_type,
            dimensions=None,
            bounds=None,
            bounds_mm=None,
            vertex_count=None,
            triangle_count=None,
            asset_path=f"{FLUENT_MESH_ASSET_PREFIX}/{mesh_guid}.glb",
            site_ids=[],
            connector_ids=[],
            texture_ids=[],
            textures=[],
            aliases=[],
        )


def _build_procedural_entries(
    *,
    procedural_assets: list[dict[str, Any]],
    mesh_manifest: dict[str, Any],
    entries: dict[str, dict[str, Any]],
) -> None:
    for procedural in procedural_assets:
        mesh_guid = procedural.get("meshGuid")
        component_guid = None
        entry_key = f"procedural:{procedural['id']}"
        mesh_row = mesh_manifest.get(mesh_guid, {}) if mesh_guid else {}
        entries[entry_key] = build_entry(
            mesh_guid=mesh_guid,
            component_guid=component_guid,
            object_name=procedural["name"],
            component_name=procedural["name"],
            renderer=None,
            source_path=procedural.get("sourcePath") or procedural.get("assetPath"),
            source_type="procedural",
            dimensions=procedural.get("dimensions"),
            bounds=mesh_row.get("bounds"),
            bounds_mm=mesh_row.get("boundsMm"),
            vertex_count=mesh_row.get("vertexCount"),
            triangle_count=mesh_row.get("triangleCount"),
            asset_path=procedural.get("assetPath"),
            site_ids=[],
            connector_ids=[],
            texture_ids=[],
            textures=[],
            aliases=sorted(set(procedural.get("aliases", []))),
        )


def build_registry(
    *,
    install_path: Path,
    manifest_path: Path | None,
    aliases_path: Path | None,
    procedural_catalog_path: Path | None,
    hardware_manifest_path: Path | None,
    texture_manifest_path: Path | None,
    refresh_index: bool,
    include_all_connectors: bool,
) -> dict[str, Any]:
    datastore_root, install_source_type = resolve_datastore_root(install_path)
    worktable_root = datastore_root / "SystemSpecific" / "Worktable"
    components_dir = worktable_root / "Components"
    meshes_dir = worktable_root / "Meshes"
    if not components_dir.exists():
        raise FileNotFoundError(f"Components directory not found at {components_dir}")

    mesh_manifest = load_mesh_manifest(manifest_path) if manifest_path and manifest_path.exists() else {}
    alias_map = load_alias_map(aliases_path) if aliases_path and aliases_path.exists() else {}
    hardware_textures = (
        load_hardware_texture_refs(hardware_manifest_path)
        if hardware_manifest_path and hardware_manifest_path.exists()
        else {}
    )
    procedural_assets = (
        load_procedural_catalog(procedural_catalog_path)
        if procedural_catalog_path and procedural_catalog_path.exists()
        else []
    )
    texture_catalog = (
        load_texture_manifest(texture_manifest_path)
        if texture_manifest_path and texture_manifest_path.exists()
        else {"textures": [], "byGuid": {}, "byName": {}}
    )

    catalog_rows = load_catalog_rows(
        datastore_root,
        refresh_index=refresh_index,
        include_all_connectors=include_all_connectors,
    )
    components_by_guid = {row["guid"]: row for row in catalog_rows["components"]}
    connectors_by_component = catalog_rows["connectors_by_component"]
    sites_by_component = catalog_rows["sites_by_component"]

    parsed_components = [
        parse_xcmp_file(path, install_source_type)
        for path in sorted(components_dir.glob("*.xcmp"))
    ]

    entries: dict[str, dict[str, Any]] = {}
    mesh_component_pairs: set[tuple[str, str | None]] = set()

    _build_component_entries(
        parsed_components=parsed_components,
        components_by_guid=components_by_guid,
        sites_by_component=sites_by_component,
        connectors_by_component=connectors_by_component,
        hardware_textures=hardware_textures,
        texture_catalog=texture_catalog,
        alias_map=alias_map,
        mesh_manifest=mesh_manifest,
        install_source_type=install_source_type,
        entries=entries,
        mesh_component_pairs=mesh_component_pairs,
    )

    _build_manifest_mesh_entries(
        mesh_manifest=mesh_manifest,
        mesh_component_pairs=mesh_component_pairs,
        alias_map=alias_map,
        entries=entries,
    )

    _build_directory_mesh_entries(
        meshes_dir=meshes_dir,
        datastore_root=datastore_root,
        mesh_manifest=mesh_manifest,
        mesh_component_pairs=mesh_component_pairs,
        install_source_type=install_source_type,
        entries=entries,
    )

    _build_procedural_entries(
        procedural_assets=procedural_assets,
        mesh_manifest=mesh_manifest,
        entries=entries,
    )

    ordered_entries = sorted(
        entries.values(),
        key=lambda row: (
            row.get("sourceType") or "",
            row.get("componentName") or "",
            row.get("objectName") or "",
            row.get("meshGuid") or "",
            row.get("componentGuid") or "",
        ),
    )

    mesh_entries = sum(1 for row in ordered_entries if row.get("meshGuid"))
    component_only = sum(1 for row in ordered_entries if row.get("componentGuid") and not row.get("meshGuid"))
    procedural_entries = sum(1 for row in ordered_entries if row.get("sourceType") == "procedural")

    return {
        "schemaVersion": REGISTRY_SCHEMA_VERSION,
        "kind": REGISTRY_KIND,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "installPath": str(datastore_root),
            "installSourceType": install_source_type,
            "productAuthority": False,
            "localRebuildOnly": True,
            "note": "Per-machine rebuild under models/fluent/local/ — not product law; do not commit.",
            "manifestPath": str(manifest_path) if manifest_path else None,
            "aliasesPath": str(aliases_path) if aliases_path else None,
            "proceduralCatalogPath": str(procedural_catalog_path) if procedural_catalog_path else None,
            "hardwareManifestPath": str(hardware_manifest_path) if hardware_manifest_path else None,
            "textureManifestPath": str(texture_manifest_path) if texture_manifest_path else None,
            "meshManifestCount": len(mesh_manifest),
            "componentCount": len(parsed_components),
        },
        "summary": {
            "entryCount": len(ordered_entries),
            "meshEntries": mesh_entries,
            "componentOnlyEntries": component_only,
            "proceduralEntries": procedural_entries,
            "aliasCount": len(alias_map),
            "textureCount": len(texture_catalog.get("textures", [])),
            "entriesWithTextures": sum(1 for row in ordered_entries if row.get("textures")),
        },
        "textures": texture_catalog.get("textures", []),
        "entries": ordered_entries,
    }


def build_entry(
    *,
    mesh_guid: str | None,
    component_guid: str | None,
    object_name: str | None,
    component_name: str | None,
    renderer: str | None,
    source_path: str | None,
    source_type: str | None,
    dimensions: dict[str, float | None] | None,
    bounds: dict[str, Any] | None,
    bounds_mm: dict[str, Any] | None,
    vertex_count: int | None,
    triangle_count: int | None,
    asset_path: str | None,
    site_ids: list[str],
    connector_ids: list[str],
    texture_ids: list[str],
    textures: list[dict[str, Any]],
    aliases: list[str],
    snap_anchors: list[dict[str, Any]] | None = None,
    child_connectors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "meshGuid": mesh_guid,
        "componentGuid": component_guid,
        "objectName": object_name,
        "componentName": component_name,
        "renderer": renderer or None,
        "sourcePath": source_path,
        "sourceType": source_type,
        "dimensions": dimensions,
        "bounds": bounds,
        "boundsMm": bounds_mm,
        "vertexCount": vertex_count,
        "triangleCount": triangle_count,
        "assetPath": asset_path,
        "siteIds": site_ids,
        "connectorIds": connector_ids,
        "textureIds": texture_ids,
        "textures": textures,
        "aliases": aliases,
        "snapAnchors": snap_anchors or [],
        "childConnectors": child_connectors or [],
    }


def resolve_datastore_root(source: Path) -> tuple[Path, str]:
    source = source.resolve()
    if (source / "SystemSpecific" / "Worktable" / "Components").exists():
        return source, classify_install_source_type(source)
    datastore = source / "DataStore"
    if (datastore / "SystemSpecific" / "Worktable" / "Components").exists():
        return datastore, "zeia"
    raise FileNotFoundError(
        f"Could not resolve a FluentControl worktable root from {source}. "
        "Pass --install pointing at the host database or an extracted DataStore folder."
    )


def classify_install_source_type(path: Path) -> str:
    env = os.environ.get("FLUENTCODER_FC_INSTALL")
    if env and path.resolve() == Path(env).resolve():
        return "host-db"
    normalized = os.path.normcase(str(path.resolve()))
    if any(marker in normalized for marker in HOST_INSTALL_MARKERS):
        return "host-db"
    if "datastore" in normalized:
        return "zeia"
    return "host-db"


def classify_source_path(source_path: str | None, archive_path: str | None = None) -> str:
    combined = os.path.normcase(" ".join(filter(None, [source_path, archive_path])))
    if archive_path and archive_path.lower().endswith(".zeia"):
        return "zeia"
    if "datastore/" in combined.replace("\\", "/"):
        return "zeia"
    if any(marker in combined for marker in HOST_INSTALL_MARKERS):
        return "host-db"
    return "zeia" if archive_path else "host-db"


def load_mesh_manifest(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    models = payload.get("models", [])
    archive_source = payload.get("source")
    by_guid: dict[str, dict[str, Any]] = {}
    for model in models:
        guid = normalize_guid(model.get("guid"))
        if not guid:
            continue
        by_guid[guid] = {
            "name": model.get("name"),
            "sourcePath": model.get("sourcePath"),
            "archivePath": model.get("archivePath") or archive_source,
            "assetPath": model.get("assetPath"),
            "bounds": model.get("bounds"),
            "boundsMm": model.get("boundsMm"),
            "vertexCount": model.get("vertexCount"),
            "triangleCount": model.get("triangleCount"),
            "sourceType": classify_source_path(model.get("sourcePath"), model.get("archivePath") or archive_source),
        }
    return by_guid


def load_alias_map(path: Path) -> dict[str, list[str]]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return load_alias_map_plaintext(path)

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    aliases = payload.get("labware_aliases") or {}
    inverted: dict[str, set[str]] = defaultdict(set)
    for alias, canonical in aliases.items():
        if not canonical:
            continue
        inverted[str(canonical)].add(str(alias))
        inverted[str(canonical)].add(str(canonical))
    return {key: sorted(values) for key, values in inverted.items()}


def load_alias_map_plaintext(path: Path) -> dict[str, list[str]]:
    inverted: dict[str, set[str]] = defaultdict(set)
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r'\s*"([^"]+)":\s*"([^"]+)"\s*,?\s*$', line)
        if not match:
            continue
        alias, canonical = match.group(1), match.group(2)
        inverted[canonical].add(alias)
        inverted[canonical].add(canonical)
    return {key: sorted(values) for key, values in inverted.items()}


def load_hardware_texture_refs(path: Path) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    refs: dict[str, set[str]] = defaultdict(set)
    for artifact in payload.get("asset_artifacts", []):
        component_guid = normalize_guid(artifact.get("component_guid"))
        if not component_guid:
            continue
        for asset_ref in artifact.get("asset_refs", []) or []:
            if asset_ref:
                refs[component_guid].add(str(asset_ref))
        object_name = artifact.get("object_name")
        if object_name:
            refs[component_guid].add(str(object_name))
    return {key: sorted(values) for key, values in refs.items()}


def load_procedural_catalog(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    catalog_section = extract_ts_array_section(text, "LABWARE_GEOMETRY_CATALOG")
    if not catalog_section:
        return []

    entries: list[dict[str, Any]] = []
    for chunk in split_ts_object_chunks(catalog_section):
        asset_path = extract_ts_asset_path(chunk, text)
        if not asset_path or asset_path.startswith("/models/fluent/"):
            continue
        name = extract_ts_string_field(chunk, "name") or asset_name_from_path(asset_path)
        mesh_guid = first_guid_hint(chunk, text, asset_path)
        dimensions = {
            "xMm": extract_ts_number_field(chunk, "physicalWidthMm"),
            "yMm": extract_ts_number_field(chunk, "physicalDepthMm"),
            "zMm": extract_ts_number_field(chunk, "physicalHeightMm"),
        }
        entries.append(
            {
                "id": slugify(name),
                "name": name,
                "aliases": extract_ts_string_array_field(chunk, "aliases"),
                "assetPath": asset_path,
                "sourcePath": asset_path,
                "meshGuid": mesh_guid,
                "dimensions": dimensions,
            }
        )
    return entries


def extract_ts_array_section(text: str, const_name: str) -> str:
    marker = f"const {const_name}"
    start = text.find(marker)
    if start < 0:
        return ""
    assign = text.find("=", start)
    if assign < 0:
        return ""
    bracket_start = text.find("[", assign)
    if bracket_start < 0:
        return ""
    depth = 0
    for index in range(bracket_start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[bracket_start + 1 : index]
    return ""


def split_ts_object_chunks(section: str) -> list[str]:
    chunks: list[str] = []
    depth = 0
    start = None
    for index, char in enumerate(section):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunks.append(section[start : index + 1])
                start = None
    return chunks


def extract_ts_string_field(chunk: str, field: str) -> str | None:
    match = re.search(rf'{field}:\s*"([^"]+)"', chunk)
    return match.group(1) if match else None


def extract_ts_number_field(chunk: str, field: str) -> float | None:
    match = re.search(rf"{field}:\s*([0-9]+(?:\.[0-9]+)?)", chunk)
    return float(match.group(1)) if match else None


def extract_ts_string_array_field(chunk: str, field: str) -> list[str]:
    match = re.search(rf"{field}:\s*\[([^\]]*)\]", chunk, re.S)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def extract_ts_asset_path(chunk: str, full_text: str) -> str | None:
    del full_text  # kept for call-site compatibility
    direct = re.search(r'modelAssetPath:\s*([^,\n]+)', chunk)
    if not direct:
        return None
    value = direct.group(1).strip()
    quoted = re.match(r'"([^"]+)"', value)
    if quoted:
        return quoted.group(1)
    helper = re.match(r"(labwareAsset|carrierAsset|deviceAsset|fluentAsset)\(([^)]+)\)", value)
    if not helper:
        return None
    fn_name, arg = helper.group(1), helper.group(2).strip().strip('"')
    base = {
        "labwareAsset": "/models/labware",
        "carrierAsset": "",  # dead path — do not invent /models/carriers/*.glb
        "deviceAsset": "/models/devices",
        "fluentAsset": FLUENT_MESH_ASSET_PREFIX,
    }[fn_name]
    if fn_name == "carrierAsset":
        return None
    if fn_name == "fluentAsset":
        # Mesh GUIDs come from ZEIA/install caches, not baked TS constants.
        guid = normalize_guid(arg) if GUID_RE.fullmatch(arg) else None
        return f"{FLUENT_MESH_ASSET_PREFIX}/{guid}.glb" if guid else None
    return f"{base}/{arg}"


def first_guid_hint(chunk: str, full_text: str, asset_path: str | None) -> str | None:
    del full_text  # kept for call-site compatibility; no host GUID map in TS catalog
    for hint in extract_ts_string_array_field(chunk, "meshGuidHints"):
        guid = normalize_guid(hint)
        if guid:
            return guid
    if asset_path and asset_path.startswith("/models/fluent/"):
        stem = Path(asset_path).stem
        guid = normalize_guid(stem)
        if guid:
            return guid
    return None


def asset_name_from_path(asset_path: str) -> str:
    stem = Path(asset_path).stem.replace("_", " ")
    return stem.strip() or "procedural asset"


def slugify(value: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", value.lower())) or "asset"


def load_catalog_rows(
    install_path: Path,
    *,
    refresh_index: bool,
    include_all_connectors: bool,
) -> dict[str, Any]:
    from fluentcoder.catalog.indexer import build_index
    from fluentcoder.catalog.paths import index_db_path_default, install_path_key

    install = install_path.resolve()
    db_path = index_db_path_default(install)
    if refresh_index:
        build_index(
            install,
            db_path,
            include_all_connectors=include_all_connectors,
        )
    if not db_path.exists():
        return {
            "components": [],
            "connectors_by_component": {},
            "sites_by_component": {},
        }

    install_key = install_path_key(install)
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            components = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT guid, name, file_path, dim_x_mm, dim_y_mm, dim_z_mm, site_count
                    FROM components
                    WHERE install_key = ?
                    """,
                    (install_key,),
                )
            ]
            connectors = conn.execute(
                """
                SELECT guid, component_guid, site_guid
                FROM connectors
                WHERE install_key = ?
                """,
                (install_key,),
            ).fetchall()
    except sqlite3.OperationalError:
        return {
            "components": [],
            "connectors_by_component": {},
            "sites_by_component": {},
        }

    sites_by_component: dict[str, set[str]] = defaultdict(set)
    connectors_by_component: dict[str, list[str]] = defaultdict(list)
    for row in connectors:
        component_guid = normalize_guid(row["component_guid"])
        connector_guid = normalize_guid(row["guid"])
        site_guid = normalize_guid(row["site_guid"])
        if component_guid and connector_guid:
            connectors_by_component[component_guid].append(connector_guid)
        if component_guid and site_guid:
            sites_by_component[component_guid].add(site_guid)

    for key in connectors_by_component:
        connectors_by_component[key] = sorted(set(connectors_by_component[key]))
    sites_by_component_out = {key: sorted(values) for key, values in sites_by_component.items()}
    return {
        "components": components,
        "connectors_by_component": connectors_by_component,
        "sites_by_component": sites_by_component_out,
    }


def parse_xcmp_file(path: Path, source_type: str) -> dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()
    payload = xml_first(root, "Payload")
    payload_data = xml_first(payload, "PayloadData") if payload is not None else None
    template = xml_first(payload_data, "CarrierOrLabwareTemplate") if payload_data is not None else None

    component_guid = normalize_guid(xml_text(template, "GUID")) or normalize_guid(path.stem)
    component_name = xml_text(payload, "ObjectName") or path.stem
    renderer = xml_text(template, "Renderer") if template is not None else ""
    dimensions = parse_dimensions(template)
    site_ids = []
    mesh_refs = []
    texture_ids = []
    texture_bindings = []

    if payload is not None:
        for ref in xml_children(payload, "Reference"):
            type_id = xml_text(ref, "TypeId")
            ref_guid = normalize_guid(xml_text(ref, "Guid") or xml_text(ref, "GUID"))
            ref_name = xml_text(ref, "ObjectName")
            if type_id == "WorktableSite" and ref_guid:
                site_ids.append(ref_guid)
            if type_id == "WorktableMesh" and ref_guid:
                mesh_refs.append(
                    {
                        "guid": ref_guid,
                        "name": ref_name or renderer or ref_guid,
                        "sourcePath": guess_mesh_source_path(ref_guid),
                    }
                )
            if type_id == "WorktableTexture":
                binding = {
                    "textureGuid": ref_guid,
                    "textureId": ref_name,
                    "position": "TOP",
                }
                texture_bindings.append(binding)
                if ref_name:
                    texture_ids.append(ref_name)
                if ref_guid:
                    texture_ids.append(ref_guid)

    if template is not None:
        texture_ids.extend(parse_texture_ids(template, renderer))
        texture_bindings.extend(parse_texture_bindings(template))

    relative_source = path.name
    try:
        install_root = path.parents[3]
        relative_source = path.relative_to(install_root).as_posix()
    except ValueError:
        pass

    return {
        "componentGuid": component_guid,
        "componentName": component_name,
        "renderer": renderer or None,
        "dimensions": dimensions,
        "siteIds": sorted(set(site_ids)),
        "meshRefs": mesh_refs,
        "textureIds": sorted(set(texture_ids)),
        "textureBindings": dedupe_texture_bindings(texture_bindings),
        "sourcePath": relative_source,
        "sourceType": source_type,
    }


def parse_dimensions(template: ET.Element | None) -> dict[str, float | None] | None:
    if template is None:
        return None
    dimension = xml_first(template, "Dimension")
    if dimension is None:
        return None
    return {
        "xMm": parse_float(xml_text(dimension, "X")),
        "yMm": parse_float(xml_text(dimension, "Y")),
        "zMm": parse_float(xml_text(dimension, "Z")),
    }


def dimensions_from_catalog(catalog: dict[str, Any]) -> dict[str, float | None] | None:
    if not catalog:
        return None
    return {
        "xMm": catalog.get("dim_x_mm"),
        "yMm": catalog.get("dim_y_mm"),
        "zMm": catalog.get("dim_z_mm"),
    }


def parse_texture_ids(template: ET.Element, renderer: str) -> list[str]:
    texture_ids: list[str] = []
    if renderer:
        texture_ids.append(renderer)
    collection = xml_first(template, "TextureCollection")
    if collection is None:
        return texture_ids
    for node in collection.iter():
        local = local_name(node.tag)
        if local in {"Key", "ObjectName", "TextureName", "TexturName", "Name"}:
            value = "".join(node.itertext()).strip()
            if value and not looks_like_guid(value):
                texture_ids.append(value)
        if local in {"Guid", "GUID"}:
            guid = normalize_guid("".join(node.itertext()).strip())
            if guid:
                texture_ids.append(guid)
    return texture_ids


def parse_texture_bindings(template: ET.Element) -> list[dict[str, str | None]]:
    bindings: list[dict[str, str | None]] = []
    collection = xml_first(template, "TextureCollection")
    if collection is None:
        return bindings
    for node in collection.iter():
        if local_name(node.tag) != "CarrierOrLabwareTexture":
            continue
        texture_id = xml_text(node, "TexturName") or xml_text(node, "TextureName") or xml_text(node, "ObjectName")
        texture_guid = normalize_guid(xml_text(node, "Guid") or xml_text(node, "GUID"))
        position = (xml_text(node, "Position") or "TOP").upper()
        if texture_id or texture_guid:
            bindings.append(
                {
                    "textureId": texture_id or None,
                    "textureGuid": texture_guid or None,
                    "position": position,
                }
            )
    return bindings


def dedupe_texture_bindings(bindings: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
    alias_to_key: dict[str, str] = {}
    merged: dict[str, dict[str, str | None]] = {}

    def register_alias(alias: str, key: str) -> None:
        if alias:
            alias_to_key[alias] = key

    def resolve_key(binding: dict[str, str | None]) -> str:
        texture_guid = normalize_guid(binding.get("textureGuid"))
        texture_id = canonical_texture_key(str(binding.get("textureId") or ""))
        for alias in (f"guid:{texture_guid}" if texture_guid else "", f"name:{texture_id}" if texture_id else ""):
            if alias and alias in alias_to_key:
                return alias_to_key[alias]
        if texture_guid:
            return f"guid:{texture_guid}"
        if texture_id:
            return f"name:{texture_id}"
        return ""

    for binding in bindings:
        key = resolve_key(binding)
        if not key:
            continue
        texture_guid = normalize_guid(binding.get("textureGuid"))
        texture_id = str(binding.get("textureId") or "").strip()
        row = merged.setdefault(
            key,
            {
                "textureId": texture_id or None,
                "textureGuid": texture_guid or None,
                "position": (binding.get("position") or "TOP").upper(),
            },
        )
        if texture_id and not row.get("textureId"):
            row["textureId"] = texture_id
        if texture_guid and not row.get("textureGuid"):
            row["textureGuid"] = texture_guid
        register_alias(f"guid:{texture_guid}", key)
        register_alias(f"name:{canonical_texture_key(texture_id)}", key)

    return list(merged.values())


def load_texture_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    textures = payload.get("textures") or []
    by_guid: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    normalized_textures: list[dict[str, Any]] = []
    for row in textures:
        if not isinstance(row, dict):
            continue
        entry = dict(row)
        guid = normalize_guid(entry.get("textureGuid"))
        asset = str(entry.get("assetPath") or "").replace("\\", "/")
        # Rewrite legacy /models/fluent/textures/... → local rebuild prefix.
        if asset.startswith("/models/fluent/textures/"):
            entry["assetPath"] = FLUENT_TEXTURE_ASSET_PREFIX + asset[len("/models/fluent/textures") :]
        elif guid and not asset:
            entry["assetPath"] = f"{FLUENT_TEXTURE_ASSET_PREFIX}/{guid}.jpg"
        if guid:
            by_guid[guid] = entry
        name_key = canonical_texture_key(str(entry.get("objectName") or ""))
        if name_key:
            by_name[name_key] = entry
        normalized_textures.append(entry)
    return {"textures": normalized_textures, "byGuid": by_guid, "byName": by_name}


def resolve_texture_bindings(
    bindings: list[dict[str, str | None]],
    texture_catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    by_guid = texture_catalog.get("byGuid") or {}
    by_name = texture_catalog.get("byName") or {}
    for binding in bindings:
        texture_guid = normalize_guid(binding.get("textureGuid"))
        texture_id = str(binding.get("textureId") or "").strip()
        row = by_guid.get(texture_guid) if texture_guid else None
        if row is None and texture_id:
            row = by_name.get(canonical_texture_key(texture_id))
        resolved.append(
            {
                "textureId": texture_id or (row or {}).get("objectName"),
                "textureGuid": texture_guid or normalize_guid((row or {}).get("textureGuid")),
                "position": binding.get("position") or "TOP",
                "assetPath": (row or {}).get("assetPath"),
                "format": (row or {}).get("format"),
            }
        )
    return resolved


def canonical_texture_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def guess_mesh_source_path(mesh_guid: str) -> str:
    return f"SystemSpecific/Worktable/Meshes/{mesh_guid}.xmsh"


def xml_first(root: ET.Element | None, name: str) -> ET.Element | None:
    if root is None:
        return None
    for node in root.iter():
        if local_name(node.tag) == name:
            return node
    return None


def xml_children(root: ET.Element, name: str) -> list[ET.Element]:
    return [node for node in root if local_name(node.tag) == name]


def xml_text(root: ET.Element | None, name: str) -> str:
    node = xml_first(root, name)
    return "".join(node.itertext()).strip() if node is not None else ""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_guid(value: str | None) -> str:
    match = GUID_RE.search(value or "")
    return match.group(0).lower() if match else ""


def looks_like_guid(value: str) -> bool:
    return bool(GUID_RE.fullmatch(value.strip()))


if __name__ == "__main__":
    raise SystemExit(main())
