#!/usr/bin/env python3
"""Extract FluentControl .xmsh meshes from a ZEIA archive and export GLB assets.

Writes only under ``public/models/fluent/local/`` (gitignored). Prefer selective
rebuild from a mesh GUID list (``labware_catalog.json``,
``preserve-mesh-guids.json``, or ``--mesh-guid``) rather than shipping host GLBs.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import re
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[3]
DEFAULT_SOURCE = None  # resolved via --source or ready-to-import discovery
DEFAULT_FLUENT_MODELS = PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent"
# Host/ZEIA mesh GLBs + manifest are local-only (gitignored) — not product law.
DEFAULT_OUTPUT = DEFAULT_FLUENT_MODELS / "local"
FLUENT_MESH_ASSET_PREFIX = "/models/fluent/local"

GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
WORKTABLE_MESH_PATH_RE = re.compile(r"(?:^|/)DataStore/SystemSpecific/Worktable/Meshes/[^/]+\.xmsh$", re.I)


LOCAL_PRESERVE_PINLIST_NAME = "preserve-mesh-guids.json"


def refuse_tracked_fluent_mesh_root(output_dir: Path, *, force: bool = False) -> None:
    """Refuse writing host/ZEIA GLBs into the tracked fluent models root."""
    if force:
        return
    if output_dir.resolve() == DEFAULT_FLUENT_MODELS.resolve():
        raise SystemExit(
            f"Refusing to write meshes into tracked models root {DEFAULT_FLUENT_MODELS}. "
            f"Use {DEFAULT_OUTPUT} (gitignored local rebuild) or pass an explicit --out under local/."
        )


def mesh_guids_from_catalog_or_pinlist(path: Path) -> set[str]:
    """Load WorktableMesh GUIDs from labware_catalog / pinlist JSON / GUID array."""
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


def resolve_mesh_guid_filter(
    *,
    cli_guids: Iterable[str],
    from_paths: Iterable[str],
    output_dir: Path,
    only_listed: bool,
) -> set[str] | None:
    """Return GUID allow-list, or None when extracting the full source mesh set.

    Sources (union): ``--mesh-guid``, ``--mesh-guids-from``, and when
    ``only_listed`` / any CLI list is present, also ``local/preserve-mesh-guids.json``.
    """
    path_args = [str(raw) for raw in from_paths]
    guids: set[str] = {normalize_guid(value) for value in cli_guids if normalize_guid(value)}
    for raw in path_args:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        else:
            path = path.resolve()
        guids |= mesh_guids_from_catalog_or_pinlist(path)
    want_list = only_listed or bool(guids) or bool(path_args)
    if want_list:
        for candidate in (
            output_dir / LOCAL_PRESERVE_PINLIST_NAME,
            DEFAULT_OUTPUT / LOCAL_PRESERVE_PINLIST_NAME,
        ):
            if candidate.is_file():
                guids |= mesh_guids_from_catalog_or_pinlist(candidate)
                break
    if only_listed and not guids:
        raise SystemExit(
            " --only-listed requires a mesh GUID list via --mesh-guid, "
            f"--mesh-guids-from, or {LOCAL_PRESERVE_PINLIST_NAME}."
        )
    if not guids and not want_list:
        return None
    return {guid for guid in guids if guid}


def portable_source_label(source: Path) -> str:
    """Avoid baking absolute host paths into local manifests."""
    try:
        return str(source.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return source.name


def filter_source_meshes(source_meshes: list[SourceMesh], allowed: set[str] | None) -> list[SourceMesh]:
    if allowed is None:
        return source_meshes
    selected: list[SourceMesh] = []
    for item in source_meshes:
        stem_guid = normalize_guid(Path(item.path).stem)
        if stem_guid and stem_guid in allowed:
            selected.append(item)
            continue
        # Fallback: GUID may live only inside the .xmsh body.
        try:
            metadata = parse_xmsh(item)
        except Exception:  # noqa: BLE001 - keep scanning remaining meshes
            continue
        if metadata.guid and metadata.guid in allowed:
            selected.append(item)
    return selected


PRIMITIVE_BOOLEAN = 1
PRIMITIVE_BYTE = 2
PRIMITIVE_CHAR = 3
PRIMITIVE_DOUBLE = 6
PRIMITIVE_INT16 = 7
PRIMITIVE_INT32 = 8
PRIMITIVE_INT64 = 9
PRIMITIVE_SBYTE = 10
PRIMITIVE_SINGLE = 11
PRIMITIVE_UINT16 = 14
PRIMITIVE_UINT32 = 15
PRIMITIVE_UINT64 = 16

BINARY_TYPE_PRIMITIVE = 0
BINARY_TYPE_STRING = 1
BINARY_TYPE_OBJECT = 2
BINARY_TYPE_SYSTEM_CLASS = 3
BINARY_TYPE_CLASS = 4
BINARY_TYPE_OBJECT_ARRAY = 5
BINARY_TYPE_STRING_ARRAY = 6
BINARY_TYPE_PRIMITIVE_ARRAY = 7

GL_FLOAT = 5126
GL_UNSIGNED_INT = 5125
GL_ARRAY_BUFFER = 34962
GL_ELEMENT_ARRAY_BUFFER = 34963


@dataclass
class SourceMesh:
    path: str
    text: str
    archive_path: str | None = None


@dataclass
class XmshMetadata:
    guid: str
    name: str
    source_path: str
    version: str
    data_version: str
    checksum: str
    base64_length: int
    payload: bytes


@dataclass
class ClassMetadata:
    name: str
    member_names: list[str]
    binary_types: list[int | None]
    additional_infos: list[Any]
    library_id: int | None


@dataclass
class Reference:
    object_id: int


@dataclass
class NullRun:
    count: int


@dataclass
class MessageEnd:
    pass


@dataclass
class NetObject:
    object_id: int
    class_name: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class NetArray:
    object_id: int
    values: list[Any]
    primitive_type: int | None = None


@dataclass
class MeshPrimitive:
    name: str
    positions: list[float]
    indices: list[int]
    color: list[float]
    normals: list[float] | None = None


@dataclass
class DecodedMesh:
    primitives: list[MeshPrimitive]
    native_format: str
    compression: str
    inner_payload_bytes: int | None
    deflate_offset: int | None
    notes: list[str]
    glb_bytes: bytes | None = None


class BinaryFormatterError(RuntimeError):
    pass


class BinaryFormatterReader:
    """Small NRBF reader for the Fluent Mesh3D object graph."""

    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0
        self.objects: dict[int, Any] = {}
        self.class_metadata: dict[int, ClassMetadata] = {}
        self.libraries: dict[int, str] = {}

    def read_all(self) -> dict[int, Any]:
        while self.offset < len(self.data):
            record = self.read_record()
            if isinstance(record, MessageEnd):
                break
        return self.objects

    def read_record(self) -> Any:
        start = self.offset
        record_type = self.u8()

        if record_type == 0:
            return ("SerializedStreamHeader", self.i32(), self.i32(), self.i32(), self.i32())
        if record_type == 1:
            return self.read_class_with_id()
        if record_type == 2:
            return self.read_class_with_members(has_types=False, has_library=False)
        if record_type == 3:
            return self.read_class_with_members(has_types=False, has_library=True)
        if record_type == 4:
            return self.read_class_with_members(has_types=True, has_library=False)
        if record_type == 5:
            return self.read_class_with_members(has_types=True, has_library=True)
        if record_type == 6:
            object_id = self.i32()
            value = self.string()
            self.objects[object_id] = value
            return value
        if record_type == 7:
            return self.read_binary_array()
        if record_type == 8:
            return self.primitive(self.u8())
        if record_type == 9:
            return Reference(self.i32())
        if record_type == 10:
            return None
        if record_type == 11:
            return MessageEnd()
        if record_type == 12:
            library_id = self.i32()
            name = self.string()
            self.libraries[library_id] = name
            return ("BinaryLibrary", library_id, name)
        if record_type == 13:
            return NullRun(self.u8())
        if record_type == 14:
            return NullRun(self.i32())
        if record_type == 15:
            return self.read_array_single_primitive()
        if record_type == 16:
            return self.read_array_single_object()
        if record_type == 17:
            return self.read_array_single_string()

        raise BinaryFormatterError(f"Unsupported BinaryFormatter record {record_type} at byte {start}")

    def read_class_with_id(self) -> NetObject:
        object_id = self.i32()
        metadata_id = self.i32()
        metadata = self.class_metadata.get(metadata_id)
        if metadata is None:
            raise BinaryFormatterError(f"Class metadata {metadata_id} not found for object {object_id}")
        return self.read_object_values(object_id, metadata)

    def read_class_with_members(self, has_types: bool, has_library: bool) -> NetObject:
        object_id = self.i32()
        class_name = self.string()
        member_count = self.i32()
        member_names = [self.string() for _ in range(member_count)]
        binary_types: list[int | None] = [None] * member_count
        additional_infos: list[Any] = [None] * member_count

        if has_types:
            binary_types = [self.u8() for _ in range(member_count)]
            additional_infos = [self.read_additional_info(binary_type) for binary_type in binary_types]

        library_id = self.i32() if has_library else None
        metadata = ClassMetadata(class_name, member_names, binary_types, additional_infos, library_id)
        self.class_metadata[object_id] = metadata
        return self.read_object_values(object_id, metadata)

    def read_object_values(self, object_id: int, metadata: ClassMetadata) -> NetObject:
        obj = NetObject(object_id=object_id, class_name=metadata.name)
        self.objects[object_id] = obj
        for member_name, binary_type, additional_info in zip(
            metadata.member_names, metadata.binary_types, metadata.additional_infos
        ):
            obj.fields[member_name] = self.read_member_value(binary_type, additional_info)
        return obj

    def read_member_value(self, binary_type: int | None, additional_info: Any) -> Any:
        if binary_type == BINARY_TYPE_PRIMITIVE:
            return self.primitive(int(additional_info))
        return self.read_record()

    def read_additional_info(self, binary_type: int) -> Any:
        if binary_type == BINARY_TYPE_PRIMITIVE:
            return self.u8()
        if binary_type == BINARY_TYPE_SYSTEM_CLASS:
            return self.string()
        if binary_type == BINARY_TYPE_CLASS:
            return {"className": self.string(), "libraryId": self.i32()}
        if binary_type == BINARY_TYPE_PRIMITIVE_ARRAY:
            return self.u8()
        return None

    def read_binary_array(self) -> NetArray:
        object_id = self.i32()
        array_type = self.u8()
        rank = self.i32()
        lengths = [self.i32() for _ in range(rank)]
        if array_type in (3, 4, 5):
            _lower_bounds = [self.i32() for _ in range(rank)]

        binary_type = self.u8()
        additional_info = self.read_additional_info(binary_type)
        count = 1
        for length in lengths:
            count *= max(0, length)

        if binary_type == BINARY_TYPE_PRIMITIVE:
            values = [self.primitive(int(additional_info)) for _ in range(count)]
        else:
            values = self.read_array_items(count)

        array = NetArray(object_id=object_id, values=values)
        self.objects[object_id] = array
        return array

    def read_array_single_primitive(self) -> NetArray:
        object_id = self.i32()
        length = self.i32()
        primitive_type = self.u8()
        values = self.primitive_array_values(length, primitive_type)
        array = NetArray(object_id=object_id, values=values, primitive_type=primitive_type)
        self.objects[object_id] = array
        return array

    def read_array_single_object(self) -> NetArray:
        object_id = self.i32()
        length = self.i32()
        array = NetArray(object_id=object_id, values=[])
        self.objects[object_id] = array
        array.values = self.read_array_items(length)
        return array

    def read_array_single_string(self) -> NetArray:
        object_id = self.i32()
        length = self.i32()
        array = NetArray(object_id=object_id, values=[])
        self.objects[object_id] = array
        array.values = self.read_array_items(length)
        return array

    def read_array_items(self, length: int) -> list[Any]:
        values: list[Any] = []
        while len(values) < length:
            item = self.read_record()
            if isinstance(item, NullRun):
                values.extend([None] * min(item.count, length - len(values)))
            else:
                values.append(item)
        return values

    def primitive_array_values(self, length: int, primitive_type: int) -> list[Any]:
        if primitive_type == PRIMITIVE_BYTE:
            values = list(self.data[self.offset : self.offset + length])
            self.offset += length
            return values
        if primitive_type == PRIMITIVE_SINGLE:
            return list(self.unpack_many("<f", length))
        if primitive_type == PRIMITIVE_DOUBLE:
            return list(self.unpack_many("<d", length))
        if primitive_type == PRIMITIVE_INT32:
            return list(self.unpack_many("<i", length))
        if primitive_type == PRIMITIVE_UINT32:
            return list(self.unpack_many("<I", length))
        return [self.primitive(primitive_type) for _ in range(length)]

    def primitive(self, primitive_type: int) -> Any:
        if primitive_type == PRIMITIVE_BOOLEAN:
            return bool(self.u8())
        if primitive_type == PRIMITIVE_BYTE:
            return self.u8()
        if primitive_type == PRIMITIVE_CHAR:
            return chr(self.u16())
        if primitive_type == PRIMITIVE_DOUBLE:
            return self.f64()
        if primitive_type == PRIMITIVE_INT16:
            return self.i16()
        if primitive_type == PRIMITIVE_INT32:
            return self.i32()
        if primitive_type == PRIMITIVE_INT64:
            return self.i64()
        if primitive_type == PRIMITIVE_SBYTE:
            return self.s8()
        if primitive_type == PRIMITIVE_SINGLE:
            return self.f32()
        if primitive_type == PRIMITIVE_UINT16:
            return self.u16()
        if primitive_type == PRIMITIVE_UINT32:
            return self.u32()
        if primitive_type == PRIMITIVE_UINT64:
            return self.u64()
        raise BinaryFormatterError(f"Unsupported primitive type {primitive_type} at byte {self.offset}")

    def unpack_many(self, fmt: str, count: int) -> Iterable[Any]:
        size = struct.calcsize(fmt)
        end = self.offset + size * count
        if end > len(self.data):
            raise BinaryFormatterError("Primitive array extends past the end of the stream")
        unpack_fmt = "<" + fmt[-1] * count
        values = struct.unpack_from(unpack_fmt, self.data, self.offset)
        self.offset = end
        return values

    def string(self) -> str:
        length = self.length_prefixed_int()
        self.require(length)
        raw = self.data[self.offset : self.offset + length]
        self.offset += length
        return raw.decode("utf-8", errors="replace")

    def length_prefixed_int(self) -> int:
        shift = 0
        value = 0
        while True:
            byte = self.u8()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 35:
                raise BinaryFormatterError("Invalid length-prefixed integer")

    def require(self, count: int) -> None:
        if self.offset + count > len(self.data):
            raise BinaryFormatterError("Unexpected end of BinaryFormatter stream")

    def take(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        self.require(size)
        value = struct.unpack_from(fmt, self.data, self.offset)[0]
        self.offset += size
        return value

    def u8(self) -> int:
        self.require(1)
        value = self.data[self.offset]
        self.offset += 1
        return value

    def s8(self) -> int:
        return self.take("<b")

    def i16(self) -> int:
        return self.take("<h")

    def u16(self) -> int:
        return self.take("<H")

    def i32(self) -> int:
        return self.take("<i")

    def u32(self) -> int:
        return self.take("<I")

    def i64(self) -> int:
        return self.take("<q")

    def u64(self) -> int:
        return self.take("<Q")

    def f32(self) -> float:
        return self.take("<f")

    def f64(self) -> float:
        return self.take("<d")


def main() -> int:
    args = parse_args()
    if str(args.source or "").strip():
        source = Path(args.source).expanduser().resolve()
    else:
        discovered = discover_ready_zeia(PROJECT_ROOT)
        if discovered is None:
            print(
                "No source ZEIA provided. Pass a path or set TECAN_SIMULATOR_SAMPLE_ZEIA / place a .zeia under ready-to-import.",
                file=sys.stderr,
            )
            return 1
        source = discovered.resolve()
    output_dir = Path(args.out).expanduser().resolve()
    refuse_tracked_fluent_mesh_root(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    allowed_guids = resolve_mesh_guid_filter(
        cli_guids=args.mesh_guid or [],
        from_paths=args.mesh_guids_from or [],
        output_dir=output_dir,
        only_listed=bool(args.only_listed),
    )

    source_meshes = read_source_meshes(source)
    if not source_meshes:
        print(f"No .xmsh entries found in {source}", file=sys.stderr)
        return 1
    source_meshes = filter_source_meshes(source_meshes, allowed_guids)
    if allowed_guids is not None and not source_meshes:
        print(
            f"No .xmsh entries matched mesh GUID filter ({len(allowed_guids)} listed) in {source}",
            file=sys.stderr,
        )
        return 1

    manifest: dict[str, Any] = {
        "source": portable_source_label(source),
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "nativeFormat": "Tecan VisionX Worktable MeshArchive BinaryFormatter",
        "productAuthority": False,
        "localRebuildOnly": True,
        "assetBasePath": FLUENT_MESH_ASSET_PREFIX,
        "meshGuidFilter": sorted(allowed_guids) if allowed_guids is not None else None,
        "models": []
    }

    converted = 0
    placeholders = 0
    failures = 0

    for item in source_meshes:
        try:
            metadata = parse_xmsh(item)
            decoded = decode_fluent_mesh(metadata)
            if decoded.glb_bytes:
                conversion_status = "copied-glb"
                converted += 1
                glb_bytes = decoded.glb_bytes
                bounds = {"min": [0, 0, 0], "max": [0, 0, 0], "size": [0, 0, 0]}
                unit_metadata = infer_unit_metadata(bounds, conversion_status)
            elif decoded.primitives:
                conversion_status = "converted"
                converted += 1
                primitives = decoded.primitives
                glb_bytes, bounds, unit_metadata = build_glb(metadata, primitives, decoded, conversion_status)
            else:
                conversion_status = "placeholder"
                placeholders += 1
                decoded.notes.append("No Mesh3D primitive arrays were decoded; wrote a diagnostic placeholder.")
                primitives = [placeholder_primitive(metadata.name)]
                glb_bytes, bounds, unit_metadata = build_glb(metadata, primitives, decoded, conversion_status)

            asset_stem = metadata.guid or slug(metadata.name or Path(item.path).stem)
            output_path = output_dir / f"{asset_stem}.glb"
            output_path.write_bytes(glb_bytes)

            manifest["models"].append(
                {
                    "guid": metadata.guid,
                    "name": metadata.name,
                    "sourcePath": metadata.source_path.replace("\\", "/"),
                    "archivePath": Path(item.archive_path).name if item.archive_path else None,
                    "assetPath": f"{FLUENT_MESH_ASSET_PREFIX}/{output_path.name}",
                    "outputFile": output_path.name,
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
                    "vertexCount": sum(len(primitive.positions) // 3 for primitive in decoded.primitives),
                    "triangleCount": sum(len(primitive.indices) // 3 for primitive in decoded.primitives),
                    "bounds": bounds,
                    "boundsMm": scale_bounds(bounds, unit_metadata["unitScaleToMm"]),
                    "unitScaleToMm": unit_metadata["unitScaleToMm"],
                    "unitScaleSource": unit_metadata["unitScaleSource"],
                    "nativeUnit": unit_metadata["nativeUnit"],
                    "notes": decoded.notes
                }
            )
        except Exception as error:  # noqa: BLE001 - CLI should keep converting the rest.
            failures += 1
            manifest["models"].append(
                {
                    "sourcePath": item.path.replace("\\", "/"),
                    "archivePath": Path(item.archive_path).name if item.archive_path else None,
                    "conversionStatus": "failed",
                    "error": str(error)
                }
            )
            print(f"Failed to convert {item.path}: {error}", file=sys.stderr)

    manifest["summary"] = {
        "entries": len(source_meshes),
        "converted": converted,
        "placeholders": placeholders,
        "failed": failures,
        "filterSize": len(allowed_guids) if allowed_guids is not None else None,
    }
    (output_dir / args.manifest_name).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"Fluent mesh extraction complete: {converted} converted, {placeholders} placeholders, "
        f"{failures} failed -> {output_dir}"
        + (f" (filter {len(allowed_guids)} GUIDs)" if allowed_guids is not None else "")
    )
    return 1 if failures and args.fail_on_error else 0



def discover_ready_zeia(repo_root: Path) -> Path | None:
    env_path = __import__("os").environ.get("TECAN_SIMULATOR_SAMPLE_ZEIA", "").strip()
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
        for rel in (("source", "original-sources"), ("original_sources",), ("source", "original_sources")):
            folder = bundle.joinpath(*rel)
            if folder.is_dir():
                found.extend(sorted(folder.glob("*.zeia")))
    return found[0] if found else None

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        default="",
        help="ZEIA/zip archive, extracted directory, or single .xmsh file."
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help="Directory for generated .glb files and the manifest (default: local/).",
    )
    parser.add_argument("--manifest-name", default="manifest.json", help="Manifest filename to write inside --out.")
    parser.add_argument(
        "--mesh-guid",
        action="append",
        default=[],
        help="WorktableMesh GUID to extract (repeatable). Enables selective rebuild.",
    )
    parser.add_argument(
        "--mesh-guids-from",
        action="append",
        default=[],
        help=(
            "labware_catalog.json, preserve-mesh-guids.json, or JSON GUID array. "
            "Extract only those mesh GUIDs into local/."
        ),
    )
    parser.add_argument(
        "--only-listed",
        action="store_true",
        help="Fail unless a mesh GUID list is provided (CLI / catalog / preserve-mesh-guids.json).",
    )
    parser.add_argument("--fail-on-error", action="store_true", help="Exit non-zero if any mesh entry fails.")
    return parser.parse_args()


def read_source_meshes(source: Path) -> list[SourceMesh]:
    if source.is_dir():
        paths = sorted(source.rglob("*.xmsh"))
        return [SourceMesh(path=str(path.relative_to(source)), text=path.read_text(encoding="utf-8-sig")) for path in paths]

    if source.is_file() and source.suffix.lower() == ".xmsh":
        return [SourceMesh(path=source.name, text=source.read_text(encoding="utf-8-sig"))]

    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/") and name.lower().endswith(".xmsh")]
            worktable_names = [name for name in names if WORKTABLE_MESH_PATH_RE.search(name.replace("\\", "/"))]
            selected_names = sorted(worktable_names or names)
            return [
                SourceMesh(
                    path=name,
                    archive_path=str(source),
                    text=archive.read(name).decode("utf-8-sig", errors="replace")
                )
                for name in selected_names
            ]

    raise FileNotFoundError(f"Source is not a ZEIA/zip archive, directory, or .xmsh file: {source}")


def parse_xmsh(item: SourceMesh) -> XmshMetadata:
    root = ElementTree.fromstring(item.text)
    mesh_text = xml_text(root, "Mesh").strip()
    if not mesh_text:
        raise ValueError("No <Mesh> payload found")
    payload = base64.b64decode(re.sub(r"\s+", "", mesh_text), validate=False)

    path_guid = normalize_guid(Path(item.path).stem)
    guid = path_guid or normalize_guid(xml_text(root, "GUID"))
    name = xml_text(root, "ObjectName") or guid or Path(item.path).stem
    mesh_node = xml_first(root, "WorktableMesh")

    return XmshMetadata(
        guid=guid,
        name=name,
        source_path=item.path,
        version=mesh_node.attrib.get("version", "") if mesh_node is not None else "",
        data_version=mesh_node.attrib.get("dataVersion", "") if mesh_node is not None else "",
        checksum=xml_text(root, "Checksum"),
        base64_length=len(re.sub(r"\s+", "", mesh_text)),
        payload=payload
    )


def decode_fluent_mesh(metadata: XmshMetadata) -> DecodedMesh:
    notes: list[str] = []
    payload = metadata.payload

    if payload[:4] == b"glTF":
        notes.append("Payload was already a GLB; copied through as a single asset.")
        return DecodedMesh([], "glb", "none", len(payload), None, notes, payload)

    inner_payload, deflate_offset = find_inner_mesh_stream(payload)
    if inner_payload is None:
        notes.append("Outer payload did not contain a raw-deflate Mesh3D BinaryFormatter stream.")
        return DecodedMesh([], "unknown", "unknown", None, None, notes)

    reader = BinaryFormatterReader(inner_payload)
    reader.read_all()
    primitives = mesh_primitives_from_objects(reader.objects, notes)
    return DecodedMesh(
        primitives=primitives,
        native_format="Tecan VisionX Mesh3D BinaryFormatter",
        compression="raw-deflate",
        inner_payload_bytes=len(inner_payload),
        deflate_offset=deflate_offset,
        notes=notes
    )


def find_inner_mesh_stream(payload: bytes) -> tuple[bytes | None, int | None]:
    scan_limit = min(len(payload), 4096)
    for offset in range(scan_limit):
        decompressor = zlib.decompressobj(-15)
        try:
            inflated = decompressor.decompress(payload[offset:]) + decompressor.flush()
        except zlib.error:
            continue
        if len(inflated) > 256 and inflated.startswith(b"\x00\x01\x00\x00") and b"Mesh3D" in inflated[:4096]:
            return inflated, offset
    return None, None


def mesh_primitives_from_objects(objects: dict[int, Any], notes: list[str]) -> list[MeshPrimitive]:
    primitives: list[MeshPrimitive] = []
    seen_signatures: set[tuple[int, int, int]] = set()

    for value in objects.values():
        obj = dereference(value, objects)
        if not isinstance(obj, NetObject) or not obj.class_name.endswith(".Mesh3D"):
            continue

        vertices = numeric_array(obj.fields.get("vertices"), objects)
        normals = numeric_array(obj.fields.get("normals"), objects)
        triangles = numeric_array(obj.fields.get("triangles"), objects)
        if not vertices or not triangles:
            continue
        if len(vertices) % 3:
            notes.append(f"Skipped {obj.class_name} with vertex array length {len(vertices)} not divisible by 3.")
            continue

        decoded_geometry = decode_triangle_geometry(vertices, normals or [], triangles, notes, obj.class_name)
        if decoded_geometry is None:
            continue
        positions, output_normals, indices = decoded_geometry

        signature = (obj.object_id, len(positions), len(indices))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        primitives.append(
            MeshPrimitive(
                name=f"Mesh3D_{obj.object_id}",
                positions=positions,
                indices=indices,
                color=mesh_color(obj, objects),
                normals=output_normals
            )
        )

    if not primitives:
        return []
    notes.append(f"Decoded {len(primitives)} Mesh3D primitive part(s).")
    return primitives


def decode_triangle_geometry(
    vertices: list[float] | list[int],
    normals: list[float] | list[int],
    triangles: list[float] | list[int],
    notes: list[str],
    class_name: str
) -> tuple[list[float], list[float] | None, list[int]] | None:
    vertex_count = len(vertices) // 3
    normal_count = len(normals) // 3 if len(normals) % 3 == 0 else 0
    raw = [int(index) for index in triangles]

    if len(raw) % 6 == 0 and fluent_face_vertices_are_valid(raw, vertex_count):
        vertex_positions = fluent_vertices_to_three(vertices)
        if normal_count and fluent_face_normals_are_valid(raw, normal_count):
            add_unique_note(notes, "Decoded Fluent triangle records as 3 vertex indices plus 3 normal indices per face.")
            return expand_vertices_with_native_normals(vertex_positions, fluent_normals_to_three(normals), raw)

        add_unique_note(notes, "Decoded Fluent triangle records as 3 vertex indices per 6-int face; native normals were unavailable.")
        indices = [raw[index + corner] for index in range(0, len(raw), 6) for corner in range(3)]
        return vertex_positions, None, indices

    if len(raw) % 3 == 0 and all(0 <= index < vertex_count for index in raw):
        add_unique_note(notes, "Decoded triangle records as plain 3-int vertex-index faces.")
        return fluent_vertices_to_three(vertices), None, raw

    notes.append(f"Skipped {class_name} with unsupported triangle index layout.")
    return None


def fluent_face_vertices_are_valid(raw: list[int], vertex_count: int) -> bool:
    return all(0 <= raw[index + corner] < vertex_count for index in range(0, len(raw), 6) for corner in range(3))


def fluent_face_normals_are_valid(raw: list[int], normal_count: int) -> bool:
    return all(0 <= raw[index + corner] < normal_count for index in range(0, len(raw), 6) for corner in range(3, 6))


def expand_vertices_with_native_normals(
    vertex_positions: list[float],
    normal_vectors: list[float],
    raw: list[int]
) -> tuple[list[float], list[float], list[int]]:
    positions: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    vertex_map: dict[tuple[int, int], int] = {}

    for index in range(0, len(raw), 6):
        for corner in range(3):
            vertex_index = raw[index + corner]
            normal_index = raw[index + 3 + corner]
            key = (vertex_index, normal_index)
            mapped_index = vertex_map.get(key)
            if mapped_index is None:
                mapped_index = len(positions) // 3
                vertex_map[key] = mapped_index
                positions.extend(vector_at(vertex_positions, vertex_index))
                normals.extend(normalized(vector_at(normal_vectors, normal_index)))
            indices.append(mapped_index)

    return positions, normals, indices


def dereference(value: Any, objects: dict[int, Any], seen: set[int] | None = None) -> Any:
    if not isinstance(value, Reference):
        return value
    seen = seen or set()
    if value.object_id in seen:
        return None
    seen.add(value.object_id)
    return dereference(objects.get(value.object_id), objects, seen)


def numeric_array(value: Any, objects: dict[int, Any]) -> list[float] | list[int] | None:
    resolved = dereference(value, objects)
    if isinstance(resolved, NetArray):
        return [item for item in resolved.values if isinstance(item, (int, float))]
    if isinstance(resolved, list):
        return [item for item in resolved if isinstance(item, (int, float))]
    return None


def mesh_color(mesh: NetObject, objects: dict[int, Any]) -> list[float]:
    material = dereference(mesh.fields.get("material"), objects)
    if isinstance(material, NetObject):
        diffuse = dereference(material.fields.get("diffuse"), objects)
        if isinstance(diffuse, NetObject):
            value = diffuse.fields.get("value")
            if isinstance(value, int):
                return argb_to_base_color(value)

    color = dereference(mesh.fields.get("color"), objects)
    if isinstance(color, NetObject):
        value = color.fields.get("value")
        if isinstance(value, int):
            return argb_to_base_color(value)

    return [0.74, 0.78, 0.76, 1.0]


def argb_to_base_color(value: int) -> list[float]:
    alpha = ((value >> 24) & 0xFF) / 255
    red = ((value >> 16) & 0xFF) / 255
    green = ((value >> 8) & 0xFF) / 255
    blue = (value & 0xFF) / 255
    return [red, green, blue, alpha or 1.0]


def fluent_vertices_to_three(values: list[float] | list[int]) -> list[float]:
    positions: list[float] = []
    for index in range(0, len(values), 3):
        x = float(values[index])
        y = float(values[index + 1])
        z = float(values[index + 2])
        positions.extend([x, z, -y])
    return positions


def fluent_normals_to_three(values: list[float] | list[int]) -> list[float]:
    normals: list[float] = []
    for index in range(0, len(values), 3):
        x = float(values[index])
        y = float(values[index + 1])
        z = float(values[index + 2])
        normals.extend(normalized((x, z, -y)))
    return normals


def add_unique_note(notes: list[str], note: str) -> None:
    if note not in notes:
        notes.append(note)


def placeholder_primitive(name: str) -> MeshPrimitive:
    size = 0.08
    height = 0.025
    positions = [
        -size, 0.0, -size,
        size, 0.0, -size,
        size, 0.0, size,
        -size, 0.0, size,
        -size, height, -size,
        size, height, -size,
        size, height, size,
        -size, height, size
    ]
    indices = [
        0, 1, 2, 0, 2, 3,
        4, 6, 5, 4, 7, 6,
        0, 4, 5, 0, 5, 1,
        1, 5, 6, 1, 6, 2,
        2, 6, 7, 2, 7, 3,
        3, 7, 4, 3, 4, 0
    ]
    return MeshPrimitive(name=f"{name}_placeholder", positions=positions, indices=indices, color=[0.84, 0.58, 0.22, 0.92])


def build_glb(
    metadata: XmshMetadata,
    primitives: list[MeshPrimitive],
    decoded: DecodedMesh,
    conversion_status: str
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    gltf_primitives: list[dict[str, Any]] = []
    all_positions: list[float] = []

    for primitive in primitives:
        positions = primitive.positions
        indices = primitive.indices
        normals = primitive.normals if primitive.normals and len(primitive.normals) == len(positions) else compute_vertex_normals(positions, indices)
        all_positions.extend(positions)

        position_accessor = append_accessor(
            binary,
            buffer_views,
            accessors,
            AccessorSpec(
                payload=pack_floats(positions),
                target=GL_ARRAY_BUFFER,
                component_type=GL_FLOAT,
                accessor_type="VEC3",
                count=len(positions) // 3,
                bounds=min_max_vectors(positions)
            )
        )
        normal_accessor = append_accessor(
            binary,
            buffer_views,
            accessors,
            AccessorSpec(
                payload=pack_floats(normals),
                target=GL_ARRAY_BUFFER,
                component_type=GL_FLOAT,
                accessor_type="VEC3",
                count=len(normals) // 3,
                bounds=None
            )
        )
        index_accessor = append_accessor(
            binary,
            buffer_views,
            accessors,
            AccessorSpec(
                payload=pack_uint32(indices),
                target=GL_ELEMENT_ARRAY_BUFFER,
                component_type=GL_UNSIGNED_INT,
                accessor_type="SCALAR",
                count=len(indices),
                bounds={"min": [min(indices) if indices else 0], "max": [max(indices) if indices else 0]}
            )
        )

        material_index = len(materials)
        materials.append(
            {
                "name": f"{primitive.name}_material",
                "pbrMetallicRoughness": {
                    "baseColorFactor": primitive.color,
                    "metallicFactor": 0.05,
                    "roughnessFactor": 0.55
                }
            }
        )

        gltf_primitives.append(
            {
                "attributes": {"POSITION": position_accessor, "NORMAL": normal_accessor},
                "indices": index_accessor,
                "material": material_index
            }
        )

    bounds = bounds_for_positions(all_positions)
    unit_metadata = infer_unit_metadata(bounds, conversion_status)
    asset_stem = metadata.guid or slug(metadata.name)
    gltf = {
        "asset": {"version": "2.0", "generator": "extract_fluent_meshes.py"},
        "scene": 0,
        "scenes": [{"nodes": [0], "extras": unit_metadata}],
        "nodes": [{"name": metadata.name or asset_stem, "mesh": 0, "extras": unit_metadata}],
        "meshes": [{"name": metadata.name or asset_stem, "primitives": gltf_primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "extras": {
            "fluentMeshGuid": metadata.guid,
            "fluentObjectName": metadata.name,
            "sourcePath": metadata.source_path,
            "conversionStatus": conversion_status,
            "nativeFormat": decoded.native_format,
            "compression": decoded.compression,
            "innerPayloadBytes": decoded.inner_payload_bytes,
            "bounds": bounds,
            "boundsMm": scale_bounds(bounds, unit_metadata["unitScaleToMm"]),
            **unit_metadata,
            "notes": decoded.notes
        }
    }
    return write_glb(gltf, bytes(binary)), bounds, unit_metadata


@dataclass
class AccessorSpec:
    payload: bytes
    target: int
    component_type: int
    accessor_type: str
    count: int
    bounds: dict[str, Any] | None = None


def append_accessor(
    binary: bytearray,
    buffer_views: list[dict[str, Any]],
    accessors: list[dict[str, Any]],
    spec: AccessorSpec
) -> int:
    align_binary(binary)
    byte_offset = len(binary)
    binary.extend(spec.payload)
    buffer_view_index = len(buffer_views)
    buffer_views.append({"buffer": 0, "byteOffset": byte_offset, "byteLength": len(spec.payload), "target": spec.target})
    accessor: dict[str, Any] = {
        "bufferView": buffer_view_index,
        "byteOffset": 0,
        "componentType": spec.component_type,
        "count": spec.count,
        "type": spec.accessor_type
    }
    if spec.bounds:
        accessor.update(spec.bounds)
    accessors.append(accessor)
    return len(accessors) - 1


def write_glb(gltf: dict[str, Any], binary: bytes) -> bytes:
    json_payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_payload = pad_bytes(json_payload, b" ")
    binary_payload = pad_bytes(binary, b"\x00")
    total_length = 12 + 8 + len(json_payload) + 8 + len(binary_payload)
    return b"".join(
        [
            b"glTF",
            struct.pack("<II", 2, total_length),
            struct.pack("<I4s", len(json_payload), b"JSON"),
            json_payload,
            struct.pack("<I4s", len(binary_payload), b"BIN\x00"),
            binary_payload
        ]
    )


def compute_vertex_normals(positions: list[float], indices: list[int]) -> list[float]:
    normals = [0.0] * len(positions)
    for index in range(0, len(indices), 3):
        ia, ib, ic = indices[index], indices[index + 1], indices[index + 2]
        ax, ay, az = vector_at(positions, ia)
        bx, by, bz = vector_at(positions, ib)
        cx, cy, cz = vector_at(positions, ic)
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = cross(ux, uy, uz, vx, vy, vz)
        for vertex_index in (ia, ib, ic):
            base = vertex_index * 3
            normals[base] += nx
            normals[base + 1] += ny
            normals[base + 2] += nz

    for index in range(0, len(normals), 3):
        nx, ny, nz = normals[index], normals[index + 1], normals[index + 2]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1e-9:
            normals[index] = nx / length
            normals[index + 1] = ny / length
            normals[index + 2] = nz / length
        else:
            normals[index + 1] = 1.0
    return normals


def vector_at(values: list[float], index: int) -> tuple[float, float, float]:
    base = index * 3
    return values[base], values[base + 1], values[base + 2]


def normalized(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = vector
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-9:
        return 0.0, 1.0, 0.0
    return x / length, y / length, z / length


def cross(ax: float, ay: float, az: float, bx: float, by: float, bz: float) -> tuple[float, float, float]:
    return ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx


def min_max_vectors(values: list[float]) -> dict[str, Any]:
    xs = values[0::3]
    ys = values[1::3]
    zs = values[2::3]
    return {
        "min": [min(xs), min(ys), min(zs)],
        "max": [max(xs), max(ys), max(zs)]
    }


def bounds_for_positions(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"min": [0, 0, 0], "max": [0, 0, 0], "size": [0, 0, 0]}
    bounds = min_max_vectors(values)
    return {
        **bounds,
        "size": [bounds["max"][index] - bounds["min"][index] for index in range(3)]
    }


def infer_unit_metadata(bounds: dict[str, Any], conversion_status: str) -> dict[str, Any]:
    sizes = [float(value) for value in bounds.get("size", []) if isinstance(value, (int, float)) and math.isfinite(value)]
    max_extent = max(sizes, default=0.0)
    if max_extent <= 0:
        return {
            "unitScaleToMm": 1,
            "unitScaleSource": f"{conversion_status}:unknown-bounds-assumed-mm",
            "nativeUnit": "mm"
        }
    if max_extent < 10:
        return {
            "unitScaleToMm": 1000,
            "unitScaleSource": "inferred:max-native-extent-under-10",
            "nativeUnit": "m"
        }
    return {
        "unitScaleToMm": 1,
        "unitScaleSource": "inferred:max-native-extent-at-least-10",
        "nativeUnit": "mm"
    }


def scale_bounds(bounds: dict[str, Any], scale: int | float) -> dict[str, Any]:
    return {
        key: [float(value) * scale for value in bounds.get(key, [])]
        for key in ("min", "max", "size")
    }


def pack_floats(values: list[float]) -> bytes:
    return struct.pack("<" + "f" * len(values), *values) if values else b""


def pack_uint32(values: list[int]) -> bytes:
    return struct.pack("<" + "I" * len(values), *values) if values else b""


def align_binary(binary: bytearray) -> None:
    while len(binary) % 4:
        binary.append(0)


def pad_bytes(payload: bytes, pad: bytes) -> bytes:
    remainder = len(payload) % 4
    if not remainder:
        return payload
    return payload + pad * (4 - remainder)


def xml_first(root: ElementTree.Element, local_name: str) -> ElementTree.Element | None:
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == local_name:
            return node
    return None


def xml_text(root: ElementTree.Element, local_name: str) -> str:
    node = xml_first(root, local_name)
    return "".join(node.itertext()).strip() if node is not None else ""


def normalize_guid(value: str) -> str:
    match = GUID_RE.search(value or "")
    return match.group(0).lower() if match else ""


def slug(value: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", value.lower())) or "mesh"


if __name__ == "__main__":
    raise SystemExit(main())
