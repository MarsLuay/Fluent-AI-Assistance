"""Build ``driver_macros.json`` from ZEIA scripts / DataStore driver objects.

Inventory is mined only from real ``LegacyDriverMacro`` / ``ApplicationDriverMacro``
usages (and optional ApplicationDriver DataStore objects). Soft-fail to an empty
catalog when absent — never invent vendor macro/module names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import xml_compat as ET
from .runner import write_json

DRIVER_MACROS_SCHEMA_VERSION = "tecan.driver_macros.v1"
DRIVER_MACROS_FILENAME = "driver_macros.json"
_MACRO_TAGS = frozenset({"legacydrivermacro", "applicationdrivermacro"})
_DRIVER_OBJECT_HINTS = frozenset(
    {
        "applicationdriver",
        "legacydriver",
        "applicationdrivermacro",
        "legacydrivermacro",
    }
)


def build_driver_macros_catalog(
    *,
    manifest: Mapping[str, Any] | None = None,
    context_root: Path | str | None = None,
    source: str = "zeia_scripts",
    max_xml_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any]:
    """Mine macro_name / module_name pairs from scripts and optional DataStore objects."""
    entries_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

    for path in _script_paths(manifest, context_root):
        try:
            for entry in _macros_from_xscr(path, max_xml_bytes=max_xml_bytes):
                key = (
                    str(entry.get("macro_name") or "").casefold(),
                    str(entry.get("module_name") or "").casefold(),
                    str(entry.get("macro_kind") or "").casefold(),
                )
                if not key[0]:
                    continue
                entries_by_key.setdefault(key, entry)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path), "error": str(exc)})

    for path in _driver_object_paths(manifest, context_root):
        try:
            for entry in _macros_from_driver_object(path, max_xml_bytes=max_xml_bytes):
                key = (
                    str(entry.get("macro_name") or "").casefold(),
                    str(entry.get("module_name") or "").casefold(),
                    str(entry.get("macro_kind") or "").casefold(),
                )
                if not key[0]:
                    continue
                entries_by_key.setdefault(key, entry)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path), "error": str(exc)})

    entries = sorted(
        entries_by_key.values(),
        key=lambda item: (
            str(item.get("macro_name") or "").casefold(),
            str(item.get("module_name") or "").casefold(),
            str(item.get("macro_kind") or ""),
        ),
    )
    catalog: dict[str, Any] = {
        "schema_version": DRIVER_MACROS_SCHEMA_VERSION,
        "source": source,
        "entry_count": len(entries),
        "entries": entries,
    }
    if errors:
        catalog["parse_errors"] = errors[:50]
        catalog["parse_error_count"] = len(errors)
    return catalog


def write_driver_macros_catalog(
    destination: Path,
    *,
    manifest: Mapping[str, Any] | None = None,
    context_root: Path | str | None = None,
    source: str = "zeia_scripts",
) -> Path | None:
    """Write ``driver_macros.json``. Empty catalog still writes (soft inventory)."""
    catalog = build_driver_macros_catalog(
        manifest=manifest,
        context_root=context_root or Path(destination).parent,
        source=source,
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, catalog)
    return destination


def write_driver_macros_for_context(
    context_root: Path,
    manifest: Mapping[str, Any] | None = None,
) -> Path | None:
    return write_driver_macros_catalog(
        Path(context_root) / DRIVER_MACROS_FILENAME,
        manifest=manifest,
        context_root=context_root,
        source="zeia_scripts",
    )


def load_driver_macros_catalog(path: Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).is_file():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _macros_from_xscr(path: Path, *, max_xml_bytes: int) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if len(data) > max_xml_bytes:
        raise ValueError(f"xscr exceeds max_xml_bytes ({len(data)} > {max_xml_bytes})")
    root = ET.fromstring(data.decode("utf-8", errors="replace"), max_bytes=max_xml_bytes)
    rows: list[dict[str, Any]] = []
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        local = _local_name(elem.tag)
        if local.casefold() not in _MACRO_TAGS:
            continue
        macro_name = str(elem.attrib.get("Name") or "").strip()
        module_name = str(elem.attrib.get("ModuleName") or "").strip()
        if not macro_name:
            continue
        kind = "legacy" if local.casefold() == "legacydrivermacro" else "application"
        rows.append(
            _clean(
                {
                    "macro_name": macro_name,
                    "module_name": module_name or None,
                    "macro_kind": kind,
                    "source_kind": "script",
                    "source_path": str(path),
                }
            )
        )
    return rows


def _macros_from_driver_object(path: Path, *, max_xml_bytes: int) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if len(data) > max_xml_bytes:
        raise ValueError(f"driver object exceeds max_xml_bytes ({len(data)} > {max_xml_bytes})")
    root = ET.fromstring(data.decode("utf-8", errors="replace"), max_bytes=max_xml_bytes)
    rows: list[dict[str, Any]] = []
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        local = _local_name(elem.tag)
        lowered = local.casefold()
        if lowered not in _MACRO_TAGS and "macro" not in lowered:
            continue
        macro_name = str(elem.attrib.get("Name") or _child_text(elem, "Name") or "").strip()
        module_name = str(
            elem.attrib.get("ModuleName") or _child_text(elem, "ModuleName") or ""
        ).strip()
        if not macro_name:
            continue
        kind = "legacy" if "legacy" in lowered else "application"
        rows.append(
            _clean(
                {
                    "macro_name": macro_name,
                    "module_name": module_name or None,
                    "macro_kind": kind,
                    "source_kind": "datastore_object",
                    "source_path": str(path),
                }
            )
        )
    return rows


def _script_paths(
    manifest: Mapping[str, Any] | None,
    context_root: Path | str | None,
) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for script in (manifest or {}).get("scripts") or [] if isinstance(manifest, Mapping) else []:
        if not isinstance(script, Mapping):
            continue
        path = _resolve_manifest_path(manifest, script)
        if path is None or not path.is_file() or path.suffix.lower() != ".xscr":
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    if paths:
        return paths
    root = Path(context_root).expanduser() if context_root else None
    if root is None or not root.is_dir():
        return []
    for search in (root / "extracted", root):
        if not search.is_dir():
            continue
        for path in sorted(search.rglob("*.xscr")):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return paths


def _driver_object_paths(
    manifest: Mapping[str, Any] | None,
    context_root: Path | str | None,
) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for item in (manifest or {}).get("objects") or [] if isinstance(manifest, Mapping) else []:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "").casefold()
        entry = str(item.get("entry") or item.get("extracted_path") or "")
        haystack = f"{kind} {entry}".casefold()
        if not any(hint in haystack for hint in _DRIVER_OBJECT_HINTS):
            continue
        path = _resolve_manifest_path(manifest, item)
        if path is None or not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    root = Path(context_root).expanduser() if context_root else None
    if root is None:
        return paths
    for search in (root / "extracted", root):
        if not search.is_dir():
            continue
        for folder_name in ("ApplicationDrivers", "Drivers", "LegacyDrivers"):
            for folder in search.glob(f"**/{folder_name}"):
                if not folder.is_dir():
                    continue
                for path in sorted(folder.rglob("*")):
                    if not path.is_file() or path.suffix.lower() not in {".xml", ".xdrv", ".adrv"}:
                        continue
                    key = str(path.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    paths.append(path)
    return paths


def _resolve_manifest_path(
    manifest: Mapping[str, Any] | None,
    item: Mapping[str, Any],
) -> Path | None:
    raw = str(item.get("extracted_path") or item.get("context_extracted_path") or item.get("entry") or "")
    if not raw:
        return None
    path = Path(raw.replace("\\", "/"))
    if path.is_file():
        return path
    if not isinstance(manifest, Mapping):
        return None
    extracted_dir = Path(str(manifest.get("extracted_dir") or "")).expanduser()
    root = Path(str(manifest.get("root") or "")).expanduser()
    for base in (extracted_dir, root):
        if base and (base / path).is_file():
            return base / path
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _child_text(elem: ET.Element | None, local_name: str) -> str:
    if elem is None:
        return ""
    for child in list(elem):
        if isinstance(child.tag, str) and _local_name(child.tag) == local_name:
            return _text(child)
    return ""


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}
