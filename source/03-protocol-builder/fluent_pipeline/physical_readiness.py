"""Evidence-backed physical hardware readiness for generated protocols.

The offline pipeline can describe the hardware identities and geometry that a
source export exposes, but it cannot prove alignment, retention, clearance, or
mechanical compliance.  This module keeps those two facts separate.  It is
deliberately conservative: absent evidence is represented as ``unknown`` and
community/forum claims never become an incompatibility rule.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .labware_catalog_export import build_labware_catalog_from_geometry


PHYSICAL_READINESS_SCHEMA_VERSION = "tecan.physical_readiness.v1"
PHYSICAL_VERIFICATION_FILENAME = "physical_verification.json"
PHYSICAL_STATUS_SUPPORTED = "supported_by_source_evidence"
PHYSICAL_STATUS_UNKNOWN = "unknown_requires_hardware_verification"
PHYSICAL_STATUS_INCOMPATIBLE = "incompatible_by_verified_contract"
PHYSICAL_STATUS_VERIFIED = "previously_verified_on_same_hardware_fingerprint"
CHECK_REQUIRED = "required_unverified"
CHECK_VERIFIED = "verified"
CHECK_FAILED = "failed"
CHECK_NOT_APPLICABLE = "not_applicable"

_HEAD_TOKENS = (
    ("mca384", "MCA384"),
    ("mca 384", "MCA384"),
    ("mca96", "MCA96"),
    ("mca 96", "MCA96"),
    ("mca", "MCA"),
    ("fca", "FCA"),
    ("liha", "LiHa"),
)
_TIP_OPERATIONS = {"pick_up_tips", "mca384_get_tips", "liha_get_tips"}
_TIP_RETURN_OPERATIONS = {"set_tips_back", "mca384_drop_tips", "liha_drop_tips", "drop_tips"}
_LIQUID_OPERATIONS = {
    "aspirate",
    "dispense",
    "mix",
    "mca384_mix",
    "liha_aspirate",
    "liha_dispense",
    "liha_mix",
}


def canonical_fingerprint(value: Any) -> str:
    """Hash JSON-like values after removing ordering and path volatility."""

    encoded = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_hardware_profile(
    protocol_ir: Mapping[str, Any] | None = None,
    source_manifest: Mapping[str, Any] | None = None,
    *,
    host_environment: Mapping[str, Any] | None = None,
    instrument_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable, source-backed hardware profile for one protocol."""

    ir = protocol_ir if isinstance(protocol_ir, Mapping) else {}
    manifest = source_manifest if isinstance(source_manifest, Mapping) else {}
    explicit = _explicit_profile(manifest, ir)
    host = _first_mapping(
        host_environment,
        manifest.get("host_environment"),
        (ir.get("source") or {}).get("host_environment") if isinstance(ir.get("source"), Mapping) else None,
    )
    config = _first_mapping(
        instrument_config,
        manifest.get("host_instrument_configuration"),
        (ir.get("source") or {}).get("host_instrument_configuration")
        if isinstance(ir.get("source"), Mapping)
        else None,
    )
    geometry = manifest.get("worktable_geometry") if isinstance(manifest.get("worktable_geometry"), Mapping) else {}
    catalog = build_labware_catalog_from_geometry(geometry) or {"entries": []}
    steps = _steps(ir)
    labware = _labware_profiles(ir, catalog)
    tips = _tip_profiles(ir, catalog, explicit)
    head = _head_profile(ir, manifest, explicit)
    software = _software_profile(host)
    configuration = _configuration_profile(config)
    devices = _device_profile(ir, manifest, explicit)
    capabilities = _capability_profile(steps, manifest, explicit)

    material = {
        "software": _fingerprint_material(software),
        "instrument_configuration": _fingerprint_material(configuration),
        "head": _fingerprint_material(head),
        "tips": [_fingerprint_material(item) for item in tips],
        "labware": [_fingerprint_material(item) for item in labware],
        "devices": _fingerprint_material(devices),
        "capabilities": _fingerprint_material(capabilities),
    }
    profile = {
        "schema_version": PHYSICAL_READINESS_SCHEMA_VERSION,
        "status": "source_backed" if _has_known_profile(material) else "unknown",
        "software": software,
        "instrument_configuration": configuration,
        "head": head,
        "tips": tips,
        "tip": tips[0] if tips else _unknown_record("tip consumable"),
        "labware": labware,
        "devices": devices,
        "capabilities": capabilities,
        "evidence": _profile_evidence(software, configuration, head, tips, labware, devices),
        "fingerprint": canonical_fingerprint(material),
    }
    return profile


def build_interaction_profiles(
    protocol_ir: Mapping[str, Any] | None,
    hardware_profile: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Derive only the physical interactions represented by the protocol IR."""

    ir = protocol_ir if isinstance(protocol_ir, Mapping) else {}
    profile = hardware_profile if isinstance(hardware_profile, Mapping) else {}
    by_label = {
        str(item.get("label")): item
        for item in profile.get("labware") or []
        if isinstance(item, Mapping) and item.get("label")
    }
    tip = profile.get("tip") if isinstance(profile.get("tip"), Mapping) else {}
    head = profile.get("head") if isinstance(profile.get("head"), Mapping) else {}
    interactions: list[dict[str, Any]] = []
    for ordinal, step in enumerate(_steps(ir), start=1):
        operation = str(step.get("operation") or "").casefold()
        params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
        if operation not in _TIP_OPERATIONS | _TIP_RETURN_OPERATIONS | _LIQUID_OPERATIONS | {"move_plate"}:
            if not _mentions_scan(step, params):
                continue
        label = str(
            step.get("target_labware")
            or params.get("labware")
            or step.get("source_labware")
            or step.get("destination_labware")
            or ""
        ).strip()
        item = by_label.get(label, {})
        selected_channels = _selected_channels(step, params)
        kind = _interaction_kind(operation, step, params)
        unmodeled = _unmodeled_effects(kind, item, selected_channels)
        interaction_material = {
            "kind": kind,
            "operation": operation,
            "step_id": step.get("id") or f"step_{ordinal:03d}",
            "labware": _fingerprint_material(item),
            "tip": _fingerprint_material(tip),
            "head": _fingerprint_material(head),
            "selected_channels": selected_channels,
            "unmodeled_effects": unmodeled,
        }
        interaction = {
            "id": f"interaction_{canonical_fingerprint(interaction_material)[:16]}",
            "kind": kind,
            "operation": operation,
            "step_id": step.get("id") or f"step_{ordinal:03d}",
            "source_path": step.get("source_path") or step.get("compiled_path") or None,
            "head": _fingerprint_material(head),
            "tip": _fingerprint_material(tip),
            "labware": _fingerprint_material(item),
            "site": _site_from_item(item),
            "selected_channels": selected_channels,
            "hardware_profile_fingerprint": profile.get("fingerprint"),
            "modeled_effects": _modeled_effects(kind),
            "unmodeled_effects": unmodeled,
            "status": PHYSICAL_STATUS_UNKNOWN if unmodeled else PHYSICAL_STATUS_SUPPORTED,
            "fingerprint": canonical_fingerprint(interaction_material),
        }
        interactions.append(interaction)
    return interactions


def build_physical_verification(
    protocol_ir: Mapping[str, Any] | None = None,
    source_manifest: Mapping[str, Any] | None = None,
    *,
    host_environment: Mapping[str, Any] | None = None,
    instrument_config: Mapping[str, Any] | None = None,
    previous_verification: Mapping[str, Any] | None = None,
    verification_results: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    offline_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic physical checks without claiming a hardware run."""

    ir = protocol_ir if isinstance(protocol_ir, Mapping) else {}
    profile = build_hardware_profile(
        ir,
        source_manifest,
        host_environment=host_environment,
        instrument_config=instrument_config,
    )
    interactions = build_interaction_profiles(ir, profile)
    protocol_fingerprint = canonical_fingerprint(_protocol_material(ir))
    verification_fingerprint = canonical_fingerprint(
        {
            "protocol": protocol_fingerprint,
            "hardware": profile.get("fingerprint"),
            "interactions": [item.get("fingerprint") for item in interactions],
        }
    )
    previous = previous_verification if isinstance(previous_verification, Mapping) else {}
    prior_matches = _prior_check_results(previous, verification_fingerprint)
    supplied = _verification_results(verification_results)
    checks: list[dict[str, Any]] = []
    for interaction in interactions:
        for check in _checks_for_interaction(interaction):
            check_id = check["id"]
            status = supplied.get(check_id) or prior_matches.get(check_id) or CHECK_REQUIRED
            if status not in {CHECK_REQUIRED, CHECK_VERIFIED, CHECK_FAILED, CHECK_NOT_APPLICABLE}:
                status = CHECK_REQUIRED
            checks.append(
                {
                    **check,
                    "status": status,
                    "interaction_id": interaction["id"],
                    "hardware_profile_fingerprint": profile.get("fingerprint"),
                    "evidence": list(profile.get("evidence") or []),
                }
            )
    stale = bool(previous) and str(previous.get("verification_fingerprint") or "") != verification_fingerprint
    if stale:
        for check in checks:
            if check["status"] == CHECK_VERIFIED and check["hardware_profile_fingerprint"] != previous.get(
                "hardware_profile_fingerprint"
            ):
                check["status"] = CHECK_REQUIRED
                check["invalidation_reason"] = "relevant protocol or hardware fingerprint changed"
    rules = _safe_rules(source_manifest)
    required = [item for item in checks if item["status"] == CHECK_REQUIRED]
    failed = [item for item in checks if item["status"] == CHECK_FAILED]
    status = CHECK_FAILED if failed else CHECK_REQUIRED if required else CHECK_VERIFIED if checks else CHECK_NOT_APPLICABLE
    return {
        "schema_version": PHYSICAL_READINESS_SCHEMA_VERSION,
        "status": status,
        "hardware_run_ready": status == CHECK_VERIFIED,
        "hardware_profile": profile,
        "hardware_profile_fingerprint": profile.get("fingerprint"),
        "protocol_fingerprint": protocol_fingerprint,
        "verification_fingerprint": verification_fingerprint,
        "interactions": interactions,
        "checks": checks,
        "compatibility_rules": rules,
        "offline_validation": _offline_summary(offline_validation),
        "summary": _summary(status, profile, interactions, checks),
    }


def physical_verification_is_stale(previous: Mapping[str, Any] | None, current: Mapping[str, Any] | None) -> bool:
    """Return whether a prior verification belongs to a different contract."""

    if not isinstance(previous, Mapping) or not previous:
        return False
    if not isinstance(current, Mapping):
        return True
    previous_fp = str(previous.get("verification_fingerprint") or "")
    current_fp = str(current.get("verification_fingerprint") or "")
    return bool(previous_fp and current_fp and previous_fp != current_fp)


def build_physical_readiness(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compatibility name for callers that treat the report as readiness."""

    return build_physical_verification(*args, **kwargs)


def write_physical_verification(path: Path, report: Mapping[str, Any]) -> Path:
    """Write a stable JSON artifact without adding generation timestamps."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(_canonical(report), indent=2) + "\n", encoding="utf-8")
    return destination


def render_physical_verification_markdown(report: Mapping[str, Any]) -> str:
    """Render a concise operator-facing summary of the machine-readable result."""

    lines = [
        "# Physical Hardware Verification",
        "",
        f"- Status: `{report.get('status') or 'unknown'}`",
        f"- Hardware run ready: `{bool(report.get('hardware_run_ready'))}`",
        f"- Hardware profile: `{report.get('hardware_profile_fingerprint') or 'unknown'}`",
        "",
        str(report.get("summary") or "Physical verification was not evaluated."),
        "",
    ]
    for check in report.get("checks") or []:
        if not isinstance(check, Mapping):
            continue
        lines.append(f"- `{check.get('id')}`: `{check.get('status')}` — {check.get('prompt') or ''}")
    return "\n".join(lines).rstrip() + "\n"


def _explicit_profile(manifest: Mapping[str, Any], ir: Mapping[str, Any]) -> Mapping[str, Any]:
    for value in (
        manifest.get("physical_hardware"),
        manifest.get("physical_profile"),
        manifest.get("hardware_profile"),
        (ir.get("source") or {}).get("physical_hardware") if isinstance(ir.get("source"), Mapping) else None,
    ):
        if isinstance(value, Mapping):
            return value
    return {}


def _head_profile(ir: Mapping[str, Any], manifest: Mapping[str, Any], explicit: Mapping[str, Any]) -> dict[str, Any]:
    raw = _first_mapping(explicit.get("head"), explicit.get("pipetting_head"))
    record = _identity_record(raw, ("family", "type", "model", "revision", "generation"), "head")
    if record["status"] == "unknown":
        candidates = _head_candidates(ir, manifest)
        if len(candidates) == 1:
            record["family"] = candidates[0]
            record["status"] = "source_backed"
            record["evidence"] = [{"source": "protocol_ir/device_aliases", "value": candidates[0]}]
    if record.get("type") and not record.get("family"):
        record["family"] = record["type"]
    record.pop("type", None)
    return record


def _tip_profiles(ir: Mapping[str, Any], catalog: Mapping[str, Any], explicit: Mapping[str, Any]) -> list[dict[str, Any]]:
    explicit_tips = explicit.get("tips")
    values: list[Mapping[str, Any]] = []
    if isinstance(explicit_tips, Mapping):
        values.append(explicit_tips)
    elif isinstance(explicit_tips, Sequence) and not isinstance(explicit_tips, (str, bytes)):
        values.extend(item for item in explicit_tips if isinstance(item, Mapping))
    if isinstance(explicit.get("tip"), Mapping):
        values.insert(0, explicit["tip"])
    labware = ir.get("labware") if isinstance(ir.get("labware"), list) else []
    entries = catalog.get("entries") if isinstance(catalog, Mapping) else []
    for item in labware:
        if not isinstance(item, Mapping) or not _looks_like_tip_box(item):
            continue
        match = _catalog_match(item, entries)
        merged = dict(match or {})
        merged.update({key: item[key] for key in ("label", "catalog", "source_path") if item.get(key) is not None})
        values.append(merged)
    unique: dict[str, dict[str, Any]] = {}
    for value in values:
        row = _identity_record(
            value,
            (
                "name",
                "label",
                "catalog",
                "guid",
                "object_id",
                "part",
                "part_number",
                "sku",
                "capacity_ul",
                "tray_subtype",
                "wafer_subtype",
                "rows",
                "cols",
                "pitch_x_mm",
                "pitch_y_mm",
                "custom_attributes",
            ),
            "tip consumable",
        )
        key = canonical_fingerprint(_fingerprint_material(row))
        unique.setdefault(key, row)
    return sorted(unique.values(), key=lambda item: str(item.get("label") or item.get("name") or "").casefold())


def _labware_profiles(ir: Mapping[str, Any], catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = catalog.get("entries") if isinstance(catalog, Mapping) else []
    rows: list[dict[str, Any]] = []
    for item in ir.get("labware") or []:
        if not isinstance(item, Mapping):
            continue
        match = _catalog_match(item, entries) or {}
        row = _identity_record(
            {**match, **{key: item[key] for key in ("label", "catalog", "location", "position", "source_path") if item.get(key) is not None}},
            (
                "name", "label", "catalog", "guid", "location", "position", "site_templates", "physical_width_mm",
                "physical_depth_mm", "physical_height_mm", "rows", "cols", "pitch_x_mm", "pitch_y_mm",
                "well_diameter_mm", "well_depth_mm", "well_shape", "grip", "custom_attributes",
            ),
            "labware",
        )
        row["site"] = _site_from_item(match, item)
        rows.append(row)
    return rows


def _catalog_match(item: Mapping[str, Any], entries: Any) -> Mapping[str, Any] | None:
    wanted = {str(item.get(key) or "").strip().casefold() for key in ("label", "catalog", "name") if item.get(key)}
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        names = {str(entry.get(key) or "").strip().casefold() for key in ("name", "guid") if entry.get(key)}
        names.update(str(value).strip().casefold() for value in entry.get("aliases") or [] if value)
        if wanted & names:
            return entry
    return None


def _site_from_item(entry: Mapping[str, Any] | None, item: Mapping[str, Any] | None = None) -> dict[str, Any]:
    entry = entry if isinstance(entry, Mapping) else {}
    item = item if isinstance(item, Mapping) else {}
    position = item.get("position")
    templates = entry.get("site_templates") or []
    selected = None
    for template in templates:
        if not isinstance(template, Mapping):
            continue
        if str(template.get("index")) in {str(position), str(_as_int(position) - 1 if _as_int(position) is not None else "")}:  # noqa: SIM114
            selected = template
            break
    return _fingerprint_material(
        {
            "location": item.get("location"),
            "position": position,
            "template": selected,
        }
    )


def _software_profile(host: Mapping[str, Any]) -> dict[str, Any]:
    product = next((item for item in host.get("products") or [] if isinstance(item, Mapping) and str(item.get("family")) == "FluentControl"), {})
    values = {"version": product.get("version"), "build": product.get("build"), "detected": product.get("detected")}
    return _identity_record(values, ("version", "build", "detected"), "FluentControl software")


def _configuration_profile(config: Mapping[str, Any]) -> dict[str, Any]:
    matches = _strings(config.get("matches"))
    expected = _strings((config.get("expected") or {}).get("exact_names") if isinstance(config.get("expected"), Mapping) else [])
    values = {
        "identity": matches[0] if len(matches) == 1 else None,
        "matches": matches,
        "expected": expected,
        "configuration_state": config.get("status"),
        "provenance": config.get("provenance"),
        "source": config.get("source"),
        "source_path": config.get("source_path"),
    }
    record = _identity_record(
        values,
        ("identity", "matches", "expected", "configuration_state"),
        "instrument configuration",
    )
    if len(matches) > 1:
        record["identity"] = None
        record["status"] = "unknown"
    return record


def _device_profile(ir: Mapping[str, Any], manifest: Mapping[str, Any], explicit: Mapping[str, Any]) -> dict[str, Any]:
    raw = _first_mapping(explicit.get("devices"), explicit.get("device"))
    aliases = _strings(raw.get("aliases")) or _strings(manifest.get("device_aliases"))
    available = _strings(raw.get("available_ids")) or _strings(manifest.get("available_ids"))
    return _identity_record({"aliases": aliases, "available_ids": available}, ("aliases", "available_ids"), "device identity")


def _capability_profile(steps: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], explicit: Mapping[str, Any]) -> dict[str, Any]:
    values = _first_mapping(explicit.get("capabilities"), manifest.get("physical_capabilities"))
    rga_required = any(str(step.get("operation") or "") == "move_plate" for step in steps)
    scanner_required = any(_mentions_scan(step, step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}) for step in steps)
    result = _identity_record(
        {
            "rga": values.get("rga"),
            "scanner": values.get("scanner"),
            "provenance": values.get("provenance"),
            "source": values.get("source"),
            "source_path": values.get("source_path"),
        },
        ("rga", "scanner"),
        "physical capability",
    )
    # These flags describe what the protocol needs, not what the hardware
    # evidence proves. They must not turn an otherwise empty profile into a
    # source-backed profile.
    result.update({"rga_required": rga_required, "scanner_required": scanner_required})
    return result


def _checks_for_interaction(interaction: Mapping[str, Any]) -> list[dict[str, Any]]:
    kind = str(interaction.get("kind") or "")
    prefix = str(interaction.get("id") or "interaction")
    effects = set(interaction.get("unmodeled_effects") or [])
    prompts = {
        "tip_rack_retention": "Confirm the selected tip rack/tray remains seated during tip pickup.",
        "tip_alignment": "Confirm mounted tips are visibly straight and parallel before the first pipetting operation.",
        "well_entry_clearance": "Confirm a representative well entry does not contact or lift the labware.",
        "partial_tip_pickup": "Confirm the selected partial-row/partial-column tip pickup behaves correctly.",
        "nest_retention": "Confirm the carrier/nest retains the labware during the gripper operation.",
        "gripper_clearance": "Confirm gripper fingers clear adjacent carriers and release/reseat the labware correctly.",
        "barcode_clearance": "Confirm the barcode/scanner path clears adjacent segments and carriers.",
    }
    checks: list[dict[str, Any]] = []
    for effect in sorted(effects):
        checks.append(
            {
                "id": f"check_{canonical_fingerprint({'interaction': prefix, 'effect': effect})[:16]}",
                "effect": effect,
                "prompt": prompts.get(effect, f"Verify physical effect: {effect}."),
                "status": CHECK_REQUIRED,
            }
        )
    return checks


def _interaction_kind(operation: str, step: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    if _mentions_scan(step, params):
        return "barcode_scan"
    if operation in _TIP_OPERATIONS:
        return "tip_pickup"
    if operation in _TIP_RETURN_OPERATIONS:
        return "tip_return"
    if operation == "move_plate":
        return "rga_transfer"
    if operation in _LIQUID_OPERATIONS:
        return "pipetting"
    return "physical_interaction"


def _unmodeled_effects(kind: str, item: Mapping[str, Any], selected_channels: list[Any]) -> list[str]:
    if kind == "tip_pickup":
        effects = ["tip_rack_retention", "tip_alignment"]
        if selected_channels and len(selected_channels) < 8:
            effects.append("partial_tip_pickup")
        return effects
    if kind == "pipetting":
        return ["well_entry_clearance", "tip_alignment"]
    if kind == "rga_transfer":
        return ["nest_retention", "gripper_clearance"]
    if kind == "barcode_scan":
        return ["barcode_clearance"]
    return []


def _modeled_effects(kind: str) -> list[str]:
    return {"tip_pickup": ["logical_tip_state"], "pipetting": ["logical_liquid_transfer"], "rga_transfer": ["logical_labware_location"], "barcode_scan": []}.get(kind, [])


def _safe_rules(source_manifest: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    raw = source_manifest.get("physical_compatibility_rules") if isinstance(source_manifest, Mapping) else None
    result: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, Mapping):
            continue
        rule = dict(item)
        provenance = str(rule.get("provenance") or rule.get("source") or "").casefold()
        state = str(rule.get("status") or PHYSICAL_STATUS_UNKNOWN)
        if ("forum" in provenance or "community" in provenance) and state == PHYSICAL_STATUS_INCOMPATIBLE:
            rule["status"] = PHYSICAL_STATUS_UNKNOWN
            rule["enforced"] = False
            rule["reason"] = "community evidence cannot establish a verified incompatibility"
        else:
            rule["status"] = state if state in {PHYSICAL_STATUS_SUPPORTED, PHYSICAL_STATUS_UNKNOWN, PHYSICAL_STATUS_INCOMPATIBLE, PHYSICAL_STATUS_VERIFIED} else PHYSICAL_STATUS_UNKNOWN
            rule["enforced"] = rule["status"] == PHYSICAL_STATUS_INCOMPATIBLE and bool(rule.get("verified_contract"))
        result.append(_canonical(rule))
    return sorted(result, key=lambda value: (str(value.get("id") or ""), str(value.get("provenance") or "")))


def _prior_check_results(previous: Mapping[str, Any], verification_fingerprint: str) -> dict[str, str]:
    if str(previous.get("verification_fingerprint") or "") != verification_fingerprint:
        return {}
    return {str(item.get("id")): str(item.get("status")) for item in previous.get("checks") or [] if isinstance(item, Mapping) and item.get("id")}


def _verification_results(value: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    if isinstance(value, Mapping):
        if isinstance(value.get("checks"), Sequence):
            value = value.get("checks")
        else:
            return {str(key): str(status) for key, status in value.items()}
    return {str(item.get("id")): str(item.get("status")) for item in value or [] if isinstance(item, Mapping) and item.get("id")}


def _profile_evidence(*records: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    pending = list(records)
    while pending:
        record = pending.pop(0)
        if isinstance(record, Mapping):
            evidence.extend(item for item in record.get("evidence") or [] if isinstance(item, Mapping))
        elif isinstance(record, Sequence) and not isinstance(record, (str, bytes)):
            pending.extend(record)
    return sorted({_stable_json(item): item for item in evidence}.values(), key=_stable_json)


def _summary(status: str, profile: Mapping[str, Any], interactions: Sequence[Mapping[str, Any]], checks: Sequence[Mapping[str, Any]]) -> str:
    if status == CHECK_NOT_APPLICABLE:
        return "The protocol contains no physical interaction requiring hardware verification."
    if status == CHECK_VERIFIED:
        return "All physical checks are verified on the current protocol and hardware fingerprint."
    if status == CHECK_FAILED:
        return "A physical verification check failed; do not run until the target setup is reviewed."
    unknown = sum(1 for item in profile.get("evidence") or [] if item.get("status") == "unknown")
    return f"{len(interactions)} physical interaction(s) require explicit hardware verification; offline simulation does not certify mechanical behavior."


def _offline_summary(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "not_provided", "hardware_run_ready": False}
    return {"status": value.get("status") or "unknown", "hardware_run_ready": False}


def _has_known_profile(material: Mapping[str, Any]) -> bool:
    for key, value in material.items():
        if key == "capabilities" and isinstance(value, Mapping):
            value = {
                name: item
                for name, item in value.items()
                if name not in {"rga_required", "scanner_required"}
            }
        if value not in (None, "", [], {}):
            return True
    return False


def _identity_record(value: Mapping[str, Any], fields: Sequence[str], label: str) -> dict[str, Any]:
    record = {field: _canonical(value.get(field)) for field in fields if value.get(field) not in (None, "", [], {})}
    known = bool(record)
    record["status"] = "source_backed" if known else "unknown"
    record["evidence"] = []
    provenance = _provenance(value)
    if known:
        for field in fields:
            if field in record and field not in {"status", "evidence"}:
                evidence = {
                    "field": field,
                    "source": str(value.get("source") or value.get("source_path") or label),
                    "value": record[field],
                }
                if provenance:
                    evidence["provenance"] = provenance
                record["evidence"].append(evidence)
    else:
        record["reason"] = f"No trusted source evidence identified the {label}."
        if provenance:
            record["evidence"].append({"source": str(value.get("source") or value.get("source_path") or label), "provenance": provenance})
    return record


def _provenance(value: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = value.get("provenance")
    if isinstance(raw, Mapping):
        return _canonical(raw)
    source = value.get("source") or value.get("source_path")
    if source in (None, ""):
        return None
    return {"source": _canonical(source)}


def _unknown_record(label: str) -> dict[str, Any]:
    return {"status": "unknown", "reason": f"No trusted source evidence identified the {label}.", "evidence": []}


def _head_candidates(ir: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for item in (ir.get("dependencies") or []):
        if isinstance(item, Mapping) and str(item.get("kind") or "").casefold() == "device":
            values.append(str(item.get("name") or ""))
    values.extend(_strings(manifest.get("device_aliases")))
    candidates: set[str] = set()
    for raw in values:
        lowered = raw.casefold()
        for token, family in _HEAD_TOKENS:
            if token in lowered:
                candidates.add(family)
    if len(candidates) > 1 and "MCA" in candidates:
        candidates.remove("MCA")
    return sorted(candidates)


def _looks_like_tip_box(item: Mapping[str, Any]) -> bool:
    role = str(item.get("role") or "").casefold()
    text = " ".join(str(item.get(key) or "") for key in ("label", "catalog", "name")).casefold()
    return role == "tips" or any(token in text for token in ("tip", "diti", "disposable"))


def _selected_channels(step: Mapping[str, Any], params: Mapping[str, Any]) -> list[Any]:
    value = params.get("tip_channels")
    if value is None:
        value = step.get("tip_channels")
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return []


def _mentions_scan(step: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in (step.get("operation"), step.get("name"), step.get("command_id"), *params.values())).casefold()
    return bool(re.search(r"\b(barcode|scan|scanner)\b", text))


def _protocol_material(ir: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol": _without_paths(ir.get("protocol")),
        "worktable": _without_paths(ir.get("worktable")),
        "labware": _without_paths(ir.get("labware")),
        "steps": _without_paths(ir.get("steps")),
    }


def _fingerprint_material(value: Any) -> Any:
    if isinstance(value, Mapping):
        excluded = {
            "status",
            "evidence",
            "reason",
            "provenance",
            "source_path",
            "compiled_path",
            "source",
        }
        return {str(key): _fingerprint_material(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0])) if str(key) not in excluded and item not in (None, "", [], {})}
    if isinstance(value, (list, tuple)):
        return [_fingerprint_material(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _without_paths(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _without_paths(item) for key, item in value.items() if str(key) not in {"source_path", "compiled_path", "path"}}
    if isinstance(value, list):
        return [_without_paths(item) for item in value]
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0])) if item not in (None, "", [], {})}
    if isinstance(value, (list, tuple, set)):
        values = [_canonical(item) for item in value]
        return sorted(values, key=_stable_json) if isinstance(value, set) else values
    if isinstance(value, Path):
        return str(value)
    return value


def _stable_json(value: Any) -> str:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _steps(ir: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [item for item in ir.get("steps") or [] if isinstance(item, Mapping)]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()}, key=str.casefold)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
