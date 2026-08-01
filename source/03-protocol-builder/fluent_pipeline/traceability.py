"""Traceability from request intent through generated and compiled artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
from . import xml_compat as ET

from .protocol_ir import protocol_ir_from_xscr
from .request_spec import request_verbatim_prompt


TRACEABILITY_VERSION = "tecan.traceability.v1"


def step_trace_ref(step: dict[str, Any], fallback_index: int | None = None) -> str:
    """Return the stable trace reference for an IR step."""
    index = _positive_int(step.get("index"), fallback_index or 0)
    return str(step.get("id") or (f"step_{index:03d}" if index else "step_unknown"))


def render_step_trace_comment(step: dict[str, Any], fallback_index: int | None = None) -> str:
    """Render a Python-only trace comment that survives repair copies."""
    index = _positive_int(step.get("index"), fallback_index or 0)
    trace_id = step_trace_ref(step, index)
    operation = str(step.get("operation") or "unknown")
    name = str(step.get("name") or operation.replace("_", " ").title())
    return f"# TRACE step_id={trace_id} ir=steps[{index}] operation={operation} name={name!r}"


def build_traceability_map(
    *,
    request_spec: dict[str, Any] | None,
    request_spec_path: Path | None,
    protocol_ir: dict[str, Any],
    protocol_ir_path: Path | None,
    python_path: Path | None = None,
    compiled_xscr_path: Path | None = None,
    validation_report: dict[str, Any] | None = None,
    runtime_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a durable map across request, IR, Python draft, and compiled XSCR."""
    request = (request_spec or {}).get("request") if isinstance(request_spec, dict) else {}
    source = (request_spec or {}).get("source") if isinstance(request_spec, dict) else {}
    verbatim_prompt = request_verbatim_prompt(request_spec)
    if not verbatim_prompt:
        ir_source = protocol_ir.get("source") or {}
        for key in ("verbatim_prompt", "original_user_prompt", "source_prompt", "intent", "generation_intent"):
            if ir_source.get(key) is not None:
                verbatim_prompt = str(ir_source.get(key))
                break
    intent = str((request or {}).get("intent") or (protocol_ir.get("source") or {}).get("intent") or "").strip()
    intent_for_clauses = intent or verbatim_prompt.strip()
    clauses = _intent_clauses(intent_for_clauses)
    python_refs = _python_trace_refs(python_path)
    compiled_steps, compiled_records = _compiled_trace_sources(compiled_xscr_path)
    compiled_by_step = _match_compiled_steps(protocol_ir.get("steps") or [], compiled_steps, compiled_records)

    entries: list[dict[str, Any]] = []
    by_trace_id: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(protocol_ir.get("steps") or [], start=1):
        if not isinstance(step, dict):
            continue
        trace_id = step_trace_ref(step, index)
        py_ref = python_refs.get(trace_id, {})
        compiled_ref = compiled_by_step.get(trace_id, {})
        entry = {
            "trace_id": trace_id,
            "request": {
                "intent": intent,
                "intent_summary": intent,
                "verbatim_prompt": verbatim_prompt,
                "clauses": _matched_intent_clauses(step, clauses),
                "spec_path": str(request_spec_path) if request_spec_path else None,
                "source_scripts": list((source or {}).get("source_scripts") or []),
                "pattern_refs": list((source or {}).get("pattern_refs") or []),
            },
            "ir": {
                "path": str(protocol_ir_path) if protocol_ir_path else None,
                "step_index": _positive_int(step.get("index"), index),
                "step_path": f"steps[{_positive_int(step.get('index'), index)}]",
                "step_id": trace_id,
                "name": step.get("name"),
                "operation": step.get("operation"),
                "command_id": step.get("command_id"),
                "target_labware": step.get("target_labware"),
                "source_labware": step.get("source_labware"),
                "destination_labware": step.get("destination_labware"),
                "source_path": step.get("source_path"),
                "compiled_path": step.get("compiled_path"),
            },
            "python": py_ref or None,
            "compiled_xscr": compiled_ref or None,
            "validation_findings": [],
            "runtime_errors": [],
        }
        entries.append(_strip_empty(entry))
        by_trace_id[trace_id] = entries[-1]

    _attach_validation_findings(entries, by_trace_id, validation_report)
    _attach_runtime_errors(entries, by_trace_id, runtime_report)
    return _strip_empty(
        {
            "schema_version": TRACEABILITY_VERSION,
            "kind": "traceability_map",
            "request": {
                "spec_path": str(request_spec_path) if request_spec_path else None,
                "intent": intent,
                "intent_summary": intent,
                "verbatim_prompt": verbatim_prompt,
                "intent_clauses": clauses,
            },
            "protocol_ir": {
                "path": str(protocol_ir_path) if protocol_ir_path else None,
                "id": protocol_ir.get("id"),
                "name": (protocol_ir.get("protocol") or {}).get("name"),
                "step_count": len(protocol_ir.get("steps") or []),
            },
            "python_draft": str(python_path) if python_path else None,
            "compiled_xscr": str(compiled_xscr_path) if compiled_xscr_path and compiled_xscr_path.exists() else None,
            "entries": entries,
        }
    )


def render_traceability_markdown(trace_map: dict[str, Any]) -> str:
    """Render a human-readable traceability artifact."""
    request = trace_map.get("request") or {}
    lines = [
        "# Traceability Map",
        "",
        "This map links request intent to request spec, IR steps, Python draft lines, internal compiled XSCR commands, and known validation/runtime findings.",
        "",
        f"- Request spec: `{request.get('spec_path') or 'not recorded'}`",
        f"- Protocol IR: `{(trace_map.get('protocol_ir') or {}).get('path') or 'not recorded'}`",
        f"- Python draft: `{trace_map.get('python_draft') or 'not recorded'}`",
        f"- Internal compiled XSCR: `{trace_map.get('compiled_xscr') or 'not retained'}`",
        "",
        "## Original Request Prompt",
        "",
    ]
    verbatim_prompt = str(request.get("verbatim_prompt") or "")
    intent = str(request.get("intent_summary") or request.get("intent") or "")
    if verbatim_prompt:
        opening_fence, closing_fence = _markdown_fences_for(verbatim_prompt)
        lines.extend([opening_fence, verbatim_prompt, closing_fence])
    else:
        lines.append("No original request prompt was recorded.")
    if intent and intent != verbatim_prompt:
        lines.extend(["", "## Intent Summary", "", intent])
    clauses = request.get("intent_clauses") or []
    if clauses:
        lines.extend(["", "## Intent Clauses", ""])
        for index, clause in enumerate(clauses, start=1):
            lines.append(f"{index}. {clause}")

    lines.extend(["", "## Step Trace", ""])
    for entry in trace_map.get("entries") or []:
        ir = entry.get("ir") or {}
        py = entry.get("python") or {}
        compiled = entry.get("compiled_xscr") or {}
        lines.append(
            f"- `{entry.get('trace_id')}` `{ir.get('step_path')}` "
            f"{ir.get('operation') or 'unknown'}: {ir.get('name') or ''}"
        )
        clauses = (entry.get("request") or {}).get("clauses") or []
        if clauses:
            lines.append(f"  - Request clause: {clauses[0]}")
        if ir.get("source_path"):
            lines.append(f"  - IR source: `{ir['source_path']}`")
        if py:
            lines.append(
                f"  - Python: comment line `{py.get('comment_line')}`, code line `{py.get('code_line')}`"
            )
        if compiled:
            parts = [
                f"command `{compiled.get('command_index')}`",
                f"line `{compiled.get('line_number') or 'unknown'}`",
                f"type `{compiled.get('object_type') or compiled.get('command_id') or 'unknown'}`",
            ]
            lines.append(f"  - Internal compiled XSCR: {', '.join(parts)}")
        for finding in entry.get("validation_findings") or []:
            lines.append(f"  - Validation: `{finding.get('gate_id')}` {finding.get('reason') or finding.get('message')}")
        for error in entry.get("runtime_errors") or []:
            lines.append(f"  - Runtime: {error.get('message') or error.get('error')}")
    markdown = "\n".join(lines).rstrip()
    markdown += "\n"
    return markdown


def annotate_findings_with_trace(findings: list[dict[str, Any]], trace_map: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Attach trace references to compiled/static findings when line or entity data matches."""
    if not trace_map:
        return findings
    return [_attach_trace_reference(dict(finding), trace_map) for finding in findings]


def annotate_runtime_report_with_trace(report: dict[str, Any], trace_map: dict[str, Any] | None) -> dict[str, Any]:
    """Attach trace references to line-specific runtime/load errors."""
    if not trace_map:
        return report
    out = dict(report)
    for key in ("errors", "runtime_errors"):
        values = []
        for value in out.get(key) or []:
            message = str(value)
            reference = trace_reference_for_error(message, trace_map)
            values.append({"message": message, "trace_reference": reference} if reference else message)
        if values:
            out[key] = values
    return out


def trace_reference_for_error(message: str, trace_map: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the best trace reference for a FluentControl line-specific error."""
    if not trace_map:
        return None
    line = _line_from_text(message)
    if line is not None:
        ref = _trace_reference_for_line(line, trace_map)
        if ref:
            return ref
    lowered = message.casefold()
    for entry in trace_map.get("entries") or []:
        compiled = entry.get("compiled_xscr") or {}
        ir = entry.get("ir") or {}
        candidates = [
            compiled.get("command_id"),
            compiled.get("object_type"),
            compiled.get("target_labware"),
            ir.get("operation"),
            ir.get("name"),
            ir.get("command_id"),
            ir.get("target_labware"),
            ir.get("source_labware"),
            ir.get("destination_labware"),
        ]
        params = ir.get("parameters") if isinstance(ir.get("parameters"), dict) else {}
        candidates.extend(str(value) for value in params.values() if value not in (None, "", [], {}))
        if any(str(value).casefold() in lowered for value in candidates if value):
            return _trace_reference(entry)
    return None


def _attach_trace_reference(finding: dict[str, Any], trace_map: dict[str, Any]) -> dict[str, Any]:
    line = _positive_int(finding.get("line_number"), 0)
    reference = _trace_reference_for_line(line, trace_map) if line else None
    if reference is None:
        message = " ".join(str(finding.get(key) or "") for key in ("message", "labware_name", "subroutine", "command_id"))
        reference = trace_reference_for_error(message, trace_map)
    if reference:
        finding["trace_reference"] = reference
    return finding


def _trace_reference_for_line(line_number: int, trace_map: dict[str, Any]) -> dict[str, Any] | None:
    for entry in trace_map.get("entries") or []:
        compiled = entry.get("compiled_xscr") or {}
        if _positive_int(compiled.get("line_number"), 0) == line_number:
            return _trace_reference(entry)
    return None


def _trace_reference(entry: dict[str, Any]) -> dict[str, Any]:
    ir = entry.get("ir") or {}
    py = entry.get("python") or {}
    compiled = entry.get("compiled_xscr") or {}
    return _strip_empty(
        {
            "trace_id": entry.get("trace_id"),
            "ir_step": ir.get("step_path"),
            "ir_operation": ir.get("operation"),
            "ir_name": ir.get("name"),
            "python_line": py.get("code_line"),
            "compiled_line": compiled.get("line_number"),
            "compiled_command_index": compiled.get("command_index"),
            "compiled_command_id": compiled.get("command_id"),
            "compiled_object_type": compiled.get("object_type"),
        }
    )


def _python_trace_refs(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    refs: dict[str, dict[str, Any]] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines, start=1):
        match = re.search(r"#\s*TRACE\s+step_id=(\S+)\s+ir=(steps\[\d+\])\s+operation=([A-Za-z0-9_]+)", line)
        if not match:
            continue
        code_line = None
        statement = ""
        for candidate_index in range(index + 1, len(lines) + 1):
            candidate = lines[candidate_index - 1].strip()
            if candidate and not candidate.startswith("#"):
                code_line = candidate_index
                statement = candidate
                break
        refs[match.group(1)] = _strip_empty(
            {
                "path": str(path),
                "comment_line": index,
                "code_line": code_line,
                "statement": statement,
                "ir_step": match.group(2),
                "operation": match.group(3),
            }
        )
    return refs


def _compiled_trace_sources(path: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path is None or not path.exists():
        return [], []
    try:
        compiled_ir = protocol_ir_from_xscr(path)
    except Exception:
        compiled_ir = {"steps": []}
    return list(compiled_ir.get("steps") or []), _compiled_command_records(path)


def _compiled_command_records(path: Path) -> list[dict[str, Any]]:
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return []
    records: list[dict[str, Any]] = []
    for element in root.iter():
        if _local_name(element.tag) != "Object":
            continue
        object_type = str(element.attrib.get("Type") or "")
        if not object_type or object_type.endswith("ScriptGroupDataV1"):
            continue
        command_id = _compiled_command_id(element)
        if not command_id:
            continue
        records.append(
            _strip_empty(
                {
                    "command_index": len(records) + 1,
                    "line_number": _first_text(element, "LineNumber"),
                    "command_id": command_id,
                    "object_type": object_type,
                    "object_name": _first_text(element, "ObjectName") or _first_text(element, "Name"),
                }
            )
        )
    return records


def _match_compiled_steps(
    source_steps: list[Any],
    compiled_steps: list[dict[str, Any]],
    compiled_records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    used: set[int] = set()
    compiled_signatures = [_step_signature(step) for step in compiled_steps]
    for source_index, source_step in enumerate(source_steps, start=1):
        if not isinstance(source_step, dict):
            continue
        trace_id = step_trace_ref(source_step, source_index)
        signature = _step_signature(source_step)
        compiled_index = None
        for index, candidate in enumerate(compiled_signatures):
            if index in used:
                continue
            if candidate == signature:
                compiled_index = index
                break
        if compiled_index is None and source_index - 1 < len(compiled_steps) and source_index - 1 not in used:
            compiled_index = source_index - 1
        if compiled_index is None:
            continue
        used.add(compiled_index)
        compiled_step = compiled_steps[compiled_index] if compiled_index < len(compiled_steps) else {}
        record = compiled_records[compiled_index] if compiled_index < len(compiled_records) else {}
        matches[trace_id] = _strip_empty(
            {
                **record,
                "compiled_ir_index": compiled_step.get("index") or compiled_index + 1,
                "compiled_path": compiled_step.get("compiled_path"),
                "operation": compiled_step.get("operation"),
                "target_labware": (
                    compiled_step.get("target_labware")
                    or compiled_step.get("source_labware")
                    or compiled_step.get("destination_labware")
                ),
            }
        )
    return matches


def _attach_validation_findings(
    entries: list[dict[str, Any]],
    by_trace_id: dict[str, dict[str, Any]],
    validation_report: dict[str, Any] | None,
) -> None:
    if not validation_report:
        return
    for gate in validation_report.get("gates") or []:
        if gate.get("status") != "failed":
            continue
        for finding in _iter_findings(gate.get("details")):
            ref = finding.get("trace_reference")
            trace_id = ref.get("trace_id") if isinstance(ref, dict) else None
            if trace_id and trace_id in by_trace_id:
                by_trace_id[trace_id].setdefault("validation_findings", []).append(
                    _strip_empty(
                        {
                            "gate_id": gate.get("id"),
                            "gate": gate.get("gate"),
                            "reason": finding.get("reason") or finding.get("code"),
                            "message": finding.get("message"),
                            "trace_reference": ref,
                        }
                    )
                )


def _attach_runtime_errors(
    entries: list[dict[str, Any]],
    by_trace_id: dict[str, dict[str, Any]],
    runtime_report: dict[str, Any] | None,
) -> None:
    if not runtime_report:
        return
    for key in ("errors", "runtime_errors"):
        for item in runtime_report.get(key) or []:
            if isinstance(item, dict):
                message = str(item.get("message") or item.get("error") or "")
                ref = item.get("trace_reference")
            else:
                message = str(item)
                ref = None
            if not isinstance(ref, dict):
                ref = trace_reference_for_error(message, {"entries": entries})
            trace_id = ref.get("trace_id") if isinstance(ref, dict) else None
            if trace_id and trace_id in by_trace_id:
                by_trace_id[trace_id].setdefault("runtime_errors", []).append(
                    _strip_empty({"source": key, "message": message, "trace_reference": ref})
                )


def _iter_findings(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "trace_reference" in value or "reason" in value or "message" in value:
            out.append(value)
        for nested in value.values():
            out.extend(_iter_findings(nested))
    elif isinstance(value, list):
        for item in value:
            out.extend(_iter_findings(item))
    return out


def _matched_intent_clauses(step: dict[str, Any], clauses: list[str]) -> list[str]:
    if not clauses:
        return []
    tokens = {
        token
        for token in re.findall(
            r"[A-Za-z][A-Za-z0-9_]{2,}",
            " ".join(
                str(value)
                for value in (
                    step.get("operation"),
                    step.get("name"),
                    step.get("target_labware"),
                    step.get("source_labware"),
                    step.get("destination_labware"),
                    step.get("liquid_class"),
                    (step.get("parameters") or {}).get("prompt") if isinstance(step.get("parameters"), dict) else "",
                    (step.get("parameters") or {}).get("subroutine") if isinstance(step.get("parameters"), dict) else "",
                )
            ),
        )
        if token.casefold() not in {"step", "command", "selected", "labware"}
    }
    matched = [
        clause
        for clause in clauses
        if any(token.casefold() in clause.casefold() for token in tokens)
    ]
    if matched:
        return matched[:3]
    return clauses[:1] if len(clauses) == 1 else []


def _intent_clauses(intent: str) -> list[str]:
    chunks = re.split(r"(?:\r?\n|[.;]\s+|\s+-\s+)", intent.strip())
    return [chunk.strip(" -\t") for chunk in chunks if chunk.strip(" -\t")]


def _markdown_fences_for(text: str) -> tuple[str, str]:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text", fence


def _step_signature(step: dict[str, Any]) -> dict[str, Any]:
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    return _strip_empty(
        {
            "operation": step.get("operation"),
            "target_labware": step.get("target_labware") or step.get("source_labware") or step.get("destination_labware") or params.get("labware"),
            "volume_ul": _normalized_number(step.get("volume_ul") or params.get("volume_ul")),
            "liquid_class": step.get("liquid_class") or params.get("liquid_class"),
            "subroutine": params.get("subroutine") or params.get("SubRoutine"),
            "prompt": params.get("prompt") or params.get("screen_title"),
            "comment": params.get("comment"),
        }
    )


def _compiled_command_id(element: ET.Element) -> str:
    object_type = str(element.attrib.get("Type") or "")
    if object_type:
        tail = object_type.rsplit(".", 1)[-1]
        return re.sub(r"DataV\d+$", "", tail)
    return ""


def _line_from_text(message: str) -> int | None:
    for pattern in (r"\bline\s*0*(\d+)\b", r"\bLineNumber\s*[:=]\s*0*(\d+)\b"):
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _first_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return (element.text or "").strip()
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _normalized_number(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    return str(int(numeric)) if numeric.is_integer() else f"{numeric:.6f}".rstrip("0").rstrip(".")


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_empty(val)
            for key, val in value.items()
            if val not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [_strip_empty(item) for item in value if item not in (None, "", [], {})]
    return value
