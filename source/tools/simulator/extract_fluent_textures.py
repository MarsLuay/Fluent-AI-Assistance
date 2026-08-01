#!/usr/bin/env python3
"""Decode FluentControl .xtx textures and publish simulator-ready image assets.

Writes only under ``public/models/fluent/local/textures/`` (gitignored). Prefer
selective rebuild from a texture GUID list rather than shipping host JPGs.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import struct
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from tecan_common import xml_compat as ET

SCRIPT_PATH = Path(__file__).resolve()
TOOLS_DIR = SCRIPT_PATH.parents[1]
PROJECT_ROOT = SCRIPT_PATH.parents[3]
DEFAULT_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
DEFAULT_FLUENT_MODELS = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent"
# Host/ZEIA textures + manifest are local-only (gitignored) — not product law.
DEFAULT_OUTPUT = DEFAULT_FLUENT_MODELS / "local" / "textures"
DEFAULT_LOCAL_MESHES = DEFAULT_FLUENT_MODELS / "local"
DEFAULT_REGISTRY = DEFAULT_LOCAL_MESHES / "registry.json"
FLUENT_TEXTURE_ASSET_PREFIX = "/models/fluent/local/textures"
TRACKED_TEXTURES_ROOT = DEFAULT_FLUENT_MODELS / "textures"

GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)

TEXTURE_MANIFEST_KIND = "fluent-texture-manifest"
TEXTURE_MANIFEST_SCHEMA_VERSION = 1
LOCAL_PRESERVE_PINLIST_NAME = "preserve-texture-guids.json"

PRIORITY_PATTERNS = (
    re.compile(r"diti", re.I),
    re.compile(r"barcode", re.I),
    re.compile(r"reference\s*plate", re.I),
)

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass
class DecodedTexture:
    texture_guid: str
    object_name: str
    source_path: str
    source_type: str
    image_bytes: bytes
    image_format: str
    checksum: str | None
    priority: bool


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out).expanduser().resolve()
    refuse_tracked_fluent_texture_root(out_dir, force=bool(args.force))
    report = extract_textures(
        install_path=Path(args.install),
        out_dir=out_dir,
        registry_path=Path(args.registry) if args.registry else None,
        mesh_dir=Path(args.mesh_dir).expanduser().resolve() if args.mesh_dir else DEFAULT_LOCAL_MESHES,
        priority_only=args.priority_only,
        attach_glbs=args.attach_glbs,
        beside_glbs=args.beside_glbs,
        overwrite=args.overwrite,
        texture_guid_filter=resolve_texture_guid_filter(
            cli_guids=args.texture_guid or [],
            from_paths=args.texture_guids_from or [],
            output_dir=out_dir,
            only_listed=bool(args.only_listed),
        ),
    )
    manifest_path = out_dir / "manifest.json"
    print(
        "Fluent texture extraction complete: "
        f"decoded={report['summary']['decodedCount']} "
        f"failed={report['summary']['failedCount']} "
        f"priority={report['summary']['priorityCount']} "
        f"glbAttached={report['summary']['glbAttachedCount']} "
        f"besideGlb={report['summary']['besideGlbCount']} -> {manifest_path}"
    )
    return 1 if report["summary"]["failedCount"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", default=str(DEFAULT_INSTALL), help="Host DB or extracted DataStore root.")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help="Directory for decoded images + manifest (default: local/textures/).",
    )
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="registry.json for GLB texture attachment.")
    parser.add_argument(
        "--mesh-dir",
        default=str(DEFAULT_LOCAL_MESHES),
        help="Directory with mesh GUID .glb files for --attach-glbs / --beside-glbs.",
    )
    parser.add_argument("--priority-only", action="store_true", help="Decode only DiTi/barcode/reference-plate textures.")
    parser.add_argument("--attach-glbs", action="store_true", help="Embed decoded textures into linked GLB materials.")
    parser.add_argument("--beside-glbs", action="store_true", help="Write texture images beside linked GLB files.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing decoded image files.")
    parser.add_argument(
        "--texture-guid",
        action="append",
        default=[],
        help="Texture GUID to extract (repeatable). Enables selective rebuild.",
    )
    parser.add_argument(
        "--texture-guids-from",
        action="append",
        default=[],
        help="JSON GUID array / preserve-texture-guids.json / object with textureGuids.",
    )
    parser.add_argument(
        "--only-listed",
        action="store_true",
        help="Fail unless a texture GUID list is provided (CLI / pinlist).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow writing outside local/textures (not recommended).",
    )
    return parser.parse_args()


def refuse_tracked_fluent_texture_root(output_dir: Path, *, force: bool = False) -> None:
    """Refuse writing host/ZEIA textures into tracked models / textures roots."""
    if force:
        return
    resolved = output_dir.resolve()
    forbidden = {
        DEFAULT_FLUENT_MODELS.resolve(),
        TRACKED_TEXTURES_ROOT.resolve(),
    }
    if resolved in forbidden:
        raise SystemExit(
            f"Refusing to write textures into tracked path {resolved}. "
            f"Use {DEFAULT_OUTPUT} (gitignored local rebuild) or pass an explicit --out under local/."
        )
    # Also refuse writing a host-path manifest into the tracked textures tree.
    try:
        resolved.relative_to(TRACKED_TEXTURES_ROOT.resolve())
    except ValueError:
        return
    raise SystemExit(
        f"Refusing to write under tracked textures root {TRACKED_TEXTURES_ROOT}. "
        f"Use {DEFAULT_OUTPUT} instead."
    )


def portable_source_label(source: Path) -> str:
    """Avoid baking absolute host paths into local manifests."""
    try:
        return str(source.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return source.name


def texture_guids_from_pinlist(path: Path) -> set[str]:
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
    for key in ("textureGuids", "texture_guids", "guids", "meshGuids", "mesh_guids"):
        values = payload.get(key)
        if isinstance(values, list):
            for item in values:
                guid = normalize_guid(item if isinstance(item, str) else str(item or ""))
                if guid:
                    guids.add(guid)
    for entry in payload.get("textures") or payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("textureGuid", "texture_guid", "guid"):
            guid = normalize_guid(str(entry.get(key) or ""))
            if guid:
                guids.add(guid)
    return guids


def resolve_texture_guid_filter(
    *,
    cli_guids: Iterable[str],
    from_paths: Iterable[str],
    output_dir: Path,
    only_listed: bool,
) -> set[str] | None:
    """Return GUID allow-list, or None when extracting the full source texture set."""
    path_args = [str(raw) for raw in from_paths]
    guids: set[str] = {normalize_guid(value) for value in cli_guids if normalize_guid(value)}
    for raw in path_args:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        else:
            path = path.resolve()
        guids |= texture_guids_from_pinlist(path)
    want_list = only_listed or bool(guids) or bool(path_args)
    if want_list:
        for candidate in (
            output_dir / LOCAL_PRESERVE_PINLIST_NAME,
            DEFAULT_OUTPUT / LOCAL_PRESERVE_PINLIST_NAME,
            DEFAULT_LOCAL_MESHES / LOCAL_PRESERVE_PINLIST_NAME,
        ):
            if candidate.is_file():
                guids |= texture_guids_from_pinlist(candidate)
                break
    if only_listed and not guids:
        raise SystemExit(
            "--only-listed requires a texture GUID list via --texture-guid, "
            f"--texture-guids-from, or {LOCAL_PRESERVE_PINLIST_NAME}."
        )
    if not guids and not want_list:
        return None
    return {guid for guid in guids if guid}


def extract_textures(
    *,
    install_path: Path,
    out_dir: Path,
    registry_path: Path | None,
    mesh_dir: Path,
    priority_only: bool,
    attach_glbs: bool,
    beside_glbs: bool,
    overwrite: bool,
    texture_guid_filter: set[str] | None = None,
) -> dict[str, Any]:
    datastore_root, source_type = resolve_datastore_root(install_path)
    textures_dir = datastore_root / "SystemSpecific" / "Worktable" / "Textures"
    if not textures_dir.exists():
        raise FileNotFoundError(f"Textures directory not found at {textures_dir}")

    decoded_rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []
    by_guid: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}

    out_dir.mkdir(parents=True, exist_ok=True)

    xtx_paths = sorted(textures_dir.glob("*.xtx"))
    for path in xtx_paths:
        try:
            decoded = parse_xtx_file(path, source_type)
        except Exception as exc:  # noqa: BLE001
            failed_rows.append({"sourcePath": path.name, "error": str(exc)})
            continue
        if texture_guid_filter is not None and decoded.texture_guid not in texture_guid_filter:
            continue
        if priority_only and not decoded.priority:
            continue
        ext = "jpg" if decoded.image_format == "jpeg" else "png"
        asset_name = f"{decoded.texture_guid}.{ext}"
        asset_path = f"{FLUENT_TEXTURE_ASSET_PREFIX}/{asset_name}"
        output_path = out_dir / asset_name
        if overwrite or not output_path.exists():
            output_path.write_bytes(decoded.image_bytes)

        row = {
            "textureGuid": decoded.texture_guid,
            "objectName": decoded.object_name,
            "sourcePath": decoded.source_path,
            "sourceType": decoded.source_type,
            "assetPath": asset_path,
            "format": decoded.image_format,
            "byteLength": len(decoded.image_bytes),
            "checksum": decoded.checksum,
            "priority": decoded.priority,
        }
        decoded_rows.append(row)
        by_guid[decoded.texture_guid] = row
        by_name[canonical_texture_key(decoded.object_name)] = row

    registry_payload = load_registry(registry_path) if registry_path and registry_path.exists() else None
    glb_attached = 0
    beside_glb_count = 0
    if registry_payload and decoded_rows and (attach_glbs or beside_glbs):
        glb_attached, beside_glb_count = apply_registry_texture_links(
            registry_payload,
            by_guid,
            by_name,
            out_dir=out_dir,
            mesh_dir=mesh_dir,
            attach_glbs=attach_glbs,
            beside_glbs=beside_glbs,
            overwrite=overwrite,
        )

    if texture_guid_filter is not None and not decoded_rows and not failed_rows:
        raise SystemExit(
            f"No .xtx entries matched texture GUID filter ({len(texture_guid_filter)} listed) in {textures_dir}"
        )

    manifest = {
        "schemaVersion": TEXTURE_MANIFEST_SCHEMA_VERSION,
        "kind": TEXTURE_MANIFEST_KIND,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "productAuthority": False,
        "localRebuildOnly": True,
        "assetBasePath": FLUENT_TEXTURE_ASSET_PREFIX,
        "textureGuidFilter": sorted(texture_guid_filter) if texture_guid_filter is not None else None,
        "sources": {
            # Portable labels only — never absolute ProgramData / user paths.
            "installPath": portable_source_label(install_path),
            "sourceType": source_type,
            "texturesDir": "DataStore/SystemSpecific/Worktable/Textures",
            "registryPath": portable_source_label(registry_path) if registry_path else None,
        },
        "summary": {
            "textureCount": len(xtx_paths),
            "decodedCount": len(decoded_rows),
            "failedCount": len(failed_rows),
            "priorityCount": sum(1 for row in decoded_rows if row.get("priority")),
            "glbAttachedCount": glb_attached,
            "besideGlbCount": beside_glb_count,
            "filterSize": len(texture_guid_filter) if texture_guid_filter is not None else None,
        },
        "textures": sorted(decoded_rows, key=lambda row: (row.get("objectName") or "", row.get("textureGuid") or "")),
        "failed": failed_rows,
    }
    refuse_host_paths_in_manifest(manifest)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def refuse_host_paths_in_manifest(manifest: dict[str, Any]) -> None:
    """Block writing product/local manifests that still embed absolute host paths."""
    blob = json.dumps(manifest)
    markers = (
        r"C:\\ProgramData\\Tecan",
        r"C:/ProgramData/Tecan",
        "/Users/",
        "C:\\Users\\",
        "ProgramData\\Tecan",
        "Program Files\\Tecan",
    )
    lowered = blob.lower()
    for marker in markers:
        if marker.lower() in lowered:
            raise SystemExit(
                "Refusing to write texture manifest containing absolute host paths "
                f"(matched {marker!r}). Use portable labels only."
            )


def parse_xtx_file(path: Path, source_type: str) -> DecodedTexture:
    tree = ET.parse(path)
    root = tree.getroot()
    payload = xml_first(root, "Payload")
    if payload is None:
        raise ValueError("missing Payload node")

    object_name = xml_text(payload, "ObjectName") or path.stem
    checksum = xml_text(root, "Checksum") or None
    bitmap_node = xml_first(payload, "Bitmap")
    if bitmap_node is None:
        raise ValueError("missing Bitmap payload")
    bitmap_text = "".join(bitmap_node.itertext()).strip()
    if not bitmap_text:
        raise ValueError("empty Bitmap payload")
    image_bytes = base64.b64decode(bitmap_text, validate=False)
    image_format = detect_image_format(image_bytes)

    # Portable archive-relative label only (never absolute install path).
    relative_source = path.name
    try:
        # .../DataStore/SystemSpecific/Worktable/Textures/<file>.xtx
        install_root = path.parents[3]
        relative_source = path.relative_to(install_root).as_posix()
    except (ValueError, IndexError):
        pass

    texture_guid = normalize_guid(path.stem) or normalize_guid(object_name)
    priority = is_priority_texture(object_name)
    return DecodedTexture(
        texture_guid=texture_guid,
        object_name=object_name,
        source_path=relative_source,
        source_type=source_type,
        image_bytes=image_bytes,
        image_format=image_format,
        checksum=checksum,
        priority=priority,
    )


def detect_image_format(image_bytes: bytes) -> str:
    if image_bytes.startswith(JPEG_MAGIC):
        return "jpeg"
    if image_bytes.startswith(PNG_MAGIC):
        return "png"
    raise ValueError("unsupported embedded bitmap format")


def is_priority_texture(object_name: str) -> bool:
    return any(pattern.search(object_name) for pattern in PRIORITY_PATTERNS)


def apply_registry_texture_links(
    registry_payload: dict[str, Any],
    by_guid: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    *,
    out_dir: Path,
    mesh_dir: Path,
    attach_glbs: bool,
    beside_glbs: bool,
    overwrite: bool,
) -> tuple[int, int]:
    glb_attached = 0
    beside_glb_count = 0
    for entry in registry_payload.get("entries", []):
        mesh_guid = normalize_guid(entry.get("meshGuid"))
        if not mesh_guid:
            continue
        glb_path = mesh_dir / f"{mesh_guid}.glb"
        if not glb_path.exists():
            continue
        textures = entry.get("textures") or []
        if not textures:
            textures = resolve_entry_textures(entry, by_guid, by_name)
        for binding in textures:
            asset_path = binding.get("assetPath")
            if not asset_path:
                continue
            image_path = resolve_texture_image_path(out_dir, str(asset_path))
            if image_path is None or not image_path.exists():
                continue
            position = str(binding.get("position") or "TOP").upper()
            if beside_glbs:
                ext = image_path.suffix.lower()
                beside_name = f"{mesh_guid}_{position.lower()}{ext}"
                beside_path = mesh_dir / beside_name
                if overwrite or not beside_path.exists():
                    beside_path.write_bytes(image_path.read_bytes())
                    beside_glb_count += 1
            if attach_glbs:
                if attach_texture_to_glb(glb_path, image_path, position=position, overwrite=overwrite):
                    glb_attached += 1
    return glb_attached, beside_glb_count


def resolve_texture_image_path(out_dir: Path, asset_path: str) -> Path | None:
    """Map /models/fluent/local/textures/<file> (or legacy /models/fluent/textures/) to disk."""
    name = Path(asset_path).name
    if not name:
        return None
    candidate = out_dir / name
    if candidate.exists():
        return candidate
    # Legacy tracked textures tree (read-only fallback for attach).
    legacy = TRACKED_TEXTURES_ROOT / name
    if legacy.exists():
        return legacy
    return candidate


def resolve_entry_textures(
    entry: dict[str, Any],
    by_guid: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for texture_id in entry.get("textureIds") or []:
        guid = normalize_guid(texture_id)
        row = by_guid.get(guid) if guid else None
        if row is None:
            row = by_name.get(canonical_texture_key(texture_id))
        if row:
            resolved.append(
                {
                    "textureId": texture_id,
                    "textureGuid": row.get("textureGuid"),
                    "position": "TOP",
                    "assetPath": row.get("assetPath"),
                }
            )
    return resolved


def attach_texture_to_glb(glb_path: Path, image_path: Path, *, position: str, overwrite: bool) -> bool:
    gltf, binary = read_glb(glb_path)
    extras = gltf.get("extras") or {}
    attached = extras.get("fluentAttachedTextures") or []
    marker = f"{position}:{image_path.name}"
    if marker in attached and not overwrite:
        return False

    image_bytes = image_path.read_bytes()
    mime = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    image_index = len(gltf.setdefault("images", []))
    gltf["images"].append({"mimeType": mime, "bufferView": None})
    buffer_view_index = append_bytes_to_glb(gltf, binary, image_bytes)
    gltf["images"][image_index]["bufferView"] = buffer_view_index

    sampler_index = len(gltf.setdefault("samplers", []))
    gltf["samplers"].append({"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497})
    texture_index = len(gltf.setdefault("textures", []))
    gltf["textures"].append({"sampler": sampler_index, "source": image_index})

    materials = gltf.setdefault("materials", [])
    if not materials:
        materials.append(
            {
                "name": "fluent_texture_material",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [1, 1, 1, 1],
                    "metallicFactor": 0.05,
                    "roughnessFactor": 0.55,
                },
            }
        )

    bounds = bounds_from_gltf(gltf, binary)
    texcoord_accessor = add_planar_texcoords(gltf, binary, bounds)
    for material in materials:
        pbr = material.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorTexture"] = {"index": texture_index}
        pbr["baseColorFactor"] = [1, 1, 1, 1]

    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            attrs = primitive.setdefault("attributes", {})
            attrs["TEXCOORD_0"] = texcoord_accessor
            if "material" not in primitive:
                primitive["material"] = 0

    extras["fluentAttachedTextures"] = sorted(set(attached + [marker]))
    gltf["extras"] = extras
    glb_path.write_bytes(write_glb(gltf, binary))
    return True


def read_glb(path: Path) -> tuple[dict[str, Any], bytearray]:
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"not a GLB file: {path}")
    json_length = struct.unpack_from("<I", data, 12)[0]
    json_chunk = data[20 : 20 + json_length]
    gltf = json.loads(json_chunk.decode("utf-8"))
    binary_offset = 20 + json_length
    while (binary_offset - 12) % 4 != 0:
        binary_offset += 1
    bin_length = struct.unpack_from("<I", data, binary_offset)[0]
    binary = bytearray(data[binary_offset + 8 : binary_offset + 8 + bin_length])
    return gltf, binary


def write_glb(gltf: dict[str, Any], binary: bytes | bytearray) -> bytes:
    json_payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_payload = pad_bytes(json_payload, b" ")
    binary_payload = pad_bytes(bytes(binary), b"\x00")
    total_length = 12 + 8 + len(json_payload) + 8 + len(binary_payload)
    return b"".join(
        [
            b"glTF",
            struct.pack("<II", 2, total_length),
            struct.pack("<I4s", len(json_payload), b"JSON"),
            json_payload,
            struct.pack("<I4s", len(binary_payload), b"BIN\x00"),
            binary_payload,
        ]
    )


def append_bytes_to_glb(gltf: dict[str, Any], binary: bytearray, payload: bytes) -> int:
    padded = pad_bytes(payload, b"\x00")
    offset = len(binary)
    binary.extend(padded)
    buffer_views = gltf.setdefault("bufferViews", [])
    buffer_view_index = len(buffer_views)
    buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(payload)})
    buffers = gltf.setdefault("buffers", [{"byteLength": len(binary)}])
    buffers[0]["byteLength"] = len(binary)
    return buffer_view_index


def bounds_from_gltf(gltf: dict[str, Any], binary: bytearray) -> dict[str, list[float]]:
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for accessor in gltf.get("accessors", []):
        if accessor.get("type") != "VEC3":
            continue
        component_type = accessor.get("componentType")
        if component_type != 5126:
            continue
        count = int(accessor.get("count") or 0)
        if count <= 0:
            continue
        view_index = accessor.get("bufferView")
        if view_index is None:
            continue
        view = gltf["bufferViews"][view_index]
        start = int(view.get("byteOffset") or 0)
        stride = 12
        for index in range(count):
            offset = start + index * stride
            x, y, z = struct.unpack_from("<fff", binary, offset)
            mins[0] = min(mins[0], x)
            mins[1] = min(mins[1], y)
            mins[2] = min(mins[2], z)
            maxs[0] = max(maxs[0], x)
            maxs[1] = max(maxs[1], y)
            maxs[2] = max(maxs[2], z)
    if mins[0] == float("inf"):
        mins = [0.0, 0.0, 0.0]
        maxs = [1.0, 1.0, 1.0]
    return {"min": mins, "max": maxs}


def add_planar_texcoords(gltf: dict[str, Any], binary: bytearray, bounds: dict[str, list[float]]) -> int:
    position_accessor_index = None
    vertex_count = 0
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            attrs = primitive.get("attributes") or {}
            if "POSITION" in attrs:
                position_accessor_index = attrs["POSITION"]
                vertex_count = int(gltf["accessors"][position_accessor_index]["count"])
                break
        if position_accessor_index is not None:
            break
    if position_accessor_index is None or vertex_count <= 0:
        raise ValueError("GLB has no POSITION accessor for UV generation")

    view_index = gltf["accessors"][position_accessor_index]["bufferView"]
    view = gltf["bufferViews"][view_index]
    start = int(view.get("byteOffset") or 0)
    min_x, min_y, min_z = bounds["min"]
    max_x, _, max_z = bounds["max"]
    span_x = max(max_x - min_x, 1e-6)
    span_z = max(max_z - min_z, 1e-6)
    uvs: list[float] = []
    for index in range(vertex_count):
        offset = start + index * 12
        x, _, z = struct.unpack_from("<fff", binary, offset)
        u = (x - min_x) / span_x
        v = 1.0 - ((z - min_z) / span_z)
        uvs.extend([u, v])

    uv_bytes = struct.pack(f"<{len(uvs)}f", *uvs)
    buffer_view_index = append_bytes_to_glb(gltf, binary, uv_bytes)
    accessors = gltf.setdefault("accessors", [])
    accessor_index = len(accessors)
    accessors.append(
        {
            "bufferView": buffer_view_index,
            "componentType": 5126,
            "count": vertex_count,
            "type": "VEC2",
        }
    )
    return accessor_index


def load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_datastore_root(source: Path) -> tuple[Path, str]:
    source = source.resolve()
    if (source / "SystemSpecific" / "Worktable" / "Textures").exists():
        return source, "host-db"
    datastore = source / "DataStore"
    if (datastore / "SystemSpecific" / "Worktable" / "Textures").exists():
        return datastore, "zeia"
    raise FileNotFoundError(f"Could not resolve FluentControl textures root from {source}")


def canonical_texture_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def normalize_guid(value: str | None) -> str:
    match = GUID_RE.search(value or "")
    return match.group(0).lower() if match else ""


def xml_first(root: ET.Element | None, name: str) -> ET.Element | None:
    if root is None:
        return None
    for node in root.iter():
        if local_name(node.tag) == name:
            return node
    return None


def xml_text(root: ET.Element | None, name: str) -> str:
    node = xml_first(root, name)
    return "".join(node.itertext()).strip() if node is not None else ""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def pad_bytes(payload: bytes, pad_byte: bytes) -> bytes:
    remainder = len(payload) % 4
    if remainder == 0:
        return payload
    return payload + pad_byte * (4 - remainder)


if __name__ == "__main__":
    raise SystemExit(main())
