"""Explicit, source-backed FluentControl target datastore profiles.

Profiles are evidence records for a selected target.  They deliberately do
not discover the current machine: callers must provide target roots or a
captured inventory.  This keeps target-dependent packaging separate from the
build host that happens to run it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

TARGET_DATASTORE_SCHEMA_VERSION = "tecan.target_datastore.v1"

_OBJECT_SUFFIXES = {
    ".xscr": "script",
    ".xwsp": "worktable_workspace",
    ".xlqc": "liquid_class",
    ".xcmp": "component",
}
_SENSITIVE_KEY_RE = re.compile(r"(?:password|secret|token|credential|license)", re.IGNORECASE)


def _xml_text(text: str, name: str) -> str:
    match = re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy evidence while dropping obvious secret-bearing fields."""
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        if _SENSITIVE_KEY_RE.search(name):
            continue
        if isinstance(item, Mapping):
            result[name] = _safe_mapping(item)
        elif isinstance(item, list):
            result[name] = [
                _safe_mapping(row) if isinstance(row, Mapping) else row
                for row in item
            ]
        else:
            result[name] = item
    return result


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return value


def fingerprint_target_datastore_profile(profile: Mapping[str, Any]) -> str:
    """Return a deterministic fingerprint excluding the stored fingerprint."""
    material = dict(profile)
    material.pop("fingerprint", None)
    blob = json.dumps(_canonical(_safe_mapping(material)), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _object_record(path: Path, root: Path, kind: str) -> dict[str, Any]:
    data = path.read_bytes()
    text = data.decode("utf-8-sig", errors="replace")
    suffix = path.suffix.casefold()
    object_kind = _OBJECT_SUFFIXES.get(suffix, kind)
    relative = path.relative_to(root).as_posix()
    return {
        "guid": path.stem,
        "object_name": _xml_text(text, "ObjectName") or path.stem,
        "object_subfolder_path": _xml_text(text, "ObjectSubfolderPath"),
        "type_id": _xml_text(text, "TypeId") or object_kind,
        "kind": object_kind,
        "relative_path": relative,
        "content_fingerprint": hashlib.sha256(data).hexdigest(),
        "source": "explicit_target_inventory",
    }


def _collect_objects(root: Path | None, kind: str, suffixes: set[str]) -> list[dict[str, Any]]:
    if root is None or not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.casefold() in suffixes:
            records.append(_object_record(path, root, kind))
    return records


def build_target_datastore_profile(
    *,
    userspecific_dir: Path | str | None = None,
    systemspecific_dir: Path | str | None = None,
    software_family: str | None = None,
    fluentcontrol_version: str | None = None,
    fluentcontrol_build: str | None = None,
    instrument: Mapping[str, Any] | None = None,
    state_classification: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    unknowns: list[str] | None = None,
    target_profile_id: str | None = None,
) -> dict[str, Any]:
    """Build a profile from explicitly selected target evidence.

    Omitting both datastore roots creates a target-unbound profile.  No
    environment variables or local FluentControl defaults are consulted.
    """
    user_root = Path(userspecific_dir) if userspecific_dir is not None else None
    system_root = Path(systemspecific_dir) if systemspecific_dir is not None else None
    bound = any(root is not None for root in (user_root, system_root)) or any(
        value not in (None, "", {}, [])
        for value in (software_family, fluentcontrol_version, fluentcontrol_build, instrument, state_classification)
    )
    inferred_unknowns = set(unknowns or ())
    if bound and not software_family:
        inferred_unknowns.add("software_family_unknown")
    if user_root is not None and not user_root.is_dir():
        inferred_unknowns.add("userspecific_target_root_unavailable")
    if system_root is not None and not system_root.is_dir():
        inferred_unknowns.add("systemspecific_target_root_unavailable")
    profile: dict[str, Any] = {
        "schema_version": TARGET_DATASTORE_SCHEMA_VERSION,
        "status": "bound" if bound else "unbound",
        "target_unbound": not bound,
        "target_profile_id": target_profile_id,
        "software": {
            "family": software_family,
            "version": fluentcontrol_version,
            "build": fluentcontrol_build,
        },
        "instrument": _safe_mapping(instrument or {}),
        "objects": {
            "userspecific": _collect_objects(user_root, "user_specific", {".xscr"}),
            "systemspecific": _collect_objects(system_root, "system_specific", {".xwsp", ".xlqc", ".xcmp"}),
        },
        "state_classification": _safe_mapping(state_classification or {}),
        "provenance": _safe_mapping(provenance or {"source": "explicit_target_evidence"}),
        "target_roots": {
            "userspecific_provided": user_root is not None,
            "systemspecific_provided": system_root is not None,
        },
        "unknowns": sorted(inferred_unknowns or ({"target_profile_not_provided"} if not bound else set())),
    }
    profile["fingerprint"] = fingerprint_target_datastore_profile(profile)
    if not profile["target_profile_id"] and bound:
        profile["target_profile_id"] = f"target-{profile['fingerprint'][:16]}"
        profile["fingerprint"] = fingerprint_target_datastore_profile(profile)
    return profile


def load_target_datastore_profile(source: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    """Load and normalize a captured target profile or explicit inventory."""
    if isinstance(source, Mapping):
        profile = dict(source)
    else:
        profile = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or profile.get("schema_version") != TARGET_DATASTORE_SCHEMA_VERSION:
        raise ValueError(f"expected {TARGET_DATASTORE_SCHEMA_VERSION} target profile")
    normalized = _safe_mapping(profile)
    normalized["fingerprint"] = fingerprint_target_datastore_profile(normalized)
    return normalized


def write_target_datastore_profile(path: Path | str, profile: Mapping[str, Any]) -> None:
    """Write a normalized profile without adding host-specific defaults."""
    normalized = load_target_datastore_profile(profile)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = [
    "TARGET_DATASTORE_SCHEMA_VERSION",
    "build_target_datastore_profile",
    "fingerprint_target_datastore_profile",
    "load_target_datastore_profile",
    "write_target_datastore_profile",
]
