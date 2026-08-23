#!/usr/bin/env python3
"""Compare Fluent mesh coverage across host DB, ZEIA export, GLBs, and registry."""

from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[3]

from tecan_tools import build_fluent_registry as registry_builder
from tecan_tools import extract_fluent_meshes as mesh_extractor
from tecan_tools import merge_fluent_mesh_libraries as mesh_merge

DEFAULT_HOST_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
DEFAULT_FLUENT_MODELS = (
    PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent"
)
DEFAULT_SIM_DIR = DEFAULT_FLUENT_MODELS / "local"
DEFAULT_ZEIA_MANIFEST = DEFAULT_SIM_DIR / "manifest.sim.bak.json"
DEFAULT_REGISTRY = DEFAULT_SIM_DIR / "registry.json"
DEFAULT_MANIFEST = DEFAULT_SIM_DIR / "manifest.json"
DEFAULT_HARDWARE_MANIFEST = None
DEFAULT_ZEIA_ARCHIVE = None
FLUENT_MESH_ASSET_PREFIX = "/models/fluent/local"


def discover_ready_zeia(repo_root: Path) -> Path | None:
    env_path = os.environ.get("TECAN_SIMULATOR_SAMPLE_ZEIA", "").strip()
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        return candidate if candidate.is_file() else None
    ready_root = repo_root / "ready-to-import"
    if not ready_root.is_dir():
        return None
    found: list[Path] = []
    for bundle in sorted(ready_root.iterdir()):
        if not bundle.is_dir() or bundle.name.startswith("."):
            continue
        for rel in (
            ("source", "original-sources"),
            ("original_sources",),
            ("source", "original_sources"),
        ):
            folder = bundle.joinpath(*rel)
            if folder.is_dir():
                found.extend(sorted(folder.glob("*.zeia")))
    return found[0] if found else None


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
DIAGNOSTIC_SCHEMA_VERSION = 1
DIAGNOSTIC_KIND = "fluent-model-coverage-diagnostic"
GUID_RE = registry_builder.GUID_RE


@dataclass
class MeshRecord:
    guid: str
    name: str
    source: str
    source_path: str | None = None
    checksum: str | None = None
    asset_path: str | None = None
    conversion_status: str | None = None
    error: str | None = None


@dataclass
class ComponentRecord:
    guid: str
    name: str
    source: str
    source_path: str
    mesh_guids: list[str] = field(default_factory=list)
    texture_ids: list[str] = field(default_factory=list)
    renderer: str | None = None


@dataclass
class DiagnosticContext:
    host_meshes: dict[str, MeshRecord]
    zeia_meshes: dict[str, MeshRecord]
    glb_guids: set[str]
    manifest_models: dict[str, dict[str, Any]]
    registry_entries: list[dict[str, Any]]
    host_components: dict[str, ComponentRecord]
    zeia_components: dict[str, ComponentRecord]
    decoded_textures: dict[str, str]
    sim_dir: Path
    manifest_path: Path | None


def main() -> int:
    args = parse_args()
    report = build_coverage_report(
        host_install=Path(args.host_install),
        sim_dir=Path(args.sim_dir),
        zeia_manifest_path=Path(args.zeia_manifest) if args.zeia_manifest else None,
        zeia_archive_path=Path(args.zeia_archive) if args.zeia_archive else None,
        registry_path=Path(args.registry) if args.registry else None,
        manifest_path=Path(args.manifest) if args.manifest else None,
        hardware_manifest_path=(
            Path(args.hardware_manifest) if args.hardware_manifest else None
        ),
        hardware_asset_dirs=[Path(value) for value in args.hardware_asset_dir],
    )

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    markdown_path = (
        Path(args.markdown_out) if args.markdown_out else output_path.with_suffix(".md")
    )
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")

    summary = report["summary"]
    print(
        "Model coverage diagnostic: "
        f"host={summary['hostMeshCount']} zeia={summary['zeiaMeshCount']} "
        f"glb={summary['glbCount']} registryMesh={summary['registryMeshCount']} "
        f"hostOnly={summary['hostOnlyCount']} zeiaOnly={summary['zeiaOnlyCount']} "
        f"missingGlb={summary['missingGlbCount']} "
        f"duplicateNames={summary['duplicateNameCount']} "
        f"guidConflicts={summary['guidConflictCount']} "
        f"componentsWithoutMesh={summary['componentsWithoutMeshCount']} "
        f"texturesNotDecoded={summary['texturesNotDecodedCount']} "
        f"failedConversions={summary['failedConversionCount']} "
        f"-> {output_path}"
    )
    return 1 if summary["failedConversionCount"] or summary["missingGlbCount"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-install", default=str(DEFAULT_HOST_INSTALL))
    parser.add_argument("--sim-dir", default=str(DEFAULT_SIM_DIR))
    parser.add_argument("--zeia-manifest", default=str(DEFAULT_ZEIA_MANIFEST))
    parser.add_argument("--zeia-archive", default="")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--hardware-manifest", default="")
    parser.add_argument(
        "--hardware-asset-dir",
        action="append",
        default=[],
        help="Directory containing decoded hardware texture/image assets. Repeatable.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_SIM_DIR / "model_coverage_report.json"),
        help="JSON diagnostic output path.",
    )
    parser.add_argument(
        "--markdown-out",
        default="",
        help="Optional markdown report path. Defaults to --out with .md suffix.",
    )
    return parser.parse_args()


def build_coverage_report(
    *,
    host_install: Path,
    sim_dir: Path,
    zeia_manifest_path: Path | None,
    zeia_archive_path: Path | None,
    registry_path: Path | None,
    manifest_path: Path | None,
    hardware_manifest_path: Path | None,
    hardware_asset_dirs: list[Path],
) -> dict[str, Any]:
    host_meshes = load_host_meshes(host_install)
    zeia_meshes = load_zeia_meshes(zeia_manifest_path, zeia_archive_path)
    glb_guids = load_glb_guids(sim_dir)
    manifest_models = load_manifest_models(manifest_path)
    registry_entries = load_registry_entries(registry_path)
    host_components = load_components(host_install, "host-db")
    zeia_components = load_zeia_components(zeia_manifest_path, zeia_archive_path)
    decoded_textures = load_decoded_texture_index(
        hardware_manifest_path, hardware_asset_dirs, sim_dir
    )

    ctx = DiagnosticContext(
        host_meshes=host_meshes,
        zeia_meshes=zeia_meshes,
        glb_guids=glb_guids,
        manifest_models=manifest_models,
        registry_entries=registry_entries,
        host_components=host_components,
        zeia_components=zeia_components,
        decoded_textures=decoded_textures,
        sim_dir=sim_dir,
        manifest_path=manifest_path,
    )

    host_only = sorted(host_meshes.keys() - zeia_meshes.keys())
    zeia_only = sorted(zeia_meshes.keys() - host_meshes.keys())

    missing_glbs = build_missing_glbs(ctx)
    duplicate_names = build_duplicate_names(ctx)
    guid_conflicts = build_guid_conflicts(ctx)
    components_without_mesh = build_components_without_mesh(ctx)
    textures_not_decoded = build_textures_not_decoded(ctx)
    failed_conversions = build_failed_conversions(ctx)

    return {
        "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "kind": DIAGNOSTIC_KIND,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "hostInstall": str(host_install),
            "simDir": str(sim_dir),
            "zeiaManifestPath": (
                str(zeia_manifest_path)
                if zeia_manifest_path and zeia_manifest_path.exists()
                else None
            ),
            "zeiaArchivePath": (
                str(zeia_archive_path)
                if zeia_archive_path and zeia_archive_path.exists()
                else None
            ),
            "registryPath": (
                str(registry_path) if registry_path and registry_path.exists() else None
            ),
            "manifestPath": (
                str(manifest_path) if manifest_path and manifest_path.exists() else None
            ),
            "hardwareManifestPath": (
                str(hardware_manifest_path)
                if hardware_manifest_path and hardware_manifest_path.exists()
                else None
            ),
            "hardwareAssetDirs": [
                str(path) for path in hardware_asset_dirs if path.exists()
            ],
        },
        "summary": {
            "hostMeshCount": len(host_meshes),
            "zeiaMeshCount": len(zeia_meshes),
            "glbCount": len(glb_guids),
            "manifestModelCount": len(manifest_models),
            "registryEntryCount": len(registry_entries),
            "registryMeshCount": sum(
                1 for entry in registry_entries if entry.get("meshGuid")
            ),
            "hostOnlyCount": len(host_only),
            "zeiaOnlyCount": len(zeia_only),
            "missingGlbCount": len(missing_glbs),
            "duplicateNameCount": len(duplicate_names),
            "guidConflictCount": len(guid_conflicts),
            "componentsWithoutMeshCount": len(components_without_mesh),
            "texturesNotDecodedCount": len(textures_not_decoded),
            "failedConversionCount": len(failed_conversions),
            "decodedTextureFileCount": len(decoded_textures),
        },
        "hostOnly": [serialize_mesh(host_meshes[guid]) for guid in host_only],
        "zeiaOnly": [serialize_mesh(zeia_meshes[guid]) for guid in zeia_only],
        "missingGlbs": missing_glbs,
        "duplicateNames": duplicate_names,
        "guidConflicts": guid_conflicts,
        "componentsWithoutMesh": components_without_mesh,
        "texturesNotDecoded": textures_not_decoded,
        "failedConversions": failed_conversions,
    }


def load_host_meshes(host_install: Path) -> dict[str, MeshRecord]:
    meshes_dir = mesh_merge.resolve_host_meshes_dir(host_install)
    records: dict[str, MeshRecord] = {}
    for path in sorted(meshes_dir.glob("*.xmsh")):
        guid = normalize_guid(path.stem)
        if not guid:
            continue
        metadata = mesh_merge.read_host_mesh_metadata(path)
        records[guid] = MeshRecord(
            guid=guid,
            name=metadata["name"],
            source="host-db",
            source_path=metadata["source_path"],
            checksum=metadata.get("checksum"),
            asset_path=f"{FLUENT_MESH_ASSET_PREFIX}/{guid}.glb",
        )
    return records


def load_zeia_meshes(
    zeia_manifest_path: Path | None, zeia_archive_path: Path | None
) -> dict[str, MeshRecord]:
    records: dict[str, MeshRecord] = {}
    if zeia_manifest_path and zeia_manifest_path.exists():
        payload = json.loads(zeia_manifest_path.read_text(encoding="utf-8"))
        for model in payload.get("models", []):
            guid = normalize_guid(model.get("guid"))
            if not guid:
                continue
            records[guid] = MeshRecord(
                guid=guid,
                name=str(model.get("name") or guid),
                source="zeia-manifest",
                source_path=model.get("sourcePath"),
                checksum=model.get("checksum"),
                asset_path=model.get("assetPath"),
                conversion_status=model.get("conversionStatus"),
            )
        if records:
            return records

    archive = resolve_zeia_archive(zeia_archive_path, zeia_manifest_path)
    if archive is None:
        return records

    if archive.is_file() and zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".xmsh"):
                    continue
                if "worktable/meshes/" not in name.replace("\\", "/").lower():
                    continue
                text = zf.read(name).decode("utf-8-sig", errors="replace")
                record = mesh_record_from_xmsh_text(name, text, "zeia-archive")
                if record:
                    records[record.guid] = record
        return records

    meshes_dir = archive / "SystemSpecific" / "Worktable" / "Meshes"
    if not meshes_dir.exists():
        meshes_dir = archive / "DataStore" / "SystemSpecific" / "Worktable" / "Meshes"
    if meshes_dir.exists():
        for path in sorted(meshes_dir.glob("*.xmsh")):
            text = path.read_text(encoding="utf-8-sig")
            record = mesh_record_from_xmsh_text(
                str(path.relative_to(archive)), text, "zeia-extract"
            )
            if record:
                records[record.guid] = record
    return records


def mesh_record_from_xmsh_text(
    source_path: str, text: str, source: str
) -> MeshRecord | None:
    item = mesh_extractor.SourceMesh(path=source_path, text=text)
    try:
        metadata = mesh_extractor.parse_xmsh(item)
    except Exception:
        return None
    guid = normalize_guid(metadata.guid)
    if not guid:
        return None
    return MeshRecord(
        guid=guid,
        name=metadata.name,
        source=source,
        source_path=source_path,
        checksum=metadata.checksum or None,
        asset_path=f"{FLUENT_MESH_ASSET_PREFIX}/{guid}.glb",
    )


def resolve_zeia_archive(
    zeia_archive_path: Path | None, zeia_manifest_path: Path | None
) -> Path | None:
    if zeia_archive_path and zeia_archive_path.exists():
        return zeia_archive_path
    if zeia_manifest_path and zeia_manifest_path.exists():
        payload = json.loads(zeia_manifest_path.read_text(encoding="utf-8"))
        archive_hint = payload.get("source") or payload.get("archivePath")
        if archive_hint:
            candidate = Path(str(archive_hint))
            if candidate.exists():
                return candidate
    discovered = discover_ready_zeia(PROJECT_ROOT)
    if discovered is not None and discovered.exists():
        return discovered
    return None


def load_glb_guids(sim_dir: Path) -> set[str]:
    return {
        normalize_guid(path.stem)
        for path in sim_dir.glob("*.glb")
        if normalize_guid(path.stem)
    }


def load_manifest_models(manifest_path: Path | None) -> dict[str, dict[str, Any]]:
    if not manifest_path or not manifest_path.exists():
        return {}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    models: dict[str, dict[str, Any]] = {}
    for model in payload.get("models", []):
        guid = normalize_guid(model.get("guid"))
        if guid:
            models[guid] = model
    return models


def load_registry_entries(registry_path: Path | None) -> list[dict[str, Any]]:
    if not registry_path or not registry_path.exists():
        return []
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    return list(payload.get("entries", []))


def load_components(root: Path, source: str) -> dict[str, ComponentRecord]:
    components_dir = root / "SystemSpecific" / "Worktable" / "Components"
    if not components_dir.exists():
        components_dir = (
            root / "DataStore" / "SystemSpecific" / "Worktable" / "Components"
        )
    if not components_dir.exists():
        return {}
    records: dict[str, ComponentRecord] = {}
    for path in sorted(components_dir.glob("*.xcmp")):
        parsed = registry_builder.parse_xcmp_file(path, source)
        guid = parsed["componentGuid"]
        records[guid] = ComponentRecord(
            guid=guid,
            name=parsed["componentName"],
            source=source,
            source_path=parsed["sourcePath"],
            mesh_guids=[ref["guid"] for ref in parsed["meshRefs"]],
            texture_ids=list(parsed["textureIds"]),
            renderer=parsed.get("renderer"),
        )
    return records


def load_zeia_components(
    zeia_manifest_path: Path | None, zeia_archive_path: Path | None
) -> dict[str, ComponentRecord]:
    archive = resolve_zeia_archive(zeia_archive_path, zeia_manifest_path)
    if archive is None:
        return {}
    if archive.is_file() and zipfile.is_zipfile(archive):
        return load_components_from_zip(archive)
    return load_components(archive, "zeia-extract")


def load_components_from_zip(archive_path: Path) -> dict[str, ComponentRecord]:
    records: dict[str, ComponentRecord] = {}
    with zipfile.ZipFile(archive_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xcmp"):
                continue
            if "worktable/components/" not in name.replace("\\", "/").lower():
                continue
            text = zf.read(name).decode("utf-8-sig", errors="replace")
            parsed = parse_xcmp_text(name, text, "zeia-archive")
            records[parsed.guid] = parsed
    return records


def parse_xcmp_text(source_path: str, text: str, source: str) -> ComponentRecord:
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".xcmp", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    try:
        parsed = registry_builder.parse_xcmp_file(temp_path, source)
    finally:
        temp_path.unlink(missing_ok=True)
    return ComponentRecord(
        guid=parsed["componentGuid"],
        name=parsed["componentName"],
        source=source,
        source_path=source_path,
        mesh_guids=[ref["guid"] for ref in parsed["meshRefs"]],
        texture_ids=list(parsed["textureIds"]),
        renderer=parsed.get("renderer"),
    )


def load_decoded_texture_index(
    hardware_manifest_path: Path | None,
    hardware_asset_dirs: list[Path],
    sim_dir: Path,
) -> dict[str, str]:
    decoded: dict[str, str] = {}

    def add_file(path: Path) -> None:
        decoded[canonical_texture_key(path.name)] = str(path)
        decoded[canonical_texture_key(path.stem)] = str(path)

    search_dirs = list(hardware_asset_dirs)
    if hardware_manifest_path and hardware_manifest_path.exists():
        search_dirs.append(hardware_manifest_path.parent / "assets")
    search_dirs.extend(
        [
            sim_dir,
            sim_dir / "textures",
            DEFAULT_FLUENT_MODELS / "textures",
        ]
    )
    ready_root = PROJECT_ROOT / "ready-to-import"
    if ready_root.is_dir():
        for bundle in sorted(ready_root.iterdir()):
            assets = bundle / "source" / "hardware" / "assets"
            if assets.is_dir():
                search_dirs.append(assets)

    for directory in search_dirs:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                add_file(path)

    if hardware_manifest_path and hardware_manifest_path.exists():
        payload = json.loads(hardware_manifest_path.read_text(encoding="utf-8"))
        for artifact in payload.get("asset_artifacts", []):
            bundle_path = artifact.get("bundle_path") or artifact.get("object_name")
            source_path = artifact.get("source_path") or artifact.get("key")
            for candidate in (bundle_path, source_path):
                if not candidate:
                    continue
                basename = Path(str(candidate)).name
                key = canonical_texture_key(basename)
                if key and key not in decoded:
                    decoded[key] = str(candidate)
    return decoded


def build_missing_glbs(ctx: DiagnosticContext) -> list[dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}

    def expect(
        guid: str, name: str, source: str, asset_path: str | None = None
    ) -> None:
        if not guid:
            return
        expected.setdefault(
            guid,
            {
                "guid": guid,
                "name": name,
                "sources": [],
                "assetPath": asset_path or f"{FLUENT_MESH_ASSET_PREFIX}/{guid}.glb",
            },
        )
        if source not in expected[guid]["sources"]:
            expected[guid]["sources"].append(source)

    for guid, record in ctx.host_meshes.items():
        expect(guid, record.name, "host-db", record.asset_path)
    for guid, record in ctx.zeia_meshes.items():
        expect(guid, record.name, "zeia", record.asset_path)
    for guid, model in ctx.manifest_models.items():
        expect(guid, str(model.get("name") or guid), "manifest", model.get("assetPath"))
    for entry in ctx.registry_entries:
        guid = normalize_guid(entry.get("meshGuid"))
        if not guid or entry.get("sourceType") == "procedural":
            continue
        expect(
            guid,
            str(entry.get("objectName") or entry.get("componentName") or guid),
            "registry",
            entry.get("assetPath"),
        )

    missing: list[dict[str, Any]] = []
    for guid, payload in sorted(expected.items()):
        if guid in ctx.glb_guids:
            continue
        glb_path = ctx.sim_dir / f"{guid}.glb"
        missing.append(
            {
                **payload,
                "glbPath": str(glb_path),
                "glbExists": glb_path.exists(),
            }
        )
    return missing


def build_duplicate_names(ctx: DiagnosticContext) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"host": set(), "zeia": set(), "manifest": set(), "registry": set()}
    )

    def add(name: str, source: str, guid: str) -> None:
        key = canonical_name(name)
        if not key:
            return
        by_name[key][source].add(guid)

    for guid, record in ctx.host_meshes.items():
        add(record.name, "host", guid)
    for guid, record in ctx.zeia_meshes.items():
        add(record.name, "zeia", guid)
    for guid, model in ctx.manifest_models.items():
        add(str(model.get("name") or guid), "manifest", guid)
    for entry in ctx.registry_entries:
        guid = normalize_guid(entry.get("meshGuid"))
        if not guid:
            continue
        add(
            str(entry.get("objectName") or entry.get("componentName") or guid),
            "registry",
            guid,
        )

    duplicates: list[dict[str, Any]] = []
    for name, buckets in sorted(by_name.items()):
        guids = sorted(set().union(*buckets.values()))
        if len(guids) <= 1:
            continue
        duplicates.append(
            {
                "name": name,
                "guids": guids,
                "hostGuids": sorted(buckets["host"]),
                "zeiaGuids": sorted(buckets["zeia"]),
                "manifestGuids": sorted(buckets["manifest"]),
                "registryGuids": sorted(buckets["registry"]),
            }
        )
    return duplicates


def build_guid_conflicts(ctx: DiagnosticContext) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    all_guids = sorted(
        set(ctx.host_meshes) | set(ctx.zeia_meshes) | set(ctx.manifest_models)
    )
    for guid in all_guids:
        host = ctx.host_meshes.get(guid)
        zeia = ctx.zeia_meshes.get(guid)
        manifest = ctx.manifest_models.get(guid)
        names = unique_strings(
            [
                host.name if host else None,
                zeia.name if zeia else None,
                str(manifest.get("name")) if manifest else None,
            ]
        )
        checksums = unique_strings(
            [
                host.checksum if host else None,
                zeia.checksum if zeia else None,
                str(manifest.get("checksum")) if manifest else None,
            ]
        )
        if len(names) <= 1 and len(checksums) <= 1:
            continue
        conflicts.append(
            {
                "guid": guid,
                "hostName": host.name if host else None,
                "zeiaName": zeia.name if zeia else None,
                "manifestName": manifest.get("name") if manifest else None,
                "hostChecksum": host.checksum if host else None,
                "zeiaChecksum": zeia.checksum if zeia else None,
                "manifestChecksum": manifest.get("checksum") if manifest else None,
                "nameMismatch": len(names) > 1,
                "checksumMismatch": len(checksums) > 1,
            }
        )
    return conflicts


def build_components_without_mesh(ctx: DiagnosticContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_name, components in (
        ("host-db", ctx.host_components),
        ("zeia", ctx.zeia_components),
    ):
        for guid, component in sorted(
            components.items(), key=lambda item: item[1].name.lower()
        ):
            if component.mesh_guids:
                continue
            key = f"{source_name}:{guid}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "componentGuid": guid,
                    "componentName": component.name,
                    "source": source_name,
                    "sourcePath": component.source_path,
                    "renderer": component.renderer,
                    "textureIds": component.texture_ids,
                }
            )
    return rows


def build_textures_not_decoded(ctx: DiagnosticContext) -> list[dict[str, Any]]:
    referenced: dict[str, dict[str, Any]] = {}

    def add_texture(
        texture_id: str,
        source: str,
        component_guid: str | None,
        component_name: str | None,
    ) -> None:
        key = canonical_texture_key(texture_id)
        if not key or looks_like_guid(key):
            return
        row = referenced.setdefault(
            key,
            {
                "textureId": texture_id,
                "sources": [],
                "componentGuids": [],
                "componentNames": [],
            },
        )
        if source not in row["sources"]:
            row["sources"].append(source)
        if component_guid and component_guid not in row["componentGuids"]:
            row["componentGuids"].append(component_guid)
        if component_name and component_name not in row["componentNames"]:
            row["componentNames"].append(component_name)

    for components, source in (
        (ctx.host_components, "host-db"),
        (ctx.zeia_components, "zeia"),
    ):
        for component in components.values():
            for texture_id in component.texture_ids:
                add_texture(texture_id, source, component.guid, component.name)

    for entry in ctx.registry_entries:
        component_guid = normalize_guid(entry.get("componentGuid"))
        component_name = entry.get("componentName")
        for texture_id in entry.get("textureIds") or []:
            add_texture(texture_id, "registry", component_guid, component_name)

    missing: list[dict[str, Any]] = []
    for key, row in sorted(referenced.items()):
        if key in ctx.decoded_textures:
            continue
        missing.append({**row, "decodedPath": None})
    return missing


def build_failed_conversions(ctx: DiagnosticContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if ctx.manifest_path and ctx.manifest_path.exists():
        payload = json.loads(ctx.manifest_path.read_text(encoding="utf-8"))
        for model in payload.get("models", []):
            status = str(model.get("conversionStatus") or "").lower()
            if status in {"failed", "placeholder"} or model.get("error"):
                guid = normalize_guid(model.get("guid"))
                rows.append(
                    {
                        "guid": guid or None,
                        "name": model.get("name") or model.get("sourcePath"),
                        "conversionStatus": model.get("conversionStatus"),
                        "error": model.get("error"),
                        "sourcePath": model.get("sourcePath"),
                        "assetPath": model.get("assetPath"),
                    }
                )
    for guid, model in ctx.manifest_models.items():
        status = str(model.get("conversionStatus") or "").lower()
        if status not in {"converted", "copied-glb"} and not any(
            row.get("guid") == guid for row in rows
        ):
            rows.append(
                {
                    "guid": guid,
                    "name": model.get("name"),
                    "conversionStatus": model.get("conversionStatus"),
                    "error": model.get("error"),
                    "sourcePath": model.get("sourcePath"),
                    "assetPath": model.get("assetPath"),
                }
            )
    return rows


def serialize_mesh(record: MeshRecord) -> dict[str, Any]:
    return {
        "guid": record.guid,
        "name": record.name,
        "source": record.source,
        "sourcePath": record.source_path,
        "checksum": record.checksum,
        "assetPath": record.asset_path,
        "conversionStatus": record.conversion_status,
        "error": record.error,
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Fluent Model Coverage Diagnostic",
        "",
        f"- Generated: `{report['generatedAt']}`",
        f"- Host meshes: `{summary['hostMeshCount']}`",
        f"- ZEIA meshes: `{summary['zeiaMeshCount']}`",
        f"- GLB files: `{summary['glbCount']}`",
        f"- Registry mesh entries: `{summary['registryMeshCount']}`",
        f"- Host-only: `{summary['hostOnlyCount']}`",
        f"- ZEIA-only: `{summary['zeiaOnlyCount']}`",
        f"- Missing GLBs: `{summary['missingGlbCount']}`",
        f"- Duplicate names: `{summary['duplicateNameCount']}`",
        f"- GUID conflicts: `{summary['guidConflictCount']}`",
        f"- Components without mesh: `{summary['componentsWithoutMeshCount']}`",
        f"- Textures not decoded: `{summary['texturesNotDecodedCount']}`",
        f"- Failed conversions: `{summary['failedConversionCount']}`",
        "",
        "## Host-Only Models",
        "",
    ]
    lines.extend(format_mesh_list(report["hostOnly"]))
    lines.extend(["", "## ZEIA-Only Models", ""])
    lines.extend(format_mesh_list(report["zeiaOnly"]))
    lines.extend(["", "## Missing GLBs", ""])
    lines.extend(
        format_generic_list(
            report["missingGlbs"],
            lambda item: f"- `{item['guid']}` {item['name']} ({', '.join(item['sources'])})",
        )
    )
    lines.extend(["", "## Duplicate Object Names", ""])
    lines.extend(
        format_generic_list(
            report["duplicateNames"],
            lambda item: f"- **{item['name']}** -> `{', '.join(item['guids'])}`",
        )
    )
    lines.extend(["", "## GUID Conflicts", ""])
    lines.extend(
        format_generic_list(
            report["guidConflicts"],
            lambda item: f"- `{item['guid']}` names=host:{item.get('hostName')} zeia:{item.get('zeiaName')} manifest:{item.get('manifestName')}",
        )
    )
    lines.extend(["", "## Components With No Mesh", ""])
    lines.extend(
        format_generic_list(
            report["componentsWithoutMesh"][:40],
            lambda item: f"- `{item['componentGuid']}` {item['componentName']} ({item['source']})",
        )
    )
    if len(report["componentsWithoutMesh"]) > 40:
        lines.append(
            f"- `{len(report['componentsWithoutMesh']) - 40}` additional entries in JSON report."
        )
    lines.extend(["", "## Textures Not Decoded", ""])
    lines.extend(
        format_generic_list(
            report["texturesNotDecoded"][:40],
            lambda item: f"- `{item['textureId']}` ({', '.join(item['sources'])})",
        )
    )
    if len(report["texturesNotDecoded"]) > 40:
        lines.append(
            f"- `{len(report['texturesNotDecoded']) - 40}` additional entries in JSON report."
        )
    lines.extend(["", "## Failed Conversions", ""])
    lines.extend(
        format_generic_list(
            report["failedConversions"],
            lambda item: f"- `{item.get('guid') or 'unknown'}` {item.get('name')} status={item.get('conversionStatus')} error={item.get('error')}",
        )
    )
    lines.append("")
    return "\n".join(lines)


def format_mesh_list(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- None"]
    return [f"- `{item['guid']}` — {item['name']}" for item in items[:40]] + (
        [f"- `{len(items) - 40}` additional entries in JSON report."]
        if len(items) > 40
        else []
    )


def format_generic_list(items: list[dict[str, Any]], formatter) -> list[str]:
    if not items:
        return ["- None"]
    return [formatter(item) for item in items]


def canonical_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def canonical_texture_key(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return Path(text).name.casefold()


def looks_like_guid(value: str) -> bool:
    return bool(GUID_RE.fullmatch(value))


def normalize_guid(value: str | None) -> str:
    match = GUID_RE.search(value or "")
    return match.group(0).lower() if match else ""


def unique_strings(values: list[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
