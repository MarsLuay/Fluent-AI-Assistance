"""Canonical FluentControl command fields that contain expressions."""

from __future__ import annotations

from typing import Iterable

from .ast import BinaryExpression, Expression, NumberLiteral, VariableReference


EXPRESSION_FIELDS: dict[str, tuple[str, ...]] = {
    "SetVariableStatement": ("Value",),
    "ConditionalGroup": ("Condition",),
    "LoopGroup": ("NumberOfLoops",),
    "AddLabwareDataV1": ("Position",),
    "Mca384Aspirate": ("Volume",),
    "Mca384Dispense": ("Volume",),
    "Mca384Mix": ("Volume", "Cycles"),
    "Mca384EmptyTips": ("Volume",),
    "LihaAspirate": ("Volume", "Volumes/string", "WellOffset"),
    "LihaDispense": ("Volume", "Volumes/string", "WellOffset"),
    "LihaMix": ("Volume", "Volumes/string", "Cycles", "WellOffset"),
    "LihaEmptyTips": ("Volume",),
    "Wait": ("Duration",),
    "WaitForTimer": ("Duration",),
    "Delay": ("Delay",),
    "SetLocation": ("Site",),
    "VariableMapping": ("Source",),
    "TeGioSetPWMOutput": ("DutyCycle", "Duration"),
    "MoveAxisCommand": ("Position", "ChargeCondition"),
}

_FIELD_TO_IR_KEY = {
    "Value": "value_expression",
    "Condition": "condition_expression",
    "NumberOfLoops": "number_of_loops_expression",
    "Position": "position_expression",
    "Volume": "volume_ul_expression",
    "Volumes/string": "volume_expressions",
    "Cycles": "cycles_expression",
    "WellOffset": "well_offset_expression",
    "Duration": "duration_expression",
    "Delay": "delay_expression",
    "Site": "site_expression",
    "Source": "source_expression",
    "DutyCycle": "duty_cycle_expression",
    "ChargeCondition": "charge_condition_expression",
}


def canonical_expression_command_id(command_id: str) -> str:
    """Normalize versioned/runtime command ids to an EXPRESSION_FIELDS key."""
    command = str(command_id or "").rsplit(".", 1)[-1]
    checks = (
        ("SetVariable", "SetVariableStatement"),
        ("ConditionalGroup", "ConditionalGroup"),
        ("LoopGroup", "LoopGroup"),
        ("AddLabware", "AddLabwareDataV1"),
        ("Mca384Aspirate", "Mca384Aspirate"),
        ("Mca384Dispense", "Mca384Dispense"),
        ("Mca384Mix", "Mca384Mix"),
        ("Mca384EmptyTips", "Mca384EmptyTips"),
        ("LihaAspirate", "LihaAspirate"),
        ("LihaDispense", "LihaDispense"),
        ("LihaMix", "LihaMix"),
        ("LihaEmptyTips", "LihaEmptyTips"),
        ("WaitForTimer", "WaitForTimer"),
        ("Wait", "Wait"),
        ("Delay", "Delay"),
        ("SetLocation", "SetLocation"),
        ("VariableMapping", "VariableMapping"),
        ("TeGioSetPWMOutput", "TeGioSetPWMOutput"),
        ("MoveAxisCommand", "MoveAxisCommand"),
    )
    for prefix, canonical in checks:
        if command.casefold().startswith(prefix.casefold()):
            return canonical
    return command


def expression_fields_for_command(command_id: str) -> tuple[str, ...]:
    return EXPRESSION_FIELDS.get(canonical_expression_command_id(command_id), ())


def is_expression_field(command_id: str, field_path: str) -> bool:
    return field_path in expression_fields_for_command(command_id)


def canonical_expression_key(field_path: str) -> str:
    return _FIELD_TO_IR_KEY[field_path]


def registered_expression_keys() -> frozenset[str]:
    return frozenset(
        canonical_expression_key(field)
        for fields in EXPRESSION_FIELDS.values()
        for field in fields
    )


def registered_expression_field_paths() -> tuple[str, ...]:
    return tuple(_dedupe(field for fields in EXPRESSION_FIELDS.values() for field in fields))


def loop_count_expression_error(expression: Expression) -> str | None:
    """Return why an expression cannot be used as FluentControl NumberOfLoops."""
    if isinstance(expression, VariableReference):
        return None
    if isinstance(expression, NumberLiteral):
        value = expression.value
        if isinstance(value, int) and value >= 1:
            return None
        if isinstance(value, float) and value.is_integer() and value >= 1:
            return None
        return "NumberOfLoops numeric literals must be positive integers"
    if _is_integer_loop_arithmetic(expression):
        return None
    return (
        "NumberOfLoops supports only a positive integer literal, variable reference, "
        "or integer arithmetic expression"
    )


def _is_integer_loop_arithmetic(expression: Expression) -> bool:
    """Accept source-supported arithmetic bounds while retaining numeric terms.

    FluentControl sources use bounds such as ``numFilterPlates-1``.  A static
    validator cannot prove their runtime positivity, but it can ensure that
    the expression is integer arithmetic rather than an arbitrary function or
    string expression.
    """
    if isinstance(expression, VariableReference):
        return True
    if isinstance(expression, NumberLiteral):
        value = expression.value
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if isinstance(expression, BinaryExpression):
        return (
            expression.operator in {"+", "-", "*"}
            and _is_integer_loop_arithmetic(expression.left)
            and _is_integer_loop_arithmetic(expression.right)
        )
    return False


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
