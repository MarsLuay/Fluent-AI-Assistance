"""Export Scripts-folder tree + script→initialization-worktable bindings from ZEIA.

Built only from already-parsed manifest script metadata (``ObjectSubfolderPath`` and
``WorktableWorkspace`` references). Site folder / worktable names stay in the local
context artifact — never hardcoded product defaults.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .runner import write_json

SCRIPT_FOLDER_BINDINGS_SCHEMA_VERSION = "tecan.script_folder_bindings.v1"
SCRIPT_FOLDER_BINDINGS_FILENAME = "script_folder_bindings.json"


def build_script_folder_bindings(
    manifest: Mapping[str, Any] | None,
    *,
    source: str = "zeia_scripts",
) -> dict[str, Any]:
    """Mine folder tree + script worktable bindings from a project manifest."""
    scripts_out: list[dict[str, Any]] = []
    folder_counts: dict[str, int] = defaultdict(int)
    bindings: list[dict[str, Any]] = []

    for script in (manifest or {}).get("scripts") or [] if isinstance(manifest, Mapping) else []:
        if not isinstance(script, Mapping):
            continue
        object_name = str(script.get("object_name") or "").strip()
        if not object_name:
            continue
        folder = _normalize_folder(
            script.get("folder")
            or script.get("object_path")
            or script.get("object_subfolder_path")
        )
        folder_counts[folder] += 1
        worktable = _worktable_binding(script)
        row = _clean(
            {
                "object_name": object_name,
                "display_name": _display_name(folder, object_name),
                "folder": folder or None,
                "guid": str(script.get("guid") or script.get("script_guid") or "").strip() or None,
                "worktable_name": worktable.get("name") or None,
                "worktable_guid": worktable.get("guid") or None,
                "extracted_path": script.get("extracted_path") or script.get("entry"),
            }
        )
        scripts_out.append(row)
        if worktable.get("name") or worktable.get("guid"):
            bindings.append(
                _clean(
                    {
                        "script": object_name,
                        "display_name": row.get("display_name"),
                        "folder": folder or None,
                        "worktable_name": worktable.get("name") or None,
                        "worktable_guid": worktable.get("guid") or None,
                    }
                )
            )

    scripts_out.sort(
        key=lambda item: (
            str(item.get("folder") or "").casefold(),
            str(item.get("object_name") or "").casefold(),
        )
    )
    bindings.sort(
        key=lambda item: (
            str(item.get("folder") or "").casefold(),
            str(item.get("script") or "").casefold(),
        )
    )
    folders = [
        {"path": path, "script_count": folder_counts[path]}
        for path in sorted(folder_counts.keys(), key=lambda value: value.casefold())
    ]
    return {
        "schema_version": SCRIPT_FOLDER_BINDINGS_SCHEMA_VERSION,
        "source": source,
        "folder_count": len(folders),
        "script_count": len(scripts_out),
        "binding_count": len(bindings),
        "folders": folders,
        "scripts": scripts_out,
        "initialization_worktable_bindings": bindings,
    }


def attach_script_folder_bindings(manifest: dict[str, Any]) -> dict[str, Any]:
    """Attach a compact binding summary onto the in-memory manifest for init preference."""
    catalog = build_script_folder_bindings(manifest)
    bindings = catalog.get("initialization_worktable_bindings") or []
    if bindings:
        manifest["script_worktable_bindings"] = list(bindings)
    return catalog


def write_script_folder_bindings(
    destination: Path,
    manifest: Mapping[str, Any] | None,
    *,
    source: str = "zeia_scripts",
) -> Path | None:
    catalog = build_script_folder_bindings(manifest, source=source)
    if not catalog.get("scripts"):
        return None
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, catalog)
    return destination


def write_script_folder_bindings_for_context(
    context_root: Path,
    manifest: Mapping[str, Any] | None = None,
) -> Path | None:
    return write_script_folder_bindings(
        Path(context_root) / SCRIPT_FOLDER_BINDINGS_FILENAME,
        manifest,
        source="zeia_scripts",
    )


def load_script_folder_bindings(path: Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).is_file():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def zeia_worktable_bindings_from_manifest(manifest: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Return exported script→worktable bindings when present on the manifest."""
    if not isinstance(manifest, Mapping):
        return []
    rows = manifest.get("script_worktable_bindings")
    if isinstance(rows, list) and rows:
        return [dict(item) for item in rows if isinstance(item, Mapping)]
    # Sidecar next to context root when import already wrote the artifact.
    root = Path(str(manifest.get("root") or "")).expanduser()
    if root.is_dir():
        catalog = load_script_folder_bindings(root / SCRIPT_FOLDER_BINDINGS_FILENAME)
        if catalog:
            return [
                dict(item)
                for item in (catalog.get("initialization_worktable_bindings") or [])
                if isinstance(item, Mapping)
            ]
    return []


def _worktable_binding(script: Mapping[str, Any]) -> dict[str, str]:
    for ref in script.get("references") or []:
        if not isinstance(ref, Mapping):
            continue
        if str(ref.get("type_id") or "") != "WorktableWorkspace":
            continue
        name = str(ref.get("object_name") or ref.get("name") or "").strip()
        guid = str(ref.get("guid") or "").strip()
        if name or guid:
            return {"name": name, "guid": guid}
    deps = script.get("dependencies") if isinstance(script.get("dependencies"), Mapping) else {}
    for value in deps.get("workspace_guids") or []:
        guid = str(value or "").strip()
        if guid:
            return {"name": "", "guid": guid}
    return {}


def _normalize_folder(value: Any) -> str:
    text = str(value or "").strip().replace("/", "\\").strip("\\")
    return text


def _display_name(folder: str, object_name: str) -> str:
    if folder and "\\" not in object_name:
        return f"{folder}\\{object_name}"
    return object_name


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}
