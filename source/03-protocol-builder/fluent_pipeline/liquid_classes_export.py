"""Build ``liquid_classes.json`` from imported ZEIA ``*.xlqc`` definitions.

Site liquid-class names/GUIDs belong in this generated artifact under the local
context/build tree — never as hardcoded ``generation.yaml`` product defaults.
``ready-to-import/`` is gitignored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from . import xml_compat as ET
from .fluent_naming import strip_fluent_instance_suffix
from .runner import write_json

LIQUID_CLASSES_SCHEMA_VERSION = "tecan.liquid_classes.v2"
LIQUID_CLASSES_FILENAME = "liquid_classes.json"
_LIQUID_CLASSES_REL = Path("SystemSpecific") / "LiquidClasses"

_EQ_SET_LOCAL = "Tecan.Core.Pipetting.LiquidClassEquationSet"
_DPS_SET_LOCAL = "Tecan.Core.Pipetting.LiquidClassDetectionAndPositioningSet"
_SUBCLASS_LOCAL = "Tecan.Core.Pipetting.LiquidSubClass"


# Legacy flat section matchers (still used as fallback when typed sets absent).
_SECTION_KINDS = {
    "aspirate": "aspirate",
    "aspiration": "aspirate",
    "dispense": "dispense",
    "dispenseation": "dispense",
    "mix": "mix",
    "mixing": "mix",
    "emptytips": "empty_tips",
    "empty_tips": "empty_tips",
}

# EquationSet / DetectionAndPositioningSet scalar → portable keys.
# Tecan XML uses "Formular" spelling; keep exact mined strings (never invent).
_EQ_FIELD_ALIASES = {
    "aspiratepipettingspeed": ("aspirate", "flow_rate_formula"),
    "aspiratepipettingspeedmin": ("aspirate", "flow_rate_min"),
    "aspiratepipettingspeedmax": ("aspirate", "flow_rate_max"),
    "dispensepipettingspeed": ("dispense", "flow_rate"),
    "dispensepipettingacceleration": ("dispense", "acceleration"),
    "dispensepipettingdeceleration": ("dispense", "deceleration"),
    "leadingairgapvolumeformular": ("aspirate", "leading_air_gap_formula"),
    "trailingairgapvolumeformular": ("aspirate", "trailing_air_gap_formula"),
    "conditioningvolumeformular": ("aspirate", "conditioning_volume_formula"),
    "excessvolumeformular": ("aspirate", "excess_volume_formula"),
    "correctionvolumeformular": ("aspirate", "correction_volume_formula"),
    "delayafteraspiration": ("aspirate", "delay_ms"),
    "aspirationdelay": ("aspirate", "aspiration_delay_ms"),
    "dispensedelayformula": ("dispense", "delay_formula"),
    "dispenseexcessvolume": ("dispense", "excess_volume"),
    "dispenseconditioningvolume": ("dispense", "conditioning_volume"),
    "dispensecorrectionvolume": ("dispense", "correction_volume"),
    "istagafterdispense": ("dispense", "tag_after_dispense"),
    "ismultipipette": ("dispense", "multi_pipette"),
    "selectedconditioningdispensetarget": ("dispense", "conditioning_dispense_target"),
    "aspiratesamplevalue": ("aspirate", "sample_value"),
    "dispensesamplevalue": ("dispense", "sample_value"),
}

_DPS_FIELD_ALIASES = {
    "submerge": "submerge_depth",
    "submergedepth": "submerge_depth",
    "zoffset": "z_offset",
    "movespeed": "move_speed",
    "movespeedoutofwell": "move_speed",
    "retractspeed": "retract_speed",
    "retractdistance": "retract_distance",
    "retracttolerancedistance": "retract_distance",
    "tolerancedistance": "tolerance_distance",
    "selectedplld": "plld",
    "plldonoff": "plld",
    "selectedclld": "clld",
    "clldonoff": "clld",
    "selectedzposition": "z_position",
    "zposition": "z_position",
    "selectedclldsensitivitygroup": "clld_sensitivity",
    "clldsensitivity": "clld_sensitivity",
    "selectedaspirationtracking": "aspiration_tracking",
    "aspirationtrackingonoff": "aspiration_tracking",
    "selecteddispensetracking": "dispense_tracking",
    "dispensetrackingonoff": "dispense_tracking",
    "selectedtrackingwithaspirationsupervision": "aspiration_supervision_tracking",
    "aspirationsupervisiononoff": "aspiration_supervision_tracking",
    "density": "density",
    "viscosity": "viscosity",
    "adpsensitivity": "adp_sensitivity",
    "adprisethreshold": "adp_rise_threshold",
    "adpdropthreshold": "adp_drop_threshold",
}

# Legacy flat field aliases for non-typed section ancestors.
_FIELD_ALIASES = {
    "flowrate": "flow_rate",
    "aspirationflowrate": "flow_rate",
    "dispenseflowrate": "flow_rate",
    "mixflowrate": "flow_rate",
    "airgap": "air_gap",
    "airgapvolume": "air_gap",
    "excessvolume": "excess_volume",
    "conditioningvolume": "conditioning_volume",
    "blowout": "blowout_volume",
    "blowoutvolume": "blowout_volume",
    "retractspeed": "retract_speed",
    "retractdelay": "retract_delay",
    "delay": "delay",
    "detection": "detection",
    "detectionsensitivity": "detection_sensitivity",
    "liquidleveldetection": "liquid_level_detection",
    "lld": "liquid_level_detection",
    "submergedepth": "submerge_depth",
    "submerge": "submerge_depth",
    "zoffset": "z_offset",
    "zposition": "z_position",
    "cycles": "cycles",
    "mixvolume": "mix_volume",
    "volume": "volume",
    "prewet": "prewet",
    "prewetvolume": "prewet_volume",
    "prewetcycles": "prewet_cycles",
    "speed": "speed",
    "aspiratepipettingspeed": "flow_rate_formula",
    "dispensepipettingspeed": "flow_rate",
    "leadingairgapvolumeformular": "leading_air_gap_formula",
    "trailingairgapvolumeformular": "trailing_air_gap_formula",
}


def parse_xlqc(path: Path | str, *, max_xml_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    """Parse one ``.xlqc`` into portable catalog fields.

    Filename stem is the LiquidClass GUID referenced by ``.xscr`` (same rule as
    fluentcoder ``load_xlqc``). Mines head×tip profiles from typed EquationSet /
    DetectionAndPositioningSet / LiquidSubClass tags when present; falls back to
    flat aspirate/dispense/mix ancestors. Never invents missing values.
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) > max_xml_bytes:
        raise ValueError(f"xlqc exceeds max_xml_bytes ({len(data)} > {max_xml_bytes})")
    root = ET.fromstring(data.decode("utf-8", errors="replace"), max_bytes=max_xml_bytes)
    payload = _find(root, "Payload")
    name = _child_text(payload, "ObjectName") or path.stem
    supported_heads: list[str] = []
    payload_data = _find(payload, "PayloadData") if payload is not None else None
    search_root = payload_data if payload_data is not None else root
    if search_root is not None:
        for elem in search_root.iter():
            if not isinstance(elem.tag, str):
                continue
            if _local_name(elem.tag) != "PipettingDeviceType":
                continue
            head = (elem.text or "").strip()
            if head and head not in supported_heads:
                supported_heads.append(head)
    profiles = _mine_profiles(search_root)
    sections = _summary_sections(profiles) or _pipetting_sections(search_root)
    return _clean(
        {
            "kind": "liquid_class",
            "guid": path.stem,
            "name": name,
            "object_name": name,
            "head": supported_heads[0] if supported_heads else None,
            "supported_heads": supported_heads or None,
            "profiles": profiles or None,
            "aspirate": sections.get("aspirate") or None,
            "dispense": sections.get("dispense") or None,
            "mix": sections.get("mix") or None,
            "empty_tips": sections.get("empty_tips") or None,
            "path": str(path),
        }
    )


def build_liquid_classes_catalog(
    *,
    manifest: Mapping[str, Any] | None = None,
    datastore_root: Path | str | None = None,
    context_root: Path | str | None = None,
    source: str = "zeia_xlqc",
    max_xml_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any]:
    """Mine liquid classes from manifest ``.xlqc`` objects and/or DataStore walk."""
    entries_by_guid: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

    for path in _xlqc_paths_from_manifest(manifest):
        try:
            entry = parse_xlqc(path, max_xml_bytes=max_xml_bytes)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path), "error": str(exc)})
            continue
        guid = str(entry.get("guid") or "").strip()
        if guid:
            entries_by_guid[guid.casefold()] = entry

    root = resolve_liquid_classes_dir(datastore_root)
    if root is None and context_root is not None:
        root = discover_liquid_classes_dir(context_root)
    if root is not None:
        for path in sorted(root.glob("*.xlqc")):
            key = path.stem.casefold()
            if key in entries_by_guid:
                continue
            try:
                entry = parse_xlqc(path, max_xml_bytes=max_xml_bytes)
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": str(path), "error": str(exc)})
                continue
            guid = str(entry.get("guid") or "").strip()
            if guid:
                entries_by_guid[guid.casefold()] = entry

    # Name-only hints from manifest liquid_classes list (no GUID until .xlqc found).
    for name in (manifest or {}).get("liquid_classes") or [] if isinstance(manifest, Mapping) else []:
        text = str(name or "").strip()
        if not text:
            continue
        if any(_norm(item.get("name")) == _norm(text) for item in entries_by_guid.values()):
            continue
        # Keep as soft name-only row only when no .xlqc was mined for it.
        # Prefer fail-closed: skip inventing GUID-less product rows.
        continue

    entries = sorted(
        entries_by_guid.values(),
        key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("guid") or "")),
    )
    catalog: dict[str, Any] = {
        "schema_version": LIQUID_CLASSES_SCHEMA_VERSION,
        "source": source,
        "entry_count": len(entries),
        "entries": [_catalog_entry(item) for item in entries],
    }
    if errors:
        catalog["parse_errors"] = errors[:50]
        catalog["parse_error_count"] = len(errors)
    return catalog


def write_liquid_classes_catalog(
    destination: Path,
    *,
    manifest: Mapping[str, Any] | None = None,
    datastore_root: Path | str | None = None,
    context_root: Path | str | None = None,
    source: str = "zeia_xlqc",
) -> Path | None:
    """Write ``liquid_classes.json`` when at least one ``.xlqc`` entry exists."""
    catalog = build_liquid_classes_catalog(
        manifest=manifest,
        datastore_root=datastore_root,
        context_root=context_root or Path(destination).parent,
        source=source,
    )
    if not catalog.get("entries"):
        return None
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, catalog)
    return destination


def write_liquid_classes_for_context(
    context_root: Path,
    manifest: Mapping[str, Any] | None = None,
) -> Path | None:
    """Write catalog next to ``manifest.json`` under a project context root."""
    return write_liquid_classes_catalog(
        Path(context_root) / LIQUID_CLASSES_FILENAME,
        manifest=manifest,
        context_root=context_root,
        source="zeia_xlqc",
    )


def load_liquid_classes_catalog(path: Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).is_file():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def alias_maps_from_liquid_classes_catalog(
    catalog: Mapping[str, Any] | None,
) -> dict[str, dict[str, str]]:
    """Derive liquid_class alias map (instance label → type name) from the catalog."""
    liquid: dict[str, str] = {}
    if not isinstance(catalog, Mapping):
        return {"liquid_class_aliases": liquid}
    for entry in catalog.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        for alias in entry.get("aliases") or [name]:
            text = str(alias or "").strip()
            if not text:
                continue
            bare = strip_fluent_instance_suffix(text)
            if text != name:
                liquid[text] = name
            if bare and bare != text:
                liquid[text] = name
            if bare and bare != name:
                liquid.setdefault(bare, name)
        liquid.setdefault(name, name)
    return {"liquid_class_aliases": liquid}


def resolve_liquid_class_guid(
    name: str,
    catalog: Mapping[str, Any] | None,
) -> str | None:
    """Exact name → GUID from mined catalog (no generation.yaml invent)."""
    text = str(name or "").strip()
    if not text or not isinstance(catalog, Mapping):
        return None
    bare = strip_fluent_instance_suffix(text)
    wanted = {_norm(text), _norm(bare)}
    for entry in catalog.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        candidates = [entry.get("name"), *(entry.get("aliases") or [])]
        if any(_norm(candidate) in wanted for candidate in candidates):
            guid = str(entry.get("guid") or "").strip()
            return guid or None
    return None


def resolve_liquid_classes_dir(path: Path | str | None) -> Path | None:
    if path is None:
        return None
    candidate = Path(path).expanduser()
    if not candidate.exists():
        return None
    if candidate.name.casefold() == "liquidclasses" and candidate.is_dir():
        return candidate
    direct = candidate / _LIQUID_CLASSES_REL
    if direct.is_dir():
        return direct
    datastore = candidate / "DataStore" / _LIQUID_CLASSES_REL
    if datastore.is_dir():
        return datastore
    return None


def discover_liquid_classes_dir(context_root: Path | str | None) -> Path | None:
    if context_root is None:
        return None
    root = Path(context_root).expanduser()
    for candidate in (
        root,
        root / "extracted",
        root / "extracted" / "DataStore",
        root / "DataStore",
    ):
        resolved = resolve_liquid_classes_dir(candidate)
        if resolved is not None:
            return resolved
    for search_root in (root / "extracted", root):
        if not search_root.is_dir():
            continue
        for folder in search_root.glob("**/SystemSpecific/LiquidClasses"):
            if folder.is_dir():
                return folder
    return None


def _xlqc_paths_from_manifest(manifest: Mapping[str, Any] | None) -> list[Path]:
    if not isinstance(manifest, Mapping):
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    extracted_dir = Path(str(manifest.get("extracted_dir") or "")).expanduser()
    root = Path(str(manifest.get("root") or "")).expanduser()
    for item in manifest.get("objects") or []:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "").casefold()
        entry = str(item.get("entry") or item.get("extracted_path") or "")
        suffix = Path(entry).suffix.lower()
        if kind != "liquid_class" and suffix != ".xlqc":
            continue
        raw = str(item.get("extracted_path") or item.get("context_extracted_path") or entry or "")
        if not raw:
            continue
        path = Path(raw.replace("\\", "/"))
        if not path.is_absolute():
            for base in (extracted_dir, root):
                if base and (base / path).is_file():
                    path = base / path
                    break
                # entry may be relative to extracted without extracted/ prefix
                if base and path.parts and path.parts[0] != "extracted":
                    candidate = base / path
                    if candidate.is_file():
                        path = candidate
                        break
        if not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _catalog_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    name = str(item.get("name") or item.get("object_name") or "").strip()
    aliases = sorted(
        {
            name,
            strip_fluent_instance_suffix(name),
            *[str(value).strip() for value in (item.get("aliases") or []) if str(value).strip()],
        },
        key=str.casefold,
    )
    return _clean(
        {
            "name": name,
            "guid": item.get("guid"),
            "aliases": aliases,
            "head": item.get("head"),
            "supported_heads": item.get("supported_heads"),
            "profiles": item.get("profiles"),
            "aspirate": item.get("aspirate"),
            "dispense": item.get("dispense"),
            "mix": item.get("mix"),
            "empty_tips": item.get("empty_tips"),
            "source_path": item.get("path") or item.get("extracted_path"),
        }
    )


def _mine_profiles(root: ET.Element | None) -> list[dict[str, Any]]:
    """Mine head×tip profiles from typed Fluent liquid-class sets."""
    if root is None:
        return []
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    # EquationSets often omit DeviceType; stash tip-keyed params and fan out later.
    tip_eq: dict[str, dict[str, dict[str, Any]]] = {}

    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        local = _local_name(elem.tag)
        if local == _EQ_SET_LOCAL:
            head, tip, section = _set_context(elem)
            asp, disp, mix = _equation_set_fields(elem, section_hint=section)
            if head:
                profile = _profile_bucket(buckets, head, tip)
                _apply_eq(profile, asp, disp, mix)
            else:
                stash = tip_eq.setdefault(tip or "default", {"aspirate": {}, "dispense": {}, "mix": {}})
                if asp:
                    stash["aspirate"] = _merge_section_fields(stash["aspirate"], asp)
                if disp:
                    stash["dispense"] = _merge_section_fields(stash["dispense"], disp)
                if mix:
                    stash["mix"] = _merge_section_fields(stash["mix"], mix)
        elif local == _DPS_SET_LOCAL:
            head, tip, section = _set_context(elem)
            fields = _detection_set_fields(elem)
            if not fields:
                continue
            profile = _profile_bucket(buckets, head, tip)
            detection = dict(profile.get("detection") or {})
            kind = section or "aspirate"
            if kind not in {"aspirate", "dispense", "mix", "empty_tips"}:
                kind = "aspirate"
            detection[kind] = _merge_section_fields(detection.get(kind) or {}, fields)
            profile["detection"] = detection
        elif local == _SUBCLASS_LOCAL:
            head = _direct_or_nested_text(elem, "PipettingDeviceType") or ""
            tip = _direct_or_nested_text(elem, "DiTiTypeGroup") or "default"
            script = _mine_microscript(elem)
            sections = [item["name"] for item in script if item.get("name")]
            if not sections and not head and not script:
                continue
            profile = _profile_bucket(buckets, head, tip)
            existing = list(profile.get("microscript_sections") or [])
            for name in sections:
                if name not in existing:
                    existing.append(name)
            if existing:
                profile["microscript_sections"] = existing
            if script:
                profile["microscript"] = _merge_microscript(profile.get("microscript") or [], script)

    for tip, stash in tip_eq.items():
        targets = [profile for (h, t), profile in buckets.items() if t == tip and h]
        if not targets:
            profile = _profile_bucket(buckets, "", tip)
            _apply_eq(profile, stash.get("aspirate"), stash.get("dispense"), stash.get("mix"))
            continue
        for profile in targets:
            _apply_eq(profile, stash.get("aspirate"), stash.get("dispense"), stash.get("mix"))

    # LiquidSubClass usually has tip=default — fan microscript onto tipped profiles.
    for (head, tip), profile in list(buckets.items()):
        if tip != "default" or not head:
            continue
        sections = list(profile.get("microscript_sections") or [])
        script = list(profile.get("microscript") or [])
        if not sections and not script:
            continue
        for (other_head, other_tip), other in buckets.items():
            if other_head != head or other_tip in ("", "default"):
                continue
            existing = list(other.get("microscript_sections") or [])
            for name in sections:
                if name not in existing:
                    existing.append(name)
            if existing:
                other["microscript_sections"] = existing
            if script:
                other["microscript"] = _merge_microscript(other.get("microscript") or [], script)

    profiles: list[dict[str, Any]] = []
    for key in sorted(buckets.keys(), key=lambda item: (item[0].casefold(), item[1].casefold())):
        profile = buckets[key]
        # Drop tip=default SubClass-only shells when a tipped profile exists for same head.
        if (
            profile.get("tip") == "default"
            and profile.get("head")
            and not profile.get("aspirate")
            and not profile.get("dispense")
            and not profile.get("mix")
            and not profile.get("detection")
            and any(
                other.get("head") == profile.get("head") and other.get("tip") not in ("", "default")
                for other in buckets.values()
            )
        ):
            continue
        cleaned = _clean(
            {
                "head": profile.get("head") or None,
                "tip": profile.get("tip") or None,
                "aspirate": profile.get("aspirate") or None,
                "dispense": profile.get("dispense") or None,
                "mix": profile.get("mix") or None,
                "detection": _clean_detection(profile.get("detection")),
                "microscript_sections": profile.get("microscript_sections") or None,
                "microscript": profile.get("microscript") or None,
            }
        )
        if cleaned:
            profiles.append(cleaned)
    return profiles


def _apply_eq(
    profile: dict[str, Any],
    asp: Mapping[str, Any] | None,
    disp: Mapping[str, Any] | None,
    mix: Mapping[str, Any] | None,
) -> None:
    if asp:
        profile["aspirate"] = _merge_section_fields(profile.get("aspirate") or {}, dict(asp))
    if disp:
        profile["dispense"] = _merge_section_fields(profile.get("dispense") or {}, dict(disp))
    if mix:
        profile["mix"] = _merge_section_fields(profile.get("mix") or {}, dict(mix))


def _profile_bucket(
    buckets: dict[tuple[str, str], dict[str, Any]],
    head: str,
    tip: str,
) -> dict[str, Any]:
    key = (head or "", tip or "default")
    profile = buckets.get(key)
    if profile is None:
        profile = {"head": head or "", "tip": tip or "default"}
        buckets[key] = profile
    return profile


def _summary_sections(profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Flatten first-seen profile params into top-level aspirate/dispense/mix."""
    sections: dict[str, dict[str, Any]] = {
        "aspirate": {},
        "dispense": {},
        "mix": {},
        "empty_tips": {},
    }
    for profile in profiles:
        for kind in ("aspirate", "dispense", "mix", "empty_tips"):
            fields = profile.get(kind)
            if isinstance(fields, Mapping) and fields:
                sections[kind] = _merge_section_fields(sections[kind], dict(fields))
        detection = profile.get("detection")
        if isinstance(detection, Mapping):
            for kind, fields in detection.items():
                if kind in sections and isinstance(fields, Mapping) and fields:
                    sections[kind] = _merge_section_fields(sections[kind], dict(fields))
    return {key: value for key, value in sections.items() if value}


def _clean_detection(detection: Any) -> dict[str, Any] | None:
    if not isinstance(detection, Mapping):
        return None
    cleaned = {
        key: dict(value)
        for key, value in detection.items()
        if isinstance(value, Mapping) and value
    }
    return cleaned or None


def _set_context(elem: ET.Element) -> tuple[str, str, str]:
    head = _direct_or_nested_text(elem, "PipettingDeviceType")
    tip = _direct_or_nested_text(elem, "DiTiTypeGroup") or "default"
    section_raw = _direct_or_nested_text(elem, "PipettingSectionName")
    if not section_raw:
        section_raw = _direct_or_nested_text(elem, "SectionName")
    section = _SECTION_KINDS.get(section_raw.casefold(), "") if section_raw else ""
    return head, tip, section


def _direct_or_nested_text(elem: ET.Element, local_name: str) -> str:
    """Prefer direct child text; else first matching descendant text."""
    wanted = local_name.casefold()
    for child in list(elem):
        if not isinstance(child.tag, str):
            continue
        if _local_name(child.tag).casefold() != wanted:
            continue
        text = _text(child)
        if text:
            return text
        nested = _find(child, local_name)
        if nested is not None and nested is not child:
            nested_text = _text(nested)
            if nested_text:
                return nested_text
        # DeviceType → PipettingDeviceType, SectionName → PipettingSectionName
        for grand in list(child):
            if not isinstance(grand.tag, str):
                continue
            grand_local = _local_name(grand.tag)
            if grand_local in {"PipettingDeviceType", "PipettingSectionName", "Name"}:
                text = _text(grand)
                if text:
                    return text
    for node in elem.iter():
        if node is elem or not isinstance(node.tag, str):
            continue
        if _local_name(node.tag).casefold() != wanted:
            continue
        text = _text(node)
        if text:
            return text
    return ""


def _equation_set_fields(
    elem: ET.Element,
    *,
    section_hint: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    aspirate: dict[str, Any] = {}
    dispense: dict[str, Any] = {}
    mix: dict[str, Any] = {}
    for child in list(elem):
        if not isinstance(child.tag, str):
            continue
        local = _local_name(child.tag)
        folded = local.casefold()
        if folded in {"dititypegroup", "version", "sectionname", "devicetype", "haserror"}:
            continue
        if folded.endswith("delimiter"):
            continue
        text = _text(child)
        if not text:
            continue
        mapped = _EQ_FIELD_ALIASES.get(folded)
        if mapped is not None:
            kind, key = mapped
            target = {"aspirate": aspirate, "dispense": dispense, "mix": mix}.get(kind)
            if target is not None:
                target.setdefault(key, _coerce_scalar(text))
            continue
        # Ambiguous formulas: route by SectionName when present.
        if folded in {"speedformular", "speedformula"}:
            kind = section_hint or "mix"
            target = {"aspirate": aspirate, "dispense": dispense, "mix": mix}.get(kind, mix)
            target.setdefault("speed_formula", _coerce_scalar(text))
            continue
        legacy = _FIELD_ALIASES.get(folded)
        if legacy is not None:
            kind = section_hint or "aspirate"
            target = {"aspirate": aspirate, "dispense": dispense, "mix": mix}.get(kind, aspirate)
            target.setdefault(legacy, _coerce_scalar(text))
    return aspirate, dispense, mix


def _detection_set_fields(elem: ET.Element) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for child in list(elem):
        if not isinstance(child.tag, str):
            continue
        local = _local_name(child.tag)
        folded = local.casefold()
        if folded in {
            "dititypegroup",
            "version",
            "sectionname",
            "devicetype",
            "haserror",
            "haschanges",
        }:
            continue
        parent_key = _DPS_FIELD_ALIASES.get(folded) or _FIELD_ALIASES.get(folded)
        # Prefer VariableName/Value pairs (Fluent DetectionAndPositioning).
        for pair in child.iter():
            if not isinstance(pair.tag, str):
                continue
            if _local_name(pair.tag) != "LiquidClassValueVariableNamePair":
                continue
            var_name = ""
            value = ""
            for part in list(pair):
                if not isinstance(part.tag, str):
                    continue
                part_local = _local_name(part.tag)
                if part_local == "VariableName":
                    var_name = _text(part)
                elif part_local == "Value":
                    value = _text(part)
            if value == "":
                continue
            key = None
            if var_name:
                key = _DPS_FIELD_ALIASES.get(var_name.casefold()) or _FIELD_ALIASES.get(
                    var_name.casefold()
                )
            if key is None:
                key = parent_key
            if key is None and var_name:
                key = _snake_case(var_name)
            if key is None:
                continue
            fields.setdefault(key, _coerce_scalar(value))
        # Direct scalar child (rare).
        text = _text(child)
        if text and parent_key is not None:
            fields.setdefault(parent_key, _coerce_scalar(text))
    return fields


def _microscript_section_names(elem: ET.Element) -> list[str]:
    return [item["name"] for item in _mine_microscript(elem) if item.get("name")]


def _mine_microscript(elem: ET.Element, *, max_commands: int = 128) -> list[dict[str, Any]]:
    """Mine MicroScriptSection bodies as ordered command-type sequences.

    Stores Object ``Type`` leaf names only (not full micro-command payloads).
    Nested ConditionalGroup / AlternateGroup Objects are walked in order.
    """
    sections: list[dict[str, Any]] = []
    for child in list(elem):
        if not isinstance(child.tag, str):
            continue
        if _local_name(child.tag) != "MicroScriptSection":
            continue
        name = _child_text(child, "Name") or _text(child)
        if not name:
            continue
        commands: list[str] = []
        truncated = False
        for objects in list(child):
            if not isinstance(objects.tag, str) or _local_name(objects.tag) != "Objects":
                continue
            truncated = _walk_script_objects(objects, commands, max_commands=max_commands) or truncated
        entry: dict[str, Any] = {"name": name}
        if commands:
            entry["commands"] = commands
        if truncated:
            entry["commands_truncated"] = True
        sections.append(entry)
    return sections


def _walk_script_objects(
    objects_elem: ET.Element,
    out: list[str],
    *,
    max_commands: int,
) -> bool:
    """Append Object Type leaf names; return True if truncated by max_commands."""
    truncated = False
    for obj in list(objects_elem):
        if len(out) >= max_commands:
            return True
        if not isinstance(obj.tag, str) or _local_name(obj.tag) != "Object":
            continue
        type_attr = str(obj.attrib.get("Type") or obj.attrib.get("type") or "").strip()
        short = type_attr.rsplit(".", 1)[-1] if type_attr else ""
        if not short:
            for typed in list(obj):
                if isinstance(typed.tag, str):
                    short = _local_name(typed.tag)
                    break
        if short:
            out.append(short)
        for typed in list(obj):
            if not isinstance(typed.tag, str):
                continue
            for maybe in list(typed):
                if not isinstance(maybe.tag, str) or _local_name(maybe.tag) != "Objects":
                    continue
                if _walk_script_objects(maybe, out, max_commands=max_commands):
                    truncated = True
    return truncated or len(out) >= max_commands


def _merge_microscript(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in [*(left or []), *(right or [])]:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        existing = by_name.get(name)
        if existing is None:
            by_name[name] = dict(item)
            order.append(name)
            continue
        left_cmds = list(existing.get("commands") or [])
        right_cmds = list(item.get("commands") or [])
        if len(right_cmds) > len(left_cmds):
            by_name[name] = dict(item)
        elif not left_cmds and right_cmds:
            by_name[name] = dict(item)
    return [by_name[name] for name in order]


def _snake_case(value: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or "").strip())
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_").casefold()


def _pipetting_sections(root: ET.Element | None) -> dict[str, dict[str, Any]]:
    """Legacy fallback: aspirate/dispense/mix scalars under matching ancestors."""
    if root is None:
        return {}
    sections: dict[str, dict[str, Any]] = {
        "aspirate": {},
        "dispense": {},
        "mix": {},
        "empty_tips": {},
    }
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        local = _local_name(elem.tag)
        kind = _SECTION_KINDS.get(local.casefold())
        if kind is None:
            continue
        fields = _section_fields(elem)
        if fields:
            sections[kind] = _merge_section_fields(sections[kind], fields)
    return {key: value for key, value in sections.items() if value}


def _section_fields(section: ET.Element) -> dict[str, Any]:
    fields: dict[str, Any] = {}

    def walk(node: ET.Element) -> None:
        for child in list(node):
            if not isinstance(child.tag, str):
                continue
            local = _local_name(child.tag)
            nested_kind = _SECTION_KINDS.get(local.casefold())
            if nested_kind is not None:
                continue
            key = _FIELD_ALIASES.get(local.casefold())
            if key is not None:
                text = _text(child)
                if text:
                    fields.setdefault(key, _coerce_scalar(text))
            walk(child)

    walk(section)
    return fields


def _merge_section_fields(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        merged.setdefault(key, value)
    return merged


def _coerce_scalar(text: str) -> Any:
    raw = str(text).strip()
    if not raw:
        return raw
    lowered = raw.casefold()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in raw or "e" in lowered:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _find(elem: ET.Element | None, local_name: str) -> ET.Element | None:
    """First descendant by local-name (pipeline copy; not fluentcoder ``xcmp._find``).

    Deliberate dual with ``fluentcoder.catalog.xcmp``: this module's ``_text`` uses
    empty-string, xcmp uses ``None`` — do not merge casually (conflicts D002).
    """
    if elem is None:
        return None
    for child in elem.iter():
        if isinstance(child.tag, str) and _local_name(child.tag) == local_name:
            return child
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(elem: ET.Element | None) -> str:
    """Element text or ``\"\"`` (never ``None`` — differs from xcmp._text)."""
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


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}
