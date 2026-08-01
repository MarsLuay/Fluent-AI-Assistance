"""Project-aware repair planning for decompiled Fluent drafts."""

from __future__ import annotations

import ast
import json
import sqlite3
from . import xml_compat as ET
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aliases import ALIAS_KINDS, load_alias_maps
from .command_registry import registry_command_support_status
from .config import fluentcoder_root
from .project_context import ProjectContext
from .runner import ensure_parent


@dataclass(frozen=True)
class RepairAction:
    kind: str
    status: str
    summary: str
    line: int | None = None
    old: str | None = None
    new: str | None = None
    command_id: str | None = None
    edits: list["RepairEdit"] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status": self.status,
            "summary": self.summary,
            "line": self.line,
            "old": self.old,
            "new": self.new,
            "command_id": self.command_id,
            "edits": [edit.to_dict() for edit in self.edits],
            "details": self.details,
        }


@dataclass(frozen=True)
class RepairEdit:
    repair_id: str = ""
    confidence: float = 0.0
    target_type: str = ""
    target_node: str = ""
    line: int = 0
    column_start: int = 0
    column_end: int = 0
    expected_old_value: str = ""
    replacement: str = ""
    expected_occurrences: int = 1
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    inverse_patch: dict[str, Any] = field(default_factory=dict)
    expected_source_text: str = ""
    replacement_source_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "repair_id": self.repair_id,
            "confidence": self.confidence,
            "target_type": self.target_type,
            "target_node": self.target_node,
            "line": self.line,
            "column_start": self.column_start,
            "column_end": self.column_end,
            "expected_old_value": self.expected_old_value,
            "replacement": self.replacement,
            "expected_occurrences": self.expected_occurrences,
            "preconditions": self.preconditions,
            "postconditions": self.postconditions,
            "inverse_patch": self.inverse_patch,
            "expected_source_text": self.expected_source_text,
            "replacement_source_text": self.replacement_source_text,
        }


@dataclass(frozen=True)
class RepairPlan:
    draft_path: Path
    context_name: str | None
    simulation_json_path: Path | None
    actions: list[RepairAction]

    def to_dict(self) -> dict[str, Any]:
        ready = [action for action in self.actions if action.status == "ready"]
        suggested = [action for action in self.actions if action.status == "suggested"]
        needs_review = [action for action in self.actions if action.status == "needs_review"]
        return {
            "draft_path": str(self.draft_path),
            "context_name": self.context_name,
            "simulation_json_path": str(self.simulation_json_path) if self.simulation_json_path else None,
            "summary": {
                "action_count": len(self.actions),
                "ready_count": len(ready),
                "suggested_count": len(suggested),
                "needs_review_count": len(needs_review),
            },
            "actions": [action.to_dict() for action in self.actions],
        }


@dataclass(frozen=True)
class PythonSourceIndex:
    source: str
    tree: ast.AST
    lines: tuple[str, ...]
    line_offsets: tuple[int, ...]
    parents: dict[ast.AST, ast.AST | None]


class RepairApplicationError(RuntimeError):
    """Raised when a structured repair no longer matches the recorded span."""


def build_repair_plan(
    draft_path: Path,
    *,
    context: ProjectContext | None = None,
    simulation_json_path: Path | None = None,
) -> RepairPlan:
    source = draft_path.read_text(encoding="utf-8")
    simulation = _load_simulation_json(simulation_json_path)
    index = _index_python_source(source)
    actions = []
    actions.extend(_catalog_alias_actions(index, context))
    actions.extend(_raw_xml_modeling_actions(index, simulation))
    actions.extend(_failure_repair_actions(index, simulation))
    return RepairPlan(
        draft_path=draft_path,
        context_name=context.name if context else None,
        simulation_json_path=simulation_json_path,
        actions=actions,
    )


def applicable_repair_actions(plan: RepairPlan, *, apply_modeling: bool = False) -> list[RepairAction]:
    """Return the repairs that should be applied under the current approval mode."""
    return [
        action
        for action in plan.actions
        if action.status == "ready"
        or (apply_modeling and action.kind == "model_raw_xml_step" and action.status == "suggested")
    ]


def _index_python_source(source: str) -> PythonSourceIndex:
    tree = ast.parse(source)
    lines = tuple(source.splitlines(keepends=True))
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))
    parents: dict[ast.AST, ast.AST | None] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return PythonSourceIndex(source=source, tree=tree, lines=lines, line_offsets=tuple(line_offsets), parents=parents)


def _apply_structured_edits(source: str, edits: list[RepairEdit]) -> str:
    if not edits:
        return source
    indexed = _index_python_source(source)
    spans: list[tuple[int, int, RepairEdit]] = []
    for edit in edits:
        start = _span_to_offset(indexed.line_offsets, edit.line, edit.column_start)
        end = _span_to_offset(indexed.line_offsets, edit.line, edit.column_end)
        if end < start:
            raise RepairApplicationError(f"repair {edit.repair_id or '<pending>'} has an invalid span")
        spans.append((start, end, edit))

    spans_sorted = sorted(spans, key=lambda item: (item[0], item[1]))
    for left, right in zip(spans_sorted, spans_sorted[1:]):
        if right[0] < left[1]:
            raise RepairApplicationError(
                f"repairs {left[2].repair_id or '<pending>'} and {right[2].repair_id or '<pending>'} overlap"
            )

    updated = source
    for start, end, edit in sorted(spans_sorted, key=lambda item: (item[0], item[1]), reverse=True):
        current = updated[start:end]
        if current != edit.expected_source_text:
            raise RepairApplicationError(
                f"repair {edit.repair_id or '<pending>'} expected {edit.expected_source_text!r} at "
                f"{edit.line}:{edit.column_start}-{edit.column_end}, found {current!r}"
            )
        updated = updated[:start] + edit.replacement_source_text + updated[end:]
    return updated


def _span_to_offset(line_offsets: tuple[int, ...], line: int, column: int) -> int:
    if line < 1 or line >= len(line_offsets):
        raise RepairApplicationError(f"repair span line {line} is outside the source")
    if column < 1:
        raise RepairApplicationError(f"repair span column {column} is invalid")
    return line_offsets[line - 1] + column - 1


def _node_span(node: ast.AST) -> tuple[int, int, int, int]:
    if not all(hasattr(node, attr) for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset")):
        raise RepairApplicationError(f"AST node {type(node).__name__} is missing span information")
    return (
        int(getattr(node, "lineno")),
        int(getattr(node, "col_offset")) + 1,
        int(getattr(node, "end_lineno")),
        int(getattr(node, "end_col_offset")) + 1,
    )


def _node_text(index: PythonSourceIndex, node: ast.AST) -> str:
    return ast.get_source_segment(index.source, node) or ""


def _build_repair_edit(
    index: PythonSourceIndex,
    *,
    target_type: str,
    target_node: str,
    node: ast.AST,
    expected_old_value: str,
    replacement: str,
    repair_id: str = "",
    confidence: float = 0.0,
    expected_occurrences: int = 1,
    preconditions: list[str] | None = None,
    postconditions: list[str] | None = None,
    inverse_patch: dict[str, Any] | None = None,
    replacement_source_text: str | None = None,
) -> RepairEdit:
    line, column_start, _, column_end = _node_span(node)
    return RepairEdit(
        repair_id=repair_id,
        confidence=confidence,
        target_type=target_type,
        target_node=target_node,
        line=line,
        column_start=column_start,
        column_end=column_end,
        expected_old_value=expected_old_value,
        replacement=replacement,
        expected_occurrences=expected_occurrences,
        preconditions=preconditions or ["recorded AST span still matches the source"],
        postconditions=postconditions or [f"{target_node} updated to the canonical value"],
        inverse_patch=inverse_patch or {"expected_old_value": replacement, "replacement": expected_old_value},
        expected_source_text=_node_text(index, node),
        replacement_source_text=replacement_source_text or replacement,
    )


def _number_edits(action_kind: str, edits: list[RepairEdit], counters: dict[str, int]) -> list[RepairEdit]:
    numbered = []
    for edit in edits:
        counters[action_kind] = counters.get(action_kind, 0) + 1
        numbered.append(dataclass_replace(edit, repair_id=f"{action_kind}_{counters[action_kind]:03d}"))
    return numbered


def _alias_target_names(kind: str) -> set[str]:
    if kind == "catalog_alias":
        return {
            "catalog",
            "Catalog",
            "labware_type",
            "LabwareType",
            "rack_type",
            "RackType",
            "carrier",
            "Carrier",
            "carrier_type",
            "CarrierType",
            "forced_rack_type",
            "ForcedRackType",
            "label",
            "Label",
            "labware",
            "Labware",
            "labware_name",
            "LabwareName",
            "source_labware",
            "SourceLabware",
            "source_labware_name",
            "SourceLabwareName",
            "destination_labware",
            "DestinationLabware",
            "destination_labware_name",
            "DestinationLabwareName",
            "target_labware",
            "TargetLabware",
        }
    if kind == "labware_alias":
        return {
            "label",
            "Label",
            "labware",
            "Labware",
            "labware_name",
            "LabwareName",
            "source_labware",
            "SourceLabware",
            "source_labware_name",
            "SourceLabwareName",
            "destination_labware",
            "DestinationLabware",
            "destination_labware_name",
            "DestinationLabwareName",
            "target_labware",
            "TargetLabware",
        }
    if kind == "liquid_class_alias":
        return {"liquid_class", "LiquidClassName", "LiquidClassNameBySelection"}
    if kind == "device_alias":
        return {"device_alias", "DeviceAlias"}
    return set()


def _alias_call_names(kind: str) -> set[str]:
    if kind == "labware_alias":
        return {"labware_by_label", "labware_by_name"}
    return set()


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return type(func).__name__


def _call_supports_positional_alias(node: ast.Call, *, kind: str) -> bool:
    return _call_name(node) in _alias_call_names(kind)


def _assignment_target_names(target: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, ast.Attribute):
        names.add(target.attr)
    elif isinstance(target, ast.Subscript):
        key = target.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            names.add(key.value)
        elif isinstance(key, ast.Tuple):
            for item in key.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    names.add(item.value)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            names.update(_assignment_target_names(item))
    return names


def _alias_repair_edits(
    index: PythonSourceIndex,
    *,
    kind: str,
    project_name: str,
    base_name: str,
) -> list[RepairEdit]:
    allowed_names = _alias_target_names(kind)
    edits: list[RepairEdit] = []
    seen: set[tuple[int, int, int, str, str]] = set()
    confidence = 0.98 if kind in {"catalog_alias", "labware_alias", "liquid_class_alias", "device_alias"} else 0.9

    for node in ast.walk(index.tree):
        if isinstance(node, ast.Assign):
            target_names = set()
            for target in node.targets:
                target_names.update(_assignment_target_names(target))
            matched = sorted(target_names & allowed_names, key=str.casefold)
            if matched and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and node.value.value == project_name:
                key = _node_span(node.value) + ("python_ast_assignment", f"Assign.value[{matched[0]}]")
                if key not in seen:
                    seen.add(key)
                    edits.append(
                        _build_repair_edit(
                            index,
                            target_type="python_ast_assignment",
                            target_node=f"Assign.value[{matched[0]}]",
                            node=node.value,
                            expected_old_value=project_name,
                            replacement=base_name,
                            confidence=confidence,
                            preconditions=[f"assignment target {matched[0]!r} still matches the alias"],
                            postconditions=[f"assignment target {matched[0]!r} now uses the canonical value"],
                            inverse_patch={
                                "expected_old_value": base_name,
                                "replacement": project_name,
                                "expected_source_text": repr(base_name),
                                "replacement_source_text": repr(project_name),
                            },
                            replacement_source_text=repr(base_name),
                        )
                    )
            continue
        if isinstance(node, ast.AnnAssign):
            target_names = _assignment_target_names(node.target)
            matched = sorted(target_names & allowed_names, key=str.casefold)
            if matched and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and node.value.value == project_name:
                key = _node_span(node.value) + ("python_ast_assignment", f"Assign.value[{matched[0]}]")
                if key not in seen:
                    seen.add(key)
                    edits.append(
                        _build_repair_edit(
                            index,
                            target_type="python_ast_assignment",
                            target_node=f"Assign.value[{matched[0]}]",
                            node=node.value,
                            expected_old_value=project_name,
                            replacement=base_name,
                            confidence=confidence,
                            preconditions=[f"assignment target {matched[0]!r} still matches the alias"],
                            postconditions=[f"assignment target {matched[0]!r} now uses the canonical value"],
                            inverse_patch={
                                "expected_old_value": base_name,
                                "replacement": project_name,
                                "expected_source_text": repr(base_name),
                                "replacement_source_text": repr(project_name),
                            },
                            replacement_source_text=repr(base_name),
                        )
                    )
            continue
        if isinstance(node, ast.keyword):
            if node.arg in allowed_names and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and node.value.value == project_name:
                key = _node_span(node.value) + ("python_ast_keyword", f"Call.keyword[{node.arg}]")
                if key not in seen:
                    seen.add(key)
                    edits.append(
                        _build_repair_edit(
                            index,
                            target_type="python_ast_keyword",
                            target_node=f"Call.keyword[{node.arg}]",
                            node=node.value,
                            expected_old_value=project_name,
                            replacement=base_name,
                            confidence=confidence,
                            preconditions=[f"keyword {node.arg!r} still matches the alias"],
                            postconditions=[f"keyword {node.arg!r} now uses the canonical value"],
                            inverse_patch={
                                "expected_old_value": base_name,
                                "replacement": project_name,
                                "expected_source_text": repr(base_name),
                                "replacement_source_text": repr(project_name),
                            },
                            replacement_source_text=repr(base_name),
                        )
                    )
            continue
        if isinstance(node, ast.Dict):
            for key_node, value_node in zip(node.keys, node.values):
                key_name = None
                if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                    key_name = key_node.value
                elif isinstance(key_node, ast.Name):
                    key_name = key_node.id
                if key_name not in allowed_names:
                    continue
                if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str) or value_node.value != project_name:
                    continue
                key = _node_span(value_node) + ("python_ast_dict_value", f"Dict.value[{key_name}]")
                if key in seen:
                    continue
                seen.add(key)
                edits.append(
                    _build_repair_edit(
                        index,
                        target_type="python_ast_dict_value",
                        target_node=f"Dict.value[{key_name}]",
                        node=value_node,
                        expected_old_value=project_name,
                        replacement=base_name,
                        confidence=confidence,
                        preconditions=[f"mapping key {key_name!r} still matches the alias"],
                        postconditions=[f"mapping key {key_name!r} now uses the canonical value"],
                        inverse_patch={
                            "expected_old_value": base_name,
                            "replacement": project_name,
                            "expected_source_text": repr(base_name),
                            "replacement_source_text": repr(project_name),
                        },
                        replacement_source_text=repr(base_name),
                    )
                )
            continue
        if isinstance(node, ast.Call) and _call_supports_positional_alias(node, kind=kind):
            for arg_index, arg in enumerate(node.args):
                if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str) or arg.value != project_name:
                    continue
                key = _node_span(arg) + ("python_ast_argument", f"Call.args[{arg_index}]")
                if key in seen:
                    continue
                seen.add(key)
                edits.append(
                    _build_repair_edit(
                        index,
                        target_type="python_ast_argument",
                        target_node=f"Call.args[{arg_index}]",
                        node=arg,
                        expected_old_value=project_name,
                        replacement=base_name,
                        confidence=confidence,
                        preconditions=[f"positional argument {arg_index} still matches the alias"],
                        postconditions=[f"positional argument {arg_index} now uses the canonical value"],
                        inverse_patch={
                            "expected_old_value": base_name,
                            "replacement": project_name,
                            "expected_source_text": repr(base_name),
                            "replacement_source_text": repr(project_name),
                        },
                        replacement_source_text=repr(base_name),
                    )
                )
            continue

    edits.sort(key=lambda edit: (edit.line, edit.column_start, edit.column_end, edit.target_type, edit.target_node))
    return edits


def apply_repair_plan(
    plan: RepairPlan,
    output_path: Path,
    *,
    apply_modeling: bool = False,
) -> list[RepairAction]:
    source = plan.draft_path.read_text(encoding="utf-8")
    actions_to_apply = applicable_repair_actions(plan, apply_modeling=apply_modeling)

    edits = [edit for action in actions_to_apply for edit in action.edits]
    if edits:
        source = _apply_structured_edits(source, edits)

    for action in actions_to_apply:
        if action.kind == "tipbox_class_repair":
            class_name = action.details.get("class_name")
            if class_name:
                source = _ensure_fluentcoder_import(source, class_name)

    ensure_parent(output_path)
    output_path.write_text(source, encoding="utf-8")
    return actions_to_apply


def render_repair_markdown(plan: RepairPlan) -> str:
    data = plan.to_dict()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Fluent Repair Plan",
        "",
        f"- Generated at: `{now}`",
        f"- Draft: `{plan.draft_path}`",
        f"- Context: `{plan.context_name or 'none'}`",
        f"- Simulation JSON: `{plan.simulation_json_path or 'none'}`",
        f"- Actions: `{data['summary']['action_count']}`",
        f"- Ready: `{data['summary']['ready_count']}`",
        f"- Suggested modeling: `{data['summary']['suggested_count']}`",
        f"- Needs review: `{data['summary']['needs_review_count']}`",
        "",
    ]

    if not plan.actions:
        lines.extend(["No project-aware repairs were found.", ""])
        return "\n".join(lines)

    for action in plan.actions:
        lines.extend(
            [
                f"## {action.kind}",
                "",
                f"- Status: `{action.status}`",
                f"- Summary: {action.summary}",
            ]
        )
        if action.line:
            lines.append(f"- Line: `{action.line}`")
        if action.command_id:
            lines.append(f"- Command: `{action.command_id}`")
        if action.old:
            lines.extend(["- Old:", "", "```text", action.old, "```"])
        if action.new:
            lines.extend(["- New:", "", "```python", action.new, "```"])
        if action.edits:
            lines.extend(["- Structured edits:", "", "```json"])
            lines.append(json.dumps([edit.to_dict() for edit in action.edits], indent=2, sort_keys=True))
            lines.append("```")
        if action.details:
            lines.extend(["- Details:", "", "```json"])
            lines.append(json.dumps(action.details, indent=2, sort_keys=True))
            lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _catalog_alias_actions(index: PythonSourceIndex, context: ProjectContext | None) -> list[RepairAction]:
    source = index.source
    actions: list[RepairAction] = []
    seen: set[tuple[str, str]] = set()
    repair_counts: dict[str, int] = {}
    alias_maps = load_alias_maps()
    for kind in ALIAS_KINDS:
        action_kind = kind[:-2] if kind.endswith("es") else kind[:-1]
        for project_name, base_name in alias_maps.get(kind, {}).items():
            if not project_name or not base_name or project_name not in source:
                continue
            key = (project_name, base_name)
            if key in seen:
                continue
            seen.add(key)
            edits = _alias_repair_edits(index, kind=action_kind, project_name=project_name, base_name=base_name)
            if not edits:
                continue
            numbered_edits = _number_edits(action_kind, edits, repair_counts)
            if kind == "catalog_aliases":
                status = "ready" if _catalog_name_exists(base_name) else "needs_review"
                detail = "global catalog contains the base name" if status == "ready" else "base name was not found in the global catalog"
            else:
                status = "ready"
                detail = "alias is configured in the protocol-builder alias map"
            actions.append(
                RepairAction(
                    kind=action_kind,
                    status=status,
                    summary=(
                        f"Replace {action_kind.replace('_', ' ')} {project_name!r} with {base_name!r} "
                        f"in {len(numbered_edits)} AST-targeted span(s); {detail}."
                    ),
                    line=numbered_edits[0].line if numbered_edits else None,
                    old=project_name,
                    new=base_name,
                    edits=numbered_edits,
                    details={
                        "line_numbers": [edit.line for edit in numbered_edits],
                        "repair_ids": [edit.repair_id for edit in numbered_edits],
                        "target_nodes": [edit.target_node for edit in numbered_edits],
                        "target_types": sorted({edit.target_type for edit in numbered_edits}),
                        "project_alias": project_name,
                        "base_name": base_name,
                        "source": "config/aliases",
                    },
                )
            )
    if context is None:
        return actions
    worktable_labels = {
        str(name).strip()
        for name in list(context.manifest.get("labware_names") or [])
        + list(context.manifest.get("rack_types") or [])
        if str(name).strip()
    }
    for alias in context.manifest.get("catalog_alias_candidates", []):
        project_name = alias.get("project_name")
        base_name = alias.get("base_name")
        if not project_name or not base_name or project_name not in source:
            continue
        # Instance labels on the worktable (FilterDWP[001]) must stay in
        # TransferLabware Labware fields; they are not catalog type names.
        if project_name in worktable_labels:
            continue
        key = (project_name, base_name)
        if key in seen:
            continue
        seen.add(key)
        edits = _alias_repair_edits(index, kind="catalog_alias", project_name=project_name, base_name=base_name)
        if not edits:
            continue
        if not _catalog_name_exists(base_name):
            # Auto-mined candidates without a catalog hit are noise, not review items.
            continue
        numbered_edits = _number_edits("catalog_alias", edits, repair_counts)
        status = "ready"
        detail = "global catalog contains the base name"
        actions.append(
            RepairAction(
                kind="catalog_alias",
                status=status,
                summary=(
                    f"Replace project alias {project_name!r} with catalog name {base_name!r} "
                    f"in {len(numbered_edits)} AST-targeted span(s); {detail}."
                ),
                line=numbered_edits[0].line if numbered_edits else None,
                old=project_name,
                new=base_name,
                edits=numbered_edits,
                details={
                    "line_numbers": [edit.line for edit in numbered_edits],
                    "repair_ids": [edit.repair_id for edit in numbered_edits],
                    "target_nodes": [edit.target_node for edit in numbered_edits],
                    "target_types": sorted({edit.target_type for edit in numbered_edits}),
                    "project_alias": project_name,
                    "base_name": base_name,
                    "source": "project_context.manifest",
                },
            )
        )
    return actions


def _raw_xml_modeling_actions(index: PythonSourceIndex, simulation: dict[str, Any] | None) -> list[RepairAction]:
    source = index.source
    unsupported = set()
    if simulation:
        unsupported.update((simulation.get("unsupported_command_ids") or {}).keys())
    actions = []
    repair_counts: dict[str, int] = {}
    label_vars = _labware_label_variables(source)
    for step in _raw_xml_steps(index):
        command_id = step["command_id"]
        if unsupported and command_id not in unsupported:
            continue
        fields = _xml_fields(step["xml"])
        replacement = _replacement_for_raw_xml(command_id, fields, label_vars, step["indent"])
        if replacement:
            edit = _build_repair_edit(
                index,
                target_type="python_ast_call",
                target_node=f"Call.raw_xml_step[{command_id}]",
                node=step["node"],
                expected_old_value=command_id,
                replacement=replacement,
                confidence=0.85,
                preconditions=[f"raw_xml_step call {command_id!r} still matches the recorded span"],
                postconditions=[f"raw_xml_step call {command_id!r} replaced with a modeled fluentcoder DSL call"],
                inverse_patch={
                    "expected_old_value": replacement,
                    "replacement": step["source_line"].strip(),
                    "expected_source_text": replacement,
                    "replacement_source_text": step["source_line"].strip(),
                },
                replacement_source_text=replacement,
            )
            numbered_edits = _number_edits("model_raw_xml_step", [edit], repair_counts)
            actions.append(
                RepairAction(
                    kind="model_raw_xml_step",
                    status="suggested",
                    summary=(
                        f"Model opaque {command_id} as a fluentcoder DSL call. "
                        "Review before applying because MCA384 raw XML may encode details "
                        "that the MCA96-style DSL call does not preserve."
                    ),
                    line=step["line"],
                    old=step["source_line"].strip(),
                    new=replacement,
                    command_id=command_id,
                    edits=numbered_edits,
                    details=fields,
                )
            )
        else:
            if registry_command_support_status(command_id):
                continue
            actions.append(
                RepairAction(
                    kind="unsupported_command_gap",
                    status="needs_review",
                    summary=f"No local modeling rule exists yet for opaque command {command_id}.",
                    line=step["line"],
                    old=step["source_line"].strip(),
                    command_id=command_id,
                    details=fields,
                )
            )
    return actions


def _failure_repair_actions(index: PythonSourceIndex, simulation: dict[str, Any] | None) -> list[RepairAction]:
    if not simulation:
        return []
    failure = simulation.get("failure") or {}
    if failure.get("category") != "tip_capacity":
        return []
    details = failure.get("details") or {}
    if details.get("capacity_ul") not in (0, 0.0):
        return []
    actions = []
    repair_counts: dict[str, int] = {}
    for tipbox in _generic_tipbox_lines(index):
        class_name = _tipbox_class_for_catalog(tipbox["catalog"])
        if not class_name:
            continue
        edit = _build_repair_edit(
            index,
            target_type="python_ast_name",
            target_node="Call.func[TipBox]",
            node=tipbox["class_node"],
            expected_old_value="TipBox",
            replacement=class_name,
            confidence=0.94,
            preconditions=["TipBox call still matches the recorded span"],
            postconditions=[f"generic TipBox updated to {class_name}"],
            inverse_patch={
                "expected_old_value": class_name,
                "replacement": "TipBox",
                "expected_source_text": class_name,
                "replacement_source_text": "TipBox",
            },
            replacement_source_text=class_name,
        )
        numbered_edits = _number_edits("tipbox_class_repair", [edit], repair_counts)
        actions.append(
            RepairAction(
                kind="tipbox_class_repair",
                status="ready",
                summary=(
                    f"Replace generic TipBox with {class_name} for catalog "
                    f"{tipbox['catalog']!r}; generic TipBox has zero simulator capacity."
                ),
                line=tipbox["line"],
                old=tipbox["source_line"].strip(),
                new=tipbox["source_line"].replace("TipBox(", f"{class_name}(", 1),
                edits=numbered_edits,
                details={
                    "class_name": class_name,
                    "catalog": tipbox["catalog"],
                    "label": tipbox["label"],
                    "failure": failure,
                },
            )
        )
    return actions


def _raw_xml_steps(index: PythonSourceIndex) -> list[dict[str, Any]]:
    root = index.tree
    lines = index.lines
    out = []
    for node in ast.walk(root):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "raw_xml_step"
            and len(node.args) >= 2
        ):
            continue
        try:
            command_id = ast.literal_eval(node.args[0])
            xml = ast.literal_eval(node.args[1])
        except Exception:
            continue
        if not isinstance(command_id, str) or not isinstance(xml, str):
            continue
        source_line = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
        indent = source_line[: len(source_line) - len(source_line.lstrip())]
        out.append(
            {
                "line": node.lineno,
                "command_id": command_id,
                "xml": xml,
                "source_line": source_line,
                "indent": indent,
                "node": node,
            }
        )
    return sorted(out, key=lambda item: item["line"])


def _labware_label_variables(source: str) -> dict[str, str]:
    root = ast.parse(source)
    labels: dict[str, str] = {}
    for node in ast.walk(root):
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "place"
            and value.args
        ):
            continue
        labware_call = value.args[0]
        if not isinstance(labware_call, ast.Call) or not labware_call.args:
            continue
        label_node = labware_call.args[0]
        if isinstance(label_node, ast.Constant) and isinstance(label_node.value, str):
            labels[label_node.value] = target.id
    return labels


def _generic_tipbox_lines(index: PythonSourceIndex) -> list[dict[str, Any]]:
    root = index.tree
    lines = index.lines
    out = []
    for node in ast.walk(root):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "place":
            continue
        if not node.args:
            continue
        labware_call = node.args[0]
        if not (
            isinstance(labware_call, ast.Call)
            and isinstance(labware_call.func, ast.Name)
            and labware_call.func.id == "TipBox"
        ):
            continue
        label = ""
        if labware_call.args and isinstance(labware_call.args[0], ast.Constant):
            label = str(labware_call.args[0].value)
        catalog = ""
        for keyword in labware_call.keywords:
            if keyword.arg == "catalog" and isinstance(keyword.value, ast.Constant):
                catalog = str(keyword.value.value)
        if not catalog:
            continue
        source_line = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
        out.append(
            {
                "line": node.lineno,
                "source_line": source_line,
                "label": label,
                "catalog": catalog,
                "class_node": labware_call.func,
            }
        )
    return out


def _tipbox_class_for_catalog(catalog: str) -> str | None:
    compact = catalog.casefold().replace(" ", "")
    if "mca96" not in compact and "mca" not in compact:
        return None
    if "500ul" in compact:
        return "MCA500Box"
    if "200ul" in compact:
        return "MCA200Box"
    if "100ul" in compact:
        return "MCA100Box"
    return None


def _replacement_for_raw_xml(
    command_id: str,
    fields: dict[str, Any],
    label_vars: dict[str, str],
    indent: str,
) -> str | None:
    labware_name = fields.get("labware_name")
    variable = label_vars.get(labware_name or "")
    if command_id.endswith("PickUpTips") and variable:
        return f"{indent}head.pick_up({variable})"
    if command_id.endswith("SetTipsBack") and variable:
        return f"{indent}head.return_tips({variable})"
    if command_id.endswith("Aspirate") and variable:
        return _pipetting_replacement(indent, "aspirate", variable, fields)
    if command_id.endswith("Dispense") and variable:
        return _pipetting_replacement(indent, "dispense", variable, fields)
    return None


def _pipetting_replacement(indent: str, method: str, variable: str, fields: dict[str, Any]) -> str | None:
    volume = fields.get("volume")
    liquid_class = fields.get("liquid_class")
    if volume is None or not liquid_class:
        return None
    return f"{indent}head.{method}({variable}, {volume!r}, liquid_class={liquid_class!r})"


def _xml_fields(xml: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    fields = {
        "labware_name": _first_xml_text(root, "LabwareName"),
        "liquid_class": _first_xml_text(root, "LiquidClassName")
        or _first_xml_text(root, "LiquidClassNameBySelection"),
        "volume": _parse_number(_first_xml_text(root, "Volume")),
        "device_alias": _first_xml_text(root, "DeviceAlias"),
        "available_id": _first_xml_text(root, "AvailableID"),
        "head_positions": _first_xml_text(root, "HeadPositions"),
        "partial_columns": _parse_number(_first_xml_text(root, "PartialColumns")),
        "partial_rows": _parse_number(_first_xml_text(root, "PartialRows")),
    }
    return {key: value for key, value in fields.items() if value not in (None, "")}


def _first_xml_text(root: ET.Element, name: str) -> str:
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == name and el.text and el.text.strip():
            return el.text.strip()
    return ""


def _parse_number(value: str) -> float | int | None:
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def _load_simulation_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _catalog_name_exists(name: str) -> bool:
    if _catalog_name_exists_in_vendored_index(name):
        return True
    try:
        from fluentcoder.catalog.catalog import resolve_by_name
    except Exception:
        return False
    try:
        return resolve_by_name(name) is not None
    except Exception:
        return False


def _catalog_name_exists_in_vendored_index(name: str) -> bool:
    db_path = fluentcoder_root() / "fluentcoder" / "catalog" / "install_index.db"
    if not db_path.exists():
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT 1 FROM components WHERE name = ? LIMIT 1", (name,)).fetchone()
    except sqlite3.DatabaseError:
        return False
    finally:
        if conn is not None:
            conn.close()
    return row is not None


def _ensure_fluentcoder_import(source: str, symbol: str) -> str:
    lines = source.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("from fluentcoder import "):
            continue
        prefix = line[: len(line) - len(line.lstrip())]
        names = [name.strip() for name in stripped[len("from fluentcoder import "):].split(",")]
        if symbol not in names:
            names.append(symbol)
        names = sorted(name for name in names if name)
        lines[index] = f"{prefix}from fluentcoder import {', '.join(names)}"
        return "\n".join(lines) + "\n"
    return f"from fluentcoder import {symbol}\n{source}"
