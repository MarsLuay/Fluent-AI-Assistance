"""Build simulator handoff artifacts for the 3D protocol simulator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .determinism import normalize_artifact_text
from .protocol_ir import load_protocol_ir
from .runner import write_json

SIMULATOR_APP = "tecan-protocol-simulator"
SIMULATOR_PROJECT_KIND = "simulator-project"
SIM_SCENE_KIND = "sim-scene"
SIMULATOR_PROJECT_SCHEMA_VERSION = 2
SIM_SCENE_SCHEMA_VERSION = 1
MAX_EMBED_BYTES = 8_000_000
TEXT_EXTENSIONS = {
    ".json",
    ".md",
    ".yaml",
    ".yml",
    ".xml",
    ".xscr",
    ".gwl",
    ".py",
    ".txt",
}


@dataclass(frozen=True)
class SceneArtifactSpec:
    role: str
    bundle_path: str
    local_path: Path | None
    kind: str
    embed: bool = True


def write_simulator_handoff(
    out_dir: Path,
    *,
    base: str,
    protocol_name: str,
    protocol_ir_path: Path,
    xscr_path: Path | None = None,
    request_spec_path: Path | None = None,
    request_spec: dict[str, Any] | None = None,
    validation_diff_json_path: Path | None = None,
    validation_diff: dict[str, Any] | None = None,
    validation_report: dict[str, Any] | None = None,
    simulation_json_path: Path | None = None,
    worktable_patch_path: Path | None = None,
    worktable_changes_path: Path | None = None,
    recreate_path: Path | None = None,
    generated_project_zeia: Path | None = None,
    hardware_manifest_path: Path | None = None,
    labware_catalog_path: Path | None = None,
    workflow_status: str | None = None,
    readiness_status: str | None = None,
    readiness: dict[str, Any] | None = None,
    ready_to_import: bool = False,
) -> dict[str, Path]:
    """Write ``sim_scene.json`` and ``simulator-project.json`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = _artifact_specs(
        base=base,
        protocol_ir_path=protocol_ir_path,
        xscr_path=xscr_path,
        request_spec_path=request_spec_path,
        validation_diff_json_path=validation_diff_json_path,
        simulation_json_path=simulation_json_path,
        worktable_patch_path=worktable_patch_path,
        worktable_changes_path=worktable_changes_path,
        recreate_path=recreate_path,
        generated_project_zeia=generated_project_zeia,
        hardware_manifest_path=hardware_manifest_path,
        labware_catalog_path=labware_catalog_path,
    )
    generation = _build_generation_block(
        base=base,
        protocol_name=protocol_name,
        protocol_ir_path=protocol_ir_path,
        request_spec=request_spec,
        validation_diff=validation_diff,
        validation_report=validation_report,
        specs=specs,
        workflow_status=workflow_status,
        readiness_status=readiness_status,
        readiness=readiness,
        ready_to_import=ready_to_import,
    )
    exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sim_scene_path = out_dir / "sim_scene.json"
    simulator_project_path = out_dir / "simulator-project.json"
    write_json(sim_scene_path, _build_sim_scene_payload(
        exported_at=exported_at,
        protocol_name=protocol_name,
        generation=generation,
        specs=specs,
    ))
    write_json(simulator_project_path, _build_simulator_project_payload(
        exported_at=exported_at,
        protocol_name=protocol_name,
        generation=generation,
        specs=specs,
    ))
    return {
        "sim_scene": sim_scene_path,
        "simulator_project": simulator_project_path,
    }


def _artifact_specs(
    *,
    base: str,
    protocol_ir_path: Path,
    xscr_path: Path | None,
    request_spec_path: Path | None,
    validation_diff_json_path: Path | None,
    simulation_json_path: Path | None,
    worktable_patch_path: Path | None,
    worktable_changes_path: Path | None,
    recreate_path: Path | None,
    generated_project_zeia: Path | None,
    hardware_manifest_path: Path | None,
    labware_catalog_path: Path | None = None,
) -> list[SceneArtifactSpec]:
    return [
        SceneArtifactSpec("protocol_ir", "source/protocol.ir.json", protocol_ir_path, "protocol-ir"),
        SceneArtifactSpec(
            "xscr",
            "generated_project.zeia",
            xscr_path,
            "xscr",
        ),
        SceneArtifactSpec(
            "zeia",
            "generated_project.zeia",
            generated_project_zeia,
            "zeia",
            embed=False,
        ),
        SceneArtifactSpec("request_spec", "source/request.spec.yaml", request_spec_path, "metadata"),
        SceneArtifactSpec(
            "validation_diff",
            "source/validation_diff.json",
            validation_diff_json_path,
            "validation-diff",
        ),
        SceneArtifactSpec(
            "simulation",
            f"source/reports/{base}_simulation.json",
            simulation_json_path,
            "simulation",
        ),
        SceneArtifactSpec(
            "worktable_patch",
            "source/worktable.patch.json",
            worktable_patch_path,
            "worktable-diff",
        ),
        SceneArtifactSpec(
            "worktable_changes",
            "source/worktable_changes.md",
            worktable_changes_path,
            "worktable-diff",
        ),
        SceneArtifactSpec("recreate_script", "RECREATE_SCRIPT.md", recreate_path, "metadata"),
        SceneArtifactSpec(
            "hardware_manifest",
            "source/hardware/hardware_manifest.json",
            hardware_manifest_path,
            "hardware",
        ),
        SceneArtifactSpec(
            "labware_catalog",
            "source/labware_catalog.json",
            labware_catalog_path,
            "labware-catalog",
        ),
    ]


def _build_generation_block(
    *,
    base: str,
    protocol_name: str,
    protocol_ir_path: Path,
    request_spec: dict[str, Any] | None,
    validation_diff: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
    specs: list[SceneArtifactSpec],
    workflow_status: str | None,
    readiness_status: str | None,
    readiness: dict[str, Any] | None,
    ready_to_import: bool,
) -> dict[str, Any]:
    ir = load_protocol_ir(protocol_ir_path)
    acceptance = (request_spec or {}).get("acceptance") or {}
    verification_recipe = (request_spec or {}).get("verification_recipe")
    verification_steps = _verification_steps(request_spec, ir)
    gates = _validation_gates(validation_report, validation_diff)
    active_gates = [gate for gate in gates if gate.get("status") != "passed"]
    block = {
        "pipeline": "fluent_pipeline",
        "protocol_name": protocol_name,
        "script_name": base,
        "worktable_name": ir.get("worktable_name"),
        "worktable_guid": ir.get("worktable_guid"),
        "workflow_status": workflow_status,
        "readiness_status": readiness_status,
        "readiness": readiness if isinstance(readiness, dict) else None,
        "ready_to_import": ready_to_import,
        "verification_script": bool(verification_recipe or verification_steps),
        "verification_steps": verification_steps,
        "required_checks": list(acceptance.get("required_checks") or []),
        "validation_gates": gates,
        "active_validation_gates": active_gates,
        "artifact_index": {
            spec.role: {
                "path": spec.bundle_path,
                "kind": spec.kind,
                "available": bool(spec.local_path and spec.local_path.exists()),
            }
            for spec in specs
        },
    }
    if block["verification_script"]:
        from .api_v2.verification_helpers import environmental_pre_run_template

        block["environmental_pre_run"] = environmental_pre_run_template()
    return block


def _verification_steps(
    request_spec: dict[str, Any] | None,
    ir: dict[str, Any],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    recipe = (request_spec or {}).get("verification_recipe") or {}
    for group in recipe.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name") or "Verification")
        for raw_step in group.get("steps") or []:
            if not isinstance(raw_step, dict):
                continue
            steps.append(_verification_step_from_recipe(group_name, raw_step))
    if steps:
        return steps
    for group in ir.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for raw_step in group.get("steps") or []:
            if not isinstance(raw_step, dict):
                continue
            if raw_step.get("operation") != "prompt_user":
                continue
            params = raw_step.get("parameters") or {}
            prompt = str(params.get("prompt") or params.get("message") or "").strip()
            if not prompt:
                continue
            steps.append({
                "group": str(group.get("name") or "Verification"),
                "type": "prompt",
                "prompt": prompt,
                "step_id": raw_step.get("id"),
                "command_id": raw_step.get("command_id") or "UserPrompt",
            })
    return steps


def _verification_step_from_recipe(group_name: str, step: dict[str, Any]) -> dict[str, Any]:
    if step.get("prompt"):
        return {
            "group": group_name,
            "type": "prompt",
            "prompt": str(step.get("prompt")),
            "name": step.get("name"),
        }
    if step.get("comment"):
        return {
            "group": group_name,
            "type": "comment",
            "comment": str(step.get("comment")),
            "name": step.get("name"),
        }
    if step.get("subroutine"):
        return {
            "group": group_name,
            "type": "subroutine",
            "subroutine": str(step.get("subroutine")),
            "name": step.get("name"),
        }
    if step.get("manual_move") or step.get("move"):
        payload = step.get("manual_move") or step.get("move") or {}
        return {
            "group": group_name,
            "type": "manual_move",
            "labware": payload.get("labware"),
            "to_location": payload.get("to_location"),
            "to_site": payload.get("to_site"),
            "name": step.get("name"),
        }
    return {
        "group": group_name,
        "type": "step",
        "name": step.get("name"),
        "raw": step,
    }


def _validation_gates(
    validation_report: dict[str, Any] | None,
    validation_diff: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if validation_report and isinstance(validation_report.get("gates"), list):
        return [_normalize_gate(gate) for gate in validation_report["gates"] if isinstance(gate, dict)]
    checks = (validation_diff or {}).get("checks") or []
    gates: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_id = str(check.get("id") or "")
        if check_id == "ready_validation" and isinstance(check.get("details"), dict):
            nested = check["details"].get("gates")
            if isinstance(nested, list):
                gates.extend(_normalize_gate(gate) for gate in nested if isinstance(gate, dict))
                continue
        gates.append({
            "id": check_id or "validation_check",
            "gate": check_id,
            "name": check_id.replace("_", " ").title(),
            "status": str(check.get("status") or "unknown"),
            "summary": check.get("summary"),
            "source": "validation_diff",
        })
    return gates


def _normalize_gate(gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": gate.get("id"),
        "gate": gate.get("gate"),
        "name": gate.get("name"),
        "status": gate.get("status"),
        "summary": gate.get("summary"),
        "details": gate.get("details"),
        "source": "ready_validation",
    }


def _build_sim_scene_payload(
    *,
    exported_at: str,
    protocol_name: str,
    generation: dict[str, Any],
    specs: list[SceneArtifactSpec],
) -> dict[str, Any]:
    return {
        "exportedAt": exported_at,
        "app": SIMULATOR_APP,
        "kind": SIM_SCENE_KIND,
        "schemaVersion": SIM_SCENE_SCHEMA_VERSION,
        "bundleRoot": ".",
        "status": f"Generated scene for {protocol_name}",
        "playback": _default_playback(),
        "generation": generation,
        "artifactIndex": [
            {
                "role": spec.role,
                "path": spec.bundle_path,
                "kind": spec.kind,
                "available": bool(spec.local_path and spec.local_path.exists()),
            }
            for spec in specs
        ],
        "editor": _default_editor(protocol_name),
    }


def _build_simulator_project_payload(
    *,
    exported_at: str,
    protocol_name: str,
    generation: dict[str, Any],
    specs: list[SceneArtifactSpec],
) -> dict[str, Any]:
    return {
        "exportedAt": exported_at,
        "app": SIMULATOR_APP,
        "kind": SIMULATOR_PROJECT_KIND,
        "schemaVersion": SIMULATOR_PROJECT_SCHEMA_VERSION,
        "bundleRoot": ".",
        "status": f"Generated simulator project for {protocol_name}",
        "sampleId": None,
        "playback": _default_playback(),
        "generation": generation,
        "protocol": None,
        "editor": _default_editor(protocol_name),
        "artifacts": [_artifact_record(spec) for spec in specs if _artifact_available(spec)],
        "assetImageOverrides": {},
        "generatedAssetBundle": {},
    }


def _default_playback() -> dict[str, Any]:
    return {
        "activeIndex": 0,
        "speed": 1,
        "staticMode": False,
        "sceneInteractionMode": "view",
    }


def _default_editor(protocol_name: str) -> dict[str, Any]:
    return {
        "version": 1,
        "scriptCommands": [],
        "addedObjects": [],
        "imageOverrides": {},
        "placementOverrides": [],
        "ui": {
            "editorTab": "script",
            "selectedScriptCommandId": "",
            "scriptTitle": protocol_name,
            "sceneInteractionMode": "view",
        },
        "notes": [
            "Auto-generated by fluent_pipeline. Drop this file into the Tecan Protocol Simulator to preload the worktable scene."
        ],
    }


def _artifact_available(spec: SceneArtifactSpec) -> bool:
    return bool(spec.local_path and spec.local_path.exists())


def _artifact_record(spec: SceneArtifactSpec) -> dict[str, Any]:
    path = spec.local_path
    assert path is not None
    name = path.name
    text = _read_embedded_text(path) if spec.embed else ""
    size = path.stat().st_size if path.exists() else 0
    return {
        "id": f"{spec.role}:{spec.bundle_path}",
        "name": name,
        "kind": spec.kind,
        "source": "fluent_pipeline",
        "path": spec.bundle_path,
        "size": size,
        "text": text,
        "mimeType": _mime_type_for_path(path),
        "embedded": bool(text),
        "role": spec.role,
    }


def _embedded_normalization_roots(path: Path) -> list[str]:
    """Absolute path roots to collapse inside embedded artifact text."""
    roots: list[str] = []
    try:
        current = path.resolve()
    except OSError:
        current = path
    for _ in range(8):
        roots.append(str(current))
        parent = current.parent
        if parent == current:
            break
        current = parent
    return roots


def _read_embedded_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    if path.stat().st_size > MAX_EMBED_BYTES:
        return ""
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""
    # Embedded artifacts are re-serialized inside simulator-project.json. Collapse
    # run-specific absolute paths and timestamps so regeneration stays deterministic
    # and the handoff bundle stays portable across machines.
    return normalize_artifact_text(text, _embedded_normalization_roots(path))


def _mime_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".json"}:
        return "application/json"
    if suffix in {".xml", ".xscr"}:
        return "application/xml"
    if suffix in {".yaml", ".yml"}:
        return "application/yaml"
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".py":
        return "text/x-python"
    if suffix == ".gwl":
        return "text/plain"
    return "text/plain"
