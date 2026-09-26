"""Block Fluent-specific generation unless source family evidence is FluentControl.

Classification comes only from the imported archive detection record. A local
FluentControl install is not an input and cannot change the decision.
"""

from __future__ import annotations

from typing import Any, Mapping


def source_family_gate(detection: Mapping[str, Any] | None) -> dict[str, Any]:
    record = {}
    if isinstance(detection, Mapping):
        candidate = detection.get("software_family")
        if isinstance(candidate, Mapping):
            record = dict(candidate)
    family = str(record.get("software_family") or "unknown")
    status = str(record.get("status") or "unknown")
    evidence = list(record.get("evidence") or [])
    if family == "fluentcontrol" and status == "verified":
        return {
            "accepted": True,
            "code": "source_family_fluentcontrol",
            "software_family": family,
            "status": status,
            "evidence": evidence,
        }
    codes = {
        "vcontrol": "source_family_vcontrol_blocked",
        "veya": "source_family_veya_blocked",
        "conflicting": "source_family_conflicting",
    }
    return {
        "accepted": False,
        "code": codes.get(family, "source_family_unknown"),
        "software_family": family,
        "status": status,
        "evidence": evidence,
        "blocked_before": "fluent_generation",
    }
