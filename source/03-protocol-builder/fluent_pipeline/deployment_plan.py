"""Deterministic, reviewable source-to-target promotion plans.

This module plans object promotion from explicit target datastore profiles. It
does not write to FluentControl, copy a live database, or infer equivalence
from a human-readable name alone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .target_datastore import (
    TARGET_DATASTORE_SCHEMA_VERSION,
    fingerprint_target_datastore_profile,
    load_target_datastore_profile,
)

DEPLOYMENT_PLAN_SCHEMA_VERSION = "tecan.deployment_plan.v1"


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return value


def _fingerprint(value: Any) -> str:
    blob = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _profile(value: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    return load_target_datastore_profile(value)


def _objects(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope, values in (profile.get("objects") or {}).items():
        for row in values or []:
            item = dict(row)
            item["scope"] = str(scope)
            rows.append(item)
    return sorted(rows, key=lambda row: (
        str(row.get("kind") or ""),
        str(row.get("type_id") or ""),
        str(row.get("object_subfolder_path") or "").casefold(),
        str(row.get("object_name") or "").casefold(),
        str(row.get("guid") or "").casefold(),
    ))


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("kind") or "").casefold(),
        str(row.get("type_id") or "").casefold(),
        str(row.get("object_subfolder_path") or "").casefold(),
        str(row.get("object_name") or "").casefold(),
    )


def _object_action(source: Mapping[str, Any], target_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    exact = [
        row for row in target_rows
        if str(row.get("guid") or "").casefold() == str(source.get("guid") or "").casefold()
        and str(row.get("content_fingerprint") or "") == str(source.get("content_fingerprint") or "")
    ]
    if exact:
        return {"action": "reuse_target", "target": dict(exact[0]), "reason": "exact_guid_and_content"}

    semantic = [row for row in target_rows if _semantic_key(row) == _semantic_key(source)]
    if semantic:
        return {
            "action": "review_conflict",
            "target_candidates": [dict(row) for row in semantic],
            "reason": "same_name_or_role_different_identity_or_content",
        }
    return {"action": "import_dependency", "reason": "missing_target_object"}


def _software_finding(source: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any] | None:
    source_family = str((source.get("software") or {}).get("family") or "").strip()
    target_family = str((target.get("software") or {}).get("family") or "").strip()
    if source_family and target_family and source_family.casefold() != target_family.casefold():
        return {
            "code": "source_target_software_family_mismatch",
            "severity": "blocked",
            "source": source_family,
            "target": target_family,
        }
    if not source_family or not target_family:
        return {
            "code": "source_target_software_family_unknown",
            "severity": "review",
        }
    return None


def build_deployment_plan(
    source_profile: Path | str | Mapping[str, Any],
    target_profile: Path | str | Mapping[str, Any] | None = None,
    *,
    mode: str | None = None,
    external_files: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a plan from explicit source/target profiles.

    A missing or unbound target is represented as blocked/reviewable evidence;
    it never falls back to the local machine.
    """
    source = _profile(source_profile)
    target = _profile(target_profile) if target_profile is not None else None
    source_fp = str(source.get("fingerprint") or fingerprint_target_datastore_profile(source))
    target_fp = str(target.get("fingerprint") or "") if target else None
    findings: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    if target is None or target.get("target_unbound") or target.get("status") != "bound":
        findings.append({"code": "target_profile_required", "severity": "blocked"})
    elif target.get("unknowns"):
        findings.append({"code": "target_profile_has_unknowns", "severity": "review", "unknowns": list(target["unknowns"])})

    family_finding = _software_finding(source, target or {})
    if family_finding:
        findings.append(family_finding)

    target_rows = _objects(target or {})
    for source_row in _objects(source):
        action = _object_action(source_row, target_rows)
        actions.append({"source": source_row, **action})
        if action["action"] == "review_conflict":
            findings.append({
                "code": "target_object_conflict",
                "severity": "review",
                "source": source_row,
                "target_candidates": action["target_candidates"],
            })

    for external in sorted(external_files or [], key=lambda row: json.dumps(_canonical(row), sort_keys=True)):
        item = dict(external)
        if item.get("source_path") and item.get("target_path") and item["source_path"] != item["target_path"]:
            actions.append({"action": "relocate_external_file", "source": item})
        else:
            actions.append({"action": "review_external_file", "source": item})

    same_target = bool(target and source_fp == target_fp)
    selected_mode = mode or ("same_target_dropin" if same_target else "cross_target_import")
    if selected_mode not in {"same_target_dropin", "cross_target_import"}:
        findings.append({"code": "unsupported_promotion_mode", "severity": "blocked", "mode": selected_mode})
    if selected_mode == "same_target_dropin" and not same_target:
        findings.append({"code": "same_target_profile_mismatch", "severity": "blocked"})

    if any(item.get("severity") == "blocked" for item in findings):
        status = "blocked"
    elif any(item.get("severity") == "review" for item in findings):
        status = "needs_review"
    elif selected_mode == "same_target_dropin":
        status = "ready_for_same_target_dropin"
    else:
        status = "ready_for_import"

    plan: dict[str, Any] = {
        "schema_version": DEPLOYMENT_PLAN_SCHEMA_VERSION,
        "source_profile_schema": TARGET_DATASTORE_SCHEMA_VERSION,
        "source_fingerprint": source_fp,
        "target_fingerprint": target_fp,
        "target_profile_id": target.get("target_profile_id") if target else None,
        "mode": selected_mode,
        "actions": actions,
        "findings": findings,
        "status": status,
        "runtime_state_excluded": True,
        "destructive_target_mutation": False,
    }
    plan["fingerprint"] = _fingerprint(plan)
    return plan


def target_drift_diagnostics(plan: Mapping[str, Any], current_target: Path | str | Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return a deterministic invalidation finding when target evidence drifts."""
    current = _profile(current_target)
    expected = str(plan.get("target_fingerprint") or "")
    actual = str(current.get("fingerprint") or "")
    if not expected or expected == actual:
        return []
    return [{
        "code": "target_profile_drift",
        "severity": "blocked",
        "expected_fingerprint": expected,
        "actual_fingerprint": actual,
    }]


__all__ = [
    "DEPLOYMENT_PLAN_SCHEMA_VERSION",
    "build_deployment_plan",
    "target_drift_diagnostics",
]
