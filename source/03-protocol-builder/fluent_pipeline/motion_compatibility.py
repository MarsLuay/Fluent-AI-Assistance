"""Source-backed FluentControl arm/pathfinder compatibility diagnostics.

This module deliberately models only evidence exposed by the canonical protocol
IR, the #156 host-environment contract, and structured FluentControl log
records.  A matching vendor issue is a compatibility signal, not a runtime
root-cause or hardware-readiness verdict.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .host_environment import HOST_ENVIRONMENT_SCHEMA_VERSION, unverified_offline_host_environment


MOTION_COMPATIBILITY_SCHEMA_VERSION = "tecan.motion_compatibility.v1"
MOTION_DEFECT_REGISTRY_SCHEMA_VERSION = "tecan.fluentcontrol_motion_defect_registry.v1"
MOTION_DEFECT_REGISTRY_RESOURCE = Path(__file__).resolve().parent / "data" / "motion_defect_registry.json"
MOTION_REGISTRY_SOURCE = (
    "https://www.tecan.com/hubfs/Knowledgebase/Manuals/Fluent%20Control/"
    "Revision%20History%20FluentControl%203.8%20SP1.pdf"
)

MOTION_FEATURES = (
    "mca384_arm_motion",
    "pathfinder",
    "rga_contouring",
)


def load_motion_defect_registry() -> dict[str, Any]:
    """Load and validate the committed vendor-issue registry."""

    payload = json.loads(MOTION_DEFECT_REGISTRY_RESOURCE.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MOTION_DEFECT_REGISTRY_SCHEMA_VERSION:
        raise ValueError("unsupported motion defect registry schema")
    source = payload.get("registry_source")
    if not isinstance(source, Mapping) or source.get("url") != MOTION_REGISTRY_SOURCE:
        raise ValueError("motion defect registry must retain the authoritative Tecan source")
    issues = payload.get("issues")
    if not isinstance(issues, list) or {str(item.get("vendor_issue_id")) for item in issues} != {"120382", "120985"}:
        raise ValueError("motion defect registry must contain exactly vendor issues 120382 and 120985")
    for issue in issues:
        if issue.get("fixed_in") != "3.5" or not issue.get("trigger_features"):
            raise ValueError("motion defect registry entries require the documented 3.5 fix and trigger evidence")
    return payload


def build_motion_compatibility_report(
    protocol_ir: Mapping[str, Any] | None = None,
    *,
    host_environment: Mapping[str, Any] | None = None,
    log_records: Sequence[Mapping[str, Any] | Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic compatibility report from supplied evidence.

    ``host_environment`` is the #156 document, not a second version detector.
    Missing version evidence is ``unknown``.  Missing trigger evidence is
    ``trigger_not_present`` and does not assert that a live run could not have
    encountered the condition.
    """

    registry = load_motion_defect_registry()
    host = _host_evidence(host_environment)
    protocol_evidence = _protocol_motion_evidence(protocol_ir)
    log_correlation = correlate_motion_logs(log_records or ())
    feature_evidence: dict[str, list[dict[str, Any]]] = {
        feature: list(protocol_evidence.get(feature, ())) for feature in MOTION_FEATURES
    }
    for record in log_correlation["records"]:
        for feature in record["features"]:
            feature_evidence[feature].append({
                "kind": "log",
                "record_index": record["record_index"],
                "source": record.get("source") or None,
                "timestamp": record.get("timestamp") or None,
                "evidence": record["evidence"],
            })

    findings: list[dict[str, Any]] = []
    for issue in sorted(registry["issues"], key=lambda item: str(item["vendor_issue_id"])):
        required = tuple(str(item) for item in issue["trigger_features"])
        trigger_present = all(feature_evidence.get(feature) for feature in required)
        trigger_status = "present" if trigger_present else "not_present"
        version_status = _version_status(host["fluentcontrol"]["version"], str(issue["fixed_in"]))
        status = version_status if trigger_present else "trigger_not_present"
        evidence = [
            {
                "kind": "vendor_registry",
                "source": registry["registry_source"]["url"],
                "document": registry["registry_source"]["name"],
                "section": registry["registry_source"]["section"],
                "vendor_issue_id": issue["vendor_issue_id"],
                "excerpt": issue["source_excerpt"],
            },
            {
                "kind": "host_environment",
                "owner": "#156",
                "schema_version": host["schema_version"],
                "version": host["fluentcontrol"]["version"],
                "build": host["fluentcontrol"]["build"],
                "version_source": host["fluentcontrol"]["version_source"],
            },
        ]
        for feature in required:
            evidence.extend(feature_evidence.get(feature, ()))
        diagnostic = None
        if trigger_present:
            diagnostic = {
                "id": issue["diagnostic_id"],
                "vendor_issue_id": issue["vendor_issue_id"],
                "observed_trigger": True,
                "root_cause": "unknown",
                "pipetting_root_cause": "not_established",
                "log_correlated": bool(log_correlation["records"]),
                "evidence": evidence,
            }
        findings.append({
            "vendor_issue_id": issue["vendor_issue_id"],
            "title": issue["title"],
            "status": status,
            "version_status": version_status,
            "trigger_status": trigger_status,
            "fixed_in": issue["fixed_in"],
            "required_trigger_features": list(required),
            "diagnostic": diagnostic,
            "evidence": evidence,
        })

    return {
        "schema_version": MOTION_COMPATIBILITY_SCHEMA_VERSION,
        "registry": {
            "schema_version": registry["schema_version"],
            "source": dict(registry["registry_source"]),
            "issue_ids": [str(item["vendor_issue_id"]) for item in sorted(registry["issues"], key=lambda item: str(item["vendor_issue_id"]))],
        },
        "host_environment": host,
        "protocol_motion": {
            "source": "canonical protocol IR",
            "features": {
                feature: list(protocol_evidence.get(feature, ()))
                for feature in MOTION_FEATURES
                if protocol_evidence.get(feature)
            },
        },
        "log_correlation": log_correlation,
        "findings": findings,
        "physical_readiness": {
            "status": "not_evaluated",
            "owner": "#160",
            "separate": True,
            "note": "Motion compatibility does not evaluate geometry, clearance, calibration, maintenance, accuracy, or hardware-run readiness.",
        },
    }


def correlate_motion_logs(records: Sequence[Mapping[str, Any] | Any]) -> dict[str, Any]:
    """Extract only explicit PathFinder/RGA/MCA384 motion signals from logs."""

    correlated: list[dict[str, Any]] = []
    unmatched = 0
    for index, record in enumerate(records):
        channel = _record_value(record, "channel")
        module = _record_value(record, "module")
        message = _record_value(record, "message")
        text = " ".join((channel, module, message))
        lowered = text.casefold()
        features: list[str] = []
        if re.search(r"path\s*finder|path\s*finding|pathfinding", lowered):
            features.append("pathfinder")
        if re.search(r"mca\s*384", lowered) and re.search(r"\b(?:arm|move|evad|path)\w*", lowered):
            features.append("mca384_arm_motion")
        if re.search(r"\brga\b", lowered) and "contour" in lowered:
            features.append("rga_contouring")
        if not features:
            unmatched += 1
            continue
        correlated.append({
            "record_index": index,
            "timestamp": _record_value(record, "timestamp"),
            "application": _record_value(record, "application"),
            "channel": channel,
            "module": module,
            "source": _record_value(record, "source") or _record_value(record, "path"),
            "features": sorted(set(features)),
            "evidence": message or channel or module,
        })
    correlated.sort(key=lambda item: (str(item.get("timestamp") or ""), int(item["record_index"])))
    return {
        "schema_version": MOTION_COMPATIBILITY_SCHEMA_VERSION,
        "records": correlated,
        "matched_record_count": len(correlated),
        "unmatched_record_count": unmatched,
        "root_cause": "unknown",
    }


def render_motion_compatibility_markdown(report: Mapping[str, Any]) -> str:
    """Render the deterministic report without adding generation timestamps."""

    lines = [
        "# FluentControl Motion Compatibility",
        "",
        f"- Schema: `{report.get('schema_version')}`",
        f"- FluentControl: `{(report.get('host_environment') or {}).get('fluentcontrol', {}).get('version') or 'unknown'}`",
        "",
        "| Vendor issue | Status | Trigger | Fixed in |",
        "| --- | --- | --- | --- |",
    ]
    for finding in report.get("findings") or ():
        lines.append(
            "| {vendor_issue_id} | {status} | {trigger_status} | {fixed_in} |".format(**finding)
        )
    lines.extend([
        "",
        "Motion findings are compatibility signals only; runtime root cause and physical readiness remain unknown unless separately evidenced.",
        "",
    ])
    return "\n".join(lines)


def _host_evidence(host_environment: Mapping[str, Any] | None) -> dict[str, Any]:
    host = host_environment if isinstance(host_environment, Mapping) else unverified_offline_host_environment()
    product = next(
        (
            item for item in host.get("products") or ()
            if isinstance(item, Mapping) and item.get("family") == "FluentControl"
        ),
        {},
    )
    return {
        "schema_version": host.get("schema_version") or HOST_ENVIRONMENT_SCHEMA_VERSION,
        "owner": "#156",
        "fingerprint": host.get("fingerprint"),
        "fluentcontrol": {
            "version": _text(product.get("version")),
            "build": _text(product.get("build")),
            "detected": bool(product.get("detected")),
            "version_source": _text(product.get("version_source")) or "unavailable",
        },
    }


def _protocol_motion_evidence(protocol_ir: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {feature: [] for feature in MOTION_FEATURES}
    steps = protocol_ir.get("steps") if isinstance(protocol_ir, Mapping) else ()
    for index, step in enumerate(steps or ()):
        if not isinstance(step, Mapping):
            continue
        operation = str(step.get("operation") or "").casefold()
        if operation != "mca384_move_arm":
            continue
        evidence["mca384_arm_motion"].append({
            "kind": "protocol_ir",
            "step_index": index,
            "step_id": step.get("id") or f"step_{index + 1:03d}",
            "source_path": step.get("source_path") or None,
            "evidence": "canonical protocol IR operation mca384_move_arm",
        })
    return evidence


def _version_status(version: str | None, fixed_in: str) -> str:
    current = _version_tuple(version)
    fixed = _version_tuple(fixed_in)
    if current is None or fixed is None:
        return "unknown"
    return "affected" if current < fixed else "fixed"


def _version_tuple(value: str | None) -> tuple[int, int] | None:
    match = re.search(r"\b(\d+)\.(\d+)\b", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _record_value(record: Mapping[str, Any] | Any, key: str) -> str:
    value = record.get(key) if isinstance(record, Mapping) else getattr(record, key, "")
    return str(value or "")


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
