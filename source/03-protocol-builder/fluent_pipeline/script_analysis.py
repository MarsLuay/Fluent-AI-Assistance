"""Script-level explanation and manual edit recommendations."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .command_registry import (
    registry_command_support_status,
    registry_manual_step,
)
from .project_context import ProjectLike, resolve_context_script
from .protocol_ir import is_ir_bundle, protocol_ir_from_path, render_recreate_markdown
from .runner import PipelineError, write_json
from .worktable_diff import diff_worktable_requirements


SCRIPT_ANALYSIS_VERSION = "tecan.script_analysis.v1"


def analyze_script(
    ctx: ProjectLike,
    *,
    script: str | None = None,
    script_index: int = 1,
    out_dir: Path | None = None,
    max_commands: int = 120,
) -> dict[str, Any]:
    """Analyze one imported script and optionally write report artifacts."""
    record, path, index = _select_script(ctx, script=script, script_index=script_index)
    protocol_ir = _single_protocol_ir(path)
    worktable_diff = diff_worktable_requirements(protocol_ir, source_manifest=ctx.manifest)
    dependencies = record.get("dependencies") or {}
    manual_commands = _manual_commands(protocol_ir, max_commands=max_commands)
    report = {
        "analysis_version": SCRIPT_ANALYSIS_VERSION,
        "context": {
            "name": ctx.name,
            "root": str(ctx.root),
            "kind": ctx.manifest.get("kind") or "project",
        },
        "script": {
            "index": index,
            "object_name": record.get("object_name") or "",
            "entry": record.get("entry") or "",
            "path": str(path),
            "command_count": record.get("command_count", 0),
            "script_version": record.get("script_version") or "",
            "checksum": record.get("checksum") or "",
        },
        "what_it_does": _what_it_does(record, protocol_ir),
        "dependencies": dependencies,
        "command_families": record.get("family_counts") or {},
        "command_counts": record.get("command_counts") or {},
        "subroutines": _subroutines(ctx, dependencies),
        "custom_parts": _custom_parts(ctx, dependencies),
        "potential_improvements": _potential_improvements(record, protocol_ir, worktable_diff),
        "manual_commands": manual_commands,
        "worktable_summary": _worktable_summary(worktable_diff),
        "artifacts": {},
    }

    if out_dir is not None:
        output_dir = out_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        base = _slug(report["script"]["object_name"] or path.stem)
        json_path = output_dir / f"{base}.script-analysis.json"
        markdown_path = output_dir / f"{base}.script-analysis.md"
        recreate_path = output_dir / f"{base}.recreate.md"
        write_json(json_path, report)
        markdown_path.write_text(render_script_analysis_markdown(report), encoding="utf-8")
        recreate_path.write_text(render_recreate_markdown(protocol_ir), encoding="utf-8")
        report["artifacts"] = {
            "script_analysis_json": str(json_path),
            "script_analysis_markdown": str(markdown_path),
            "recreate_markdown": str(recreate_path),
        }
        write_json(json_path, report)
    return report


def render_script_analysis_markdown(report: dict[str, Any]) -> str:
    script = report.get("script") or {}
    lines = [
        f"# Script Analysis: {script.get('object_name') or Path(str(script.get('path') or 'script')).stem}",
        "",
        f"- Context: `{(report.get('context') or {}).get('name', '')}`",
        f"- Script index: `{script.get('index')}`",
        f"- Entry: `{script.get('entry')}`",
        f"- Commands: `{script.get('command_count')}`",
        "",
        "## What It Does",
        "",
    ]
    for item in report.get("what_it_does") or []:
        lines.append(f"- {item}")

    dependencies = report.get("dependencies") or {}
    lines.extend(["", "## Key Dependencies", ""])
    for key in (
        "workspace_guids",
        "labware_names",
        "liquid_classes",
        "device_aliases",
        "subroutine_refs",
        "custom_asset_refs",
        "pin_refs",
        "worktable_pin_locations",
    ):
        values = dependencies.get(key) or []
        if values:
            lines.append(f"- {key}: `{', '.join(str(value) for value in values[:20])}`")

    subroutines = report.get("subroutines") or []
    if subroutines:
        lines.extend(["", "## Subroutines", ""])
        for item in subroutines:
            status = item.get("status") or "unknown"
            target = item.get("resolved_script") or "not found"
            lines.append(f"- `{item.get('ref')}`: `{status}` -> `{target}`")
            if item.get("status") == "ambiguous":
                alternatives = ", ".join(
                    f"`{alt.get('object_name') or alt.get('entry') or '?'}`"
                    for alt in item.get("alternatives") or []
                )
                lines.append(
                    f"  - Ambiguous; also matched: {alternatives or '`unknown`'}. "
                    "Verify the correct subroutine."
                )

    custom_parts = report.get("custom_parts") or {}
    if custom_parts:
        lines.extend(["", "## Custom Parts And Pins", ""])
        for key in ("pin_refs", "custom_asset_refs", "barcode_refs", "context_pin_refs", "context_asset_refs"):
            values = custom_parts.get(key) or []
            if values:
                lines.append(f"- {key}: `{', '.join(str(value) for value in values[:20])}`")
        if custom_parts.get("pin_connector_count"):
            lines.append(f"- pin_connector_count: `{custom_parts.get('pin_connector_count')}`")

    lines.extend(["", "## Potential Improvements", ""])
    improvements = report.get("potential_improvements") or []
    if improvements:
        for item in improvements:
            lines.append(f"- `{item.get('severity')}` {item.get('title')}")
            for command in item.get("manual_commands") or []:
                lines.append(f"  - Manual command/check: {command}")
    else:
        lines.append("- No specific static improvement candidates were found.")

    lines.extend(["", "## Exact Manual Command Reference", ""])
    commands = report.get("manual_commands") or []
    if commands:
        for item in commands:
            lines.append(
                f"{item.get('index')}. `{item.get('command_id') or item.get('operation')}` "
                f"({item.get('group') or 'Ungrouped'}): {item.get('manual_step')}"
            )
            specs = item.get("parameters") or {}
            if specs:
                rendered_specs = ", ".join(f"{key}={value!r}" for key, value in specs.items())
                lines.append(f"   - Parameters: `{rendered_specs}`")
            if item.get("source_path"):
                lines.append(f"   - Source: `{item.get('source_path')}`")
    else:
        lines.append("- No editable protocol commands were parsed from this script.")

    artifacts = report.get("artifacts") or {}
    if artifacts:
        lines.extend(["", "## Artifacts", ""])
        for key, value in artifacts.items():
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines).rstrip() + "\n"


def _select_script(
    ctx: ProjectLike,
    *,
    script: str | None,
    script_index: int,
) -> tuple[dict[str, Any], Path, int]:
    scripts = list(ctx.manifest.get("scripts") or [])
    if script:
        path = resolve_context_script(ctx, script)
        for index, record in enumerate(scripts, start=1):
            candidate = Path(str(record.get("extracted_path") or ""))
            resolved = candidate if candidate.is_absolute() else ctx.root / candidate
            if resolved.resolve() == path.resolve():
                return record, path, index
        raise PipelineError(f"script {script!r} resolved to {path}, but no manifest record matched it")
    if not scripts:
        raise PipelineError(f"context {ctx.name!r} has no scripts")
    if script_index < 1 or script_index > len(scripts):
        raise PipelineError(f"script index {script_index} is outside 1..{len(scripts)}")
    record = scripts[script_index - 1]
    path = ctx.root / str(record.get("extracted_path") or "")
    return record, path.resolve(), script_index


def _single_protocol_ir(path: Path) -> dict[str, Any]:
    payload = protocol_ir_from_path(path)
    if is_ir_bundle(payload):
        protocols = payload.get("protocols") or []
        if len(protocols) != 1:
            raise PipelineError(f"{path} produced {len(protocols)} protocols; choose a single .xscr script")
        return protocols[0]
    return payload


def _what_it_does(record: dict[str, Any], protocol_ir: dict[str, Any]) -> list[str]:
    name = str(record.get("object_name") or (protocol_ir.get("protocol") or {}).get("name") or "This script")
    deps = record.get("dependencies") or {}
    families = record.get("family_counts") or {}
    steps = protocol_ir.get("steps") or []
    out = [f"`{name}` contains `{record.get('command_count', 0)}` FluentControl command objects and `{len(steps)}` parsed protocol IR step(s)."]
    if families:
        dominant = ", ".join(f"{key}={value}" for key, value in Counter(families).most_common(6))
        out.append(f"Dominant command families: `{dominant}`.")
    if deps.get("labware_names"):
        out.append(f"Works with labware such as `{', '.join(deps['labware_names'][:8])}`.")
    if deps.get("liquid_classes"):
        out.append(f"Uses liquid classes `{', '.join(deps['liquid_classes'][:8])}`.")
    if deps.get("device_aliases"):
        out.append(f"Targets device aliases `{', '.join(deps['device_aliases'][:8])}`.")
    if deps.get("subroutine_refs"):
        out.append(f"Calls subroutines `{', '.join(deps['subroutine_refs'][:8])}`.")
    if deps.get("pin_refs") or deps.get("worktable_pin_locations"):
        out.append("Uses pin-controlled or pin-located hardware that must be checked in FluentControl.")
    return out


def _manual_commands(protocol_ir: dict[str, Any], *, max_commands: int) -> list[dict[str, Any]]:
    commands = []
    for index, step in enumerate(protocol_ir.get("steps") or [], start=1):
        if len(commands) >= max_commands:
            break
        command_id = str(step.get("command_id") or step.get("operation") or "")
        params = _canonical_parameters(step)
        manual = registry_manual_step(command_id, _registry_fields(step, params))
        if not manual:
            manual = _manual_fallback(command_id, step, params)
        commands.append(
            {
                "index": index,
                "group": step.get("group") or "Ungrouped",
                "operation": step.get("operation") or "",
                "command_id": command_id,
                "support_status": registry_command_support_status(command_id) or "",
                "manual_step": manual,
                "parameters": params,
                "source_path": step.get("source_path") or step.get("compiled_path") or "",
                "raw_xml_preserved": bool((step.get("parameters") or {}).get("raw_xml")),
            }
        )
    return commands


def _canonical_parameters(step: dict[str, Any]) -> dict[str, Any]:
    params = dict(step.get("parameters") or {})
    params.pop("raw_xml", None)
    for key in ("target_labware", "source_labware", "destination_labware", "volume_ul", "liquid_class"):
        value = step.get(key)
        if value not in (None, "", []):
            params.setdefault(key, value)
    return {key: value for key, value in params.items() if value not in (None, "", [])}


def _registry_fields(step: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    fields = dict(params)
    labware = step.get("target_labware") or step.get("source_labware") or step.get("destination_labware") or params.get("labware")
    if labware:
        fields.setdefault("LabwareName", labware)
        fields.setdefault("LabwareLable", labware)
    if step.get("volume_ul") not in (None, "", []):
        fields.setdefault("Volume", step.get("volume_ul"))
    if step.get("liquid_class"):
        fields.setdefault("LiquidClassName", step.get("liquid_class"))
    for canonical, fluent_name in (
        ("device_alias", "DeviceAlias"),
        ("worklist", "WorklistName"),
        ("prompt", "QueryPrompt"),
        ("comment", "Comment"),
    ):
        if params.get(canonical):
            fields.setdefault(fluent_name, params[canonical])
    return fields


def _manual_fallback(command_id: str, step: dict[str, Any], params: dict[str, Any]) -> str:
    operation = str(step.get("operation") or command_id or "command").replace("_", " ")
    if params.get("raw_xml"):
        return f"Preserve raw XML command `{command_id}`."
    target = step.get("target_labware") or step.get("source_labware") or step.get("destination_labware") or params.get("labware")
    pieces = [f"Run {operation}"]
    if target:
        pieces.append(f"on `{target}`")
    if step.get("volume_ul"):
        pieces.append(f"with `{step.get('volume_ul')}` uL")
    if step.get("liquid_class"):
        pieces.append(f"using `{step.get('liquid_class')}`")
    return " ".join(pieces) + "."


def _subroutines(ctx: ProjectLike, dependencies: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [str(ref).strip().strip('"') for ref in dependencies.get("subroutine_refs") or [] if str(ref or "").strip()]
    scripts = ctx.manifest.get("scripts") or []
    out = []
    for ref in refs:
        matches = _rank_subroutine_matches(ref, scripts)
        if not matches:
            out.append({"ref": ref, "status": "unresolved", "resolved_script": "", "entry": ""})
            continue
        chosen = matches[0]
        record: dict[str, Any] = {
            "ref": ref,
            "status": "ambiguous" if len(matches) > 1 else "resolved",
            "resolved_script": str(chosen.get("object_name") or ""),
            "entry": str(chosen.get("entry") or ""),
        }
        if len(matches) > 1:
            record["alternatives"] = [
                {
                    "object_name": str(item.get("object_name") or ""),
                    "entry": str(item.get("entry") or ""),
                    "source_context": str(item.get("source_context") or ""),
                }
                for item in matches[1:]
            ]
        out.append(record)
    return out


def _rank_subroutine_matches(ref: str, scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_path = ref.replace("\\", "/")
    name = Path(normalized_path).name
    forms = {
        "clean": ref.casefold(),
        "normalized_path": normalized_path.casefold(),
        "name": name.casefold(),
        "stem": Path(name).stem.casefold(),
    }
    scored = [
        (strength, item)
        for item, strength in ((item, _subroutine_match_strength(item, forms)) for item in scripts)
        if strength
    ]
    if not scored:
        return []
    best = max(strength for strength, _ in scored)
    matches = [item for strength, item in scored if strength == best]
    return sorted(
        matches,
        key=lambda item: (
            str(item.get("source_context") or ""),
            str(item.get("object_name") or ""),
            str(item.get("entry") or ""),
        ),
    )


def _subroutine_match_strength(script: dict[str, Any], forms: dict[str, str]) -> int:
    object_name = str(script.get("object_name") or "").casefold()
    qualified = str(script.get("qualified_name") or "").replace("\\", "/").casefold()
    entry = str(script.get("entry") or "").replace("\\", "/").casefold()
    extracted = str(script.get("extracted_path") or "").replace("\\", "/").casefold()
    entry_name = Path(entry).name if entry else ""
    extracted_name = Path(extracted).name if extracted else ""
    entry_stem = Path(entry).stem if entry else ""
    extracted_stem = Path(extracted).stem if extracted else ""
    clean = forms["clean"]
    normalized_path = forms["normalized_path"]
    name = forms["name"]
    stem = forms["stem"]
    if object_name and object_name in {clean, name}:
        return 4
    if qualified and qualified in {clean, normalized_path}:
        return 4
    if normalized_path and normalized_path in {entry, extracted}:
        return 3
    if name and name in {entry_name, extracted_name}:
        return 2
    if stem and stem in {entry_stem, extracted_stem}:
        return 1
    return 0


def _custom_parts(ctx: ProjectLike, dependencies: dict[str, Any]) -> dict[str, Any]:
    summary = ctx.manifest.get("custom_part_summary") or {}
    return {
        "pin_refs": sorted(
            set(str(value) for value in dependencies.get("pin_refs") or [] if value)
            | set(str(value) for value in dependencies.get("worktable_pin_locations") or [] if value)
        ),
        "custom_asset_refs": sorted(str(value) for value in dependencies.get("custom_asset_refs") or [] if value),
        "barcode_refs": sorted(str(value) for value in dependencies.get("barcode_refs") or [] if value),
        "context_pin_refs": summary.get("pin_refs") or [],
        "context_asset_refs": summary.get("asset_refs") or [],
        "pin_connector_count": summary.get("pin_connector_count", 0),
        "asset_count": summary.get("asset_count", 0),
    }


def _potential_improvements(
    record: dict[str, Any],
    protocol_ir: dict[str, Any],
    worktable_diff: dict[str, Any],
) -> list[dict[str, Any]]:
    improvements: list[dict[str, Any]] = []
    command_counts = record.get("command_counts") or {}
    unsupported = {
        name: count
        for name, count in command_counts.items()
        if count and registry_command_support_status(name) is None and name not in {"ScriptGroup"}
    }
    if unsupported:
        improvements.append(
            {
                "severity": "high",
                "title": "Map or approve remaining command IDs before semantic editing",
                "manual_commands": [
                    f"Add `{name}` to `tecan_common/data/command_registry.json` with operation/support status, or preserve it as approved raw XML ({count} occurrence(s))."
                    for name, count in sorted(unsupported.items())[:12]
                ],
            }
        )
    raw_xml_steps = [
        step
        for step in protocol_ir.get("steps") or []
        if (step.get("parameters") or {}).get("raw_xml")
    ]
    if raw_xml_steps:
        improvements.append(
            {
                "severity": "medium",
                "title": "Replace opaque raw XML with modeled DSL only when the command needs editing",
                "manual_commands": [
                    f"Review `{step.get('command_id') or step.get('operation')}` at `{step.get('compiled_path') or step.get('source_path')}`."
                    for step in raw_xml_steps[:12]
                ],
            }
        )
    missing_labware = [item for item in worktable_diff.get("missing_labware") or [] if item.get("status") != "available"]
    if missing_labware:
        improvements.append(
            {
                "severity": "high",
                "title": "Confirm unresolved labware/worktable items in FluentControl",
                "manual_commands": [
                    f"Add or alias labware `{item.get('label') or item.get('name')}` with catalog `{item.get('catalog') or item.get('resolved_catalog') or ''}`."
                    for item in missing_labware[:12]
                ],
            }
        )
    deps = record.get("dependencies") or {}
    if deps.get("subroutine_refs"):
        improvements.append(
            {
                "severity": "medium",
                "title": "Validate parent script and subroutines together",
                "manual_commands": [
                    f"Keep SubRoutineStatement reference `{ref}` or explicitly inline only after validating the called script."
                    for ref in deps.get("subroutine_refs", [])[:12]
                ],
            }
        )
    if deps.get("pin_refs") or deps.get("worktable_pin_locations"):
        improvements.append(
            {
                "severity": "medium",
                "title": "Verify pin-controlled hardware before import",
                "manual_commands": [
                    f"Check FluentControl hardware/worktable pin `{ref}` against the instrument configuration."
                    for ref in sorted(set((deps.get("pin_refs") or []) + (deps.get("worktable_pin_locations") or [])))[:12]
                ],
            }
        )
    return improvements


def _worktable_summary(diff: dict[str, Any]) -> dict[str, int]:
    return {
        "missing_labware": len([item for item in diff.get("missing_labware") or [] if item.get("status") == "missing"]),
        "unverified_labware": len([item for item in diff.get("missing_labware") or [] if item.get("status") == "unverified"]),
        "changed_deck_positions": len(diff.get("changed_deck_positions") or []),
        "warnings": len(diff.get("warnings") or []),
    }


def _slug(value: Any) -> str:
    chars = []
    previous_dash = False
    for char in str(value or "").casefold():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-") or "script"
