"""Pure safety policy for physical RGA/gripper moves in Protocol IR."""

from __future__ import annotations

from typing import Any


def apply_rga_move_pattern_policy(ir: dict[str, Any]) -> dict[str, Any]:
    """Require physical RGA/gripper moves to come from mined source windows."""
    adapter_labels = {
        str(item.get("label") or "")
        for item in ir.get("labware") or []
        if isinstance(item, dict) and "adapter" in str(item.get("catalog") or item.get("python_class") or "").casefold()
    }

    converted: list[dict[str, Any]] = []
    report = {
        "policy": "physical RGA/gripper moves require a mined/source command window",
        "pattern_backed": [],
        "approved_automated": [],
        "manual_fallback": [],
    }
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            converted.append(step)
            continue
        if str(step.get("operation") or "") != "move_plate":
            converted.append(step)
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        onto_labware = str(params.get("onto_labware") or params.get("onto") or "")
        labware = str(params.get("labware") or step.get("target_labware") or "selected labware")
        target_is_adapter = labware in adapter_labels
        onto_is_adapter = onto_labware in adapter_labels
        pattern = _rga_source_pattern(params)
        if pattern:
            params.setdefault("rga_move_source", "mined_pattern_window")
            params.setdefault("rga_move_policy", "pattern_backed")
            step["parameters"] = params
            report["pattern_backed"].append(
                _rga_move_report_entry(
                    step,
                    labware=labware,
                    onto_labware=onto_labware,
                    reason="source_pattern_window",
                    pattern=pattern,
                )
            )
            converted.append(step)
            continue
        # verified_move / explicit automated verification motion must stay as
        # real TransferLabware commands — never rewrite into "Manual verification
        # only" operator prompts. Notes about missing mines belong outside XSCR.
        if _allows_automated_verification_motion(params, step):
            params.setdefault("rga_move_source", "approved_automated_verification_motion")
            params.setdefault("rga_move_policy", "approved_automated")
            step["parameters"] = params
            report.setdefault("approved_automated", []).append(
                _rga_move_report_entry(
                    step,
                    labware=labware,
                    onto_labware=onto_labware,
                    reason="allow_automated_verification_motion",
                    pattern=pattern,
                )
            )
            converted.append(step)
            continue
        destination = _deck_location(
            {
                "location": params.get("destination_location") or params.get("to_location"),
                "position": params.get("destination_site") or params.get("to_site"),
            }
        )
        destination_text = f" to `{destination}`" if destination else ""
        onto_text = f" onto `{onto_labware}`" if onto_labware else destination_text
        reason = (
            "rga_adapter_onto_move_requires_manual_verification"
            if target_is_adapter or onto_is_adapter
            else "rga_move_requires_mined_source_pattern"
        )
        prompt = (
            f"Manual verification only: do not run an automated RGA transfer of `{labware}`{onto_text} here. "
            "No mined source command window was selected for this physical gripper move. With the A200/RGA "
            "connected and initialized, have the operator verify the requested placement without tilt, diagonal "
            "finger travel, wiggling, collision risk, or a loose seat. Continue only after the manual/source-validated "
            "motion check is complete."
        )
        converted_step = {
            key: value
            for key, value in step.items()
            if key not in {"operation", "parameters", "target_labware", "source_labware", "destination_labware"}
        }
        converted_step.update(
            {
                "operation": "prompt_user",
                "name": _manual_rga_adapter_move_name(labware, onto_labware, destination),
                "parameters": {
                    "prompt": prompt,
                    "timeout": 0,
                    "converted_from": "move_plate",
                    "reason": reason,
                    "rga_move_policy": "manual_fallback",
                    "labware": labware,
                    "onto_labware": onto_labware,
                    "destination": destination,
                },
                "safety_flags": sorted(
                    set(step.get("safety_flags") or [])
                    | {"manual_verification", "rga_move_manual_check"}
                    | ({"rga_adapter_manual_check"} if target_is_adapter or onto_is_adapter else set())
                ),
            }
        )
        report["manual_fallback"].append(
            _rga_move_report_entry(step, labware=labware, onto_labware=onto_labware, reason=reason)
        )
        converted.append(converted_step)

    ir["steps"] = converted
    ir.setdefault("source", {})["rga_move_policy"] = report
    assumptions = ir.setdefault("safety_assumptions", [])
    if not any(isinstance(item, dict) and item.get("id") == "rga_moves_require_source_pattern" for item in assumptions):
        assumptions.append(
            {
                "id": "rga_moves_require_source_pattern",
                "text": (
                    "Automated RGA/gripper plate moves are emitted only when backed by a selected mined source "
                    "command window; otherwise the move is rendered as an operator verification prompt."
                ),
            }
        )
    if report["manual_fallback"] and not any(
        isinstance(item, dict) and item.get("id") == "rga_adapter_moves_prompt_only" for item in assumptions
    ):
        assumptions.append(
            {
                "id": "rga_adapter_moves_prompt_only",
                "text": (
                    "Unbacked RGA moves, including adapter-labware moves, are rendered as operator prompts because "
                    "synthesized finger commands have caused FluentControl load and compatibility errors."
                ),
            }
        )
    return ir


def convert_unsafe_rga_adapter_moves_to_prompts(ir: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible wrapper for the stricter RGA source-pattern policy."""
    return apply_rga_move_pattern_policy(ir)


def _rga_source_pattern(params: dict[str, Any]) -> dict[str, Any] | None:
    pattern_id = params.get("source_pattern_id")
    if pattern_id in (None, "", []):
        return None
    return {
        "source_pattern_id": pattern_id,
        "source_pattern_type": params.get("source_pattern_type"),
        "source_script": params.get("source_script"),
        "zeia_file": params.get("zeia_file"),
        "command_index": params.get("command_index"),
        "command_family": params.get("command_family"),
    }


def _allows_automated_verification_motion(params: dict[str, Any], step: dict[str, Any]) -> bool:
    if params.get("allow_automated_verification_motion") or params.get("verification_after_motion_required"):
        return True
    if params.get("force_manual_verification") is False:
        return True
    flags = step.get("safety_flags") or []
    return "automated_verification_motion" in {str(flag) for flag in flags}


def _rga_move_report_entry(
    step: dict[str, Any],
    *,
    labware: str,
    onto_labware: str,
    reason: str,
    pattern: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = {
        "step_id": step.get("id"),
        "step_index": step.get("index"),
        "name": step.get("name"),
        "labware": labware,
        "onto_labware": onto_labware,
        "reason": reason,
        "source_path": step.get("source_path"),
    }
    if pattern:
        entry["source_pattern"] = {key: value for key, value in pattern.items() if value not in (None, "", [])}
    return {key: value for key, value in entry.items() if value not in (None, "", [], {})}


def _manual_rga_adapter_move_name(labware: str, onto_labware: str, destination: str) -> str:
    if onto_labware:
        return f"Manually verify {labware} on {onto_labware}"
    if destination:
        return f"Manually verify {labware} at {destination}"
    return f"Manually verify {labware} adapter move"


def _deck_location(item: dict[str, Any]) -> str:
    if item.get("deck_location"):
        return str(item["deck_location"])
    if item.get("location") and item.get("position"):
        return f"{item['location']} {item['position']}"
    if item.get("position"):
        return str(item["position"])
    return ""
