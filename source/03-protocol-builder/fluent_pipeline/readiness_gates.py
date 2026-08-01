"""Helpers for the machine-readable readiness gate registry."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
import json
from typing import Any, Mapping


REQUIRED_OFFLINE_GATE = "required_offline_gate"
OPTIONAL_DIAGNOSTIC = "optional_diagnostic"

CLASSIFICATION_LABELS = {
    REQUIRED_OFFLINE_GATE: "Required offline gate",
    OPTIONAL_DIAGNOSTIC: "Optional diagnostic",
}

_REGISTRY_RESOURCE = ("data", "readiness_gate_registry.json")
_REQUEST_SPEC_APPROVAL_PATH_ALIASES = {
    "deck_layout_consistent": (
        "review.deck_layout_changes_approved",
        "review.deck_changes_approved",
    ),
}
_APPROVAL_CONTEXT_KEY_ALIASES = {
    "deck_layout_consistent": ("deck_changes",),
}


def _read_registry_payload() -> dict[str, Any]:
    resource = resources.files(__package__).joinpath(*_REGISTRY_RESOURCE)
    return json.loads(resource.read_text(encoding="utf-8"))


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_artifact_inputs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            items.append(text)
    return tuple(items)


def _lookup_path(payload: Mapping[str, Any] | None, path: str) -> Any:
    if not isinstance(payload, Mapping):
        return None
    current: Any = payload
    for segment in str(path or "").split("."):
        if not segment or not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _approval_value_is_true(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key in ("approved", "value", "enabled", "status"):
            if key in value:
                return _approval_value_is_true(value[key])
        return any(_approval_value_is_true(item) for item in value.values())
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"true", "yes", "y", "1", "approved", "reviewed", "passed", "ok"}:
            return True
        if text in {"false", "no", "n", "0", "rejected", "blocked", "failed", "pending", "unknown"}:
            return False
    return bool(value)


@dataclass(frozen=True)
class ReadinessGateDefinition:
    gate_number: int
    id: str
    name: str
    classification: str
    description: str
    implementation: str | None = None
    review_policy: str | None = None
    approval_key: str | None = None
    cli_flag: str | None = None
    mcp_capability: str | None = None
    request_spec_path: str | None = None
    remediation: str | None = None
    artifact_inputs: tuple[str, ...] = ()

    @property
    def display_label(self) -> str:
        return f"Gate {self.gate_number}"

    @property
    def gate_label(self) -> str:
        return self.display_label

    @property
    def classification_label(self) -> str:
        return CLASSIFICATION_LABELS[self.classification]

    @property
    def is_required_offline_gate(self) -> bool:
        return self.classification == REQUIRED_OFFLINE_GATE

    @property
    def is_optional_diagnostic(self) -> bool:
        return self.classification == OPTIONAL_DIAGNOSTIC

    @property
    def approval_context_key(self) -> str | None:
        if not self.approval_key:
            return None
        return f"{self.approval_key}_approved"

    def capability_record(self) -> dict[str, Any]:
        return {
            "number": self.gate_number,
            "id": self.id,
            "name": self.name,
            "classification": self.classification,
            "description": self.description,
            "implementation": self.implementation,
            "review_policy": self.review_policy,
            "approval_key": self.approval_key,
            "cli_flag": self.cli_flag,
            "mcp_capability": self.mcp_capability,
            "request_spec_path": self.request_spec_path,
            "remediation": self.remediation,
            "artifact_inputs": list(self.artifact_inputs),
        }

    def typescript_record(self) -> dict[str, Any]:
        return {
            "gateNumber": self.gate_number,
            "id": self.id,
            "gate": self.gate_label,
            "name": self.name,
            "classification": self.classification,
            "description": self.description,
            "implementation": self.implementation,
            "reviewPolicy": self.review_policy,
            "approvalKey": self.approval_key,
            "cliFlag": self.cli_flag,
            "mcpCapability": self.mcp_capability,
            "requestSpecPath": self.request_spec_path,
            "remediation": self.remediation,
            "artifactInputs": list(self.artifact_inputs),
        }


@lru_cache(maxsize=1)
def readiness_gate_registry_version() -> str:
    payload = _read_registry_payload()
    return str(payload.get("version") or "").strip()


@lru_cache(maxsize=1)
def readiness_gates() -> tuple[ReadinessGateDefinition, ...]:
    payload = _read_registry_payload()
    raw_gates = payload.get("gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise ValueError("Readiness gate registry is missing gates.")
    gates: list[ReadinessGateDefinition] = []
    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    for index, raw in enumerate(raw_gates, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Readiness gate registry entry #{index} is not an object.")
        gate = ReadinessGateDefinition(
            gate_number=int(raw["number"]),
            id=str(raw["id"]),
            name=str(raw["name"]),
            classification=str(raw["classification"]),
            description=str(raw["description"]),
            implementation=_clean_text(raw.get("implementation")),
            review_policy=_clean_text(raw.get("review_policy")),
            approval_key=_clean_text(raw.get("approval_key")),
            cli_flag=_clean_text(raw.get("cli_flag")),
            mcp_capability=_clean_text(raw.get("mcp_capability")),
            request_spec_path=_clean_text(raw.get("request_spec_path")),
            remediation=_clean_text(raw.get("remediation")),
            artifact_inputs=_clean_artifact_inputs(raw.get("artifact_inputs")),
        )
        if gate.gate_number != index:
            raise ValueError(
                "Readiness gate registry numbers must stay consecutive and sorted: "
                f"expected Gate {index}, found {gate.gate_number}."
            )
        if gate.id in seen_ids:
            raise ValueError(f"Duplicate readiness gate id {gate.id!r}.")
        if gate.gate_number in seen_numbers:
            raise ValueError(f"Duplicate readiness gate number {gate.gate_number}.")
        if gate.classification not in CLASSIFICATION_LABELS:
            raise ValueError(
                f"Unsupported readiness gate classification {gate.classification!r} "
                f"for {gate.id!r}."
            )
        if gate.id == "deck_layout_consistent":
            required_fields = [
                ("implementation", gate.implementation),
                ("review_policy", gate.review_policy),
                ("approval_key", gate.approval_key),
                ("cli_flag", gate.cli_flag),
                ("mcp_capability", gate.mcp_capability),
                ("request_spec_path", gate.request_spec_path),
                ("remediation", gate.remediation),
            ]
            missing = [field_name for field_name, value in required_fields if not value]
            if not gate.artifact_inputs:
                missing.append("artifact_inputs")
            if missing:
                missing_fields = ", ".join(missing)
                raise ValueError(
                    "deck_layout_consistent must declare capability metadata for the "
                    f"cross-system contract: {missing_fields}."
                )
        seen_ids.add(gate.id)
        seen_numbers.add(gate.gate_number)
        gates.append(gate)
    return tuple(gates)


def readiness_gate(gate_id: str) -> ReadinessGateDefinition:
    for gate in readiness_gates():
        if gate.id == gate_id:
            return gate
    raise KeyError(f"Unknown readiness gate id {gate_id!r}.")


def registered_readiness_gate_evaluators() -> tuple[Any, ...]:
    """Return statically registered evaluators after checking registry parity.

    This deliberately imports a fixed registry module instead of discovering
    plugins, entry points, or files.  A migrated gate must declare the same
    implementation and artifact inputs in both reviewed registry layers.
    """
    from .gates import readiness_evaluator_registry

    definitions = {gate.id: gate for gate in readiness_gates()}
    evaluators = readiness_evaluator_registry()
    evaluator_ids = {evaluator.gate_id for evaluator in evaluators}
    for definition in definitions.values():
        if (
            definition.implementation
            and definition.implementation.startswith("fluent_pipeline.gates.")
            and definition.id not in evaluator_ids
        ):
            raise ValueError(
                f"Readiness gate {definition.id!r} declares a gate evaluator but is not registered."
            )
    for evaluator in evaluators:
        definition = definitions.get(evaluator.gate_id)
        if definition is None:
            raise ValueError(
                f"Readiness evaluator {evaluator.gate_id!r} has no registry gate definition."
            )
        if definition.implementation != evaluator.implementation:
            raise ValueError(
                f"Readiness gate {evaluator.gate_id!r} implementation mismatch: "
                f"registry={definition.implementation!r}, evaluator={evaluator.implementation!r}."
            )
        if definition.artifact_inputs != evaluator.artifact_inputs:
            raise ValueError(
                f"Readiness gate {evaluator.gate_id!r} artifact-input mismatch: "
                f"registry={definition.artifact_inputs!r}, evaluator={evaluator.artifact_inputs!r}."
            )
    return evaluators


def readiness_gate_request_spec_paths(gate_id: str) -> tuple[str, ...]:
    gate = readiness_gate(gate_id)
    paths = [gate.request_spec_path] if gate.request_spec_path else []
    paths.extend(_REQUEST_SPEC_APPROVAL_PATH_ALIASES.get(gate_id, ()))
    return tuple(dict.fromkeys(path for path in paths if path))


def readiness_gate_approval_context_keys(gate_id: str) -> tuple[str, ...]:
    gate = readiness_gate(gate_id)
    keys = [gate.approval_context_key] if gate.approval_context_key else []
    keys.extend(f"{alias}_approved" for alias in _APPROVAL_CONTEXT_KEY_ALIASES.get(gate_id, ()))
    return tuple(dict.fromkeys(key for key in keys if key))


def readiness_gate_request_spec_approved(
    request_spec: Mapping[str, Any] | None,
    gate_id: str,
) -> bool:
    for path in readiness_gate_request_spec_paths(gate_id):
        if _approval_value_is_true(_lookup_path(request_spec, path)):
            return True
    return False


def active_validation_gate_tuples() -> tuple[tuple[str, str, str], ...]:
    return tuple((gate.id, gate.gate_label, gate.name) for gate in readiness_gates())


def required_offline_gate_count() -> int:
    return sum(1 for gate in readiness_gates() if gate.is_required_offline_gate)


def optional_diagnostic_gate_count() -> int:
    return sum(1 for gate in readiness_gates() if gate.is_optional_diagnostic)


def render_readiness_gate_registry_markdown(
    *,
    registry_path: str = "fluent_pipeline/data/readiness_gate_registry.json",
) -> str:
    fluent_gate = readiness_gate("fluent_context_check")
    lines = [
        "# Readiness Gate Registry",
        "",
        f"Generated from `{registry_path}`. Do not edit by hand.",
        "",
        "## Active Summary",
        "",
        f"- Required offline ready-to-import gates: `{required_offline_gate_count()}`",
        f"- Optional diagnostics: `{optional_diagnostic_gate_count()}`",
        f"- Total active entries: `{len(readiness_gates())}`",
        "- Stable IDs are the contract; gate numbers are display labels only.",
        "",
        f"`{fluent_gate.gate_label}` is an optional FluentControl import/load diagnostic. "
        "It is not required for offline `ready_to_import` status; use it when a live provider "
        "is configured or manually open the generated artifact in Script Editor instead.",
        "",
        "## Active Gates",
        "",
        "| Gate | ID | Classification | Description | Implementation | Review Policy | Approval Key | CLI Flag | MCP Capability | Request Spec Path | Remediation | Artifact Inputs |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for gate in readiness_gates():
        artifact_inputs = ", ".join(gate.artifact_inputs)
        lines.append(
            "| "
            f"{_markdown_cell(gate.gate_label)} | "
            f"{_markdown_cell(gate.id, code=True)} | "
            f"{_markdown_cell(gate.classification_label)} | "
            f"{_markdown_cell(gate.description)} | "
            f"{_markdown_cell(gate.implementation, code=True)} | "
            f"{_markdown_cell(gate.review_policy)} | "
            f"{_markdown_cell(gate.approval_key, code=True)} | "
            f"{_markdown_cell(gate.cli_flag, code=True)} | "
            f"{_markdown_cell(gate.mcp_capability, code=True)} | "
            f"{_markdown_cell(gate.request_spec_path, code=True)} | "
            f"{_markdown_cell(gate.remediation, code=True)} | "
            f"{_markdown_cell(artifact_inputs, code=True)} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_readiness_gate_registry_typescript(
    *,
    registry_path: str = "../03-protocol-builder/fluent_pipeline/data/readiness_gate_registry.json",
) -> str:
    records = [gate.typescript_record() for gate in readiness_gates()]
    payload = json.dumps(records, indent=2)
    return "\n".join(
        [
            f"// Generated from {registry_path}. Do not edit by hand.",
            "// Stable IDs are the contract; gate numbers are display labels only.",
            "",
            "export type ReadinessGateDefinition = {",
            "  gateNumber: number;",
            "  id: string;",
            "  gate: string;",
            "  name: string;",
            '  classification: "required_offline_gate" | "optional_diagnostic";',
            "  description: string;",
            "  implementation: string | null;",
            "  reviewPolicy: string | null;",
            "  approvalKey: string | null;",
            "  cliFlag: string | null;",
            "  mcpCapability: string | null;",
            "  requestSpecPath: string | null;",
            "  remediation: string | null;",
            "  artifactInputs: string[];",
            "};",
            "",
            f"export const READINESS_GATE_DEFINITIONS: ReadinessGateDefinition[] = {payload};",
            "",
        ]
    )


def _markdown_cell(value: Any, *, code: bool = False) -> str:
    if value in (None, "", [], ()):  # pragma: no branch - tiny helper
        return ""
    text = str(value)
    if isinstance(value, (list, tuple)):
        text = ", ".join(str(item) for item in value if str(item).strip())
    text = text.replace("|", "\\|").replace("\n", "<br>")
    return f"`{text}`" if code else text
