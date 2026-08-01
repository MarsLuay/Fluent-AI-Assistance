"""Versioned schema, validation, and migrations for Tecan Protocol IR."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from fluentcoder.expressions import (
    canonical_expression_key,
    expression_to_mapping,
    expression_from_mapping,
    loop_count_expression_error,
    parse_expression,
    parse_or_preserve_source_expression,
    registered_expression_field_paths,
    registered_expression_keys,
    render_expression,
    walk_expression_values,
)

PROTOCOL_IR_V1_VERSION = "tecan.protocol_ir.v1"
PROTOCOL_IR_V1_BUNDLE_VERSION = "tecan.protocol_ir.bundle.v1"
CURRENT_PROTOCOL_IR_VERSION = "tecan.protocol_ir.v2"
CURRENT_PROTOCOL_IR_BUNDLE_VERSION = "tecan.protocol_ir.bundle.v2"
PROTOCOL_IR_V1_SCHEMA_ID = "https://fluent-ai-assistance.local/schemas/protocol_ir.v1.schema.json"
PROTOCOL_IR_V1_BUNDLE_SCHEMA_ID = "https://fluent-ai-assistance.local/schemas/protocol_ir.bundle.v1.schema.json"
PROTOCOL_IR_SCHEMA_ID = "https://fluent-ai-assistance.local/schemas/protocol_ir.v2.schema.json"
PROTOCOL_IR_BUNDLE_SCHEMA_ID = "https://fluent-ai-assistance.local/schemas/protocol_ir.bundle.v2.schema.json"
EXPRESSION_SCHEMA_VERSION = "fluent_control.expression.v1"
FC_VAR_PREFIX = "@fc:"
_FC_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOOP_COUNT_EXPRESSION_KEYS = frozenset(
    {
        "number_of_loops_expression",
        "loop_count_expression",
        "iterations_expression",
        "count_expression",
    }
)
_REGISTERED_LEGACY_KEYS: dict[str, tuple[str, ...]] = {
    "Value": ("value",),
    "Condition": ("condition",),
    "NumberOfLoops": ("number_of_loops", "iterations", "loop_count", "count"),
    "Position": ("position",),
    "Volume": ("volume_ul", "volume"),
    "Volumes/string": ("volumes",),
    "Cycles": ("cycles",),
    "WellOffset": ("well_offset",),
    "Duration": ("duration", "duration_seconds"),
    "Delay": ("delay",),
    "Site": ("site", "destination_site", "to_site"),
    "Source": ("source",),
    "DutyCycle": ("duty_cycle",),
    "ChargeCondition": ("charge_condition",),
}
_SITE_EXPRESSION_KEY = "site_expression"
_SITE_EXPRESSION_ALIAS_KEYS = ("destination_site_expression", "to_site_expression")
_SITE_EXPRESSION_KEYS = (_SITE_EXPRESSION_KEY, *_SITE_EXPRESSION_ALIAS_KEYS)


class Operation(str, Enum):
    ADD_LABWARE = "add_labware"
    LOAD_LABWARE = "load_labware"
    INITIALIZE_DEVICE = "initialize_device"
    MOVE_PLATE = "move_plate"
    GET_HEAD_ADAPTER = "get_head_adapter"
    DROP_HEAD_ADAPTER = "drop_head_adapter"
    PICK_UP_TIPS = "pick_up_tips"
    SET_TIPS_BACK = "set_tips_back"
    DROP_TIPS = "drop_tips"
    MCA384_GET_TIPS = "mca384_get_tips"
    MCA384_DROP_TIPS = "mca384_drop_tips"
    LIHA_GET_TIPS = "liha_get_tips"
    LIHA_DROP_TIPS = "liha_drop_tips"
    ASPIRATE = "aspirate"
    DISPENSE = "dispense"
    MIX = "mix"
    MCA384_MIX = "mca384_mix"
    LIHA_ASPIRATE = "liha_aspirate"
    LIHA_DISPENSE = "liha_dispense"
    LIHA_MIX = "liha_mix"
    WASH = "wash"
    READ_WORKLIST = "read_worklist"
    LOOP_OVER_WELLS = "loop_over_wells"
    CONDITIONAL_BRANCH = "conditional_branch"
    DEFAULT_BRANCH = "default_branch"
    QUERY_VARIABLE = "query_variable"
    SET_VARIABLE = "set_variable"
    SET_REMAINING_RUNTIME = "set_remaining_runtime"
    RUNTIME_VARIABLE_PROMPT = "runtime_variable_prompt"
    PROMPT_USER = "prompt_user"
    CALL_SUBROUTINE = "call_subroutine"
    EXECUTE_APPLICATION = "execute_application"
    EXECUTE_VB_SCRIPT = "execute_vb_script"
    APPLICATION_DRIVER_MACRO = "application_driver_macro"
    COMMENT = "comment"


OPERATION_VALUES = tuple(operation.value for operation in Operation)

OPERATION_ALIASES = {
    "return_tips": Operation.SET_TIPS_BACK.value,
    "drop_head": Operation.DROP_HEAD_ADAPTER.value,
    "mount_head_adapter": Operation.GET_HEAD_ADAPTER.value,
    "mount_adapter": Operation.GET_HEAD_ADAPTER.value,
    "drop_adapter": Operation.DROP_HEAD_ADAPTER.value,
    "pick_up": Operation.PICK_UP_TIPS.value,
    "pickup_tips": Operation.PICK_UP_TIPS.value,
}

LABWARE_TARGET_OPERATIONS = {
    Operation.ADD_LABWARE.value,
    Operation.LOAD_LABWARE.value,
    Operation.PICK_UP_TIPS.value,
    Operation.SET_TIPS_BACK.value,
    Operation.DROP_TIPS.value,
    Operation.MCA384_GET_TIPS.value,
    Operation.MCA384_DROP_TIPS.value,
    Operation.LIHA_GET_TIPS.value,
    Operation.LIHA_DROP_TIPS.value,
    Operation.ASPIRATE.value,
    Operation.DISPENSE.value,
    Operation.MIX.value,
    Operation.MCA384_MIX.value,
    Operation.LIHA_ASPIRATE.value,
    Operation.LIHA_DISPENSE.value,
    Operation.LIHA_MIX.value,
    Operation.MOVE_PLATE.value,
    Operation.READ_WORKLIST.value,
}

VOLUME_OPERATIONS = {
    Operation.ASPIRATE.value,
    Operation.DISPENSE.value,
    Operation.MIX.value,
    Operation.MCA384_MIX.value,
    Operation.LIHA_ASPIRATE.value,
    Operation.LIHA_DISPENSE.value,
    Operation.LIHA_MIX.value,
}

LIQUID_CLASS_OPERATIONS = {
    Operation.ASPIRATE.value,
    Operation.DISPENSE.value,
    Operation.MIX.value,
    Operation.MCA384_MIX.value,
    Operation.LIHA_ASPIRATE.value,
    Operation.LIHA_DISPENSE.value,
    Operation.LIHA_MIX.value,
}

ROOT_REQUIRED_TYPES = {
    "ir_version": str,
    "id": str,
    "protocol": dict,
    "source": dict,
    "worktable": dict,
    "labware": list,
    "reagents": list,
    "liquid_classes": list,
    "variables": list,
    "worklists": list,
    "dependencies": list,
    "safety_assumptions": list,
    "steps": list,
}

LIST_FIELDS = {
    "labware",
    "reagents",
    "liquid_classes",
    "variables",
    "worklists",
    "dependencies",
    "safety_assumptions",
    "steps",
}

PROTOCOL_IR_MIGRATIONS: dict[str, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {}
PROTOCOL_IR_SCHEMAS: dict[str, dict[str, Any]] = {}
PROTOCOL_IR_VERSION_DESCRIPTIONS: dict[str, str] = {
    PROTOCOL_IR_V1_VERSION: "Canonical Tecan protocol IR v1 with scalar expression-capable fields.",
    CURRENT_PROTOCOL_IR_VERSION: "Canonical Tecan protocol IR v2 with typed FluentControl expression objects.",
}


@dataclass(frozen=True)
class ProtocolIRIssue:
    path: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message, "severity": self.severity}


class ProtocolIRValidationError(ValueError):
    """Raised when protocol IR fails schema validation."""

    def __init__(self, issues: list[ProtocolIRIssue]):
        self.issues = issues
        message = "; ".join(f"{issue.path}: {issue.message}" for issue in issues[:8])
        if len(issues) > 8:
            message += f"; {len(issues) - 8} more issue(s)"
        super().__init__(message or "Protocol IR validation failed")


@dataclass(frozen=True)
class ProtocolIRSchemaInfo:
    """Public metadata for one registered protocol IR schema version."""

    version: str
    schema_id: str
    current: bool
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "schema_id": self.schema_id,
            "current": self.current,
            "description": self.description,
        }


@dataclass(frozen=True)
class OperationSpec:
    """Typed operation contract used by validation, JSON Schema, and docs."""

    operation: str
    label: str
    requires_labware_target: bool = False
    requires_volume_ul: bool = False
    requires_liquid_class: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "label": self.label,
            "requires_labware_target": self.requires_labware_target,
            "requires_volume_ul": self.requires_volume_ul,
            "requires_liquid_class": self.requires_liquid_class,
        }


def protocol_ir_json_schema(version: str = CURRENT_PROTOCOL_IR_VERSION) -> dict[str, Any]:
    """Return the JSON Schema for a protocol IR version."""
    if not PROTOCOL_IR_SCHEMAS:
        _register_builtin_schemas()
    schema = PROTOCOL_IR_SCHEMAS.get(version)
    if schema is None:
        raise ValueError(f"No JSON Schema registered for protocol IR version {version!r}")
    return copy.deepcopy(schema)


def protocol_ir_bundle_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for a protocol IR bundle document."""
    return copy.deepcopy(PROTOCOL_IR_BUNDLE_V2_JSON_SCHEMA)


def protocol_ir_schema_versions() -> list[ProtocolIRSchemaInfo]:
    """Return registered protocol IR schema versions."""
    if not PROTOCOL_IR_SCHEMAS:
        _register_builtin_schemas()
    return [
        ProtocolIRSchemaInfo(
            version=version,
            schema_id=schema.get("$id", ""),
            current=version == CURRENT_PROTOCOL_IR_VERSION,
            description=PROTOCOL_IR_VERSION_DESCRIPTIONS.get(version, ""),
        )
        for version, schema in sorted(PROTOCOL_IR_SCHEMAS.items())
    ]


def operation_specs() -> list[OperationSpec]:
    """Return the operation enum with required field contracts."""
    return [
        OperationSpec(
            operation=operation,
            label=operation_name(operation),
            requires_labware_target=operation in LABWARE_TARGET_OPERATIONS,
            requires_volume_ul=operation in VOLUME_OPERATIONS,
            requires_liquid_class=operation in LIQUID_CLASS_OPERATIONS,
        )
        for operation in OPERATION_VALUES
    ]


def protocol_ir_schema_markdown(version: str = CURRENT_PROTOCOL_IR_VERSION) -> str:
    """Render concise schema documentation for humans and Codex."""
    schema = protocol_ir_json_schema(version)
    lines = [
        f"# Protocol IR Schema: {version}",
        "",
        f"- Schema ID: `{schema.get('$id', '')}`",
        f"- JSON Schema draft: `{schema.get('$schema', '')}`",
        "- Additional root properties are allowed for forward-compatible annotations.",
        "",
        "## Required Root Fields",
        "",
    ]
    for key in schema.get("required", []):
        prop = schema.get("properties", {}).get(key, {})
        lines.append(f"- `{key}`: {_schema_type_label(prop)}")

    if "expression" in schema.get("$defs", {}):
        lines.extend(
            [
                "",
                "## Typed Expression Fields",
                "",
                f"- Expression schema version: `{EXPRESSION_SCHEMA_VERSION}`.",
                "- Variable defaults: `variables[].default_expression`.",
                "- Variable assignments: `steps[].parameters.value_expression` and `variables[].assignments[].value_expression`.",
                "- Conditions: `steps[].parameters.condition_expression`.",
                "- Volumes: `steps[].volume_ul_expression` and `steps[].parameters.volume_ul_expression`.",
                "- Positions and loop-like counts: `*_expression` companions such as `position_expression`, `site_expression`, `destination_site_expression`, `loop_count_expression`, and `number_of_loops_expression`.",
                "- `*_expression` fields are authoritative in v2. Any accompanying legacy scalar is a compatibility projection and must agree exactly.",
                "- New v2 documents may omit legacy scalar projections; emit them only when adapting to older interfaces.",
                "",
            ]
        )

    lines.extend(["", "## Operation Enum", ""])
    for spec in operation_specs():
        requirements = []
        if spec.requires_labware_target:
            requirements.append("labware target")
        if spec.requires_volume_ul:
            requirements.append("volume_ul")
        if spec.requires_liquid_class:
            requirements.append("liquid_class")
        suffix = f" requires {', '.join(requirements)}" if requirements else " no extra required fields"
        lines.append(f"- `{spec.operation}`: {spec.label};{suffix}.")

    lines.extend(
        [
            "",
            "## Migrations",
            "",
            f"- Current protocol version: `{CURRENT_PROTOCOL_IR_VERSION}`",
            "- Register future migrations with `register_protocol_ir_migration(from_version, to_version, migrator, schema=...)`.",
            "- Migrations run one version edge at a time and must return a JSON-serializable dict.",
            "",
        ]
    )
    return "\n".join(lines)


def register_protocol_ir_migration(
    from_version: str,
    to_version: str,
    migrator: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    schema: dict[str, Any] | None = None,
    description: str = "",
) -> None:
    """Register one directed migration edge and optional schema for its target."""
    if not PROTOCOL_IR_SCHEMAS:
        _register_builtin_schemas()
    if not from_version or not to_version:
        raise ValueError("Migration versions must be non-empty strings")
    if from_version == to_version:
        raise ValueError("Migration versions must be different")
    PROTOCOL_IR_MIGRATIONS[from_version] = (to_version, migrator)
    if schema is not None:
        PROTOCOL_IR_SCHEMAS[to_version] = copy.deepcopy(schema)
    if description:
        PROTOCOL_IR_VERSION_DESCRIPTIONS[to_version] = description


def protocol_ir_migration_path(from_version: str, to_version: str) -> list[str]:
    """Return the registered migration path from one version to another."""
    if not PROTOCOL_IR_SCHEMAS:
        _register_builtin_schemas()
    if from_version == to_version:
        return [to_version]
    path = [from_version]
    current = from_version
    seen = set()
    while current != to_version:
        if current in seen or current not in PROTOCOL_IR_MIGRATIONS:
            raise ValueError(f"No migration path from {from_version!r} to {to_version!r}")
        seen.add(current)
        current = PROTOCOL_IR_MIGRATIONS[current][0]
        path.append(current)
    return path


def validate_protocol_ir_document(
    payload: dict[str, Any],
    *,
    strict: bool = True,
    normalize: bool = False,
) -> list[ProtocolIRIssue]:
    """Validate a single protocol IR as written, optionally after migration."""
    if normalize:
        try:
            payload = migrate_protocol_ir(payload, validate=False)
        except Exception as exc:
            return [ProtocolIRIssue("$", str(exc))]
    return validate_protocol_ir(payload, strict=strict)


def validate_protocol_ir_bundle_document(
    payload: dict[str, Any],
    *,
    strict: bool = True,
    normalize: bool = False,
) -> list[ProtocolIRIssue]:
    """Validate a bundle as written, optionally after migrating contained protocols."""
    if normalize:
        try:
            payload = migrate_protocol_ir_bundle(payload, validate=False)
        except Exception as exc:
            return [ProtocolIRIssue("$", str(exc))]
    return validate_protocol_ir_bundle(payload, strict=strict)


def migrate_protocol_ir(
    payload: dict[str, Any],
    *,
    to_version: str = CURRENT_PROTOCOL_IR_VERSION,
    validate: bool = True,
) -> dict[str, Any]:
    """Migrate and normalize a protocol IR payload to the requested version."""
    if not PROTOCOL_IR_SCHEMAS:
        _register_builtin_schemas()
    if not isinstance(payload, dict):
        raise ProtocolIRValidationError([ProtocolIRIssue("$", "payload must be an object")])

    version = payload.get("ir_version")
    if version in (None, "") and _looks_like_protocol_ir(payload):
        version = PROTOCOL_IR_V1_VERSION
    if version in {PROTOCOL_IR_V1_BUNDLE_VERSION, CURRENT_PROTOCOL_IR_BUNDLE_VERSION}:
        raise ValueError("Expected a single protocol IR, got a protocol IR bundle")
    if not isinstance(version, str):
        raise ValueError("Protocol IR is missing ir_version and does not look like a v1 payload")

    if version == PROTOCOL_IR_V1_VERSION:
        migrated = _normalize_v1(payload)
    elif version == CURRENT_PROTOCOL_IR_VERSION:
        migrated = _normalize_v2(payload)
    else:
        migrated = copy.deepcopy(payload)

    if version == CURRENT_PROTOCOL_IR_VERSION and to_version == PROTOCOL_IR_V1_VERSION:
        # Downgrading is an explicit compatibility boundary. Validate the
        # authoritative V2 expressions before deriving any legacy scalars.
        assert_valid_protocol_ir(migrated)
        migrated = _project_v2_to_v1(migrated)
    elif version != to_version:
        migrated = _run_registered_migrations(migrated, version, to_version)

    if to_version == PROTOCOL_IR_V1_VERSION:
        migrated = _normalize_v1(migrated)
    elif to_version == CURRENT_PROTOCOL_IR_VERSION:
        migrated = _normalize_v2(migrated)
    if validate and to_version == CURRENT_PROTOCOL_IR_VERSION:
        assert_valid_protocol_ir(migrated)
    elif validate and to_version == PROTOCOL_IR_V1_VERSION:
        _assert_v1_payload(migrated)
    elif validate:
        _assert_registered_future_payload(migrated, to_version)
    return migrated


def migrate_protocol_ir_bundle(
    payload: dict[str, Any],
    *,
    to_version: str = CURRENT_PROTOCOL_IR_VERSION,
    validate: bool = True,
) -> dict[str, Any]:
    """Normalize a ZEIA-derived bundle and migrate every contained protocol."""
    if not PROTOCOL_IR_SCHEMAS:
        _register_builtin_schemas()
    if not isinstance(payload, dict):
        raise ProtocolIRValidationError([ProtocolIRIssue("$", "bundle payload must be an object")])
    bundle_version = payload.get("ir_version")
    if bundle_version not in {PROTOCOL_IR_V1_BUNDLE_VERSION, CURRENT_PROTOCOL_IR_BUNDLE_VERSION}:
        raise ValueError(
            f"Expected {CURRENT_PROTOCOL_IR_BUNDLE_VERSION}, got {payload.get('ir_version')!r}"
        )
    bundle = copy.deepcopy(payload)
    protocols = bundle.get("protocols") or []
    if not isinstance(protocols, list):
        raise ProtocolIRValidationError([ProtocolIRIssue("$.protocols", "must be an array")])
    bundle["protocols"] = [
        migrate_protocol_ir(protocol, to_version=to_version, validate=validate)
        for protocol in protocols
    ]
    bundle["protocol_count"] = len(bundle["protocols"])
    bundle["ir_version"] = (
        PROTOCOL_IR_V1_BUNDLE_VERSION
        if to_version == PROTOCOL_IR_V1_VERSION
        else CURRENT_PROTOCOL_IR_BUNDLE_VERSION
    )
    return bundle


def validate_protocol_ir(payload: dict[str, Any], *, strict: bool = True) -> list[ProtocolIRIssue]:
    """Return schema issues for a normalized protocol IR payload."""
    issues = _validate_protocol_ir_v1(payload, strict=strict)
    semantic_context, variable_types = _expression_semantic_context_for_ir(payload)
    if payload.get("ir_version") != CURRENT_PROTOCOL_IR_VERSION:
        issues.append(
            ProtocolIRIssue(
                "$.ir_version",
                f"must equal {CURRENT_PROTOCOL_IR_VERSION!r}",
            )
        )
    validated_expression_paths: set[str] = set()

    def validate_expression(
        path: str,
        value: Any,
        *,
        expected_type: Any = None,
        assignment_target: str | None = None,
        enforce_semantics: bool = True,
    ) -> None:
        validated_expression_paths.add(path)
        if not enforce_semantics:
            _validate_expression(issues, path, value)
            return
        _validate_expression_with_semantics(
            issues,
            path,
            value,
            semantic_context=semantic_context,
            expected_type=expected_type,
            assignment_target=assignment_target,
        )

    for variable_index, variable in enumerate(payload.get("variables") or []):
        if not isinstance(variable, dict):
            continue
        variable_name = str(variable.get("name") or "")
        expected_type = variable_types.get(variable_name)
        for key in ("default_expression", "value_expression"):
            if key in variable:
                validate_expression(
                    f"$.variables[{variable_index}].{key}",
                    variable.get(key),
                    expected_type=expected_type,
                    assignment_target=variable_name or None,
                )
        value_expressions = variable.get("value_expressions")
        if isinstance(value_expressions, list):
            for value_index, expression in enumerate(value_expressions):
                validate_expression(
                    f"$.variables[{variable_index}].value_expressions[{value_index}]",
                    expression,
                    expected_type=expected_type,
                    assignment_target=variable_name or None,
                )
        for assignment_index, assignment in enumerate(variable.get("assignments") or []):
            if isinstance(assignment, dict) and "value_expression" in assignment:
                validate_expression(
                    f"$.variables[{variable_index}].assignments[{assignment_index}].value_expression",
                    assignment.get("value_expression"),
                    expected_type=expected_type,
                    assignment_target=variable_name or None,
                )
    for labware_index, labware in enumerate(payload.get("labware") or []):
        if not isinstance(labware, dict):
            continue
        if "position_expression" in labware:
            validate_expression(
                f"$.labware[{labware_index}].position_expression",
                labware.get("position_expression"),
                expected_type=("number", "string"),
            )
        initial = labware.get("initial_contents")
        if isinstance(initial, dict) and "volume_ul_expression" in initial:
            validate_expression(
                f"$.labware[{labware_index}].initial_contents.volume_ul_expression",
                initial.get("volume_ul_expression"),
                expected_type="number",
            )
    for step_index, step in enumerate(payload.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if "volume_ul_expression" in step:
            validate_expression(
                f"$.steps[{step_index}].volume_ul_expression",
                step.get("volume_ul_expression"),
                expected_type="number",
            )
        params = step.get("parameters")
        if isinstance(params, dict):
            operation = str(step.get("operation") or "")
            disabled_branch = bool(params.get("is_disabled_for_execution")) and operation in {
                "conditional_branch",
                "default_branch",
            }
            _validate_step_assignment_target(
                issues,
                f"$.steps[{step_index}].parameters",
                params=params,
                operation=operation,
                variable_types=variable_types,
            )
            registered_keys = registered_expression_keys()
            for key, value in params.items():
                if key in registered_keys or key in _LOOP_COUNT_EXPRESSION_KEYS:
                    expected_type, assignment_target = _step_expression_expected_type(
                        key,
                        params=params,
                        operation=operation,
                        variable_types=variable_types,
                    )
                    values = value if isinstance(value, list) else [value]
                    for expression_index, expression in enumerate(values):
                        suffix = f"[{expression_index}]" if isinstance(value, list) else ""
                        validate_expression(
                            f"$.steps[{step_index}].parameters.{key}{suffix}",
                            expression,
                            expected_type=expected_type,
                            assignment_target=assignment_target,
                            enforce_semantics=not (
                                disabled_branch and key == "condition_expression"
                            ),
                        )
                        if key in _LOOP_COUNT_EXPRESSION_KEYS:
                            _validate_loop_count_expression(
                                issues,
                                f"$.steps[{step_index}].parameters.{key}{suffix}",
                                expression,
                            )
            for section in ("variable_mappings_start", "variable_mappings_end"):
                for mapping_index, mapping in enumerate(params.get(section) or []):
                    if not isinstance(mapping, dict) or "source_expression" not in mapping:
                        continue
                    validate_expression(
                        f"$.steps[{step_index}].parameters.{section}[{mapping_index}].source_expression",
                        mapping.get("source_expression"),
                    )
            for item_index, item in enumerate(params.get("variables") or []):
                if isinstance(item, dict) and "value_expression" in item:
                    variable_name = str(item.get("name") or item.get("variable") or "")
                    validate_expression(
                        f"$.steps[{step_index}].parameters.variables[{item_index}].value_expression",
                        item.get("value_expression"),
                        expected_type=variable_types.get(variable_name),
                        assignment_target=variable_name or None,
                    )
    for record in walk_expression_values(payload):
        if record.path in validated_expression_paths:
            continue
        expected_type, assignment_target = _walked_expression_expected_type(
            record,
            payload=payload,
            variable_types=variable_types,
        )
        validate_expression(
            record.path,
            record.expression,
            expected_type=expected_type,
            assignment_target=assignment_target,
        )
        if record.key in _LOOP_COUNT_EXPRESSION_KEYS:
            _validate_loop_count_expression(issues, record.path, record.expression)
    _validate_v2_site_expression_alias_consistency(issues, payload)
    _validate_v2_scalar_expression_consistency(issues, payload)
    return issues


def _validate_step_assignment_target(
    issues: list[ProtocolIRIssue],
    path: str,
    *,
    params: dict[str, Any],
    operation: str,
    variable_types: dict[str, str],
) -> None:
    if operation not in {"set_variable", "set_remaining_runtime"}:
        return
    variable_name = str(params.get("variable") or params.get("name") or "").strip()
    if not variable_name or variable_name in variable_types:
        return
    issues.append(
        ProtocolIRIssue(
            f"{path}.variable",
            f"undefined_assignment_target: SetVariable target {variable_name!r} is not declared.",
        )
    )


def _walked_expression_expected_type(
    record: Any,
    *,
    payload: dict[str, Any],
    variable_types: dict[str, str],
) -> tuple[Any, str | None]:
    key = str(getattr(record, "key", "") or "")
    path = str(getattr(record, "path", "") or "")
    variable_match = re.match(r"^\$\.variables\[(\d+)\]", path)
    if variable_match:
        variable = _list_item(payload.get("variables"), int(variable_match.group(1)))
        variable_name = str(variable.get("name") or "") if isinstance(variable, dict) else ""
        if key in {"default_expression", "value_expression", "value_expressions"}:
            return variable_types.get(variable_name), variable_name or None
    labware_match = re.match(r"^\$\.labware\[(\d+)\]", path)
    if labware_match:
        if key == "position_expression":
            return ("number", "string"), None
        if key == "volume_ul_expression":
            return "number", None
    step_match = re.match(r"^\$\.steps\[(\d+)\]", path)
    if not step_match:
        return None, None
    step = _list_item(payload.get("steps"), int(step_match.group(1)))
    if not isinstance(step, dict):
        return None, None
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    operation = str(step.get("operation") or "")
    if key == "volume_ul_expression" and ".parameters." not in path:
        return "number", None
    if ".parameters.variables[" in path and key in {"value_expression", "value_expressions"}:
        container = getattr(record, "container", None)
        variable_name = (
            str(container.get("name") or container.get("variable") or "")
            if isinstance(container, dict)
            else ""
        )
        return variable_types.get(variable_name), variable_name or None
    if ".parameters." in path:
        return _step_expression_expected_type(
            key,
            params=params,
            operation=operation,
            variable_types=variable_types,
        )
    return None, None


def _list_item(value: Any, index: int) -> Any:
    if not isinstance(value, list) or index < 0 or index >= len(value):
        return None
    return value[index]


def _validate_loop_count_expression(
    issues: list[ProtocolIRIssue],
    path: str,
    value: Any,
) -> None:
    if not isinstance(value, dict):
        return
    try:
        expression = expression_from_mapping(value)
    except Exception:
        return
    if error := loop_count_expression_error(expression):
        issues.append(ProtocolIRIssue(path, error))


def _validate_v2_scalar_expression_consistency(
    issues: list[ProtocolIRIssue],
    payload: dict[str, Any],
) -> None:
    if payload.get("ir_version") != CURRENT_PROTOCOL_IR_VERSION:
        return

    variable_types = {
        str(variable.get("name") or ""): _variable_type_name(variable)
        for variable in payload.get("variables") or []
        if isinstance(variable, dict)
    }
    for variable_index, variable in enumerate(payload.get("variables") or []):
        if not isinstance(variable, dict):
            continue
        path = f"$.variables[{variable_index}]"
        variable_type = _variable_type_name(variable)
        _validate_expression_projection(
            issues,
            path,
            variable,
            "default_value",
            "default_expression",
            lambda value, type_name=variable_type: _literal_expression_for_type(value, type_name),
        )
        _validate_expression_projection(
            issues,
            path,
            variable,
            "value",
            "value_expression",
            lambda value, type_name=variable_type: _assignment_expression(
                value,
                target_type=type_name,
                variable_type_names=variable_types,
            ),
        )
        _validate_expression_projection_list(
            issues,
            path,
            variable,
            "values",
            "value_expressions",
            lambda value, type_name=variable_type: _literal_expression_for_type(value, type_name),
        )
        for assignment_index, assignment in enumerate(variable.get("assignments") or []):
            if not isinstance(assignment, dict):
                continue
            _validate_expression_projection(
                issues,
                f"{path}.assignments[{assignment_index}]",
                assignment,
                "value",
                "value_expression",
                lambda value, type_name=variable_type: _assignment_expression(
                    value,
                    target_type=type_name,
                    variable_type_names=variable_types,
                ),
            )

    for labware_index, labware in enumerate(payload.get("labware") or []):
        if not isinstance(labware, dict):
            continue
        path = f"$.labware[{labware_index}]"
        _validate_expression_projection(
            issues,
            path,
            labware,
            "position",
            "position_expression",
            _source_expression,
        )
        initial = labware.get("initial_contents")
        if isinstance(initial, dict):
            _validate_expression_projection(
                issues,
                f"{path}.initial_contents",
                initial,
                "volume_ul",
                "volume_ul_expression",
                _source_expression,
            )

    for step_index, step in enumerate(payload.get("steps") or []):
        if not isinstance(step, dict):
            continue
        path = f"$.steps[{step_index}]"
        _validate_expression_projection(
            issues,
            path,
            step,
            "volume_ul",
            "volume_ul_expression",
            _source_expression,
        )
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        params_path = f"{path}.parameters"
        _validate_cross_container_expression_projection(
            issues,
            params_path,
            primary=step,
            secondary=params,
            legacy_key="volume_ul",
            expression_key="volume_ul_expression",
            expression_factory=_source_expression,
        )
        operation = str(step.get("operation") or "")
        for field_path in registered_expression_field_paths():
            expression_key = canonical_expression_key(field_path)
            if expression_key not in params:
                continue
            for legacy_key in _REGISTERED_LEGACY_KEYS.get(field_path, ()):
                if expression_key == "volume_expressions":
                    _validate_expression_projection_list(
                        issues,
                        params_path,
                        params,
                        legacy_key,
                        expression_key,
                        _source_expression,
                    )
                else:
                    factory: Callable[[Any], dict[str, Any]] = _source_expression
                    if expression_key == "value_expression" and operation in {
                        "set_variable",
                        "set_remaining_runtime",
                    }:
                        target = str(params.get("variable") or params.get("name") or "")
                        factory = lambda value, target=target: _assignment_expression(
                            value,
                            target_type=variable_types.get(target),
                            variable_type_names=variable_types,
                        )
                    _validate_expression_projection(
                        issues,
                        params_path,
                        params,
                        legacy_key,
                        expression_key,
                        factory,
                    )
        for section in ("variable_mappings_start", "variable_mappings_end"):
            for mapping_index, mapping in enumerate(params.get(section) or []):
                if isinstance(mapping, dict):
                    _validate_expression_projection(
                        issues,
                        f"{params_path}.{section}[{mapping_index}]",
                        mapping,
                        "source",
                        "source_expression",
                        _source_expression,
                    )
        for item_index, item in enumerate(params.get("variables") or []):
            if not isinstance(item, dict):
                continue
            variable_name = str(item.get("name") or item.get("variable") or "")
            _validate_expression_projection(
                issues,
                f"{params_path}.variables[{item_index}]",
                item,
                "value",
                "value_expression",
                lambda value, variable_name=variable_name: _assignment_expression(
                    value,
                    target_type=variable_types.get(variable_name),
                    variable_type_names=variable_types,
                ),
            )


def _validate_v2_site_expression_alias_consistency(
    issues: list[ProtocolIRIssue],
    payload: dict[str, Any],
) -> None:
    if payload.get("ir_version") != CURRENT_PROTOCOL_IR_VERSION:
        return
    _validate_site_expression_alias_container(issues, "$", payload)


def _validate_site_expression_alias_container(
    issues: list[ProtocolIRIssue],
    path: str,
    value: Any,
) -> None:
    if isinstance(value, dict):
        expressions = [
            (key, value.get(key))
            for key in _SITE_EXPRESSION_KEYS
            if isinstance(value.get(key), dict)
        ]
        if expressions:
            canonical_key, canonical = expressions[0]
            for alias_key, expression in expressions[1:]:
                if not _expression_mappings_equivalent(canonical, expression):
                    issues.append(
                        ProtocolIRIssue(
                            f"{path}.{alias_key}",
                            f"site expression alias {alias_key} disagrees with {canonical_key}",
                        )
                    )
        for key, child in value.items():
            _validate_site_expression_alias_container(issues, f"{path}.{key}", child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_site_expression_alias_container(issues, f"{path}[{index}]", child)


def _validate_expression_projection(
    issues: list[ProtocolIRIssue],
    path: str,
    container: dict[str, Any],
    legacy_key: str,
    expression_key: str,
    expression_factory: Callable[[Any], dict[str, Any]],
) -> None:
    if expression_key not in container or not _has_value(container.get(legacy_key)):
        return
    expression = container.get(expression_key)
    if not isinstance(expression, dict):
        return
    projected = _legacy_projection_expression(
        container.get(legacy_key),
        authoritative_expression=expression,
        fallback_factory=expression_factory,
    )
    if not _expression_mappings_equivalent(projected, expression):
        issues.append(
            ProtocolIRIssue(
                f"{path}.{expression_key}",
                f"authoritative {expression_key} disagrees with legacy compatibility field {legacy_key}",
            )
        )


def _validate_expression_projection_list(
    issues: list[ProtocolIRIssue],
    path: str,
    container: dict[str, Any],
    legacy_key: str,
    expression_key: str,
    expression_factory: Callable[[Any], dict[str, Any]],
) -> None:
    legacy_values = container.get(legacy_key)
    expressions = container.get(expression_key)
    if not isinstance(legacy_values, list) or not isinstance(expressions, list):
        return
    if len(legacy_values) != len(expressions):
        issues.append(
            ProtocolIRIssue(
                f"{path}.{expression_key}",
                f"authoritative {expression_key} length disagrees with legacy compatibility field {legacy_key}",
            )
        )
        return
    for index, (legacy_value, expression) in enumerate(zip(legacy_values, expressions)):
        if not isinstance(expression, dict):
            continue
        projected = _legacy_projection_expression(
            legacy_value,
            authoritative_expression=expression,
            fallback_factory=expression_factory,
        )
        if not _expression_mappings_equivalent(projected, expression):
            issues.append(
                ProtocolIRIssue(
                    f"{path}.{expression_key}[{index}]",
                    f"authoritative {expression_key} disagrees with legacy compatibility field {legacy_key}[{index}]",
                )
            )


def _validate_cross_container_expression_projection(
    issues: list[ProtocolIRIssue],
    path: str,
    *,
    primary: dict[str, Any],
    secondary: dict[str, Any],
    legacy_key: str,
    expression_key: str,
    expression_factory: Callable[[Any], dict[str, Any]],
) -> None:
    primary_expression = primary.get(expression_key)
    secondary_expression = secondary.get(expression_key)
    if isinstance(primary_expression, dict) and isinstance(secondary_expression, dict):
        if not _expression_mappings_equivalent(primary_expression, secondary_expression):
            issues.append(
                ProtocolIRIssue(
                    f"{path}.{expression_key}",
                    f"authoritative {expression_key} disagrees between step and parameters",
                )
            )
    if isinstance(primary_expression, dict) and _has_value(secondary.get(legacy_key)):
        projected = _legacy_projection_expression(
            secondary.get(legacy_key),
            authoritative_expression=primary_expression,
            fallback_factory=expression_factory,
        )
        if not _expression_mappings_equivalent(
            primary_expression,
            projected,
        ):
            issues.append(
                ProtocolIRIssue(
                    f"{path}.{legacy_key}",
                    f"legacy compatibility field {legacy_key} disagrees with authoritative {expression_key}",
                )
            )
    if isinstance(secondary_expression, dict) and _has_value(primary.get(legacy_key)):
        projected = _legacy_projection_expression(
            primary.get(legacy_key),
            authoritative_expression=secondary_expression,
            fallback_factory=expression_factory,
        )
        if not _expression_mappings_equivalent(
            projected,
            secondary_expression,
        ):
            issues.append(
                ProtocolIRIssue(
                    f"{path}.{expression_key}",
                    f"authoritative {expression_key} disagrees with step legacy compatibility field {legacy_key}",
                )
            )


def _legacy_projection_expression(
    value: Any,
    *,
    authoritative_expression: dict[str, Any],
    fallback_factory: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    kind = str(authoritative_expression.get("kind") or "")
    if kind == "string_literal":
        if isinstance(value, str):
            parsed = _source_expression(value)
            if parsed.get("kind") == "string_literal":
                return parsed
        return {"kind": "string_literal", "value": "" if value is None else str(value)}
    if kind == "number_literal":
        return _literal_expression_for_type(value, "number")
    if kind == "boolean_literal":
        return _literal_expression_for_type(value, "boolean")
    if kind == "source_preserved_expression" and isinstance(value, str):
        if value.strip() == str(authoritative_expression.get("source") or "").strip():
            return dict(authoritative_expression)
    return fallback_factory(value)


def _expression_mappings_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    try:
        return render_expression(expression_from_mapping(left)) == render_expression(
            expression_from_mapping(right)
        )
    except Exception:
        return False


def _validate_protocol_ir_v1(payload: dict[str, Any], *, strict: bool = True) -> list[ProtocolIRIssue]:
    issues: list[ProtocolIRIssue] = []
    if not isinstance(payload, dict):
        return [ProtocolIRIssue("$", "payload must be an object")]

    for key, expected_type in ROOT_REQUIRED_TYPES.items():
        value = payload.get(key)
        if not isinstance(value, expected_type):
            issues.append(ProtocolIRIssue(f"$.{key}", f"must be {expected_type.__name__}"))

    if payload.get("ir_version") not in {PROTOCOL_IR_V1_VERSION, CURRENT_PROTOCOL_IR_VERSION}:
        issues.append(
            ProtocolIRIssue(
                "$.ir_version",
                f"must equal {PROTOCOL_IR_V1_VERSION!r} or {CURRENT_PROTOCOL_IR_VERSION!r}",
            )
        )
    if isinstance(payload.get("id"), str) and not payload["id"].strip():
        issues.append(ProtocolIRIssue("$.id", "must not be empty"))

    protocol = payload.get("protocol")
    if isinstance(protocol, dict):
        _require_text(issues, "$.protocol.name", protocol.get("name"))
    source = payload.get("source")
    if isinstance(source, dict):
        _require_text(issues, "$.source.format", source.get("format"))
    worktable = payload.get("worktable")
    if isinstance(worktable, dict) and "auto_place" in worktable and not isinstance(worktable["auto_place"], bool):
        issues.append(ProtocolIRIssue("$.worktable.auto_place", "must be boolean"))

    _validate_named_items(issues, payload.get("labware"), "$.labware", required_key="label")
    _validate_named_items(issues, payload.get("reagents"), "$.reagents", required_key="name")
    _validate_named_items(issues, payload.get("liquid_classes"), "$.liquid_classes", required_key="name")
    _validate_named_items(issues, payload.get("variables"), "$.variables", required_key="name")
    _validate_named_items(issues, payload.get("dependencies"), "$.dependencies", required_key="kind")
    _validate_steps(
        issues,
        payload.get("steps"),
        strict=strict,
        expression_authoritative=payload.get("ir_version") == CURRENT_PROTOCOL_IR_VERSION,
    )

    return issues


def assert_valid_protocol_ir(payload: dict[str, Any], *, strict: bool = True) -> None:
    issues = validate_protocol_ir(payload, strict=strict)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise ProtocolIRValidationError(errors)


def validate_protocol_ir_bundle(payload: dict[str, Any], *, strict: bool = True) -> list[ProtocolIRIssue]:
    issues: list[ProtocolIRIssue] = []
    if not isinstance(payload, dict):
        return [ProtocolIRIssue("$", "bundle payload must be an object")]
    if payload.get("ir_version") != CURRENT_PROTOCOL_IR_BUNDLE_VERSION:
        issues.append(
            ProtocolIRIssue(
                "$.ir_version",
                f"must equal {CURRENT_PROTOCOL_IR_BUNDLE_VERSION!r}",
            )
        )
    protocols = payload.get("protocols")
    if not isinstance(protocols, list):
        issues.append(ProtocolIRIssue("$.protocols", "must be an array"))
        return issues
    for index, protocol in enumerate(protocols):
        for issue in validate_protocol_ir(protocol, strict=strict):
            issues.append(ProtocolIRIssue(f"$.protocols[{index}]{issue.path[1:]}", issue.message, issue.severity))
    if isinstance(payload.get("protocol_count"), int) and payload["protocol_count"] != len(protocols):
        issues.append(ProtocolIRIssue("$.protocol_count", "must match protocols length"))
    return issues


def _run_registered_migrations(
    payload: dict[str, Any],
    from_version: Any,
    to_version: str,
) -> dict[str, Any]:
    if not isinstance(from_version, str):
        raise ValueError("Protocol IR is missing ir_version and does not look like a v1 payload")
    current = from_version
    migrated = copy.deepcopy(payload)
    seen = set()
    while current != to_version:
        if current in seen or current not in PROTOCOL_IR_MIGRATIONS:
            raise ValueError(f"No migration path from {from_version!r} to {to_version!r}")
        seen.add(current)
        next_version, fn = PROTOCOL_IR_MIGRATIONS[current]
        migrated = fn(migrated)
        migrated["ir_version"] = next_version
        current = next_version
    return migrated


def _assert_registered_future_payload(payload: dict[str, Any], version: str) -> None:
    if not isinstance(payload, dict):
        raise ProtocolIRValidationError([ProtocolIRIssue("$", "payload must be an object")])
    if payload.get("ir_version") != version:
        raise ProtocolIRValidationError(
            [ProtocolIRIssue("$.ir_version", f"migration must produce {version!r}")]
        )
    if version not in PROTOCOL_IR_SCHEMAS:
        return
    if not isinstance(payload.get("id"), str) or not payload.get("id", "").strip():
        raise ProtocolIRValidationError([ProtocolIRIssue("$.id", "must be a non-empty string")])


def _assert_v1_payload(payload: dict[str, Any]) -> None:
    issues = _validate_protocol_ir_v1(payload, strict=True)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise ProtocolIRValidationError(errors)


def _normalize_v1(payload: dict[str, Any]) -> dict[str, Any]:
    ir = copy.deepcopy(payload)
    ir["ir_version"] = PROTOCOL_IR_V1_VERSION

    protocol = _as_dict(ir.get("protocol"))
    protocol.setdefault("name", ir.get("name") or ir.get("id") or "Generated protocol")
    protocol.setdefault("comment", "")
    protocol.setdefault("schema_intent", "canonical source of truth for generated Tecan artifacts")
    ir["protocol"] = protocol

    ir["id"] = str(ir.get("id") or _safe_id(str(protocol.get("name") or "protocol")))
    source = _as_dict(ir.get("source"))
    source.setdefault("format", "unknown")
    source.setdefault("path", "")
    ir["source"] = source

    worktable = _as_dict(ir.get("worktable"))
    worktable.setdefault("name", "")
    worktable.setdefault("guid", "")
    worktable["auto_place"] = bool(worktable.get("auto_place", False))
    ir["worktable"] = worktable

    for key in LIST_FIELDS:
        ir[key] = _as_list(ir.get(key))

    ir["labware"] = [_normalize_named_item(item, "label") for item in ir["labware"]]
    ir["reagents"] = [_normalize_named_item(item, "name") for item in ir["reagents"]]
    ir["liquid_classes"] = [_normalize_named_item(item, "name") for item in ir["liquid_classes"]]
    ir["variables"] = [_normalize_variable(item) for item in ir["variables"]]
    ir["dependencies"] = [_normalize_dependency(item) for item in ir["dependencies"]]
    ir["steps"] = [_normalize_step(step, index) for index, step in enumerate(ir["steps"], start=1)]

    if not ir["safety_assumptions"]:
        ir["safety_assumptions"] = [
            {
                "id": "manual_validation_required",
                "text": "Generated artifacts must be reviewed, simulated, and validated in FluentControl before instrument use.",
            }
        ]
    return ir


def _normalize_v2(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("ir_version") == PROTOCOL_IR_V1_VERSION:
        return _migrate_v1_to_v2(payload)
    legacy = copy.deepcopy(payload)
    legacy["ir_version"] = PROTOCOL_IR_V1_VERSION
    ir = _normalize_v1(legacy)
    ir["ir_version"] = CURRENT_PROTOCOL_IR_VERSION
    ir.setdefault("expression_schema_version", EXPRESSION_SCHEMA_VERSION)
    _add_v2_expression_fields(ir)
    _canonicalize_v2_expression_aliases(ir)
    return ir


def _migrate_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    ir = _normalize_v1(payload)
    ir["ir_version"] = CURRENT_PROTOCOL_IR_VERSION
    ir.setdefault("expression_schema_version", EXPRESSION_SCHEMA_VERSION)
    schema_notes = _as_dict(ir.get("schema_notes"))
    schema_notes.setdefault("migrated_from", PROTOCOL_IR_V1_VERSION)
    schema_notes.setdefault(
        "expression_contract",
        "Expression-capable fields carry typed FluentControl expression objects in *_expression fields.",
    )
    ir["schema_notes"] = schema_notes
    _add_v2_expression_fields(ir)
    _canonicalize_v2_expression_aliases(ir)
    return ir


def _canonicalize_v2_expression_aliases(ir: dict[str, Any]) -> None:
    issues: list[ProtocolIRIssue] = []
    _canonicalize_site_expression_aliases(ir, "$", issues)
    if issues:
        raise ProtocolIRValidationError(issues)


def _canonicalize_site_expression_aliases(
    value: Any,
    path: str,
    issues: list[ProtocolIRIssue],
) -> None:
    if isinstance(value, dict):
        _canonicalize_site_expression_aliases_in_container(value, path, issues)
        for key, child in list(value.items()):
            _canonicalize_site_expression_aliases(child, f"{path}.{key}", issues)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _canonicalize_site_expression_aliases(child, f"{path}[{index}]", issues)


def _canonicalize_site_expression_aliases_in_container(
    container: dict[str, Any],
    path: str,
    issues: list[ProtocolIRIssue],
) -> None:
    expressions = [
        (key, container.get(key))
        for key in _SITE_EXPRESSION_KEYS
        if isinstance(container.get(key), dict)
    ]
    if not expressions:
        return
    canonical_key, canonical = expressions[0]
    for alias_key, expression in expressions[1:]:
        if not _expression_mappings_equivalent(canonical, expression):
            issues.append(
                ProtocolIRIssue(
                    f"{path}.{alias_key}",
                    f"site expression alias {alias_key} disagrees with {canonical_key}",
                )
            )
    container[_SITE_EXPRESSION_KEY] = canonical
    for alias_key in _SITE_EXPRESSION_ALIAS_KEYS:
        container.pop(alias_key, None)


def _project_v2_to_v1(payload: dict[str, Any]) -> dict[str, Any]:
    ir = copy.deepcopy(payload)
    _canonicalize_v2_expression_aliases(ir)
    ir["ir_version"] = PROTOCOL_IR_V1_VERSION
    ir.pop("expression_schema_version", None)

    for variable in ir.get("variables") or []:
        if not isinstance(variable, dict):
            continue
        _project_expression_field(variable, "default_expression", "default_value")
        _project_expression_field(variable, "value_expression", "value")
        _project_expression_list(variable, "value_expressions", "values")
        for assignment in variable.get("assignments") or []:
            if isinstance(assignment, dict):
                _project_expression_field(
                    assignment,
                    "value_expression",
                    "value",
                    expression_text=True,
                )

    for labware in ir.get("labware") or []:
        if not isinstance(labware, dict):
            continue
        _project_expression_field(labware, "position_expression", "position")
        initial = labware.get("initial_contents")
        if isinstance(initial, dict):
            _project_expression_field(initial, "volume_ul_expression", "volume_ul")

    preferred_legacy_keys = {
        canonical_expression_key(field_path): legacy_keys[0]
        for field_path, legacy_keys in _REGISTERED_LEGACY_KEYS.items()
        if legacy_keys
    }
    text_expression_keys = {
        "value_expression",
        "condition_expression",
        "source_expression",
    }
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        _project_expression_field(step, "volume_ul_expression", "volume_ul")
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        for expression_key, legacy_key in preferred_legacy_keys.items():
            if expression_key == "volume_expressions":
                _project_expression_list(
                    params,
                    expression_key,
                    legacy_key,
                    expression_text=True,
                )
            else:
                _project_expression_field(
                    params,
                    expression_key,
                    legacy_key,
                    expression_text=expression_key in text_expression_keys,
                )
        for alias in _LOOP_COUNT_EXPRESSION_KEYS - {"number_of_loops_expression"}:
            _project_expression_field(params, alias, alias.removesuffix("_expression"))
        for section in ("variable_mappings_start", "variable_mappings_end"):
            for mapping in params.get(section) or []:
                if isinstance(mapping, dict):
                    _project_expression_field(
                        mapping,
                        "source_expression",
                        "source",
                        expression_text=True,
                    )
                    _remove_expression_fields(mapping)
        for item in params.get("variables") or []:
            if isinstance(item, dict):
                _project_expression_field(
                    item,
                    "value_expression",
                    "value",
                    expression_text=True,
                )
                _remove_expression_fields(item)
        _remove_expression_fields(params)

    for variable in ir.get("variables") or []:
        if isinstance(variable, dict):
            _remove_expression_fields(variable)
            for assignment in variable.get("assignments") or []:
                if isinstance(assignment, dict):
                    _remove_expression_fields(assignment)
    for labware in ir.get("labware") or []:
        if isinstance(labware, dict):
            _remove_expression_fields(labware)
            initial = labware.get("initial_contents")
            if isinstance(initial, dict):
                _remove_expression_fields(initial)
    for step in ir.get("steps") or []:
        if isinstance(step, dict):
            _remove_expression_fields(step)
    return ir


def _project_expression_field(
    container: dict[str, Any],
    expression_key: str,
    legacy_key: str,
    *,
    expression_text: bool = False,
) -> None:
    expression = container.get(expression_key)
    if not isinstance(expression, dict):
        return
    container[legacy_key] = _expression_legacy_value(
        expression,
        expression_text=expression_text,
    )


def _project_expression_list(
    container: dict[str, Any],
    expression_key: str,
    legacy_key: str,
    *,
    expression_text: bool = False,
) -> None:
    expressions = container.get(expression_key)
    if not isinstance(expressions, list):
        return
    container[legacy_key] = [
        _expression_legacy_value(expression, expression_text=expression_text)
        for expression in expressions
        if isinstance(expression, dict)
    ]


def _expression_legacy_value(expression: dict[str, Any], *, expression_text: bool) -> Any:
    kind = str(expression.get("kind") or "")
    if not expression_text and kind in {
        "string_literal",
        "number_literal",
        "boolean_literal",
    }:
        return expression.get("value")
    return render_expression(expression_from_mapping(expression))


def _remove_expression_fields(container: dict[str, Any]) -> None:
    for key in tuple(container):
        if key.endswith("_expression") or key.endswith("_expressions"):
            container.pop(key, None)


def _add_v2_expression_fields(ir: dict[str, Any]) -> None:
    variable_type_names: dict[str, Any] = {}
    for variable in ir.get("variables") or []:
        if not isinstance(variable, dict):
            continue
        variable_name = str(variable.get("name") or "").strip()
        if variable_name:
            variable_type_names[variable_name] = _variable_type_name(variable)

    for variable in ir.get("variables") or []:
        if not isinstance(variable, dict):
            continue
        variable_type = _variable_type_name(variable)
        if "default_expression" not in variable and _has_value(variable.get("default_value")):
            variable["default_expression"] = _literal_expression_for_type(variable.get("default_value"), variable_type)
        if "value_expression" not in variable and _has_value(variable.get("value")):
            variable["value_expression"] = _literal_expression_for_type(variable.get("value"), variable_type)
        values = variable.get("values")
        if "value_expressions" not in variable and isinstance(values, list):
            variable["value_expressions"] = [_literal_expression_for_type(value, variable_type) for value in values]
        for assignment in variable.get("assignments") or []:
            if not isinstance(assignment, dict):
                continue
            if "value_expression" not in assignment and _has_value(assignment.get("value")):
                assignment["value_expression"] = _assignment_expression(
                    assignment.get("value"),
                    target_type=variable_type,
                    variable_type_names=variable_type_names,
                )

    for labware in ir.get("labware") or []:
        if isinstance(labware, dict) and "position_expression" not in labware and _has_value(labware.get("position")):
            labware["position_expression"] = _expression_from_value(labware.get("position"), source_for_strings=True)
        initial = labware.get("initial_contents") if isinstance(labware, dict) else None
        if isinstance(initial, dict) and "volume_ul_expression" not in initial and _has_value(initial.get("volume_ul")):
            initial["volume_ul_expression"] = _expression_from_value(initial.get("volume_ul"), source_for_strings=True)

    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if "volume_ul_expression" not in step and _has_value(step.get("volume_ul")):
            step["volume_ul_expression"] = _expression_from_value(step.get("volume_ul"), source_for_strings=True)
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        operation = str(step.get("operation") or "")
        if operation in {"set_variable", "set_remaining_runtime"} and "value_expression" not in params and _has_value(params.get("value")):
            target_variable = str(params.get("variable") or params.get("name") or "").strip()
            params["value_expression"] = _assignment_expression(
                params.get("value"),
                target_type=variable_type_names.get(target_variable),
                variable_type_names=variable_type_names,
            )
        if operation in {"conditional_branch", "default_branch"} and "condition_expression" not in params and _has_value(params.get("condition")):
            params["condition_expression"] = _source_expression(params.get("condition"))
        if "volume_ul_expression" not in params and _has_value(params.get("volume_ul")):
            params["volume_ul_expression"] = _expression_from_value(params.get("volume_ul"), source_for_strings=True)
        _migrate_registered_parameter_expressions(params)
        if operation == "call_subroutine":
            for section in ("variable_mappings_start", "variable_mappings_end"):
                for mapping in params.get(section) or []:
                    if not isinstance(mapping, dict):
                        continue
                    source_key = canonical_expression_key("Source")
                    if source_key not in mapping and _has_value(mapping.get("source")):
                        mapping[source_key] = _source_expression(mapping.get("source"))
        if operation == "runtime_variable_prompt":
            raw_xml = params.get("raw_xml")
            if isinstance(raw_xml, str):
                params["raw_xml"] = _normalize_rup_allowed_values_in_xml(raw_xml)
            for item in params.get("variables") or []:
                if not isinstance(item, dict):
                    continue
                if "allowed_values" in item:
                    item["allowed_values"] = _normalize_rup_allowed_values(item.get("allowed_values"))
                if "allowed_values_raw" in item:
                    item["allowed_values_raw"] = _normalize_rup_allowed_values(item.get("allowed_values_raw"))
                if "value_expression" not in item and _has_value(item.get("value")):
                    item["value_expression"] = _literal_expression(item.get("value"))


def _normalize_rup_allowed_values(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_rup_allowed_value_item(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_rup_allowed_value_item(item) for item in value]
    if isinstance(value, str) and ";" in value:
        return ";".join(_normalize_rup_allowed_value_item(item) for item in value.split(";"))
    return _normalize_rup_allowed_value_item(value)


def _normalize_rup_allowed_values_in_xml(xml: str) -> str:
    def replace(match: re.Match[str]) -> str:
        normalized = _normalize_rup_allowed_values(match.group(2))
        return f"{match.group(1)}{normalized}{match.group(3)}"

    return re.sub(r"(<AllowedValues>)(.*?)(</AllowedValues>)", replace, xml, flags=re.DOTALL)


def _normalize_rup_allowed_value_item(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return value


def _migrate_registered_parameter_expressions(params: dict[str, Any]) -> None:
    for field_path in registered_expression_field_paths():
        if field_path == "Source":
            continue
        expression_key = canonical_expression_key(field_path)
        if expression_key in params:
            continue
        for value_key in _REGISTERED_LEGACY_KEYS.get(field_path, ()):
            value = params.get(value_key)
            if not _has_value(value):
                continue
            if expression_key == "volume_expressions" and isinstance(value, list):
                params[expression_key] = [
                    _expression_from_value(item, source_for_strings=True)
                    for item in value
                ]
            else:
                params[expression_key] = _expression_from_value(
                    value,
                    source_for_strings=True,
                )
            break


def _normalize_step(raw: Any, index: int) -> dict[str, Any]:
    step = _as_dict(raw)
    operation = normalize_operation(str(step.get("operation") or ""))
    step["operation"] = operation
    step["index"] = _positive_int(step.get("index"), default=index)
    step["id"] = str(step.get("id") or f"step_{index:03d}")
    step["group"] = str(step.get("group") or "Steps")
    step["name"] = str(step.get("name") or operation_name(operation))
    step["parameters"] = _as_dict(step.get("parameters"))
    step["safety_flags"] = _as_list(step.get("safety_flags"))
    return step


def normalize_operation(value: str) -> str:
    cleaned = value.strip()
    return OPERATION_ALIASES.get(cleaned, cleaned)


def operation_name(operation: str) -> str:
    names = {
        Operation.ADD_LABWARE.value: "Add Labware",
        Operation.LOAD_LABWARE.value: "Load Labware",
        Operation.INITIALIZE_DEVICE.value: "Initialize Device",
        Operation.MOVE_PLATE.value: "Move Plate",
        Operation.GET_HEAD_ADAPTER.value: "Mount Head Adapter",
        Operation.DROP_HEAD_ADAPTER.value: "Drop Head Adapter",
        Operation.PICK_UP_TIPS.value: "Pick Up Tips",
        Operation.SET_TIPS_BACK.value: "Return Tips",
        Operation.DROP_TIPS.value: "Drop Tips",
        Operation.MCA384_GET_TIPS.value: "Pick Up Tips",
        Operation.MCA384_DROP_TIPS.value: "Drop Tips",
        Operation.LIHA_GET_TIPS.value: "LiHa Get Tips",
        Operation.LIHA_DROP_TIPS.value: "LiHa Drop Tips",
        Operation.ASPIRATE.value: "Aspirate",
        Operation.DISPENSE.value: "Dispense",
        Operation.MIX.value: "Mix",
        Operation.MCA384_MIX.value: "Mix",
        Operation.LIHA_ASPIRATE.value: "LiHa Aspirate",
        Operation.LIHA_DISPENSE.value: "LiHa Dispense",
        Operation.LIHA_MIX.value: "LiHa Mix",
        Operation.WASH.value: "Wash",
        Operation.READ_WORKLIST.value: "Read Worklist",
        Operation.LOOP_OVER_WELLS.value: "Loop Over Wells",
        Operation.CONDITIONAL_BRANCH.value: "Conditional Branch",
        Operation.DEFAULT_BRANCH.value: "Default Branch",
        Operation.QUERY_VARIABLE.value: "Query Variable",
        Operation.SET_VARIABLE.value: "Set Variable",
        Operation.SET_REMAINING_RUNTIME.value: "Set Remaining Runtime",
        Operation.RUNTIME_VARIABLE_PROMPT.value: "Runtime Variable Prompt",
        Operation.PROMPT_USER.value: "Prompt User",
        Operation.EXECUTE_APPLICATION.value: "Execute Application",
        Operation.EXECUTE_VB_SCRIPT.value: "Execute VBScript",
        Operation.APPLICATION_DRIVER_MACRO.value: "Application Driver Macro",
        Operation.COMMENT.value: "Comment",
    }
    return names.get(operation, operation.replace("_", " ").title())


def _validate_steps(
    issues: list[ProtocolIRIssue],
    steps: Any,
    *,
    strict: bool,
    expression_authoritative: bool = False,
) -> None:
    if not isinstance(steps, list):
        return
    expected_index = 1
    for index, step in enumerate(steps):
        path = f"$.steps[{index}]"
        if not isinstance(step, dict):
            issues.append(ProtocolIRIssue(path, "must be an object"))
            continue
        operation = step.get("operation")
        if operation not in OPERATION_VALUES:
            issues.append(
                ProtocolIRIssue(
                    f"{path}.operation",
                    f"must be one of: {', '.join(OPERATION_VALUES)}",
                )
            )
            continue
        if step.get("index") != expected_index:
            issues.append(ProtocolIRIssue(f"{path}.index", f"must equal {expected_index}"))
        expected_index += 1
        _require_text(issues, f"{path}.id", step.get("id"))
        _require_text(issues, f"{path}.name", step.get("name"))
        _require_text(issues, f"{path}.group", step.get("group"))
        if not isinstance(step.get("parameters"), dict):
            issues.append(ProtocolIRIssue(f"{path}.parameters", "must be an object"))
        if strict and operation in LABWARE_TARGET_OPERATIONS and not _has_any_labware_target(step):
            issues.append(ProtocolIRIssue(path, f"{operation} requires a labware target"))
        if (
            strict
            and operation in VOLUME_OPERATIONS
            and not _step_has_volume(step, expression_authoritative=expression_authoritative)
        ):
            issues.append(ProtocolIRIssue(f"{path}.volume_ul", f"{operation} requires volume_ul"))
        if strict and operation in LIQUID_CLASS_OPERATIONS and not _has_value(step.get("liquid_class")):
            issues.append(ProtocolIRIssue(f"{path}.liquid_class", f"{operation} requires liquid_class"))
        if operation == Operation.ADD_LABWARE.value:
            _validate_add_labware_parameters(issues, f"{path}.parameters", step.get("parameters"))


def _step_has_volume(step: dict[str, Any], *, expression_authoritative: bool) -> bool:
    if _has_value(step.get("volume_ul")):
        return True
    params = step.get("parameters")
    if isinstance(params, dict) and _has_value(params.get("volume_ul")):
        return True
    if not expression_authoritative:
        return False
    if _has_value(step.get("volume_ul_expression")):
        return True
    return isinstance(params, dict) and _has_value(params.get("volume_ul_expression"))


def _validate_named_items(
    issues: list[ProtocolIRIssue],
    items: Any,
    path: str,
    *,
    required_key: str,
) -> None:
    if not isinstance(items, list):
        return
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            issues.append(ProtocolIRIssue(item_path, "must be an object"))
            continue
        _require_text(issues, f"{item_path}.{required_key}", item.get(required_key))


def _normalize_named_item(raw: Any, required_key: str) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {required_key: raw}
    item = _as_dict(raw)
    if required_key == "label" and not item.get("label") and item.get("name"):
        item["label"] = item["name"]
    if required_key == "name" and not item.get("name") and item.get("label"):
        item["name"] = item["label"]
    if required_key == "label" and item.get("label") and not item.get("id"):
        item["id"] = _safe_id(str(item["label"]))
    return item


def _normalize_dependency(raw: Any) -> dict[str, Any]:
    item = _as_dict(raw)
    item.setdefault("kind", "dependency")
    item.setdefault("name", "")
    item["required"] = bool(item.get("required", True))
    return item


def _normalize_variable(raw: Any) -> dict[str, Any]:
    item = _normalize_named_item(raw, "name")
    if "query_on_startup" in item and "query_at_startup" not in item:
        item["query_at_startup"] = item.pop("query_on_startup")
    if "query_at_startup" in item:
        item["query_at_startup"] = _coerce_bool(item.get("query_at_startup"))
    if "read_only" in item:
        item["read_only"] = _coerce_bool(item.get("read_only"))
    return item


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return bool(value)


def _literal_expression(value: Any) -> dict[str, Any]:
    if _is_expression_mapping(value):
        return copy.deepcopy(value)
    if isinstance(value, bool):
        return {"kind": "boolean_literal", "value": value}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"kind": "number_literal", "value": value}
    return {"kind": "string_literal", "value": "" if value is None else str(value)}


def _literal_expression_for_type(value: Any, type_name: Any) -> dict[str, Any]:
    if _is_expression_mapping(value):
        return copy.deepcopy(value)
    normalized_type = _normal_expression_type(type_name)
    if normalized_type == "string":
        return {"kind": "string_literal", "value": "" if value is None else str(value)}
    if normalized_type == "number":
        try:
            number = float(str(value).strip()) if isinstance(value, str) else float(value)
        except (TypeError, ValueError):
            return _literal_expression(value)
        return {"kind": "number_literal", "value": int(number) if number.is_integer() else number}
    if normalized_type == "boolean":
        if isinstance(value, bool):
            return {"kind": "boolean_literal", "value": value}
        if isinstance(value, str):
            folded = value.strip().casefold()
            if folded in {"true", "1", "yes", "y"}:
                return {"kind": "boolean_literal", "value": True}
            if folded in {"false", "0", "no", "n"}:
                return {"kind": "boolean_literal", "value": False}
        return _literal_expression(value)
    return _literal_expression(value)


def _normal_expression_type(type_name: Any) -> str:
    text = str(type_name or "").strip().casefold().replace("system.", "")
    if text in {"integer", "int", "int16", "int32", "int64", "short", "long", "floating point", "float", "single", "double", "decimal", "number", "numeric"}:
        return "number"
    if text in {"boolean", "bool"}:
        return "boolean"
    if text in {"string", "str", "text"}:
        return "string"
    return "unknown"


def _variable_type_name(variable: dict[str, Any]) -> Any:
    return (
        variable.get("type")
        or variable.get("type_name")
        or variable.get("variable_type")
        or variable.get("data_type")
        or variable.get("fluent_type")
        or "unknown"
    )


def _expression_from_value(value: Any, *, source_for_strings: bool) -> dict[str, Any]:
    if _is_expression_mapping(value):
        return copy.deepcopy(value)
    if isinstance(value, str) and source_for_strings:
        return _source_expression(value)
    return _literal_expression(value)


def _assignment_expression(
    value: Any,
    *,
    target_type: Any,
    variable_type_names: dict[str, Any],
) -> dict[str, Any]:
    if _is_expression_mapping(value):
        return copy.deepcopy(value)
    target_expression_type = _normal_expression_type(target_type)
    if target_expression_type == "string" and isinstance(value, str):
        text = value.strip()
        parsed = _try_parse_expression_mapping(text)
        if parsed is None:
            return {"kind": "string_literal", "value": text}
        if parsed.get("kind") == "variable_reference" and parsed.get("name") not in variable_type_names:
            return {"kind": "string_literal", "value": text}
        return parsed
    return _source_expression(value)


def _source_expression(value: Any) -> dict[str, Any]:
    if _is_expression_mapping(value):
        return copy.deepcopy(value)
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return _literal_expression(value)
    text = str(value if value is not None else "").strip()
    if not text:
        return {"kind": "string_literal", "value": ""}
    return expression_to_mapping(parse_or_preserve_source_expression(text))


def _try_parse_expression_mapping(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return expression_to_mapping(parse_expression(text))
    except Exception:
        return None


def _is_expression_mapping(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("kind"), str)


def _validate_expression(issues: list[ProtocolIRIssue], path: str, value: Any) -> None:
    if not isinstance(value, dict):
        issues.append(ProtocolIRIssue(path, "must be a typed expression object"))
        return
    kind = str(value.get("kind") or "")
    if kind == "string_literal":
        if not isinstance(value.get("value"), str):
            issues.append(ProtocolIRIssue(f"{path}.value", "must be string"))
        return
    if kind == "number_literal":
        number = value.get("value")
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            issues.append(ProtocolIRIssue(f"{path}.value", "must be number"))
        return
    if kind == "boolean_literal":
        if not isinstance(value.get("value"), bool):
            issues.append(ProtocolIRIssue(f"{path}.value", "must be boolean"))
        return
    if kind == "variable_reference":
        name = str(value.get("name") or "")
        if not _FC_VARIABLE_NAME_RE.fullmatch(name):
            issues.append(ProtocolIRIssue(f"{path}.name", "must be a valid FluentControl variable name"))
        return
    if kind == "function_call":
        name = str(value.get("name") or "")
        if not _FC_VARIABLE_NAME_RE.fullmatch(name):
            issues.append(ProtocolIRIssue(f"{path}.name", "must be a valid FluentControl function name"))
        args = value.get("arguments")
        if not isinstance(args, list):
            issues.append(ProtocolIRIssue(f"{path}.arguments", "must be an array"))
            return
        for index, arg in enumerate(args):
            _validate_expression(issues, f"{path}.arguments[{index}]", arg)
        return
    if kind == "unary_expression":
        if value.get("operator") not in {"+", "-"}:
            issues.append(ProtocolIRIssue(f"{path}.operator", "must be '+' or '-'"))
        _validate_expression(issues, f"{path}.operand", value.get("operand"))
        return
    if kind == "binary_expression":
        if value.get("operator") not in {"+", "-", "*", "/", "=", "<>", "<", ">", "<=", ">=", "AND", "OR"}:
            issues.append(ProtocolIRIssue(f"{path}.operator", "must be a supported binary operator"))
        _validate_expression(issues, f"{path}.left", value.get("left"))
        _validate_expression(issues, f"{path}.right", value.get("right"))
        return
    if kind == "source_preserved_expression":
        if not isinstance(value.get("source"), str) or not value.get("source"):
            issues.append(ProtocolIRIssue(f"{path}.source", "must be non-empty string"))
        if not isinstance(value.get("source_entry"), str) or not value.get("source_entry"):
            issues.append(ProtocolIRIssue(f"{path}.source_entry", "must be non-empty string"))
        source_hash = str(value.get("source_hash") or "")
        if not source_hash.startswith("sha256:") or len(source_hash.split(":", 1)[1]) != 64:
            issues.append(ProtocolIRIssue(f"{path}.source_hash", "must be sha256:<hex>"))
        if "provenance_id" in value:
            provenance_id = str(value.get("provenance_id") or "")
            if not re.fullmatch(r"exprprov:[0-9a-fA-F]{64}", provenance_id):
                issues.append(ProtocolIRIssue(f"{path}.provenance_id", "must be exprprov:<sha256 hex>"))
        if "byte_stable" in value and not isinstance(value.get("byte_stable"), bool):
            issues.append(ProtocolIRIssue(f"{path}.byte_stable", "must be boolean"))
        if value.get("reference_metadata_origin") != "source_ingestion":
            issues.append(
                ProtocolIRIssue(
                    f"{path}.reference_metadata_origin",
                    "must equal 'source_ingestion'",
                )
            )
        _validate_expression_reference_list(issues, f"{path}.referenced_variables", value.get("referenced_variables"))
        _validate_expression_reference_list(issues, f"{path}.referenced_functions", value.get("referenced_functions"))
        return
    if kind == "reviewed_raw_expression":
        if not isinstance(value.get("source"), str) or not value.get("source"):
            issues.append(ProtocolIRIssue(f"{path}.source", "must be non-empty string"))
        if not isinstance(value.get("approval_id"), str) or not value.get("approval_id"):
            issues.append(ProtocolIRIssue(f"{path}.approval_id", "must be non-empty string"))
        if not isinstance(value.get("reviewer"), str) or not value.get("reviewer"):
            issues.append(ProtocolIRIssue(f"{path}.reviewer", "must be non-empty string"))
        if value.get("reference_metadata_origin") != "reviewer_approved":
            issues.append(
                ProtocolIRIssue(
                    f"{path}.reference_metadata_origin",
                    "must equal 'reviewer_approved'",
                )
            )
        _validate_expression_reference_list(issues, f"{path}.referenced_variables", value.get("referenced_variables"))
        _validate_expression_reference_list(issues, f"{path}.referenced_functions", value.get("referenced_functions"))
        return
    issues.append(ProtocolIRIssue(f"{path}.kind", "must be a supported expression kind"))


def _validate_expression_reference_list(
    issues: list[ProtocolIRIssue],
    path: str,
    value: Any,
) -> None:
    if not isinstance(value, list):
        issues.append(ProtocolIRIssue(path, "must be an array of non-empty strings"))
        return
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(ProtocolIRIssue(f"{path}[{index}]", "must be non-empty string"))
            continue
        if item in seen:
            issues.append(ProtocolIRIssue(f"{path}[{index}]", "must not contain duplicates"))
        seen.add(item)


def _expression_semantic_context_for_ir(payload: dict[str, Any]) -> tuple[Any, dict[str, str]]:
    from fluentcoder.expressions import normalize_fluent_type_name, semantic_context_from_variables

    variable_types: dict[str, str] = {}
    for variable in payload.get("variables") or []:
        if not isinstance(variable, dict):
            continue
        name = str(variable.get("name") or "").strip()
        if not name:
            continue
        type_name = _variable_type_name(variable)
        variable_types[name] = normalize_fluent_type_name(str(type_name))
    return (
        semantic_context_from_variables(
            variable_types,
            enforce_declared_variables=True,
        ),
        variable_types,
    )


def _validate_expression_with_semantics(
    issues: list[ProtocolIRIssue],
    path: str,
    value: Any,
    *,
    semantic_context: Any,
    expected_type: Any = None,
    assignment_target: str | None = None,
) -> None:
    before = len(issues)
    _validate_expression(issues, path, value)
    if len(issues) != before:
        return
    try:
        from fluentcoder.expressions import check_expression_semantics, expression_from_mapping

        expression = expression_from_mapping(value)
        result = check_expression_semantics(
            expression,
            semantic_context,
            expected_type=expected_type,
            assignment_target=assignment_target,
        )
    except Exception as exc:
        issues.append(ProtocolIRIssue(path, f"could not semantically validate expression: {exc}"))
        return
    for issue in result.issues:
        if issue.severity != "error":
            continue
        issues.append(
            ProtocolIRIssue(
                _semantic_issue_path(path, issue.path),
                f"{issue.code}: {issue.message}",
                severity=issue.severity,
            )
        )


def _semantic_issue_path(path: str, semantic_path: str) -> str:
    if semantic_path in {"", "$"}:
        return path
    if semantic_path.startswith("$."):
        return f"{path}.{semantic_path[2:]}"
    if semantic_path.startswith("$["):
        return f"{path}{semantic_path[1:]}"
    return path


def _step_expression_expected_type(
    key: str,
    *,
    params: dict[str, Any],
    operation: str,
    variable_types: dict[str, str],
) -> tuple[Any, str | None]:
    if key == "condition_expression":
        return "boolean", None
    if key == "charge_condition_expression":
        return "boolean", None
    if key in {"value_expression", "value_expressions"} and operation in {"set_variable", "set_remaining_runtime"}:
        variable_name = str(params.get("variable") or params.get("name") or "").strip()
        return variable_types.get(variable_name), variable_name or None
    if key in {
        "volume_ul_expression",
        "volume_expressions",
        "cycles_expression",
        "well_offset_expression",
        "duration_expression",
        "delay_expression",
        "duty_cycle_expression",
        "loop_count_expression",
        "iterations_expression",
        "number_of_loops_expression",
        "count_expression",
    }:
        return "number", None
    if key == "position_expression" or key in _SITE_EXPRESSION_KEYS:
        return ("number", "string"), None
    return None, None


def _looks_like_protocol_ir(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("protocol", "worktable", "steps", "labware", "liquid_classes"))


def _require_text(issues: list[ProtocolIRIssue], path: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(ProtocolIRIssue(path, "must be a non-empty string"))


def _validate_fc_variable_name(issues: list[ProtocolIRIssue], path: str, name: str) -> None:
    if not name or not _FC_VARIABLE_NAME_RE.fullmatch(name):
        issues.append(
            ProtocolIRIssue(
                path,
                "must be a valid FluentControl variable name (letters, digits, underscore; no leading digit)",
            )
        )


def _validate_add_labware_parameters(issues: list[ProtocolIRIssue], path: str, params: Any) -> None:
    if not isinstance(params, dict):
        return
    fc_explicit = params.get("labware_type_fc_variable")
    if fc_explicit is not None and str(fc_explicit).strip():
        _validate_fc_variable_name(issues, f"{path}.labware_type_fc_variable", str(fc_explicit).strip())
    labware_type = params.get("labware_type")
    if isinstance(labware_type, str) and labware_type.startswith(FC_VAR_PREFIX):
        _validate_fc_variable_name(issues, f"{path}.labware_type", labware_type[len(FC_VAR_PREFIX):])


def _has_any_labware_target(step: dict[str, Any]) -> bool:
    return any(
        _has_value(step.get(key))
        for key in ("target_labware", "source_labware", "destination_labware")
    )


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _as_dict(value: Any) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return copy.deepcopy(value) if isinstance(value, list) else [value]


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    cleaned = cleaned.strip("._-").lower()
    return cleaned or "protocol"


def _schema_type_label(prop: dict[str, Any]) -> str:
    if "$ref" in prop:
        return str(prop["$ref"]).split("/")[-1]
    if "const" in prop:
        return f"constant `{prop['const']}`"
    value = prop.get("type")
    if isinstance(value, list):
        return " or ".join(str(item) for item in value)
    if value:
        return str(value)
    return "object"


def _register_builtin_schemas() -> None:
    PROTOCOL_IR_SCHEMAS.setdefault(PROTOCOL_IR_V1_VERSION, copy.deepcopy(PROTOCOL_IR_V1_JSON_SCHEMA))
    PROTOCOL_IR_SCHEMAS.setdefault(CURRENT_PROTOCOL_IR_VERSION, copy.deepcopy(PROTOCOL_IR_V2_JSON_SCHEMA))
    PROTOCOL_IR_MIGRATIONS.setdefault(PROTOCOL_IR_V1_VERSION, (CURRENT_PROTOCOL_IR_VERSION, _migrate_v1_to_v2))


PROTOCOL_IR_V1_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": PROTOCOL_IR_V1_SCHEMA_ID,
    "title": "Canonical Tecan Protocol IR v1",
    "description": (
        "Tool-neutral protocol plan used by protocol-builder to render Python drafts, "
        "GWL worklists, XSCR drafts, worktable reports, and handoff documentation."
    ),
    "type": "object",
    "additionalProperties": True,
    "required": [
        "ir_version",
        "id",
        "protocol",
        "source",
        "worktable",
        "labware",
        "reagents",
        "liquid_classes",
        "variables",
        "worklists",
        "dependencies",
        "safety_assumptions",
        "steps",
    ],
    "properties": {
        "ir_version": {"const": PROTOCOL_IR_V1_VERSION},
        "id": {"type": "string", "minLength": 1},
        "protocol": {"$ref": "#/$defs/protocol"},
        "source": {"$ref": "#/$defs/source"},
        "worktable": {"$ref": "#/$defs/worktable"},
        "labware": {"type": "array", "items": {"$ref": "#/$defs/labware"}},
        "reagents": {"type": "array", "items": {"$ref": "#/$defs/namedItem"}},
        "liquid_classes": {"type": "array", "items": {"$ref": "#/$defs/namedItem"}},
        "variables": {"type": "array", "items": {"$ref": "#/$defs/variable"}},
        "worklists": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "dependencies": {"type": "array", "items": {"$ref": "#/$defs/dependency"}},
        "safety_assumptions": {"type": "array", "items": {"$ref": "#/$defs/safetyAssumption"}},
        "steps": {"type": "array", "items": {"$ref": "#/$defs/step"}},
    },
    "$defs": {
        "protocol": {
            "type": "object",
            "additionalProperties": True,
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "comment": {"type": "string"},
                "schema_intent": {"type": "string"},
            },
        },
        "source": {
            "type": "object",
            "additionalProperties": True,
            "required": ["format"],
            "properties": {
                "format": {"type": "string", "minLength": 1},
                "path": {"type": "string"},
            },
        },
        "worktable": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "name": {"type": "string"},
                "guid": {"type": "string"},
                "auto_place": {"type": "boolean"},
            },
        },
        "labware": {
            "type": "object",
            "additionalProperties": True,
            "required": ["label"],
            "properties": {
                "id": {"type": "string"},
                "label": {"type": "string", "minLength": 1},
                "catalog": {"type": "string"},
                "python_class": {"type": "string"},
                "location": {"type": "string"},
                "position": {"type": ["string", "number", "integer", "null"]},
                "deck_location": {"type": "string"},
                "role": {"type": "string"},
            },
        },
        "namedItem": {
            "type": "object",
            "additionalProperties": True,
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "role": {"type": "string"},
                "guid": {"type": "string"},
            },
        },
        "variable": {
            "type": "object",
            "additionalProperties": True,
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "type": {"type": "string"},
                "scope": {"type": "string"},
                "default_value": {"type": ["string", "number", "integer", "boolean", "null"]},
                "values": {"type": "array", "items": {"type": ["string", "number", "integer", "boolean", "null"]}},
                "query_at_startup": {"type": "boolean"},
                "query_prompt": {"type": "string"},
                "read_only": {"type": "boolean"},
                "source_path": {"type": "string"},
            },
        },
        "dependency": {
            "type": "object",
            "additionalProperties": True,
            "required": ["kind"],
            "properties": {
                "kind": {"type": "string", "minLength": 1},
                "name": {"type": "string"},
                "guid": {"type": "string"},
                "required": {"type": "boolean"},
                "source_path": {"type": "string"},
            },
        },
        "safetyAssumption": {
            "type": "object",
            "additionalProperties": True,
            "required": ["text"],
            "properties": {
                "id": {"type": "string"},
                "text": {"type": "string", "minLength": 1},
            },
        },
        "step": {
            "type": "object",
            "additionalProperties": True,
            "required": ["id", "index", "group", "operation", "name", "parameters"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "index": {"type": "integer", "minimum": 1},
                "group": {"type": "string", "minLength": 1},
                "operation": {"enum": list(OPERATION_VALUES)},
                "name": {"type": "string", "minLength": 1},
                "command_id": {"type": "string"},
                "target_labware": {"type": "string"},
                "source_labware": {"type": "string"},
                "destination_labware": {"type": "string"},
                "volume_ul": {"type": ["number", "integer", "string", "null"]},
                "liquid_class": {"type": ["string", "null"]},
                "parameters": {"type": "object", "additionalProperties": True},
                "source_path": {"type": "string"},
                "compiled_path": {"type": "string"},
                "safety_flags": {"type": "array", "items": {"type": "string"}},
            },
            "allOf": [
                {
                    "if": {"properties": {"operation": {"enum": sorted(LABWARE_TARGET_OPERATIONS)}}},
                    "then": {
                        "anyOf": [
                            {"required": ["target_labware"]},
                            {"required": ["source_labware"]},
                            {"required": ["destination_labware"]},
                        ]
                    },
                },
                {
                    "if": {"properties": {"operation": {"enum": sorted(VOLUME_OPERATIONS)}}},
                    "then": {"required": ["volume_ul"]},
                },
                {
                    "if": {"properties": {"operation": {"enum": sorted(LIQUID_CLASS_OPERATIONS)}}},
                    "then": {"required": ["liquid_class"]},
                },
            ],
        },
    },
}


EXPRESSION_JSON_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"$ref": "#/$defs/stringExpression"},
        {"$ref": "#/$defs/numberExpression"},
        {"$ref": "#/$defs/booleanExpression"},
        {"$ref": "#/$defs/variableExpression"},
        {"$ref": "#/$defs/functionExpression"},
        {"$ref": "#/$defs/unaryExpression"},
        {"$ref": "#/$defs/binaryExpression"},
        {"$ref": "#/$defs/sourcePreservedExpression"},
        {"$ref": "#/$defs/reviewedRawExpression"},
    ]
}


def _build_protocol_ir_v2_schema() -> dict[str, Any]:
    schema = copy.deepcopy(PROTOCOL_IR_V1_JSON_SCHEMA)
    schema["$id"] = PROTOCOL_IR_SCHEMA_ID
    schema["title"] = "Canonical Tecan Protocol IR v2"
    schema["description"] = (
        "Tool-neutral protocol plan with typed FluentControl expressions for "
        "variable defaults, assignments, conditions, loop counts, positions, and volumes."
    )
    schema["properties"]["ir_version"] = {"const": CURRENT_PROTOCOL_IR_VERSION}
    schema["properties"]["expression_schema_version"] = {"const": EXPRESSION_SCHEMA_VERSION}

    defs = schema["$defs"]
    defs["expression"] = EXPRESSION_JSON_SCHEMA
    defs["stringExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "value"],
        "properties": {"kind": {"const": "string_literal"}, "value": {"type": "string"}},
    }
    defs["numberExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "value"],
        "properties": {"kind": {"const": "number_literal"}, "value": {"type": "number"}},
    }
    defs["booleanExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "value"],
        "properties": {"kind": {"const": "boolean_literal"}, "value": {"type": "boolean"}},
    }
    defs["variableExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "name"],
        "properties": {
            "kind": {"const": "variable_reference"},
            "name": {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$"},
        },
    }
    defs["functionExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "name", "arguments"],
        "properties": {
            "kind": {"const": "function_call"},
            "name": {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$"},
            "arguments": {"type": "array", "items": {"$ref": "#/$defs/expression"}},
        },
    }
    defs["unaryExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "operator", "operand"],
        "properties": {
            "kind": {"const": "unary_expression"},
            "operator": {"enum": ["+", "-"]},
            "operand": {"$ref": "#/$defs/expression"},
        },
    }
    defs["binaryExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "operator", "left", "right"],
        "properties": {
            "kind": {"const": "binary_expression"},
            "operator": {"enum": ["+", "-", "*", "/", "=", "<>", "<", ">", "<=", ">=", "AND", "OR"]},
            "left": {"$ref": "#/$defs/expression"},
            "right": {"$ref": "#/$defs/expression"},
        },
    }
    defs["sourcePreservedExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "source",
            "source_hash",
            "source_entry",
            "byte_stable",
            "reference_metadata_origin",
            "referenced_variables",
            "referenced_functions",
        ],
        "properties": {
            "kind": {"const": "source_preserved_expression"},
            "source": {"type": "string", "minLength": 1},
            "source_hash": {"type": "string", "pattern": r"^sha256:[0-9a-fA-F]{64}$"},
            "source_entry": {"type": "string", "minLength": 1},
            "provenance_id": {"type": "string", "pattern": r"^exprprov:[0-9a-fA-F]{64}$"},
            "byte_stable": {"type": "boolean"},
            "reference_metadata_origin": {"const": "source_ingestion"},
            "referenced_variables": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "referenced_functions": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
        },
    }
    defs["reviewedRawExpression"] = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "source",
            "approval_id",
            "reviewer",
            "reference_metadata_origin",
            "referenced_variables",
            "referenced_functions",
        ],
        "properties": {
            "kind": {"const": "reviewed_raw_expression"},
            "source": {"type": "string", "minLength": 1},
            "approval_id": {"type": "string", "minLength": 1},
            "reviewer": {"type": "string", "minLength": 1},
            "reference_metadata_origin": {"const": "reviewer_approved"},
            "referenced_variables": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "referenced_functions": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
        },
    }
    defs["loopCountExpression"] = {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "value"],
                "properties": {
                    "kind": {"const": "number_literal"},
                    "value": {"type": "integer", "minimum": 1},
                },
            },
            {"$ref": "#/$defs/variableExpression"},
        ]
    }

    defs["variable"]["properties"]["default_expression"] = {"$ref": "#/$defs/expression"}
    defs["variable"]["properties"]["value_expression"] = {"$ref": "#/$defs/expression"}
    defs["variable"]["properties"]["value_expressions"] = {
        "type": "array",
        "items": {"$ref": "#/$defs/expression"},
    }
    defs["labware"]["properties"]["position_expression"] = {"$ref": "#/$defs/expression"}
    defs["step"]["properties"]["volume_ul_expression"] = {"$ref": "#/$defs/expression"}
    parameter_properties = defs["step"]["properties"]["parameters"].setdefault("properties", {})
    for expression_key in registered_expression_keys():
        parameter_properties[expression_key] = {"$ref": "#/$defs/expression"}
    for expression_key in _LOOP_COUNT_EXPRESSION_KEYS:
        parameter_properties[expression_key] = {"$ref": "#/$defs/loopCountExpression"}
    for condition in defs["step"].get("allOf", []):
        operation_filter = (
            condition.get("if", {})
            .get("properties", {})
            .get("operation", {})
            .get("enum", [])
        )
        if set(operation_filter) != set(VOLUME_OPERATIONS):
            continue
        condition["then"] = {
            "anyOf": [
                {"required": ["volume_ul_expression"]},
                {"required": ["volume_ul"]},
                {
                    "properties": {
                        "parameters": {
                            "anyOf": [
                                {"required": ["volume_ul_expression"]},
                                {"required": ["volume_ul"]},
                            ]
                        }
                    }
                },
            ]
        }
        break
    return schema


PROTOCOL_IR_V2_JSON_SCHEMA: dict[str, Any] = _build_protocol_ir_v2_schema()


PROTOCOL_IR_BUNDLE_V1_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": PROTOCOL_IR_V1_BUNDLE_SCHEMA_ID,
    "title": "Canonical Tecan Protocol IR Bundle v1",
    "description": "Bundle emitted when a ZEIA archive contains one or more script IR documents.",
    "type": "object",
    "additionalProperties": True,
    "required": ["ir_version", "source", "protocol_count", "protocols"],
    "properties": {
        "ir_version": {"const": PROTOCOL_IR_V1_BUNDLE_VERSION},
        "source": {
            "type": "object",
            "additionalProperties": True,
            "required": ["format"],
            "properties": {
                "format": {"type": "string", "minLength": 1},
                "path": {"type": "string"},
            },
        },
        "protocol_count": {"type": "integer", "minimum": 0},
        "protocols": {
            "type": "array",
            "items": copy.deepcopy(PROTOCOL_IR_V1_JSON_SCHEMA),
        },
    },
}


PROTOCOL_IR_BUNDLE_V2_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": PROTOCOL_IR_BUNDLE_SCHEMA_ID,
    "title": "Canonical Tecan Protocol IR Bundle v2",
    "description": "Bundle emitted when a ZEIA archive contains one or more v2 script IR documents.",
    "type": "object",
    "additionalProperties": True,
    "required": ["ir_version", "source", "protocol_count", "protocols"],
    "properties": {
        "ir_version": {"const": CURRENT_PROTOCOL_IR_BUNDLE_VERSION},
        "source": copy.deepcopy(PROTOCOL_IR_BUNDLE_V1_JSON_SCHEMA["properties"]["source"]),
        "protocol_count": {"type": "integer", "minimum": 0},
        "protocols": {
            "type": "array",
            "items": copy.deepcopy(PROTOCOL_IR_V2_JSON_SCHEMA),
        },
    },
}
