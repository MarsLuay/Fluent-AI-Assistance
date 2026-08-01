#!/usr/bin/env python3
"""Merge host FluentControl mesh assets with the simulator mesh library without blind overwrite.

Preserve pinlist (meshes protected from ``--force-host-overlap``) comes from the
current install/ZEIA — never a baked ``DEFAULT_PRESERVE_SIM_GUIDS`` host list:

- ``--preserve-from-install`` (default): WorktableMesh GUIDs on ``--host-install`` Components
- ``--preserve-from``: ``labware_catalog.json``, pinlist JSON, or DataStore root
- ``public/models/fluent/local/preserve-mesh-guids.json`` when present
- ``--preserve-sim-guid`` CLI extras

GLBs + ``manifest.json`` write only under ``public/models/fluent/local/``
(gitignored). Asset URLs: ``/models/fluent/local/<guid>.glb``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[3]

from tecan_tools import extract_fluent_meshes as mesh_extractor

DEFAULT_SIM_DIR = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent/local"
DEFAULT_FLUENT_MODELS = DEFAULT_SIM_DIR.parent
DEFAULT_HOST_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
# No baked host/ZEIA mesh GUID pinlist — preserve set comes from --preserve-from,
# install/ZEIA Components, labware_catalog.json, or local/preserve-mesh-guids.json.
DEFAULT_PRESERVE_SIM_GUIDS: set[str] = set()
LOCAL_PRESERVE_PINLIST_NAME = "preserve-mesh-guids.json"
FLUENT_MESH_ASSET_PREFIX = "/models/fluent/local"

MERGE_SCHEMA_VERSION = 1


def refuse_tracked_fluent_mesh_root(sim_dir: Path, *, force: bool = False) -> None:
    """Refuse writing host/ZEIA GLBs into the tracked fluent models root."""
    if force:
        return
    if sim_dir.resolve() == DEFAULT_FLUENT_MODELS.resolve():
        raise SystemExit(
            f"Refusing to write meshes into tracked models root {DEFAULT_FLUENT_MODELS}. "
            f"Use {DEFAULT_SIM_DIR} (gitignored local rebuild)."
        )


@dataclass
class MeshRecord:
    guid: str
    name: str
    source_path: str
    checksum: str | None = None
    archive_path: str | None = None
    source_type: str = "unknown"
    manifest_entry: dict[str, Any] | None = None
    glb_path: Path | None = None
    glb_sha256: str | None = None
    xmsh_path: Path | None = None


@dataclass
class MergeAction:
    guid: str
    name: str
    action: str = ""
    origin: str = "unknown"
    preserve: bool = False
    write_glb: bool = False
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    sim: MeshRecord | None = None
    host: MeshRecord | None = None
    manifest_entry: dict[str, Any] | None = None


def main() -> int:
    args = parse_args()
    sim_dir = Path(args.sim_dir).resolve()
    refuse_tracked_fluent_mesh_root(sim_dir)
    host_install = Path(args.host_install).resolve()
    preserve_guids = resolve_preserve_sim_guids(
        cli_guids=list(args.preserve_sim_guid or []),
        preserve_from=list(args.preserve_from or []),
        host_install=host_install if args.preserve_from_install else None,
        sim_dir=sim_dir,
    )
    sim_manifest = Path(args.sim_manifest).resolve() if args.sim_manifest else None
    report = merge_libraries(
        sim_dir=sim_dir,
        host_install=host_install,
        preserve_sim_guids=preserve_guids,
        apply=bool(args.apply),
        force_host_overlap=bool(args.force_host_overlap),
        sim_manifest_path=sim_manifest,
    )

    report_path = sim_dir / args.report_name
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path = sim_dir / args.report_markdown_name
    markdown_path.write_text(render_merge_report_markdown(report), encoding="utf-8")

    summary = report["summary"]
    print(
        "Fluent mesh merge "
        f"{'applied' if args.apply else 'planned'}: "
        f"sim={summary['simCount']} host={summary['hostCount']} "
        f"merged={summary['mergedCount']} "
        f"preservedSimOnly={summary['preservedSimOnlyCount']} "
        f"preservePinlist={len(preserve_guids)} "
        f"addedFromHost={summary['addedFromHostCount']} "
        f"guidConflicts={summary['guidConflictCount']} "
        f"nameConflicts={summary['nameConflictCount']} "
        f"-> {report_path}"
    )
    if args.apply:
        print(f"Merged manifest -> {sim_dir / args.manifest_name}")
    else:
        print("Dry run only. Re-run with --apply to write merged GLBs and manifest.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim-dir", default=str(DEFAULT_SIM_DIR), help="Simulator fluent model directory.")
    parser.add_argument(
        "--host-install",
        default=str(resolve_host_install_default()),
        help="FluentControl host install/DataStore root (or extracted ZEIA DataStore).",
    )
    parser.add_argument(
        "--sim-manifest",
        default="",
        help="Optional simulator manifest path. Defaults to manifest.json in --sim-dir.",
    )
    parser.add_argument("--manifest-name", default="manifest.json", help="Simulator manifest filename.")
    parser.add_argument("--report-name", default="merge_report.json", help="Machine-readable merge report filename.")
    parser.add_argument(
        "--report-markdown-name",
        default="merge_report.md",
        help="Human-readable merge report filename.",
    )
    parser.add_argument(
        "--preserve-sim-guid",
        action="append",
        default=[],
        help="Additional simulator mesh GUIDs that must never be force-overwritten.",
    )
    parser.add_argument(
        "--preserve-from",
        action="append",
        default=[],
        help=(
            "Path to labware_catalog.json, preserve-mesh-guids.json, or a ZEIA/install "
            "DataStore root. Mesh GUIDs mined from Components / catalog entries."
        ),
    )
    parser.add_argument(
        "--preserve-from-install",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mine preserve GUIDs from --host-install Components WorktableMesh refs (default: on).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write new host-only GLBs and update manifest.json. Without this flag the run is plan-only.",
    )
    parser.add_argument(
        "--force-host-overlap",
        action="store_true",
        help="Replace overlapping GUID GLBs with host conversions instead of keeping simulator copies.",
    )
    return parser.parse_args()


def resolve_preserve_sim_guids(
    *,
    cli_guids: list[str],
    preserve_from: list[str],
    host_install: Path | None,
    sim_dir: Path,
) -> set[str]:
    """Build preserve pinlist from CLI + per-install/ZEIA sources (never a baked GUID set)."""
    guids: set[str] = {normalize_guid(value) for value in cli_guids if normalize_guid(value)}
    for raw in preserve_from:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        else:
            path = path.resolve()
        guids |= mesh_guids_from_preserve_source(path)
    if host_install is not None and Path(host_install).exists():
        guids |= mesh_guids_from_install_components(Path(host_install))
    local_pin_candidates = [
        Path(sim_dir) / LOCAL_PRESERVE_PINLIST_NAME,
        Path(sim_dir) / "local" / LOCAL_PRESERVE_PINLIST_NAME,
    ]
    if Path(sim_dir).name == "local":
        local_pin_candidates.append(Path(sim_dir).parent / "local" / LOCAL_PRESERVE_PINLIST_NAME)
    for local_pin in local_pin_candidates:
        if local_pin.is_file():
            guids |= mesh_guids_from_preserve_source(local_pin)
            break
    return {guid for guid in guids if guid}


def mesh_guids_from_preserve_source(path: Path) -> set[str]:
    """Load mesh GUIDs from catalog JSON, pinlist JSON, or install/ZEIA DataStore tree."""
    if path.is_dir():
        return mesh_guids_from_install_components(path)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    guids: set[str] = set()
    if isinstance(payload, list):
        for item in payload:
            guid = normalize_guid(item if isinstance(item, str) else str(item or ""))
            if guid:
                guids.add(guid)
        return guids
    if not isinstance(payload, dict):
        return set()
    for key in ("meshGuids", "mesh_guids", "preserveSimGuids", "guids"):
        values = payload.get(key)
        if isinstance(values, list):
            for item in values:
                guid = normalize_guid(item if isinstance(item, str) else str(item or ""))
                if guid:
                    guids.add(guid)
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        guid = normalize_guid(str(entry.get("mesh_guid") or ""))
        if guid:
            guids.add(guid)
        for item in entry.get("mesh_guids") or []:
            mesh_guid = normalize_guid(str(item or ""))
            if mesh_guid:
                guids.add(mesh_guid)
    return guids


def mesh_guids_from_install_components(install_root: Path) -> set[str]:
    """Mine WorktableMesh GUIDs from install/ZEIA Components/*.xcmp."""
    components_dirs = [
        install_root / "SystemSpecific" / "Worktable" / "Components",
        install_root / "DataStore" / "SystemSpecific" / "Worktable" / "Components",
    ]
    guids: set[str] = set()
    for components_dir in components_dirs:
        if not components_dir.is_dir():
            continue
        for path in components_dir.glob("*.xcmp"):
            guids |= mesh_guids_from_xcmp(path)
    return guids


def mesh_guids_from_xcmp(path: Path) -> set[str]:
    guids: set[str] = set()
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return guids
    root = tree.getroot()
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        local = elem.tag.rsplit("}", 1)[-1]
        if local != "Reference":
            continue
        type_id = ""
        ref_guid = ""
        for child in list(elem):
            if not isinstance(child.tag, str):
                continue
            child_local = child.tag.rsplit("}", 1)[-1]
            text = (child.text or "").strip()
            if child_local == "TypeId":
                type_id = text
            elif child_local in {"Guid", "GUID"}:
                ref_guid = text
        if type_id == "WorktableMesh":
            guid = normalize_guid(ref_guid)
            if guid:
                guids.add(guid)
    return guids


def resolve_host_install_default() -> Path:
    env = os.environ.get("FLUENTCODER_FC_INSTALL")
    return Path(env) if env else DEFAULT_HOST_INSTALL


def merge_libraries(
    *,
    sim_dir: Path,
    host_install: Path,
    preserve_sim_guids: set[str],
    apply: bool,
    force_host_overlap: bool,
    sim_manifest_path: Path | None = None,
) -> dict[str, Any]:
    sim_library = load_sim_library(sim_dir, sim_manifest_path)
    host_library = load_host_library(host_install)
    actions = plan_merge(sim_library, host_library, preserve_sim_guids)

    if apply:
        apply_merge(actions, sim_dir=sim_dir, host_install=host_install, force_host_overlap=force_host_overlap)
        merged_manifest = build_merged_manifest(actions, sim_dir, host_install)
        manifest_path = sim_dir / "manifest.json"
        backup_path = sim_dir / "manifest.sim.bak.json"
        if manifest_path.exists() and not backup_path.exists():
            shutil.copy2(manifest_path, backup_path)
        manifest_path.write_text(json.dumps(merged_manifest, indent=2) + "\n", encoding="utf-8")

    return build_merge_report(actions, sim_library, host_library, preserve_sim_guids, applied=apply)


def load_sim_library(sim_dir: Path, manifest_path: Path | None = None) -> dict[str, MeshRecord]:
    manifest_path = manifest_path or (sim_dir / "manifest.json")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Simulator manifest not found: {manifest_path}")

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: dict[str, MeshRecord] = {}
    for entry in payload.get("models", []):
        guid = normalize_guid(entry.get("guid"))
        if not guid:
            continue
        glb_path = sim_dir / f"{guid}.glb"
        if not glb_path.exists():
            output_path = entry.get("outputPath")
            if output_path:
                candidate = Path(output_path)
                if candidate.exists():
                    glb_path = candidate
        records[guid] = MeshRecord(
            guid=guid,
            name=str(entry.get("name") or guid),
            source_path=str(entry.get("sourcePath") or ""),
            checksum=entry.get("checksum"),
            archive_path=entry.get("archivePath") or payload.get("source"),
            source_type="zeia",
            manifest_entry=entry,
            glb_path=glb_path if glb_path.exists() else None,
            glb_sha256=sha256_file(glb_path) if glb_path.exists() else None,
        )
    return records


def load_host_library(host_install: Path) -> dict[str, MeshRecord]:
    meshes_dir = resolve_host_meshes_dir(host_install)
    records: dict[str, MeshRecord] = {}
    for path in sorted(meshes_dir.glob("*.xmsh")):
        guid = normalize_guid(path.stem)
        if not guid:
            continue
        metadata = read_host_mesh_metadata(path)
        records[guid] = MeshRecord(
            guid=guid,
            name=metadata["name"],
            source_path=metadata["source_path"],
            checksum=metadata.get("checksum"),
            source_type="host-db",
            xmsh_path=path,
        )
    return records


def resolve_host_meshes_dir(host_install: Path) -> Path:
    candidates = [
        host_install / "SystemSpecific" / "Worktable" / "Meshes",
        host_install / "DataStore" / "SystemSpecific" / "Worktable" / "Meshes",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Host mesh directory not found under {host_install}")


def read_host_mesh_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    item = mesh_extractor.SourceMesh(
        path=str(path.relative_to(path.parents[2])) if len(path.parents) >= 3 else path.name,
        text=text,
        archive_path=str(path),
    )
    metadata = mesh_extractor.parse_xmsh(item)
    return {
        "name": metadata.name,
        "source_path": f"SystemSpecific/Worktable/Meshes/{path.name}",
        "checksum": metadata.checksum or None,
    }


def plan_merge(
    sim_library: dict[str, MeshRecord],
    host_library: dict[str, MeshRecord],
    preserve_sim_guids: set[str],
) -> list[MergeAction]:
    actions: list[MergeAction] = []
    all_guids = sorted(set(sim_library) | set(host_library))

    sim_by_name = index_by_name(sim_library)
    host_by_name = index_by_name(host_library)
    name_conflicts = detect_name_conflicts(sim_by_name, host_by_name)

    for guid in all_guids:
        sim = sim_library.get(guid)
        host = host_library.get(guid)
        record_name = (sim or host).name if (sim or host) else guid
        action = MergeAction(guid=guid, name=record_name, origin="both" if sim and host else ("sim" if sim else "host"))

        if sim and not host:
            action.action = "preserve_sim_only"
            action.origin = "sim"
            action.sim = sim
            action.preserve = guid in preserve_sim_guids
            action.manifest_entry = annotate_manifest_entry(sim.manifest_entry or {}, action)
            actions.append(action)
            continue

        if host and not sim:
            action.action = "add_from_host"
            action.origin = "host"
            action.host = host
            action.write_glb = True
            if canonical_mesh_name(host.name) in name_conflicts:
                action.conflicts.append(name_conflict_payload(host.name, name_conflicts[canonical_mesh_name(host.name)]))
            actions.append(action)
            continue

        action.sim = sim
        action.host = host
        action.preserve = guid in preserve_sim_guids
        action.action = "keep_shared"
        action.origin = "both"
        action.manifest_entry = annotate_manifest_entry(sim.manifest_entry or {}, action)

        if guid_conflict(sim, host):
            action.conflicts.append(
                {
                    "type": "guid",
                    "guid": guid,
                    "simName": sim.name,
                    "hostName": host.name,
                    "simChecksum": sim.checksum,
                    "hostChecksum": host.checksum,
                    "resolution": "keep-simulator-glb",
                }
            )
        if canonical_mesh_name(sim.name) != canonical_mesh_name(host.name):
            action.conflicts.append(
                {
                    "type": "guid-name-mismatch",
                    "guid": guid,
                    "simName": sim.name,
                    "hostName": host.name,
                    "resolution": "keep-simulator-metadata",
                }
            )
        actions.append(action)

    return actions


def detect_name_conflicts(
    sim_by_name: dict[str, list[str]],
    host_by_name: dict[str, list[str]],
) -> dict[str, dict[str, list[str]]]:
    conflicts: dict[str, dict[str, list[str]]] = {}
    for name, sim_guids in sim_by_name.items():
        host_guids = host_by_name.get(name, [])
        if not host_guids:
            continue
        if set(sim_guids) == set(host_guids):
            continue
        conflicts[name] = {
            "simGuids": sorted(set(sim_guids)),
            "hostGuids": sorted(set(host_guids)),
        }
    return conflicts


def name_conflict_payload(name: str, payload: dict[str, list[str]]) -> dict[str, Any]:
    canonical = canonical_mesh_name(name)
    return {
        "type": "name",
        "name": canonical,
        "displayName": name.strip() or canonical,
        "simGuids": payload["simGuids"],
        "hostGuids": payload["hostGuids"],
        "resolution": "keep-both",
    }


def guid_conflict(sim: MeshRecord, host: MeshRecord) -> bool:
    if sim.checksum and host.checksum:
        return sim.checksum.upper() != host.checksum.upper()
    return canonical_mesh_name(sim.name) != canonical_mesh_name(host.name)


def apply_merge(
    actions: list[MergeAction],
    *,
    sim_dir: Path,
    host_install: Path,
    force_host_overlap: bool,
) -> None:
    for action in actions:
        if action.action == "add_from_host":
            if action.host and action.host.xmsh_path:
                entry = convert_host_mesh_to_glb(action.host.xmsh_path, sim_dir / f"{action.guid}.glb")
                action.manifest_entry = annotate_manifest_entry(entry, action)
                action.write_glb = True
            continue

        if action.action != "keep_shared" or not action.host or not action.host.xmsh_path:
            continue

        target = sim_dir / f"{action.guid}.glb"
        if force_host_overlap and not action.preserve:
            entry = convert_host_mesh_to_glb(action.host.xmsh_path, target)
            action.manifest_entry = annotate_manifest_entry(entry, action)
        elif not target.exists():
            entry = convert_host_mesh_to_glb(action.host.xmsh_path, target)
            action.manifest_entry = annotate_manifest_entry(entry, action)


def convert_host_mesh_to_glb(xmsh_path: Path, output_path: Path) -> dict[str, Any]:
    text = xmsh_path.read_text(encoding="utf-8-sig")
    item = mesh_extractor.SourceMesh(
        path=f"SystemSpecific/Worktable/Meshes/{xmsh_path.name}",
        text=text,
        archive_path=str(xmsh_path),
    )
    metadata = mesh_extractor.parse_xmsh(item)
    decoded = mesh_extractor.decode_fluent_mesh(metadata)
    if decoded.glb_bytes:
        conversion_status = "copied-glb"
        glb_bytes = decoded.glb_bytes
        bounds = {"min": [0, 0, 0], "max": [0, 0, 0], "size": [0, 0, 0]}
        unit_metadata = mesh_extractor.infer_unit_metadata(bounds, conversion_status)
        primitives = []
    elif decoded.primitives:
        conversion_status = "converted"
        primitives = decoded.primitives
        glb_bytes, bounds, unit_metadata = mesh_extractor.build_glb(metadata, primitives, decoded, conversion_status)
    else:
        conversion_status = "placeholder"
        decoded.notes.append("No Mesh3D primitive arrays were decoded; wrote a diagnostic placeholder.")
        primitives = [mesh_extractor.placeholder_primitive(metadata.name)]
        glb_bytes, bounds, unit_metadata = mesh_extractor.build_glb(metadata, primitives, decoded, conversion_status)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(glb_bytes)
    return {
        "guid": metadata.guid,
        "name": metadata.name,
        "sourcePath": item.path,
        "archivePath": item.archive_path,
        "assetPath": f"{FLUENT_MESH_ASSET_PREFIX}/{output_path.name}",
        "outputPath": str(output_path),
        "conversionStatus": conversion_status,
        "nativeFormat": decoded.native_format,
        "compression": decoded.compression,
        "deflateOffset": decoded.deflate_offset,
        "payloadBytes": len(metadata.payload),
        "innerPayloadBytes": decoded.inner_payload_bytes,
        "base64Length": metadata.base64_length,
        "version": metadata.version,
        "dataVersion": metadata.data_version,
        "checksum": metadata.checksum,
        "meshParts": len(decoded.primitives),
        "vertexCount": sum(len(primitive.positions) // 3 for primitive in primitives),
        "triangleCount": sum(len(primitive.indices) // 3 for primitive in primitives),
        "bounds": bounds,
        "boundsMm": mesh_extractor.scale_bounds(bounds, unit_metadata["unitScaleToMm"]),
        "unitScaleToMm": unit_metadata["unitScaleToMm"],
        "unitScaleSource": unit_metadata["unitScaleSource"],
        "nativeUnit": unit_metadata["nativeUnit"],
        "notes": decoded.notes,
    }


def build_merged_manifest(actions: list[MergeAction], sim_dir: Path, host_install: Path) -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    for action in sorted(actions, key=lambda row: (row.name.lower(), row.guid)):
        if action.manifest_entry:
            models.append(action.manifest_entry)
            continue

        if action.sim and action.sim.manifest_entry:
            models.append(annotate_manifest_entry(action.sim.manifest_entry, action))

    preserved = [action.guid for action in actions if action.action == "preserve_sim_only"]
    added = [action.guid for action in actions if action.action == "add_from_host"]
    guid_conflicts = [conflict for action in actions for conflict in action.conflicts if conflict["type"].startswith("guid")]
    name_conflicts = collect_name_conflicts(actions)

    return {
        "source": f"merged:sim+host:{host_install}",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "nativeFormat": "Tecan VisionX Worktable MeshArchive BinaryFormatter",
        "productAuthority": False,
        "localRebuildOnly": True,
        "assetBasePath": FLUENT_MESH_ASSET_PREFIX,
        "merge": {
            "schemaVersion": MERGE_SCHEMA_VERSION,
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "simDir": str(sim_dir),
            "hostInstall": str(host_install),
            "simCount": sum(1 for action in actions if action.sim),
            "hostCount": sum(1 for action in actions if action.host),
            "mergedCount": len(models),
            "preservedSimOnly": preserved,
            "addedFromHost": added,
            "guidConflicts": guid_conflicts,
            "nameConflicts": name_conflicts,
        },
        "models": models,
        "summary": {
            "entries": len(models),
            "converted": sum(1 for model in models if model.get("conversionStatus") in {"converted", "copied-glb"}),
            "placeholders": sum(1 for model in models if model.get("conversionStatus") == "placeholder"),
            "failed": 0,
        },
    }


def build_merge_report(
    actions: list[MergeAction],
    sim_library: dict[str, MeshRecord],
    host_library: dict[str, MeshRecord],
    preserve_sim_guids: set[str],
    *,
    applied: bool,
) -> dict[str, Any]:
    preserved = [action for action in actions if action.action == "preserve_sim_only"]
    added = [action for action in actions if action.action == "add_from_host"]
    shared = [action for action in actions if action.action == "keep_shared"]
    guid_conflicts = [conflict for action in actions for conflict in action.conflicts if conflict["type"].startswith("guid")]
    name_conflicts = collect_name_conflicts(actions)

    return {
        "schemaVersion": MERGE_SCHEMA_VERSION,
        "kind": "fluent-mesh-merge-report",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "applied": applied,
        "summary": {
            "simCount": len(sim_library),
            "hostCount": len(host_library),
            "mergedCount": len(actions),
            "preservedSimOnlyCount": len(preserved),
            "addedFromHostCount": len(added),
            "sharedGuidCount": len(shared),
            "guidConflictCount": len(guid_conflicts),
            "nameConflictCount": len(name_conflicts),
        },
        "preservedSimOnly": [serialize_action(action) for action in preserved],
        "addedFromHost": [serialize_action(action) for action in added],
        "guidConflicts": guid_conflicts,
        "nameConflicts": name_conflicts,
        "actions": [serialize_action(action) for action in actions],
    }


def collect_name_conflicts(actions: list[MergeAction]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    conflicts: list[dict[str, Any]] = []
    for action in actions:
        for conflict in action.conflicts:
            if conflict.get("type") != "name":
                continue
            key = json.dumps(conflict, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            conflicts.append(conflict)
    return conflicts


def serialize_action(action: MergeAction) -> dict[str, Any]:
    return {
        "guid": action.guid,
        "name": action.name,
        "action": action.action,
        "origin": action.origin,
        "preserve": action.preserve,
        "writeGlb": action.write_glb,
        "conflicts": action.conflicts,
        "sim": serialize_record(action.sim),
        "host": serialize_record(action.host),
    }


def serialize_record(record: MeshRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "guid": record.guid,
        "name": record.name,
        "sourcePath": record.source_path,
        "checksum": record.checksum,
        "sourceType": record.source_type,
        "glbPath": str(record.glb_path) if record.glb_path else None,
        "glbSha256": record.glb_sha256,
        "xmshPath": str(record.xmsh_path) if record.xmsh_path else None,
    }


def annotate_manifest_entry(entry: dict[str, Any], action: MergeAction) -> dict[str, Any]:
    annotated = dict(entry)
    annotated["mergeOrigin"] = action.origin
    annotated["mergeAction"] = action.action
    annotated["mergePreserve"] = action.preserve
    if action.conflicts:
        annotated["mergeConflicts"] = action.conflicts
    asset = str(annotated.get("assetPath") or "")
    if asset.startswith("/models/fluent/") and not asset.startswith(f"{FLUENT_MESH_ASSET_PREFIX}/"):
        name = asset.rsplit("/", 1)[-1]
        if name.lower().endswith(".glb"):
            annotated["assetPath"] = f"{FLUENT_MESH_ASSET_PREFIX}/{name}"
    elif action.guid and not asset:
        annotated["assetPath"] = f"{FLUENT_MESH_ASSET_PREFIX}/{action.guid}.glb"
    return annotated


def render_merge_report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Fluent Mesh Merge Report",
        "",
        f"- Applied: `{report['applied']}`",
        f"- Simulator meshes: `{summary['simCount']}`",
        f"- Host DB meshes: `{summary['hostCount']}`",
        f"- Merged unique GUIDs: `{summary['mergedCount']}`",
        f"- Preserved simulator-only: `{summary['preservedSimOnlyCount']}`",
        f"- Added from host: `{summary['addedFromHostCount']}`",
        f"- Shared GUIDs: `{summary['sharedGuidCount']}`",
        f"- GUID conflicts: `{summary['guidConflictCount']}`",
        f"- Name conflicts: `{summary['nameConflictCount']}`",
        "",
        "## Preserved Simulator-Only Meshes",
        "",
    ]
    if report["preservedSimOnly"]:
        for item in report["preservedSimOnly"]:
            lines.append(f"- `{item['guid']}` — {item['name']}")
    else:
        lines.append("- None")

    lines.extend(["", "## Added From Host", ""])
    if report["addedFromHost"]:
        for item in report["addedFromHost"]:
            lines.append(f"- `{item['guid']}` — {item['name']}")
    else:
        lines.append("- None")

    lines.extend(["", "## Name Conflicts", ""])
    if report["nameConflicts"]:
        for conflict in report["nameConflicts"]:
            lines.append(f"- **{conflict.get('displayName') or conflict['name']}**")
            lines.append(f"  - sim GUIDs: `{', '.join(conflict['simGuids'])}`")
            lines.append(f"  - host GUIDs: `{', '.join(conflict['hostGuids'])}`")
            lines.append(f"  - resolution: {conflict['resolution']}")
    else:
        lines.append("- None")

    lines.extend(["", "## GUID Conflicts", ""])
    if report["guidConflicts"]:
        for conflict in report["guidConflicts"]:
            lines.append(
                f"- `{conflict['guid']}` sim=`{conflict.get('simName')}` host=`{conflict.get('hostName')}` "
                f"resolution={conflict.get('resolution')}"
            )
    else:
        lines.append("- None")

    lines.append("")
    return "\n".join(lines)


def index_by_name(records: dict[str, MeshRecord]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for guid, record in records.items():
        grouped[canonical_mesh_name(record.name)].append(guid)
    return dict(grouped)


def canonical_mesh_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def normalize_guid(value: str | None) -> str:
    match = mesh_extractor.GUID_RE.search(value or "")
    return match.group(0).lower() if match else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
