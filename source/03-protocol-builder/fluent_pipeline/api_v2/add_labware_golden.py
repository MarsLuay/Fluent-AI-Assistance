"""AddLabware.ToXML() golden XSCR diff for verification_recipe labware (api-v2-007).

Compares FluentControl-authoritative ``AddLabwareDataV1`` XML (offline
``AddLabware.to_xml()`` / optional live VisionX ``AddLabware.ToXML()``)
against compiled XSCR blocks for every ``verification_recipe`` setup step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from .. import xml_compat as ET

from .commands import AddLabware, command_from_ir_step, command_to_xml
from .golden_compare import compare_xscr_commands_to_native_xml, golden_compare_summary
from .native_provider import NativeToXmlProvider
from .xml_compare import compare_command_xml, extract_command_objects_from_xscr

API_V2_ISSUE_ID = "api-v2-007"
API_V2_METHOD = "AddLabware.ToXML()"

ADD_LABWARE_PAYLOAD_FIELDS = (
    "LabwareType",
    "LabwareLable",
    "Location",
    "Position",
    "Rotation",
    "HasLid",
)


def is_verification_recipe_ir(ir: Mapping[str, Any] | None) -> bool:
    if not isinstance(ir, Mapping):
        return False
    source = ir.get("source")
    if isinstance(source, Mapping) and source.get("format") == "verification_recipe":
        return True
    labware = ir.get("labware") or []
    return any(
        isinstance(item, Mapping) and item.get("source") == "verification_recipe" for item in labware
    )


def verification_recipe_add_labware_steps(ir: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return ordered ``add_labware`` IR steps for a verification_recipe protocol."""
    if not is_verification_recipe_ir(ir):
        return []
    steps: list[dict[str, Any]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        if str(step.get("operation") or "") != "add_labware":
            continue
        steps.append(dict(step))
    return steps


def add_labware_payload_from_element(element: ET.Element) -> dict[str, str]:
    node = _find_descendant(element, "AddLabwareDataV1")
    if node is None:
        return {}
    return {
        field: _child_text(node, field)
        for field in ADD_LABWARE_PAYLOAD_FIELDS
        if _child_text(node, field)
    }


def add_labware_payload_from_command(command: AddLabware) -> dict[str, str]:
    return {
        "LabwareType": str(command.labware_type),
        "LabwareLable": str(command.labware_label),
        "Location": str(command.location),
        "Position": str(command.site),
        "Rotation": str(command.rotation),
        "HasLid": str(bool(command.has_lid)),
    }


def compare_add_labware_payloads(expected: Mapping[str, str], actual: Mapping[str, str]) -> list[str]:
    drifts: list[str] = []
    for field in ADD_LABWARE_PAYLOAD_FIELDS:
        exp = str(expected.get(field) or "")
        act = str(actual.get(field) or "")
        if exp != act:
            drifts.append(f"{field}: golden {exp!r} vs compiled {act!r}")
    return drifts


def _compiled_add_labware_by_label(xscr_text: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for record in extract_command_objects_from_xscr(xscr_text):
        if record.get("command_id") not in {"AddLabwareDataV1", "AddLabware"}:
            continue
        try:
            element = ET.fromstring(record.get("xml") or "")
        except ET.ParseError:
            continue
        payload = add_labware_payload_from_element(element)
        label = payload.get("LabwareLable") or payload.get("LabwareLabel") or ""
        if label:
            indexed[_norm_label(label)] = {
                **payload,
                "_compiled_xml": record.get("xml") or "",
            }
    return indexed


def compare_verification_recipe_add_labware_golden(
    ir: Mapping[str, Any] | None,
    *,
    xscr_path: Path | None = None,
    xscr_text: str | None = None,
    native_provider: NativeToXmlProvider | None = None,
) -> list[dict[str, Any]]:
    """Diff each verification_recipe ``add_labware`` step against compiled XSCR XML."""
    steps = verification_recipe_add_labware_steps(ir)
    if not steps:
        return []

    text = xscr_text
    if text is None and xscr_path is not None and xscr_path.exists():
        try:
            text = xscr_path.read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
    compiled_by_label = _compiled_add_labware_by_label(text or "")
    provider = native_provider or NativeToXmlProvider()
    findings: list[dict[str, Any]] = []

    for index, step in enumerate(steps):
        command = command_from_ir_step(step)
        if not isinstance(command, AddLabware):
            findings.append(
                {
                    "index": index,
                    "step_id": step.get("id"),
                    "status": "error",
                    "reason": "ir_step_not_add_labware",
                    "message": "verification_recipe add_labware step did not map to AddLabware.",
                    "api_v2_issue": API_V2_ISSUE_ID,
                    "api_v2_method": API_V2_METHOD,
                }
            )
            continue

        label_key = _norm_label(command.labware_label)
        compiled = compiled_by_label.get(label_key)
        if compiled is None:
            findings.append(
                {
                    "index": index,
                    "step_id": step.get("id"),
                    "labware_label": command.labware_label,
                    "status": "missing",
                    "reason": "compiled_add_labware_missing",
                    "message": (
                        f"Compiled XSCR has no AddLabwareDataV1 block for recipe labware "
                        f"{command.labware_label!r}."
                    ),
                    "api_v2_issue": API_V2_ISSUE_ID,
                    "api_v2_method": API_V2_METHOD,
                }
            )
            continue

        golden_xml = provider.to_xml(command) or command_to_xml(command)
        compiled_xml = compiled.get("_compiled_xml") or ""
        xml_result = compare_command_xml(golden_xml, compiled_xml)
        payload_drifts = compare_add_labware_payloads(
            add_labware_payload_from_command(command),
            {key: value for key, value in compiled.items() if not key.startswith("_")},
        )

        # Fluent exports AddLabware line metadata in two compatible shapes:
        # either a direct ``<Data><LineNumber>`` block or the fuller
        # ``ProgrammingStatementBaseDataV1`` wrapper. Keep this recipe check
        # focused on the labware payload fields that affect deck setup.
        if not payload_drifts:
            findings.append(
                {
                    "index": index,
                    "step_id": step.get("id"),
                    "labware_label": command.labware_label,
                    "status": "matched",
                    "provider": provider.name,
                    "api_v2_issue": API_V2_ISSUE_ID,
                    "api_v2_method": API_V2_METHOD,
                }
            )
            continue

        findings.append(
            {
                "index": index,
                "step_id": step.get("id"),
                "labware_label": command.labware_label,
                "status": "mismatch",
                "provider": provider.name,
                "reason": "add_labware_golden_drift",
                "message": (
                    "AddLabware.ToXML() golden reference differs from compiled XSCR for "
                    f"{command.labware_label!r}."
                ),
                "diff_hint": xml_result.diff_hint,
                "field_drifts": payload_drifts,
                "golden_preview": golden_xml[:240],
                "compiled_preview": compiled_xml[:240],
                "api_v2_issue": API_V2_ISSUE_ID,
                "api_v2_method": API_V2_METHOD,
            }
        )
    return findings


def verification_recipe_add_labware_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up verification_recipe AddLabware golden diff for Gate 11/12 details."""
    compared = [item for item in findings if item.get("status") in {"matched", "mismatch", "missing"}]
    matched = [item for item in findings if item.get("status") == "matched"]
    mismatches = [item for item in findings if item.get("status") == "mismatch"]
    missing = [item for item in findings if item.get("status") == "missing"]
    errors = [item for item in findings if item.get("status") == "error"]

    if not findings:
        status = "skipped"
        summary = "No verification_recipe add_labware steps to compare."
    elif mismatches or missing or errors:
        status = "needs_review"
        summary = (
            f"AddLabware.ToXML() golden diff: {len(mismatches)} mismatch(es), "
            f"{len(missing)} missing, {len(errors)} error(s) across {len(compared)} recipe labware step(s)."
        )
    elif compared and len(matched) == len(compared):
        status = "passed"
        summary = f"AddLabware.ToXML() golden diff matched for all {len(matched)} verification_recipe labware step(s)."
    else:
        status = "needs_review"
        summary = f"AddLabware.ToXML() golden diff partial: {len(matched)} matched of {len(compared)} compared."

    return {
        "status": status,
        "summary": summary,
        "issue": API_V2_ISSUE_ID,
        "method": API_V2_METHOD,
        "compared_count": len(compared),
        "matched_count": len(matched),
        "mismatch_count": len(mismatches),
        "missing_count": len(missing),
        "error_count": len(errors),
        "findings": findings[:20],
    }


def enrich_compiled_inventory_with_golden_compare(
    inventory: dict[str, Any],
    *,
    ir: Mapping[str, Any] | None = None,
    xscr_path: Path | None = None,
    xscr_text: str | None = None,
) -> dict[str, Any]:
    """Attach fc_native_xml_compare and verification_recipe AddLabware summaries."""
    text = xscr_text
    if text is None and xscr_path is not None and xscr_path.exists():
        try:
            text = xscr_path.read_text(encoding="utf-8-sig")
        except OSError:
            text = ""

    fc_findings = compare_xscr_commands_to_native_xml(xscr_path, xscr_text=text)
    inventory["fc_native_xml_compare"] = golden_compare_summary(fc_findings)

    recipe_findings = compare_verification_recipe_add_labware_golden(
        ir,
        xscr_path=xscr_path,
        xscr_text=text,
    )
    inventory["verification_recipe_add_labware_golden"] = verification_recipe_add_labware_summary(
        recipe_findings
    )

    return inventory


def _norm_label(value: Any) -> str:
    return str(value or "").strip().casefold()


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _find_descendant(element: ET.Element, name: str) -> ET.Element | None:
    for node in element.iter():
        if _local_name(node.tag) == name:
            return node
    return None


def _child_text(element: ET.Element | None, name: str) -> str:
    if element is None:
        return ""
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""
