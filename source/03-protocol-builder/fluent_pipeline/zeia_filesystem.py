"""Embed prompt media, audio, and external files into packaged ``generated_project.zeia``."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from .checksums import entry_checksum_state, recompute_checksum_bytes

_FS_MAPPING_ENTRY = "fs/mapping.xml"
_CONTENT_XML_ENTRY = "meta/content.xml"
_FILESYSTEM_ENTRIES_BLOCK_RE = re.compile(r"<FilesystemEntries>.*?</FilesystemEntries>", re.DOTALL)
_CONTENT_ENTRY_RE = re.compile(r"<Entry>(.*?)</Entry>")
_FILE_REFERENCE_BLOCK_RE = re.compile(r"<FileReference>.*?</FileReference>", re.DOTALL | re.IGNORECASE)
_FILE_REFERENCE_FILE_RE = re.compile(r"<File>(.*?)</File>", re.DOTALL | re.IGNORECASE)
_PAYLOAD_DATA_RE = re.compile(r"<PayloadData\b")
_DIRECTORY_BLOCK_RE = re.compile(r"<Directory>.*?</Directory>", re.DOTALL)
_KEY_RE = re.compile(r"<Key>(\d+)</Key>")
_PATH_RE = re.compile(r"<Path>(.*?)</Path>", re.DOTALL)
_FS_PATH_TAG_RES = (
    re.compile(r"<FileReference>.*?<File>(.*?)</File>.*?</FileReference>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<SelectedImagePath\b[^>]*>(.*?)</SelectedImagePath>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<CustomDetailImageFilePath\b[^>]*>(.*?)</CustomDetailImageFilePath>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<SelectedSoundPath\b[^>]*>(.*?)</SelectedSoundPath>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<SoundFile\b[^>]*>(.*?)</SoundFile>", re.DOTALL | re.IGNORECASE),
)


def _xml_escape_text(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _normalize_windows_path(value: str) -> str:
    text = str(value or "").strip().replace("/", "\\")
    return text.rstrip("\\")


def _windows_parent(path: str) -> str:
    parent = PureWindowsPath(_normalize_windows_path(path)).parent
    return str(parent) if str(parent) not in {"", "."} else _normalize_windows_path(path)


def _basename_for_fs(path: str) -> str:
    return PureWindowsPath(_normalize_windows_path(path)).name


def parse_fs_mapping_directories(data: bytes) -> list[tuple[int, str]]:
    """Parse ``fs/mapping.xml`` into ``(key, directory_path)`` pairs."""
    text = data.decode("utf-8-sig", errors="replace")
    directories: list[tuple[int, str]] = []
    for block in _DIRECTORY_BLOCK_RE.findall(text):
        key_match = _KEY_RE.search(block)
        path_match = _PATH_RE.search(block)
        if not key_match or not path_match:
            continue
        directories.append((int(key_match.group(1)), _normalize_windows_path(path_match.group(1))))
    return directories


def archive_fs_path_to_content_entry(archive_path: str) -> str:
    """Convert a packaged ``fs/`` zip member to a ``content.xml`` Entry value."""
    normalized = str(archive_path or "").strip().replace("\\", "/").lstrip("/")
    lowered = normalized.lower()
    if not lowered.startswith("fs/"):
        raise ValueError(f"not an fs archive path: {archive_path!r}")
    remainder = normalized[3:]
    if not remainder:
        raise ValueError(f"empty fs archive path: {archive_path!r}")
    parts = [part for part in remainder.split("/") if part]
    if len(parts) == 1:
        return parts[0]
    return "\\".join(parts)


def content_entry_to_archive_fs_path(content_entry: str) -> str:
    """Convert a ``content.xml`` FilesystemEntries Entry value to a packaged ``fs/`` path."""
    text = str(content_entry or "").strip().replace("/", "\\")
    if not text:
        raise ValueError("empty content filesystem entry")
    if "\\" in text:
        return "fs/" + text.replace("\\", "/")
    return f"fs/{text}"


def _order_filesystem_entry_names(entry_names: list[str]) -> list[str]:
    unique = sorted({str(name) for name in entry_names if str(name).strip()}, key=str.casefold)
    without_mapping = [name for name in unique if name.casefold() != "mapping.xml"]
    ordered = without_mapping
    if any(name.casefold() == "mapping.xml" for name in unique):
        ordered.append("mapping.xml")
    return ordered


def _build_filesystem_entries_block(entry_names: list[str]) -> str:
    lines = ["\t\t<FilesystemEntries>"]
    for name in _order_filesystem_entry_names(entry_names):
        lines.append(f"\t\t\t<Entry>{_xml_escape_text(name)}</Entry>")
    lines.append("\t\t</FilesystemEntries>")
    return "\r\n".join(lines) + "\r\n"


def _parse_content_filesystem_entries(content_xml_bytes: bytes) -> list[str]:
    text = content_xml_bytes.decode("utf-8-sig", errors="replace")
    block = _FILESYSTEM_ENTRIES_BLOCK_RE.search(text)
    if not block:
        return []
    return [match.group(1).strip() for match in _CONTENT_ENTRY_RE.finditer(block.group(0)) if match.group(1).strip()]


def update_archive_content_filesystem(content_xml_bytes: bytes, filesystem_entry_names: list[str]) -> bytes:
    """Insert or replace ``<FilesystemEntries>`` in ``meta/content.xml`` and restamp checksum."""
    text = content_xml_bytes.decode("utf-8-sig", errors="replace")
    new_block = _build_filesystem_entries_block(filesystem_entry_names)
    if _FILESYSTEM_ENTRIES_BLOCK_RE.search(text):
        updated = _FILESYSTEM_ENTRIES_BLOCK_RE.sub(lambda _match: new_block, text, count=1)
    elif "<DatastoreEntries>" in text:
        updated = text.replace("<DatastoreEntries>", new_block + "\t\t<DatastoreEntries>", 1)
    else:
        updated, count = re.subn(
            r"(\s*</Payload>)",
            lambda match: new_block + match.group(1),
            text,
            count=1,
        )
        if count == 0:
            updated = text
    encoded = updated.encode("utf-8")
    recomputed = recompute_checksum_bytes(encoded)
    return recomputed if recomputed is not None else encoded


def build_fs_mapping_xml(directories: list[tuple[int, str]]) -> bytes:
    """Build ``fs/mapping.xml`` bytes with a valid ``DirectoryMappings`` checksum."""
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<DirectoryMappings>", "\t<Payload>"]
    for key, directory in sorted(directories, key=lambda item: item[0]):
        lines.extend(
            [
                "\t\t<Directory>",
                f"\t\t\t<Key>{int(key)}</Key>",
                f"\t\t\t<Path>{_xml_escape_text(_normalize_windows_path(directory))}</Path>",
                "\t\t</Directory>",
            ]
        )
    lines.extend(["\t</Payload>", "\t<Checksum></Checksum>", "</DirectoryMappings>"])
    payload = "\r\n".join(lines) + "\r\n"
    recomputed = recompute_checksum_bytes(payload.encode("utf-8"))
    return recomputed if recomputed is not None else payload.encode("utf-8")


@dataclass
class FsEmbedFile:
    key: int
    archive_entry: str
    source_path: Path
    target_absolute: str


@dataclass
class FsEmbedPlan:
    directories: list[tuple[int, str]] = field(default_factory=list)
    files: list[FsEmbedFile] = field(default_factory=list)


def _resolve_media_source(media_dir: Path, basename: str) -> Path | None:
    if not basename:
        return None
    for candidate in (
        media_dir / "processed" / basename,
        media_dir / basename,
    ):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()
    return None


def _touchtools_target_directory(media_path_map: Mapping[str, Any]) -> str:
    base = _normalize_windows_path(str(media_path_map.get("touchtools_dir") or ""))
    sub = str(media_path_map.get("subfolder") or "").strip()
    if not base:
        return ""
    if not sub:
        return base
    return _normalize_windows_path(str(PureWindowsPath(base) / sub))


def _assign_directory_keys(
    requested: list[str],
    *,
    existing: list[tuple[int, str]] | None = None,
) -> list[tuple[int, str]]:
    """Assign stable fs keys for directory paths, reusing existing mappings when possible."""
    ordered: list[tuple[int, str]] = [
        (int(key), _normalize_windows_path(path)) for key, path in (existing or []) if _normalize_windows_path(path)
    ]
    by_path = {path.casefold(): int(key) for key, path in ordered}
    used_keys = {int(key) for key, _ in ordered}
    next_key = (max(used_keys) + 1) if used_keys else 1

    for raw in requested:
        path = _normalize_windows_path(raw)
        if not path or path.casefold() in by_path:
            continue
        while next_key in used_keys:
            next_key += 1
        by_path[path.casefold()] = next_key
        ordered.append((next_key, path))
        used_keys.add(next_key)
        next_key += 1
    return sorted(ordered, key=lambda item: item[0])


def plan_fs_embed(
    *,
    media_dir: Path | None,
    media_path_map: Mapping[str, Any] | None,
    external_files_dir: Path | None,
    external_entries: list[Mapping[str, Any]] | None,
    existing_directories: list[tuple[int, str]] | None = None,
) -> FsEmbedPlan:
    """Plan ``fs/`` payload files and directory mappings for one packaged ZEIA."""
    requested_directories: list[str] = []
    media_target = _touchtools_target_directory(media_path_map or {})
    if media_target:
        requested_directories.append(media_target)

    external_targets: list[tuple[str, Path, str]] = []
    external_root = external_files_dir if external_files_dir is not None else None
    for item in external_entries or []:
        expected = _normalize_windows_path(str(item.get("expected_path") or ""))
        staged_raw = str(item.get("staged_path") or "").strip()
        if not expected or not staged_raw:
            continue
        staged = Path(staged_raw)
        if not staged.is_file() and external_root is not None:
            staged = external_root / staged.name
        if not staged.is_file():
            continue
        parent = _windows_parent(expected)
        if parent:
            requested_directories.append(parent)
        external_targets.append((expected, staged.resolve(), parent))

    resolved_directories = _assign_directory_keys(
        requested_directories,
        existing=existing_directories,
    )
    path_keys = {path.casefold(): key for key, path in resolved_directories}
    files: list[FsEmbedFile] = []

    if media_dir is not None and media_target:
        media_key = path_keys.get(media_target.casefold(), 1)
        seen_basenames: set[str] = set()
        for entry in (media_path_map or {}).get("entries") or []:
            if not isinstance(entry, dict):
                continue
            absolute_path = _normalize_windows_path(str(entry.get("absolute_path") or ""))
            filename = str(entry.get("filename") or _basename_for_fs(absolute_path)).strip()
            if not filename or filename in seen_basenames:
                continue
            source = _resolve_media_source(media_dir, filename)
            if source is None:
                continue
            seen_basenames.add(filename)
            archive_entry = f"fs/{media_key}/{filename}"
            files.append(
                FsEmbedFile(
                    key=media_key,
                    archive_entry=archive_entry.replace("/", "\\"),
                    source_path=source,
                    target_absolute=absolute_path or str(PureWindowsPath(media_target) / filename),
                )
            )

    for expected, staged, parent in external_targets:
        key = path_keys.get(parent.casefold())
        if key is None:
            continue
        basename = _basename_for_fs(expected)
        archive_entry = f"fs/{key}/{basename}"
        files.append(
            FsEmbedFile(
                key=key,
                archive_entry=archive_entry.replace("/", "\\"),
                source_path=staged,
                target_absolute=expected,
            )
        )

    return FsEmbedPlan(directories=resolved_directories, files=files)


def embed_filesystem_in_archive(archive_path: Path, plan: FsEmbedPlan) -> dict[str, Any]:
    """Merge ``fs/`` files and ``fs/mapping.xml`` into an existing ZEIA zip."""
    summary: dict[str, Any] = {
        "archive": str(archive_path),
        "embedded_files": [],
        "mapping_entry": _FS_MAPPING_ENTRY,
        "directory_count": len(plan.directories),
        "file_count": 0,
    }
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        summary["error"] = "archive_missing_or_invalid"
        return summary
    if not plan.files and not plan.directories:
        summary["skipped"] = True
        return summary

    existing_mapping: list[tuple[int, str]] = []
    with zipfile.ZipFile(archive_path, "r") as source:
        try:
            mapping_name = next(
                (name for name in source.namelist() if name.replace("\\", "/").lower() == _FS_MAPPING_ENTRY),
                None,
            )
            if mapping_name:
                existing_mapping = parse_fs_mapping_directories(source.read(mapping_name))
        except (KeyError, OSError):
            existing_mapping = []

        merged_directories = list(existing_mapping)
        seen_paths = {path.casefold() for _, path in merged_directories}
        for key, path in plan.directories:
            if path.casefold() in seen_paths:
                continue
            merged_directories.append((key, path))
            seen_paths.add(path.casefold())
        merged_directories.sort(key=lambda item: item[0])
        mapping_bytes = build_fs_mapping_xml(merged_directories)

        entries: dict[str, bytes] = {}
        for info in source.infolist():
            normalized = info.filename.replace("\\", "/").lower()
            if normalized == _FS_MAPPING_ENTRY:
                continue
            if any(
                normalized == embed.archive_entry.replace("\\", "/").lower()
                for embed in plan.files
            ):
                continue
            entries[info.filename] = source.read(info.filename)

        for embed in plan.files:
            try:
                payload = embed.source_path.read_bytes()
            except OSError:
                continue
            entry_name = embed.archive_entry
            entries[entry_name] = payload
            summary["embedded_files"].append(
                {
                    "archive_entry": entry_name,
                    "source_path": str(embed.source_path),
                    "target_absolute": embed.target_absolute,
                    "fs_key": embed.key,
                }
            )
        entries[_FS_MAPPING_ENTRY] = mapping_bytes

        fs_archive_paths = sorted(
            name
            for name in entries
            if name.replace("\\", "/").lower().startswith("fs/")
        )
        content_name = next(
            (
                name
                for name in entries
                if name.replace("\\", "/").lower() == _CONTENT_XML_ENTRY
            ),
            None,
        )
        if content_name and fs_archive_paths:
            content_entries = [archive_fs_path_to_content_entry(path) for path in fs_archive_paths]
            entries[content_name] = update_archive_content_filesystem(
                entries[content_name],
                content_entries,
            )
            summary["content_filesystem_entries"] = len(content_entries)

    tmp_path = archive_path.with_suffix(archive_path.suffix + ".fs.tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        for name, payload in entries.items():
            dest.writestr(name, payload)
    tmp_path.replace(archive_path)

    summary["file_count"] = len(summary["embedded_files"])
    summary["directories"] = [{"key": key, "path": path} for key, path in merged_directories]
    return summary


def copy_referenced_filesystem_from_archives(
    source_archives: list[Path],
    destination_archive: Path,
) -> dict[str, Any]:
    """Copy the file closure required by every XSCR shipped in the destination."""
    referenced_paths = collect_archive_file_reference_paths(destination_archive)
    ordered_sources: list[Path] = []
    seen_sources: set[str] = set()
    for raw_source in source_archives:
        source = Path(raw_source)
        key = str(source.resolve()).casefold()
        if key in seen_sources:
            continue
        seen_sources.add(key)
        ordered_sources.append(source)

    summary: dict[str, Any] = {
        "source_archive": str(ordered_sources[0]) if ordered_sources else None,
        "source_archives": [str(path) for path in ordered_sources],
        "destination_archive": str(destination_archive),
        "referenced_path_count": len(_dedupe_windows_paths(referenced_paths)),
        "copied_files": [],
        "unresolved_paths": [],
        "conflicting_paths": [],
        "source_archive_errors": [],
        "directory_count": 0,
    }
    if not destination_archive.is_file() or not zipfile.is_zipfile(destination_archive):
        summary["error"] = "destination_archive_missing_or_invalid"
        return summary

    source_indexes: list[dict[str, Any]] = []
    for source_archive in ordered_sources:
        if not source_archive.is_file() or not zipfile.is_zipfile(source_archive):
            summary["source_archive_errors"].append(
                {
                    "source_archive": str(source_archive),
                    "reason": "source_archive_missing_or_invalid",
                }
            )
            continue
        with zipfile.ZipFile(source_archive, "r") as source:
            source_entries = {
                info.filename: source.read(info.filename) for info in source.infolist()
            }
        mapping_name = next(
            (
                name
                for name in source_entries
                if name.replace("\\", "/").casefold() == _FS_MAPPING_ENTRY
            ),
            None,
        )
        source_directories = (
            parse_fs_mapping_directories(source_entries[mapping_name])
            if mapping_name is not None
            else []
        )
        source_indexes.append(
            {
                "path": source_archive,
                "entries": source_entries,
                "directory_by_path": {
                    path.casefold(): (key, path) for key, path in source_directories
                },
                "name_by_normalized": {
                    name.replace("\\", "/").casefold(): name for name in source_entries
                },
            }
        )

    if not source_indexes:
        summary["error"] = "source_archives_missing_or_invalid"
        summary["unresolved_paths"] = _dedupe_windows_paths(referenced_paths)
        return summary

    selected_directories: dict[int, str] = {}
    selected_entries: dict[str, bytes] = {}
    output_key_by_directory: dict[str, int] = {}
    used_output_keys: set[int] = set()

    def _output_directory_key(directory_path: str, preferred_key: int) -> int:
        normalized = directory_path.casefold()
        existing = output_key_by_directory.get(normalized)
        if existing is not None:
            return existing
        output_key = preferred_key
        if output_key in used_output_keys:
            output_key = max(used_output_keys, default=0) + 1
            while output_key in used_output_keys:
                output_key += 1
        output_key_by_directory[normalized] = output_key
        used_output_keys.add(output_key)
        selected_directories[output_key] = directory_path
        return output_key

    for referenced_path in _dedupe_windows_paths(referenced_paths):
        parent = _windows_parent(referenced_path)
        candidates: list[dict[str, Any]] = []
        for source_index in source_indexes:
            directory = source_index["directory_by_path"].get(parent.casefold())
            if directory is None:
                continue
            source_key, mapped_path = directory
            wanted = f"fs/{source_key}/{_basename_for_fs(referenced_path)}".casefold()
            source_name = source_index["name_by_normalized"].get(wanted)
            if source_name is None:
                continue
            payload = source_index["entries"][source_name]
            candidates.append(
                {
                    "source_archive": source_index["path"],
                    "source_entry": source_name,
                    "source_key": source_key,
                    "mapped_path": mapped_path,
                    "payload": payload,
                }
            )
        if not candidates:
            summary["unresolved_paths"].append(referenced_path)
            continue

        selected = candidates[0]
        conflicting = [
            candidate
            for candidate in candidates[1:]
            if candidate["payload"] != selected["payload"]
        ]
        if conflicting:
            summary["conflicting_paths"].append(
                {
                    "referenced_path": referenced_path,
                    "candidates": [
                        {
                            "source_archive": str(candidate["source_archive"]),
                            "archive_entry": candidate["source_entry"],
                            "size": len(candidate["payload"]),
                            "sha256": hashlib.sha256(candidate["payload"]).hexdigest(),
                        }
                        for candidate in candidates
                    ],
                }
            )
            continue

        output_key = _output_directory_key(
            selected["mapped_path"],
            selected["source_key"],
        )
        destination_entry = (
            f"fs/{output_key}/{_basename_for_fs(referenced_path)}"
        )
        selected_entries[destination_entry] = selected["payload"]
        summary["copied_files"].append(
            {
                "referenced_path": referenced_path,
                "archive_entry": destination_entry,
                "fs_key": output_key,
                "source_archive": str(selected["source_archive"]),
                "source_archive_entry": selected["source_entry"],
                "matching_source_count": len(candidates),
            }
        )

    with zipfile.ZipFile(destination_archive, "r") as destination:
        destination_entries = {
            info.filename: destination.read(info.filename) for info in destination.infolist()
        }
    for name in list(destination_entries):
        if name.replace("\\", "/").casefold().startswith("fs/"):
            del destination_entries[name]
    destination_entries.update(selected_entries)

    if selected_entries:
        destination_entries[_FS_MAPPING_ENTRY] = build_fs_mapping_xml(
            sorted(selected_directories.items())
        )
        content_name = next(
            (
                name
                for name in destination_entries
                if name.replace("\\", "/").casefold() == _CONTENT_XML_ENTRY
            ),
            None,
        )
        if content_name is None:
            summary["error"] = "destination_content_xml_missing"
            return summary
        fs_archive_paths = sorted(
            name
            for name in destination_entries
            if name.replace("\\", "/").casefold().startswith("fs/")
        )
        destination_entries[content_name] = update_archive_content_filesystem(
            destination_entries[content_name],
            [archive_fs_path_to_content_entry(path) for path in fs_archive_paths],
        )

    tmp_path = destination_archive.with_suffix(destination_archive.suffix + ".fs-copy.tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        for name, payload in destination_entries.items():
            dest.writestr(name, payload)
    tmp_path.replace(destination_archive)
    summary["file_count"] = len(selected_entries)
    summary["directory_count"] = len(selected_directories)
    summary["complete"] = (
        not summary["unresolved_paths"]
        and not summary["conflicting_paths"]
        and not summary["source_archive_errors"]
        and not summary.get("error")
    )
    return summary


def collect_archive_file_reference_paths(archive_path: Path) -> list[str]:
    """Collect the union of file references from every XSCR shipped in a ZEIA."""
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        return []

    referenced_paths: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as archive:
        for info in archive.infolist():
            if not info.filename.replace("\\", "/").casefold().endswith(".xscr"):
                continue
            text = archive.read(info.filename).decode("utf-8-sig", errors="replace")
            referenced_paths.extend(_extract_file_reference_paths(text))
    return _dedupe_windows_paths(referenced_paths)


_MEDIA_PAYLOAD_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".mp3",
        ".wav",
        ".m4a",
        ".mp4",
        ".webm",
        ".mov",
    }
)


def extract_archive_filesystem_payloads(
    archive_path: Path,
    destination_dir: Path,
) -> list[Path]:
    """Extract packaged ``fs/{key}/file`` media payloads for the V2 delivery media folder.

    Full-export ZEIA packages can ship the same basename under different ``fs/{key}/``
    folders with different bytes. When that happens, keep both files by prefixing the
    destination name with the filesystem key instead of aborting publication.

    Only image/audio/video extensions are extracted into ``media/``. Scripts such as
    ``.bat`` / ``.vb`` stay inside the ZEIA (and may be staged separately under
    ``source/external-files/``) so V2 delivery still has a single root setup BAT.
    """
    extracted: list[Path] = []
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        return extracted
    destination_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as source:
        for info in source.infolist():
            normalized = info.filename.replace("\\", "/")
            if not normalized.casefold().startswith("fs/") or normalized.casefold() == _FS_MAPPING_ENTRY:
                continue
            parts = PurePosixPath(normalized).parts
            filename = PureWindowsPath(normalized).name
            if not filename:
                continue
            if PureWindowsPath(filename).suffix.casefold() not in _MEDIA_PAYLOAD_SUFFIXES:
                continue
            payload = source.read(info.filename)
            destination = destination_dir / filename
            if destination.exists() and destination.read_bytes() != payload:
                fs_key = parts[1] if len(parts) >= 3 else "dup"
                safe_key = re.sub(r"[^A-Za-z0-9._-]+", "_", str(fs_key)).strip("._") or "dup"
                destination = destination_dir / f"{safe_key}__{filename}"
                suffix = 2
                while destination.exists() and destination.read_bytes() != payload:
                    destination = destination_dir / f"{safe_key}__{suffix}__{filename}"
                    suffix += 1
            if not destination.exists():
                destination.write_bytes(payload)
            extracted.append(destination)
    return extracted


def _dedupe_windows_paths(values: list[str]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_windows_path(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        paths.append(normalized)
    return paths


def _extract_file_reference_paths(text: str) -> list[str]:
    paths: list[str] = []
    for block in _FILE_REFERENCE_BLOCK_RE.findall(text):
        match = _FILE_REFERENCE_FILE_RE.search(block)
        if not match:
            continue
        value = re.sub(r"\s+", " ", match.group(1)).strip()
        if value and value not in paths:
            paths.append(value)
    return paths


def collect_file_reference_paths(
    media_path_map: Mapping[str, Any] | None,
    ir: Mapping[str, Any] | None,
    external_report: Mapping[str, Any] | None,
) -> list[str]:
    """Collect absolute paths that should appear as script ``FileReference`` entries."""
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        text = _normalize_windows_path(path)
        if not text or not re.match(r"^[A-Za-z]:\\", text):
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        ordered.append(text)

    for entry in (media_path_map or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        _add(str(entry.get("absolute_path") or ""))

    for item in (external_report or {}).get("entries") or []:
        if not isinstance(item, dict):
            continue
        if item.get("status") in {"present", "found_elsewhere"}:
            _add(str(item.get("expected_path") or ""))

    return ordered


def ensure_script_file_references(xscr_path: Path, paths: list[str]) -> list[str]:
    """Inject missing ``<FileReference><File>`` blocks before ``<PayloadData>``."""
    if not xscr_path.is_file() or not paths:
        return []
    try:
        text = xscr_path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    if not _PAYLOAD_DATA_RE.search(text):
        return []

    existing = {path.casefold() for path in _extract_file_reference_paths(text)}
    to_add: list[str] = []
    for raw in paths:
        path = _normalize_windows_path(raw)
        if not path or path.casefold() in existing:
            continue
        existing.add(path.casefold())
        to_add.append(path)
    if not to_add:
        return []

    blocks = "\n".join(
        f"    <FileReference>\n      <File>{_xml_escape_text(path)}</File>\n    </FileReference>"
        for path in to_add
    )
    updated = _PAYLOAD_DATA_RE.sub(lambda match: blocks + "\n" + match.group(0), text, count=1)
    if updated == text:
        return []
    xscr_path.write_text(updated, encoding="utf-8")
    return to_add


_TOUCHTOOLS_IMAGES_MARKER = "TouchToolsData\\Images\\"


def strip_orphan_touchtools_media_file_references(xscr_path: Path) -> list[str]:
    """Drop TouchTools media FileReferences not used by prompt image/sound fields.

    Regeneration that replaces a same-GUID baseline can retain legacy ``stepN.*``
    FileReference entries from FluentControl's archive writer merge. Prompt
    SelectedImagePath/CustomDetail/Sound paths are the source of truth.
    """
    if not xscr_path.is_file():
        return []
    try:
        text = xscr_path.read_text(encoding="utf-8-sig")
    except OSError:
        return []

    referenced_names: set[str] = set()
    for pattern in _FS_PATH_TAG_RES[1:]:
        for raw in pattern.findall(text):
            name = PureWindowsPath(_normalize_windows_path(raw)).name
            if name:
                referenced_names.add(name.casefold())

    removed: list[str] = []

    def _keep_block(match: re.Match[str]) -> str:
        block = match.group(0)
        file_match = _FILE_REFERENCE_FILE_RE.search(block)
        if file_match is None:
            return block
        path = _normalize_windows_path(file_match.group(1))
        if _TOUCHTOOLS_IMAGES_MARKER.casefold() not in path.casefold():
            return block
        name = PureWindowsPath(path).name
        if name.casefold() in referenced_names:
            return block
        removed.append(path)
        return ""

    updated = _FILE_REFERENCE_BLOCK_RE.sub(_keep_block, text)
    updated = re.sub(r"\n{3,}", "\n\n", updated)
    if removed:
        xscr_path.write_text(updated, encoding="utf-8")
    return removed


def repair_archive_content_filesystem(archive_path: Path) -> dict[str, Any]:
    """Patch ``meta/content.xml`` FilesystemEntries from existing ``fs/`` zip members."""
    summary: dict[str, Any] = {
        "archive": str(archive_path),
        "filesystem_entry_count": 0,
        "patched": False,
    }
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        summary["error"] = "archive_missing_or_invalid"
        return summary

    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(archive_path, "r") as source:
        for info in source.infolist():
            entries[info.filename] = source.read(info.filename)

    fs_archive_paths = sorted(
        name for name in entries if name.replace("\\", "/").lower().startswith("fs/")
    )
    if not fs_archive_paths:
        summary["skipped"] = True
        return summary

    content_name = next(
        (
            name
            for name in entries
            if name.replace("\\", "/").lower() == _CONTENT_XML_ENTRY
        ),
        None,
    )
    if content_name is None:
        summary["error"] = "content_xml_missing"
        return summary

    content_entries = [archive_fs_path_to_content_entry(path) for path in fs_archive_paths]
    entries[content_name] = update_archive_content_filesystem(entries[content_name], content_entries)
    summary["filesystem_entry_count"] = len(content_entries)
    summary["patched"] = True

    tmp_path = archive_path.with_suffix(archive_path.suffix + ".repair.tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        for name, payload in entries.items():
            dest.writestr(name, payload)
    tmp_path.replace(archive_path)
    return summary


def _archive_fs_mapping(archive_data: dict[str, bytes]) -> dict[str, str]:
    mapping_name = next(
        (name for name in archive_data if name.replace("\\", "/").lower() == _FS_MAPPING_ENTRY),
        None,
    )
    if not mapping_name:
        return {}
    directories = parse_fs_mapping_directories(archive_data[mapping_name])
    return {path.casefold(): f"fs/{key}/" for key, path in directories}


def _path_maps_to_fs_directory(path: str, mapping: dict[str, str]) -> tuple[str, str] | None:
    normalized = _normalize_windows_path(path)
    if not normalized:
        return None
    parent = _windows_parent(normalized).casefold()
    prefix = mapping.get(parent)
    if not prefix:
        return None
    basename = _basename_for_fs(normalized)
    return prefix, basename


def _audit_content_filesystem_manifest(archive_data: dict[str, bytes]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    archive_names = {name.replace("\\", "/").lower(): name for name in archive_data}
    fs_archive_paths = sorted(
        name for name in archive_data if name.replace("\\", "/").lower().startswith("fs/")
    )
    if not fs_archive_paths:
        return findings

    content_name = next(
        (
            name
            for name in archive_data
            if name.replace("\\", "/").lower() == _CONTENT_XML_ENTRY
        ),
        None,
    )
    if content_name is None:
        findings.append(
            {
                "kind": "missing_content_xml",
                "detail": (
                    "archive contains fs/ payload files but meta/content.xml is missing"
                ),
            }
        )
        return findings

    content_bytes = archive_data[content_name]
    declared_entries = _parse_content_filesystem_entries(content_bytes)
    expected_entries = {archive_fs_path_to_content_entry(path) for path in fs_archive_paths}
    declared_set = set(declared_entries)

    if not declared_entries:
        findings.append(
            {
                "kind": "missing_filesystem_entries_manifest",
                "entry": content_name,
                "detail": (
                    f"archive has {len(fs_archive_paths)} fs/ payload file(s) but "
                    "meta/content.xml lacks a FilesystemEntries manifest"
                ),
            }
        )
    else:
        for missing in sorted(expected_entries - declared_set, key=str.casefold):
            archive_path = content_entry_to_archive_fs_path(missing)
            findings.append(
                {
                    "kind": "filesystem_manifest_missing_entry",
                    "entry": content_name,
                    "content_entry": missing,
                    "fs_entry": archive_names.get(
                        archive_path.replace("\\", "/").lower(),
                        archive_path,
                    ),
                    "detail": (
                        f"fs payload `{archive_path}` is present in the archive but "
                        f"FilesystemEntries omits `{missing}`"
                    ),
                }
            )
        for extra in sorted(declared_set - expected_entries, key=str.casefold):
            archive_path = content_entry_to_archive_fs_path(extra)
            normalized = archive_path.replace("\\", "/").lower()
            if normalized not in archive_names:
                findings.append(
                    {
                        "kind": "filesystem_manifest_orphan_entry",
                        "entry": content_name,
                        "content_entry": extra,
                        "fs_entry": archive_path,
                        "detail": (
                            f"FilesystemEntries declares `{extra}` but archive file "
                            f"`{archive_path}` is missing"
                        ),
                    }
                )

    checksum_state = entry_checksum_state(content_bytes)
    if checksum_state != "valid":
        findings.append(
            {
                "kind": "invalid_content_checksum",
                "entry": content_name,
                "checksum_state": checksum_state,
                "detail": (
                    "meta/content.xml ArchiveContent checksum is not valid after fs embed"
                ),
            }
        )
    return findings


def audit_archive_filesystem(archive_data: dict[str, bytes]) -> list[dict[str, Any]]:
    """Return blocking findings when mapped file refs lack ``fs/{key}/`` payloads."""
    findings = _audit_content_filesystem_manifest(archive_data)
    mapping = _archive_fs_mapping(archive_data)

    archive_names = {name.replace("\\", "/").lower(): name for name in archive_data}
    seen: set[tuple[str, str]] = set()

    for entry, data in archive_data.items():
        if not entry.replace("\\", "/").lower().endswith(".xscr"):
            continue
        text = data.decode("utf-8-sig", errors="replace")
        for pattern in _FS_PATH_TAG_RES:
            for match in pattern.finditer(text):
                raw = re.sub(r"\s+", " ", match.group(1)).strip()
                resolved = _path_maps_to_fs_directory(raw, mapping)
                if resolved is None:
                    if re.match(r"^[A-Za-z]:[\\/]", raw):
                        key = (entry, raw.casefold())
                        if key in seen:
                            continue
                        seen.add(key)
                        findings.append(
                            {
                                "kind": "unmapped_file_reference",
                                "entry": entry,
                                "referenced_path": raw,
                                "detail": (
                                    f"script references `{raw}` but fs/mapping.xml does not "
                                    "map its parent directory"
                                ),
                            }
                        )
                    continue
                prefix, basename = resolved
                fs_entry = f"{prefix}{basename}".replace("/", "\\").lower()
                key = (entry, fs_entry)
                if key in seen:
                    continue
                seen.add(key)
                if fs_entry not in archive_names and fs_entry.replace("\\", "/") not in archive_names:
                    findings.append(
                        {
                            "kind": "missing_fs_payload",
                            "entry": entry,
                            "fs_entry": fs_entry,
                            "referenced_path": raw,
                            "detail": (
                                f"script references `{raw}` which maps to `{fs_entry}` "
                                "but that file is missing from the packaged ZEIA fs/ payload"
                            ),
                        }
                    )
    return findings
