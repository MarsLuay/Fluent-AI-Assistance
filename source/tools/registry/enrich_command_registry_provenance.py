"""Annotate the editable command registry source file with fluentcontrol_name provenance."""

from __future__ import annotations

import json
from typing import Any

from tecan_common.command_registry import source_command_registry_path

REGISTRY_PATH = source_command_registry_path()
REGISTRY_VERSION = "tecan.command_registry.v2"

NAME_PROVENANCE_SCHEMA = {
    "fluentcontrol_name_source": {
        "fluentcontrol_control_bar": "Exact Control Bar command title observed in FluentControl UI.",
        "fluentcontrol_script_palette": "Script Editor palette / grouped statement label.",
        "source_script_observed": "Observed in decompiled FluentControl project scripts.",
        "connector_display_name": "Connector capability DisplayName from FluentControl install metadata.",
        "manual_curated": "Human-curated descriptive title; not necessarily the exact UI string.",
        "ir_operation_label": "Derived from canonical IR operation or structural metadata label.",
    },
    "confidence": {
        "verified": "Human-verified against FluentControl UI.",
        "high": "Strong documentary evidence (source scripts, connector metadata).",
        "medium": "Reasonable curated title; not UI-verified.",
        "low": "Structural / placeholder label only.",
    },
}

# command_id -> (fluentcontrol_name_source, confidence)
PROVENANCE_BY_COMMAND_ID: dict[str, tuple[str, str]] = {
    "AddLabwareDataV1": ("fluentcontrol_control_bar", "verified"),
    "Mca384PickUpTipsScriptCommandDataV5": ("fluentcontrol_control_bar", "verified"),
    "Mca384DropTipsScriptCommandDataV5": ("fluentcontrol_control_bar", "verified"),
    "Mca384SetTipsBackScriptCommandDataV5": ("fluentcontrol_control_bar", "verified"),
    "Mca384AspirateScriptCommandDataV2": ("fluentcontrol_control_bar", "verified"),
    "Mca384DispenseScriptCommandDataV2": ("fluentcontrol_control_bar", "verified"),
    "Mca384MixScriptCommandDataV2": ("fluentcontrol_control_bar", "verified"),
    "LihaAspirateScriptCommandDataV5": ("fluentcontrol_control_bar", "verified"),
    "LihaDispenseScriptCommandDataV5": ("fluentcontrol_control_bar", "verified"),
    "LihaMixScriptCommandDataV5": ("fluentcontrol_control_bar", "verified"),
    "LihaGetTipsScriptCommandDataV5": ("fluentcontrol_control_bar", "verified"),
    "LihaDropTipsScriptCommandDataV5": ("fluentcontrol_control_bar", "verified"),
    "LihaWashScriptCommandDataV1": ("fluentcontrol_control_bar", "verified"),
    "Mca384WashScriptCommandDataV1": ("fluentcontrol_control_bar", "verified"),
    "ReadWorklistScriptCommandDataV1": ("fluentcontrol_control_bar", "verified"),
    "MovePlateScriptCommandDataV1": ("fluentcontrol_control_bar", "verified"),
    "InitializeDeviceScriptCommandDataV1": ("fluentcontrol_control_bar", "verified"),
    "QueryVariableStatement": ("fluentcontrol_control_bar", "verified"),
    "LoopGroupDataV1": ("fluentcontrol_control_bar", "verified"),
    "CommentStatement": ("fluentcontrol_control_bar", "verified"),
    "GetHeadAdapterScriptCommandDataV1": ("fluentcontrol_control_bar", "verified"),
    "DropHeadAdapterScriptCommandDataV1": ("fluentcontrol_control_bar", "verified"),
    "SetVariableStatement": ("fluentcontrol_control_bar", "verified"),
    "SubRoutineStatement": ("fluentcontrol_control_bar", "verified"),
    "UserPromptStatement": ("fluentcontrol_control_bar", "verified"),
    "LihaDetectLiquidScriptCommand": ("fluentcontrol_control_bar", "verified"),
    "CgaDropFingersScriptCommand": ("fluentcontrol_control_bar", "verified"),
    "AlternateGroup": ("fluentcontrol_script_palette", "medium"),
    "ConditionalGroup": ("fluentcontrol_script_palette", "medium"),
    "RUPVariableStatement": ("source_script_observed", "high"),
    "RUPStandardStatement": ("source_script_observed", "high"),
    "GenerateReportStatement": ("source_script_observed", "high"),
    "VariableMapping": ("ir_operation_label", "low"),
    "LeaveStatement": ("manual_curated", "medium"),
    "ExecuteApplicationStatement": ("manual_curated", "medium"),
    "ExecuteVbScriptStatement": ("manual_curated", "medium"),
    "ApplicationDriverMacro": ("manual_curated", "medium"),
    "MoveAxisCommandScriptStatement": ("manual_curated", "medium"),
    "StartMoveCommandScriptStatement": ("manual_curated", "medium"),
    "WaitForAsyncResponseScriptStatement": ("manual_curated", "medium"),
    "TeGioSetPWMOutputStatement": ("manual_curated", "medium"),
    "LegacyDriverMacro": ("manual_curated", "medium"),
    "RUPWorktableStatement": ("manual_curated", "medium"),
    "RaiseErrorStatement": ("manual_curated", "medium"),
}


def enrich_registry(payload: dict[str, Any]) -> dict[str, Any]:
    commands = payload.get("commands")
    if not isinstance(commands, dict):
        raise ValueError("command_registry.json must contain a commands object")

    missing: list[str] = []
    for command_id, entry in commands.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("fluentcontrol_name"):
            continue
        provenance = PROVENANCE_BY_COMMAND_ID.get(command_id)
        if provenance is None:
            missing.append(command_id)
            continue
        source, confidence = provenance
        entry["fluentcontrol_name_source"] = source
        entry["confidence"] = confidence

    if missing:
        raise ValueError(f"Missing provenance mapping for: {', '.join(sorted(missing))}")

    payload["schema_version"] = REGISTRY_VERSION
    payload["name_provenance_schema"] = NAME_PROVENANCE_SCHEMA
    payload["description"] = (
        "Internal registry for mapping FluentControl command ids and aliases to canonical "
        "protocol IR operations. Each fluentcontrol_name carries fluentcontrol_name_source "
        "and confidence so UI titles are traceable."
    )
    return payload


def main() -> None:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    enriched = enrich_registry(payload)
    REGISTRY_PATH.write_text(json.dumps(enriched, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated {REGISTRY_PATH}")


if __name__ == "__main__":
    main()
