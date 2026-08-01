"""Local FluentControl datastore inventory for import-safe packaging.

Builds ``(ObjectSubfolderPath\\ObjectName) -> [guid, ...]`` maps from
UserSpecific ``.xscr`` files, collision preflight, subroutine GUID rewrite
helpers, and TARGET_PREREQ checks against SystemSpecific libraries.

See ready-to-import dump ``INCORPORATION_MAP.md`` (method-source collect).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_REFERENCE_RE = re.compile(
    rb"<Reference>\s*<Guid>([^<]+)</Guid>\s*<TypeId>([^<]+)</TypeId>\s*"
    rb"<ObjectName>([^<]*)</ObjectName>\s*</Reference>",
    re.DOTALL,
)


def normalize_script_folder(value: Any) -> str:
    return str(value or "").strip().strip("\\/")


def inventory_key(folder: str, object_name: str) -> str:
    folder_part = normalize_script_folder(folder)
    name = str(object_name or "").strip()
    if folder_part:
        return f"{folder_part}\\{name}"
    return name


def fluentcontrol_userspecific_dirs() -> list[Path]:
    override = str(os.environ.get("TECAN_VISIONX_USERSPECIFIC") or "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    program_data = str(os.environ.get("ProgramData") or r"C:\ProgramData").strip()
    candidates.append(Path(program_data) / "Tecan" / "VisionX" / "DataBase" / "UserSpecific")
    return candidates


def fluentcontrol_systemspecific_dirs() -> list[Path]:
    override = str(os.environ.get("TECAN_VISIONX_SYSTEMSPECIFIC") or "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    # Sibling of UserSpecific override
    user_override = str(os.environ.get("TECAN_VISIONX_USERSPECIFIC") or "").strip()
    if user_override:
        parent = Path(user_override).parent
        sibling = parent / "DataBase_SystemSpecific"
        if sibling.is_dir():
            candidates.append(sibling)
        # live layout: .../DataBase/UserSpecific -> .../DataBase/SystemSpecific
        if Path(user_override).name.casefold() == "userspecific":
            candidates.append(Path(user_override).parent / "SystemSpecific")
    program_data = str(os.environ.get("ProgramData") or r"C:\ProgramData").strip()
    candidates.append(Path(program_data) / "Tecan" / "VisionX" / "DataBase" / "SystemSpecific")
    return candidates


def _first_xml_text(text: str, name: str) -> str:
    match = re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, flags=re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _read_script_identity(path: Path) -> tuple[str, str] | None:
    try:
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return None
    name = _first_xml_text(text, "ObjectName")
    if not name:
        return None
    folder = normalize_script_folder(_first_xml_text(text, "ObjectSubfolderPath"))
    return name, folder


def build_scripts_inventory(userspecific_dir: Path | None = None) -> dict[str, Any]:
    """Scan UserSpecific ``.xscr`` files into a packaging inventory."""
    roots = [userspecific_dir] if userspecific_dir is not None else fluentcontrol_userspecific_dirs()
    rows: list[dict[str, str]] = []
    by_key: dict[str, list[str]] = {}
    scanned_root = ""
    for root in roots:
        if root is None or not root.is_dir():
            continue
        scanned_root = str(root)
        for path in sorted(root.glob("*.xscr")):
            if not _GUID_RE.fullmatch(path.stem):
                continue
            identity = _read_script_identity(path)
            if identity is None:
                continue
            name, folder = identity
            key = inventory_key(folder, name)
            rows.append(
                {
                    "guid": path.stem,
                    "object_name": name,
                    "folder": folder,
                    "key": key,
                }
            )
            by_key.setdefault(key, []).append(path.stem)
        if rows:
            break
    collisions = {key: guids for key, guids in sorted(by_key.items()) if len(guids) > 1}
    return {
        "schema": "tecan.fluentcontrol.scripts_inventory.v1",
        "userspecific_dir": scanned_root,
        "script_count": len(rows),
        "name_folder_to_guids": {key: guids for key, guids in sorted(by_key.items())},
        "collisions": collisions,
        "scripts": rows,
    }


def write_scripts_inventory(path: Path, inventory: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")


def load_scripts_inventory(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_unique_guid(
    inventory: dict[str, Any],
    object_name: str,
    folder: str,
) -> str | None:
    key = inventory_key(folder, object_name)
    mapping = inventory.get("name_folder_to_guids") or {}
    # exact key first
    guids = mapping.get(key)
    if guids is None:
        # case-insensitive fallback
        wanted = key.casefold()
        for existing_key, values in mapping.items():
            if str(existing_key).casefold() == wanted:
                guids = values
                break
    if not guids:
        return None
    if len(guids) != 1:
        return None
    return str(guids[0])


def collision_preflight(
    inventory: dict[str, Any],
    object_name: str,
    folder: str,
) -> dict[str, Any]:
    key = inventory_key(folder, object_name)
    mapping = inventory.get("name_folder_to_guids") or {}
    guids: list[str] | None = mapping.get(key)
    if guids is None:
        wanted = key.casefold()
        for existing_key, values in mapping.items():
            if str(existing_key).casefold() == wanted:
                guids = list(values)
                key = str(existing_key)
                break
    if not guids:
        return {
            "status": "missing",
            "key": key,
            "object_name": object_name,
            "folder": normalize_script_folder(folder),
            "guids": [],
        }
    if len(guids) > 1:
        return {
            "status": "collision",
            "key": key,
            "object_name": object_name,
            "folder": normalize_script_folder(folder),
            "guids": list(guids),
        }
    return {
        "status": "ok",
        "key": key,
        "object_name": object_name,
        "folder": normalize_script_folder(folder),
        "guids": list(guids),
        "guid": guids[0],
    }


def find_local_script_guid(
    object_name: str,
    folder: str,
    *,
    userspecific_dir: Path | None = None,
    inventory: dict[str, Any] | None = None,
) -> str | None:
    """Unique local GUID for name+folder, or None if missing/ambiguous."""
    inv = inventory if inventory is not None else build_scripts_inventory(userspecific_dir)
    return find_unique_guid(inv, object_name, folder)


def _scripts_by_object_name(inventory: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    by_name: dict[str, list[dict[str, str]]] = {}
    for row in inventory.get("scripts") or []:
        name = str(row.get("object_name") or "").strip()
        if not name:
            continue
        by_name.setdefault(name.casefold(), []).append(
            {
                "guid": str(row.get("guid") or ""),
                "object_name": name,
                "folder": normalize_script_folder(row.get("folder")),
            }
        )
    return by_name


def resolve_local_script_guid_for_name(
    inventory: dict[str, Any],
    object_name: str,
    *,
    preferred_folder: str = "",
) -> tuple[str | None, str]:
    """Resolve a local GUID for a script ObjectName.

    Returns ``(guid, reason)`` where reason is ``unique``, ``folder_unique``,
    ``ambiguous``, or ``missing``.
    """
    name = str(object_name or "").strip()
    if not name:
        return None, "missing"
    candidates = _scripts_by_object_name(inventory).get(name.casefold()) or []
    if not candidates:
        return None, "missing"
    if len(candidates) == 1:
        return candidates[0]["guid"], "unique"
    preferred = normalize_script_folder(preferred_folder)
    if preferred:
        folder_hits = [
            row for row in candidates if row["folder"].casefold() == preferred.casefold()
        ]
        if len(folder_hits) == 1:
            return folder_hits[0]["guid"], "folder_unique"
    return None, "ambiguous"


def rewrite_script_reference_guids(
    payload: bytes,
    inventory: dict[str, Any],
    *,
    type_ids: set[str] | None = None,
) -> tuple[bytes, list[dict[str, str]]]:
    """Rewrite ``<Reference>`` Script GUIDs to unique local GUIDs; skip ambiguous."""
    wanted_types = {value.casefold() for value in (type_ids or {"Script"})}
    rewrites: list[dict[str, str]] = []

    def repl(match: re.Match[bytes]) -> bytes:
        old_guid = match.group(1).decode("utf-8", errors="replace").strip()
        type_id = match.group(2).decode("utf-8", errors="replace").strip()
        object_name = match.group(3).decode("utf-8", errors="replace").strip()
        if type_id.casefold() not in wanted_types:
            return match.group(0)
        new_guid, reason = resolve_local_script_guid_for_name(inventory, object_name)
        if not new_guid or new_guid.casefold() == old_guid.casefold():
            return match.group(0)
        if reason not in {"unique", "folder_unique"}:
            return match.group(0)
        # Locate folder for audit
        folder = ""
        for row in inventory.get("scripts") or []:
            if str(row.get("guid") or "").casefold() == new_guid.casefold():
                folder = normalize_script_folder(row.get("folder"))
                break
        rewrites.append(
            {
                "object_name": object_name,
                "from_guid": old_guid,
                "to_guid": new_guid,
                "folder": folder,
                "reason": reason,
            }
        )
        return (
            b"<Reference>\n      <Guid>"
            + new_guid.encode("ascii")
            + b"</Guid>\n      <TypeId>"
            + type_id.encode("utf-8")
            + b"</TypeId>\n      <ObjectName>"
            + object_name.encode("utf-8")
            + b"</ObjectName>\n    </Reference>"
        )

    rewritten = _REFERENCE_RE.sub(repl, payload)
    return rewritten, rewrites


def _index_systemspecific_objects(
    systemspecific_dir: Path,
    *,
    suffixes: set[str],
) -> dict[str, list[dict[str, str]]]:
    """Map ObjectName.casefold() -> [{guid, path, suffix}]."""
    index: dict[str, list[dict[str, str]]] = {}
    if not systemspecific_dir.is_dir():
        return index
    for path in systemspecific_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in suffixes:
            continue
        if not _GUID_RE.fullmatch(path.stem):
            # still allow named files but prefer GUID stems
            pass
        try:
            text = path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            continue
        name = _first_xml_text(text, "ObjectName") or path.stem
        index.setdefault(name.casefold(), []).append(
            {
                "guid": path.stem,
                "object_name": name,
                "path": str(path),
                "suffix": suffix,
            }
        )
    return index


def extract_typed_references(payload: bytes) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for match in _REFERENCE_RE.finditer(payload):
        refs.append(
            {
                "guid": match.group(1).decode("utf-8", errors="replace").strip(),
                "type_id": match.group(2).decode("utf-8", errors="replace").strip(),
                "object_name": match.group(3).decode("utf-8", errors="replace").strip(),
            }
        )
    return refs


_TYPE_TO_SUFFIX = {
    "liquidclass": {".xlqc"},
    "worktableworkspace": {".xwsp"},
    "workspace": {".xwsp"},
    "worktablecomponent": {".xcmp"},
    "component": {".xcmp"},
}


def report_missing_system_dependencies(
    payload: bytes,
    *,
    systemspecific_dir: Path | None = None,
    base_archive_guids: set[str] | None = None,
) -> dict[str, Any]:
    """Report referenced workspaces/LCs/components missing from SystemSpecific + base."""
    roots = [systemspecific_dir] if systemspecific_dir is not None else fluentcontrol_systemspecific_dirs()
    root = next((path for path in roots if path is not None and path.is_dir()), None)
    suffixes = {".xwsp", ".xlqc", ".xcmp"}
    index = _index_systemspecific_objects(root, suffixes=suffixes) if root else {}
    base_guids = {guid.casefold() for guid in (base_archive_guids or set()) if guid}

    missing: list[dict[str, str]] = []
    present: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for ref in extract_typed_references(payload):
        type_id = ref["type_id"]
        type_key = type_id.replace(" ", "").casefold()
        wanted_suffixes = _TYPE_TO_SUFFIX.get(type_key)
        if not wanted_suffixes:
            skipped.append({**ref, "reason": "type_not_tracked"})
            continue
        guid = ref["guid"]
        name = ref["object_name"]
        if guid and guid.casefold() in base_guids:
            present.append({**ref, "resolved_via": "base_zeia"})
            continue
        hits = [
            row
            for row in index.get(name.casefold(), [])
            if row["suffix"] in wanted_suffixes
        ]
        if any(row["guid"].casefold() == guid.casefold() for row in hits if guid):
            present.append({**ref, "resolved_via": "systemspecific_guid"})
            continue
        if hits:
            present.append(
                {
                    **ref,
                    "resolved_via": "systemspecific_name",
                    "local_guid": hits[0]["guid"],
                }
            )
            continue
        missing.append(
            {
                **ref,
                "status": "TARGET_PREREQ",
                "reason": "not_in_systemspecific_or_base",
            }
        )

    return {
        "schema": "tecan.fluentcontrol.target_prereq_report.v1",
        "systemspecific_dir": str(root) if root else "",
        "missing": missing,
        "present": present,
        "skipped_untracked_types": skipped,
        "missing_count": len(missing),
    }
