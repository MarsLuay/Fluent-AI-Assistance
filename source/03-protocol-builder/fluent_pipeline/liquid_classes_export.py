"""Build ``liquid_classes.json`` from imported ZEIA ``*.xlqc`` definitions.

Site liquid-class names/GUIDs belong in this generated artifact under the local
context/build tree — never as hardcoded ``generation.yaml`` product defaults.
``ready-to-import/`` is gitignored.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from . import xml_compat as ET
from .fluent_naming import strip_fluent_instance_suffix
from .runner import write_json

LIQUID_CLASSES_SCHEMA_VERSION = "tecan.liquid_classes.v3"
LIQUID_CLASSES_COMPATIBLE_SCHEMAS = frozenset(
    {"tecan.liquid_classes.v2", "tecan.liquid_classes.v3"}
)
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
    "errpressureoutofrange": "err_pressure_out_of_range",
    "errpressureoutofrangeretry": "err_pressure_out_of_range_retry",
    "errpmpaspiration": "err_pmp_aspiration",
    "pmpevaluationmodel": "pmp_evaluation_model",
    "pmpcorrectlimit": "pmp_correct_limit",
    "pmperrorlimit": "pmp_error_limit",
    "pmpwarninglimit": "pmp_warning_limit",
    "errsupervisionfailed": "err_supervision_failed",
    "retractsupervisiononoff": "retract_supervision",
    "errliquidexitnotfound": "err_liquid_exit_not_found",
    "errfailedclld": "err_failed_clld",
    "safepathonblockedtiponoff": "safe_path_on_blocked_tip",
}

_PRESSURE_SUPERVISION_KEYS = frozenset(
    {
        "err_pressure_out_of_range",
        "err_pressure_out_of_range_retry",
        "err_pmp_aspiration",
        "pmp_evaluation_model",
        "pmp_correct_limit",
        "pmp_error_limit",
        "pmp_warning_limit",
        "adp_sensitivity",
        "adp_rise_threshold",
        "adp_drop_threshold",
        "aspiration_supervision_tracking",
        "err_supervision_failed",
    }
)
_FORMULA_FUNCTION_NAMES = frozenset(
    {
        "round",
        "exp",
        "log",
        "ln",
        "min",
        "max",
        "abs",
        "adjustaccuracy",
        "if",
        "true",
        "false",
    }
)
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")

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
    entry = _clean(
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
            "source_path": str(path),
        }
    )
    fingerprint = _entry_fingerprint(entry)
    if fingerprint:
        entry["fingerprint"] = fingerprint
    return entry


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
            "fingerprint": item.get("fingerprint"),
            "source_path": item.get("path") or item.get("extracted_path") or item.get("source_path"),
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
            pressure = _pressure_supervision_summary(cleaned)
            if pressure:
                cleaned["pressure_supervision"] = pressure
            formulas = _formula_view(cleaned)
            if formulas:
                cleaned["formulas"] = formulas
                deps = _formula_dependencies(formulas)
                if deps:
                    cleaned["formula_dependencies"] = deps
            mixing = _mixing_implementation(cleaned)
            if mixing:
                cleaned["mixing_implementation"] = mixing
            fingerprint = _profile_fingerprint(cleaned)
            if fingerprint:
                cleaned["fingerprint"] = fingerprint
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
    """Mine MicroScriptSection bodies as ordered commands plus captured payloads."""
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
        records: list[dict[str, Any]] = []
        truncated = False
        for objects in list(child):
            if not isinstance(objects.tag, str) or _local_name(objects.tag) != "Objects":
                continue
            truncated = _walk_script_objects(
                objects, commands, records, max_commands=max_commands
            ) or truncated
        entry: dict[str, Any] = {"name": name}
        if commands:
            entry["commands"] = commands
        if records:
            entry["command_records"] = records
            entry["fingerprint"] = _stable_hash(records)
        if truncated:
            entry["commands_truncated"] = True
        sections.append(entry)
    return sections


def _walk_script_objects(
    objects_elem: ET.Element,
    out: list[str],
    records: list[dict[str, Any]],
    *,
    max_commands: int,
) -> bool:
    """Append Object Type leaf names and nested command records."""
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
        fields, unknown = _micro_command_payload(obj)
        if short:
            out.append(short)
            record: dict[str, Any] = {"type": short}
            if type_attr and type_attr != short:
                record.setdefault("source_metadata", {})["type_full"] = type_attr
            if fields:
                record["fields"] = fields
            if unknown:
                record.setdefault("source_metadata", {})["unknown_children"] = unknown
            children: list[dict[str, Any]] = []
            for typed in list(obj):
                if not isinstance(typed.tag, str):
                    continue
                for maybe in list(typed):
                    if not isinstance(maybe.tag, str) or _local_name(maybe.tag) != "Objects":
                        continue
                    if _walk_script_objects(maybe, out, children, max_commands=max_commands):
                        truncated = True
            if children:
                record["children"] = children
            records.append(record)
            continue
        for typed in list(obj):
            if not isinstance(typed.tag, str):
                continue
            for maybe in list(typed):
                if not isinstance(maybe.tag, str) or _local_name(maybe.tag) != "Objects":
                    continue
                if _walk_script_objects(maybe, out, records, max_commands=max_commands):
                    truncated = True
    return truncated or len(out) >= max_commands


def _micro_command_payload(obj: ET.Element) -> tuple[dict[str, Any], dict[str, Any]]:
    """Capture scalar/variable children without inventing FluentControl tags."""
    fields: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    extra_attrs = {
        key: value
        for key, value in obj.attrib.items()
        if key not in {"Type", "type"} and str(value).strip()
    }
    if extra_attrs:
        unknown["attributes"] = extra_attrs
    for typed in list(obj):
        if not isinstance(typed.tag, str):
            continue
        for child in list(typed):
            if not isinstance(child.tag, str):
                continue
            name = _local_name(child.tag)
            if name in {"Objects"}:
                continue
            if name == "LiquidClassValueVariableNamePair" or any(
                isinstance(grand.tag, str) and _local_name(grand.tag) == "VariableName"
                for grand in list(child)
            ):
                variable = _child_text(child, "VariableName")
                value = _child_text(child, "Value")
                if not variable:
                    continue
                if value == "":
                    unknown.setdefault("empty_variables", {})[variable] = ""
                    continue
                fields[variable] = _coerce_scalar(value)
                continue
            text = _text(child)
            nested_pairs = [
                grand
                for grand in list(child)
                if isinstance(grand.tag, str)
                and _local_name(grand.tag) == "LiquidClassValueVariableNamePair"
            ]
            if nested_pairs:
                for pair in nested_pairs:
                    variable = _child_text(pair, "VariableName")
                    value = _child_text(pair, "Value")
                    if variable and value != "":
                        fields[variable] = _coerce_scalar(value)
                    elif variable:
                        unknown.setdefault("empty_variables", {})[variable] = ""
                continue
            if text:
                fields[name] = _coerce_scalar(text)
                continue
            if list(child):
                unknown.setdefault("structured_children", []).append(name)
            else:
                unknown.setdefault("empty_tags", []).append(name)
    return fields, unknown


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
        left_records = list(existing.get("command_records") or [])
        right_records = list(item.get("command_records") or [])
        if len(right_records) > len(left_records) or len(right_cmds) > len(left_cmds):
            by_name[name] = dict(item)
        elif not left_cmds and right_cmds:
            by_name[name] = dict(item)
        elif not left_records and right_records:
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


def _stable_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _pressure_supervision_summary(profile: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Source-backed pressure/ADP/PMP evidence only; never invent numeric limits."""
    if not isinstance(profile, Mapping):
        return None
    by_section: dict[str, dict[str, Any]] = {}
    detection = profile.get("detection")
    if isinstance(detection, Mapping):
        for section, fields in detection.items():
            if not isinstance(fields, Mapping):
                continue
            hit = {
                key: value
                for key, value in fields.items()
                if key in _PRESSURE_SUPERVISION_KEYS
            }
            if hit:
                by_section[str(section)] = hit
    microscript_hit: dict[str, Any] = {}
    for section in profile.get("microscript") or []:
        if not isinstance(section, Mapping):
            continue
        for record in _iter_command_records(list(section.get("command_records") or [])):
            fields = record.get("fields") or {}
            if not isinstance(fields, Mapping):
                continue
            for key, value in fields.items():
                mapped = _DPS_FIELD_ALIASES.get(str(key).casefold()) or _snake_case(str(key))
                if mapped in _PRESSURE_SUPERVISION_KEYS:
                    microscript_hit.setdefault(mapped, value)
    if microscript_hit:
        existing = dict(by_section.get("microscript") or {})
        existing.update(microscript_hit)
        by_section["microscript"] = existing
    if not by_section:
        return None
    return {
        "presence": "present",
        "by_section": by_section,
        "threshold_recommendation": None,
    }


def _formula_view(profile: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    formulas: dict[str, dict[str, Any]] = {}
    for kind in ("aspirate", "dispense", "mix", "empty_tips"):
        fields = profile.get(kind)
        if not isinstance(fields, Mapping):
            continue
        section = {
            key: value
            for key, value in fields.items()
            if "formula" in str(key).casefold() or isinstance(value, str) and any(
                token in str(value) for token in ("volume", "round(", "AdjustAccuracy")
            )
        }
        if section:
            formulas[kind] = section
    return formulas


def _formula_dependencies(formulas: Mapping[str, Mapping[str, Any]]) -> list[str]:
    names: set[str] = set()
    for section in formulas.values():
        for value in section.values():
            text = str(value or "")
            for match in _IDENT_RE.finditer(text):
                token = match.group(0)
                if token.casefold() in _FORMULA_FUNCTION_NAMES:
                    continue
                if token.isdecimal():
                    continue
                names.add(token)
    return sorted(names, key=str.casefold)


def _mixing_implementation(profile: Mapping[str, Any]) -> str | None:
    sections = [str(item) for item in (profile.get("microscript_sections") or [])]
    if any(item.casefold() == "mix" for item in sections):
        return "liquid_class_microscript"
    script = profile.get("microscript") or []
    for item in script:
        if not isinstance(item, Mapping):
            continue
        commands = [str(cmd) for cmd in (item.get("commands") or [])]
        if any("mix" in cmd.casefold() for cmd in commands):
            return "liquid_class_microscript"
    if profile.get("mix"):
        return "equation_set"
    return None


def _profile_fingerprint(profile: Mapping[str, Any]) -> str:
    payload = {
        "head": profile.get("head"),
        "tip": profile.get("tip"),
        "aspirate": profile.get("aspirate"),
        "dispense": profile.get("dispense"),
        "mix": profile.get("mix"),
        "detection": profile.get("detection"),
        "pressure_supervision": profile.get("pressure_supervision"),
        "microscript": [
            {
                "name": item.get("name"),
                "commands": item.get("commands"),
                "command_records": item.get("command_records"),
                "fingerprint": item.get("fingerprint"),
            }
            for item in (profile.get("microscript") or [])
            if isinstance(item, Mapping)
        ],
    }
    return _stable_hash(payload)


def _entry_fingerprint(entry: Mapping[str, Any]) -> str:
    payload = {
        "guid": entry.get("guid"),
        "name": entry.get("name"),
        "profiles": [
            {
                "head": item.get("head"),
                "tip": item.get("tip"),
                "fingerprint": item.get("fingerprint"),
            }
            for item in (entry.get("profiles") or [])
            if isinstance(item, Mapping)
        ],
    }
    return _stable_hash(payload)


def load_liquid_classes_catalog(source: Mapping[str, Any] | Path | str | None) -> dict[str, Any] | None:
    """Load a v2/v3 liquid-class catalog from a mapping, path, or context root."""
    if source is None:
        return None
    if isinstance(source, Mapping):
        if isinstance(source.get("entries"), list) and source.get("schema_version"):
            return _normalize_loaded_catalog(dict(source))
        nested = source.get("liquid_classes_catalog")
        if nested is not None:
            loaded = load_liquid_classes_catalog(nested)  # type: ignore[arg-type]
            if loaded:
                return loaded
        for key in ("context_root", "root", "extracted_dir"):
            raw = source.get(key)
            if not raw:
                continue
            path = Path(str(raw))
            candidate = path / LIQUID_CLASSES_FILENAME if path.is_dir() else path
            loaded = load_liquid_classes_catalog(candidate)
            if loaded:
                return loaded
        return None
    path = Path(source)
    if path.is_dir():
        path = path / LIQUID_CLASSES_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return _normalize_loaded_catalog(payload)


def _normalize_loaded_catalog(payload: dict[str, Any]) -> dict[str, Any] | None:
    version = str(payload.get("schema_version") or "").strip()
    if version and version not in LIQUID_CLASSES_COMPATIBLE_SCHEMAS:
        payload = dict(payload)
        payload["schema_unsupported"] = True
        return payload
    if not isinstance(payload.get("entries"), list):
        return None
    return payload


def find_liquid_class_entry(catalog: Mapping[str, Any] | None, name: str) -> dict[str, Any] | None:
    wanted = _norm(name)
    if not wanted or not isinstance(catalog, Mapping):
        return None
    for entry in catalog.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        aliases = [entry.get("name"), entry.get("guid"), *(entry.get("aliases") or [])]
        if any(_norm(item) == wanted for item in aliases):
            return dict(entry)
    return None


def matching_liquid_class_profiles(
    entry: Mapping[str, Any] | None,
    *,
    head: str | None = None,
    tip: str | None = None,
    operation: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(entry, Mapping):
        return []
    profiles = [item for item in (entry.get("profiles") or []) if isinstance(item, Mapping)]
    if head:
        wanted_head = _norm(head)
        profiles = [item for item in profiles if _norm(item.get("head")) == wanted_head]
    if tip:
        wanted_tip = _norm(tip)
        profiles = [item for item in profiles if _norm(item.get("tip")) == wanted_tip]
    if operation:
        wanted_op = _operation_section(operation)
        if wanted_op:
            profiles = [
                item
                for item in profiles
                if item.get(wanted_op)
                or (item.get("detection") or {}).get(wanted_op)
                or wanted_op.title() in [str(section) for section in (item.get("microscript_sections") or [])]
            ]
    return [dict(item) for item in profiles]


def _operation_section(operation: str | None) -> str:
    text = str(operation or "").strip().casefold()
    if text in {"aspirate", "dispense", "mix", "empty_tips"}:
        return text
    if "aspirat" in text:
        return "aspirate"
    if "dispens" in text:
        return "dispense"
    if text.startswith("mix"):
        return "mix"
    return ""


def diff_liquid_class_entries(
    source: Mapping[str, Any] | None,
    edited: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Deterministic semantic diff of two liquid-class catalog entries."""
    if source is None and edited is None:
        return {"changed": False, "changes": []}
    left = source or {}
    right = edited or {}
    changes: list[dict[str, Any]] = []
    if (left.get("fingerprint") or "") != (right.get("fingerprint") or ""):
        changes.append(
            {
                "path": "fingerprint",
                "source": left.get("fingerprint"),
                "edited": right.get("fingerprint"),
            }
        )
    left_profiles = {
        (str(item.get("head") or ""), str(item.get("tip") or "")): item
        for item in (left.get("profiles") or [])
        if isinstance(item, Mapping)
    }
    right_profiles = {
        (str(item.get("head") or ""), str(item.get("tip") or "")): item
        for item in (right.get("profiles") or [])
        if isinstance(item, Mapping)
    }
    for key in sorted(set(left_profiles) | set(right_profiles)):
        src = left_profiles.get(key)
        dst = right_profiles.get(key)
        label = f"profiles[{key[0] or '_'} x {key[1] or '_'}]"
        if src is None:
            changes.append({"path": label, "source": None, "edited": dst.get("fingerprint") if dst else None})
            continue
        if dst is None:
            changes.append({"path": label, "source": src.get("fingerprint"), "edited": None})
            continue
        for field in (
            "aspirate",
            "dispense",
            "mix",
            "detection",
            "pressure_supervision",
            "formulas",
            "microscript",
            "fingerprint",
        ):
            if json.dumps(src.get(field), sort_keys=True, default=str) != json.dumps(
                dst.get(field), sort_keys=True, default=str
            ):
                changes.append(
                    {
                        "path": f"{label}.{field}",
                        "source": src.get("fingerprint") if field == "fingerprint" else src.get(field),
                        "edited": dst.get("fingerprint") if field == "fingerprint" else dst.get(field),
                    }
                )
    return {"changed": bool(changes), "changes": changes[:50]}


def diff_liquid_class_catalogs(
    source: Mapping[str, Any] | None,
    edited: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not source and not edited:
        return {
            "requested_liquid_class_modification": False,
            "changed": False,
            "entries": [],
        }
    if edited is None:
        return {
            "requested_liquid_class_modification": False,
            "changed": False,
            "entries": [],
        }
    src_entries = {
        _norm(item.get("guid") or item.get("name")): item
        for item in ((source or {}).get("entries") or [])
        if isinstance(item, Mapping)
    }
    dst_entries = {
        _norm(item.get("guid") or item.get("name")): item
        for item in ((edited or {}).get("entries") or [])
        if isinstance(item, Mapping)
    }
    rows = []
    for key in sorted(set(src_entries) | set(dst_entries)):
        row = diff_liquid_class_entries(src_entries.get(key), dst_entries.get(key))
        row["key"] = key
        rows.append(row)
    changed = any(item.get("changed") for item in rows)
    return {
        "requested_liquid_class_modification": True,
        "changed": changed,
        "entries": rows,
    }


def analyze_liquid_class_use(
    *,
    name: str,
    operation: str | None,
    catalog: Mapping[str, Any] | None,
    head: str | None = None,
    tip: str | None = None,
    faithful_generation: bool = False,
) -> dict[str, Any]:
    """Return failures/reviews for one liquid-class use against a mined catalog."""
    failures: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    if catalog is None:
        return {"failures": failures, "reviews": reviews, "matched_profiles": []}
    if catalog.get("schema_unsupported"):
        reviews.append(
            {
                "reason": "unsupported_liquid_class_schema",
                "message": (
                    f"liquid_classes catalog schema {catalog.get('schema_version')!r} is not "
                    "v2/v3; re-import the ZEIA with current protocol-builder."
                ),
            }
        )
        return {"failures": failures, "reviews": reviews, "matched_profiles": []}
    entry = find_liquid_class_entry(catalog, name)
    if entry is None:
        return {"failures": failures, "reviews": reviews, "matched_profiles": []}
    profiles = matching_liquid_class_profiles(entry, head=head, tip=tip, operation=operation)
    unconstrained = matching_liquid_class_profiles(entry, operation=operation)
    if head and not matching_liquid_class_profiles(entry, head=head):
        failures.append(
            {
                "reason": "liquid_class_head_missing",
                "message": f"{name!r} has no mined profile for head {head!r}.",
            }
        )
    elif operation and unconstrained and not profiles and (head or tip):
        failures.append(
            {
                "reason": "liquid_class_profile_mismatch",
                "message": (
                    f"{name!r} has no {operation} profile matching head={head!r} tip={tip!r}."
                ),
            }
        )
    elif not head and not tip and len(unconstrained) > 1:
        reviews.append(
            {
                "reason": "ambiguous_liquid_class_profile",
                "message": (
                    f"{name!r} has {len(unconstrained)} head×tip profiles for {operation or 'this operation'}; "
                    "the first profile was not selected automatically."
                ),
                "profile_count": len(unconstrained),
            }
        )
    for profile in profiles:
        for section in profile.get("microscript") or []:
            if not isinstance(section, Mapping):
                continue
            if section.get("commands_truncated"):
                item = {
                    "reason": "microscript_truncated",
                    "message": f"{name!r} microscript section {section.get('name')!r} was truncated.",
                }
                if faithful_generation:
                    failures.append(item)
                else:
                    reviews.append(item)
            for record in _iter_command_records(section.get("command_records") or []):
                unknown = (record.get("source_metadata") or {}).get("unknown_children") or {}
                if unknown.get("structured_children") or unknown.get("attributes"):
                    item = {
                        "reason": "unknown_micro_command_payload",
                        "message": (
                            f"{name!r} command {record.get('type')!r} preserved unknown source fields."
                        ),
                        "unknown_children": unknown,
                    }
                    if faithful_generation:
                        failures.append(item)
                    else:
                        reviews.append(item)
        deps = [str(item) for item in (profile.get("formula_dependencies") or [])]
        mined_vars = set(_mined_variable_names(profile))
        missing = [
            item
            for item in deps
            if item.casefold() not in {"volume", "vol"}
            and item not in mined_vars
            and item.casefold() not in {name.casefold() for name in mined_vars}
        ]
        if missing:
            reviews.append(
                {
                    "reason": "unresolved_formula_dependency",
                    "message": f"{name!r} formulas reference {missing} without a mined variable.",
                    "identifiers": missing,
                }
            )
    return {"failures": failures, "reviews": reviews, "matched_profiles": profiles}


def _iter_command_records(records: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, Mapping):
            continue
        out.append(dict(item))
        out.extend(_iter_command_records(list(item.get("children") or [])))
    return out


def _mined_variable_names(profile: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    detection = profile.get("detection") or {}
    if isinstance(detection, Mapping):
        for fields in detection.values():
            if isinstance(fields, Mapping):
                names.update(str(key) for key in fields)
    for section in profile.get("microscript") or []:
        if not isinstance(section, Mapping):
            continue
        for record in _iter_command_records(list(section.get("command_records") or [])):
            fields = record.get("fields") or {}
            if isinstance(fields, Mapping):
                names.update(str(key) for key in fields)
    return names
