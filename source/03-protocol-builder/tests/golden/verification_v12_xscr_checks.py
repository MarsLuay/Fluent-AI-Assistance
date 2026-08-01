"""Synthetic XSCR fragment checks for optional private golden regressions.

These helpers assert structural CapBC/gripper/RUP invariants against XSCR text.
They are not a lab protocol and do not bake deck names or grip widths as product
defaults. Private end-to-end goldens may pass expected values mined from their
own ZEIA/spec via ``TECAN_ENABLE_PRIVATE_GOLDENS`` (see fixtures/README.md).
"""

from __future__ import annotations

import re
from typing import Any

CAPBC_SUBROUTINE_MARKER = "CapBC"
EXPECTED_CAPBC_INPUT_SUB_LOCATION_COUNT = 5

CONFIGURE_DATA_LABWARE_BLOCK = re.compile(
    r"<ConfigureDataLabwareDataModel\b.*?</ConfigureDataLabwareDataModel>",
    re.DOTALL,
)
CAPBC_SUBROUTINE_BLOCK = re.compile(
    r"<SubRoutineStatement>.*?CapBC.*?</SubRoutineStatement>",
    re.DOTALL | re.IGNORECASE,
)
VARIABLE_DEFAULT = re.compile(
    r"<(?:[\w]+:)?Name>({name})</(?:[\w]+:)?Name>.*?"
    r"<(?:[\w]+:)?Values>\s*<(?:[\w]+:)?string>([^<]+)</(?:[\w]+:)?string>",
    re.DOTALL,
)
INPUT_SUB_LOCATION_QUOTED_LITERAL = re.compile(
    r'<Target>InputSubLocation</Target>\s*<Source>"([^"]+)"</Source>',
    re.DOTALL,
)


def startup_variable_value(xscr_text: str, name: str) -> str | None:
    pattern = VARIABLE_DEFAULT.pattern.format(name=re.escape(name))
    match = re.search(pattern, xscr_text, re.DOTALL)
    if not match:
        return None
    return match.group(2).strip()


def assert_rup_configure_data_labware_booleans_are_lowercase(xscr_text: str) -> None:
    blocks = CONFIGURE_DATA_LABWARE_BLOCK.findall(xscr_text)
    if not blocks:
        raise AssertionError("expected at least one ConfigureDataLabwareDataModel block in XSCR")
    for index, block in enumerate(blocks):
        if ">True<" in block or ">False<" in block:
            raise AssertionError(
                "ConfigureDataLabwareDataModel block "
                f"{index + 1} still contains Python-style True/False booleans"
            )
        if not re.search(r"<IsCustomDetailImageUsed>(?:true|false)</IsCustomDetailImageUsed>", block):
            raise AssertionError(
                "ConfigureDataLabwareDataModel block "
                f"{index + 1} is missing lowercase IsCustomDetailImageUsed boolean"
            )


def assert_gripper_prep_defaults(
    xscr_text: str,
    *,
    expected_close: float | None = None,
    expected_open: float | None = None,
) -> None:
    """Require mined/explicit non-zero grippers; optionally pin private-golden values."""
    close = startup_variable_value(xscr_text, "GripperClose")
    open_ = startup_variable_value(xscr_text, "GripperOpen")
    if close is None:
        raise AssertionError("GripperClose startup variable not found in XSCR")
    if open_ is None:
        raise AssertionError("GripperOpen startup variable not found in XSCR")
    close_value = float(close)
    open_value = float(open_)
    if close_value == 0.0:
        raise AssertionError(f"GripperClose remains zero-like: {close!r}")
    if open_value == 0.0:
        raise AssertionError(f"GripperOpen remains zero-like: {open_!r}")
    if expected_close is not None and close_value != float(expected_close):
        raise AssertionError(f"GripperClose expected {expected_close}, got {close!r}")
    if expected_open is not None and open_value != float(expected_open):
        raise AssertionError(f"GripperOpen expected {expected_open}, got {open_!r}")


def assert_capbc_input_sub_location_literals(
    xscr_text: str,
    *,
    expected_location: str | None = None,
    expected_count: int = EXPECTED_CAPBC_INPUT_SUB_LOCATION_COUNT,
) -> None:
    """Require CapBC InputSubLocation mappings to be quoted deck literals (not vars/0)."""
    capbc_blocks = CAPBC_SUBROUTINE_BLOCK.findall(xscr_text)
    if not capbc_blocks:
        raise AssertionError("expected CapBC subroutine statement(s) in XSCR")
    literal_count = 0
    seen_locations: set[str] = set()
    for block in capbc_blocks:
        match = INPUT_SUB_LOCATION_QUOTED_LITERAL.search(block)
        if match:
            location = match.group(1).strip()
            if not location or location in {"0", "0.0"}:
                raise AssertionError(f"CapBC InputSubLocation quoted literal is invalid: {location!r}")
            if expected_location is not None and location != expected_location:
                raise AssertionError(
                    f'CapBC InputSubLocation expected "{expected_location}", got {location!r}'
                )
            seen_locations.add(location)
            literal_count += 1
        elif re.search(
            r"<Target>InputSubLocation</Target>\s*<Source>TubeLocationName</Source>",
            block,
        ):
            raise AssertionError(
                "CapBC InputSubLocation still maps TubeLocationName instead of a deck literal"
            )
        elif re.search(
            r"<Target>InputSubLocation</Target>\s*<Source>0</Source>",
            block,
        ):
            raise AssertionError("CapBC InputSubLocation still maps literal 0")
    if literal_count < expected_count:
        raise AssertionError(
            f"expected {expected_count} CapBC InputSubLocation quoted literal(s), "
            f"found {literal_count} ({sorted(seen_locations)!r})"
        )


def assert_ir_capbc_prep_metadata(
    ir: dict[str, Any],
    *,
    expected_close: float | None = None,
    expected_open: float | None = None,
    expected_location: str | None = None,
) -> None:
    report = ir.get("source", {}).get("subroutine_deck_locations") or {}
    prep = report.get("prep_variables") or {}
    close = float(prep.get("GripperClose", 0) or 0)
    open_ = float(prep.get("GripperOpen", 0) or 0)
    location = str(prep.get("TubeLocationName") or "").strip()
    if close == 0.0:
        raise AssertionError(f"IR prep_variables.GripperClose remains zero-like: {prep!r}")
    if open_ == 0.0:
        raise AssertionError(f"IR prep_variables.GripperOpen remains zero-like: {prep!r}")
    if not location or location in {"0", "0.0"}:
        raise AssertionError(f"IR prep_variables.TubeLocationName missing/invalid: {prep!r}")
    if expected_close is not None and close != float(expected_close):
        raise AssertionError(f"IR prep_variables.GripperClose expected {expected_close}, got {prep!r}")
    if expected_open is not None and open_ != float(expected_open):
        raise AssertionError(f"IR prep_variables.GripperOpen expected {expected_open}, got {prep!r}")
    if expected_location is not None and location != expected_location:
        raise AssertionError(
            f'IR prep_variables.TubeLocationName expected "{expected_location}", got {prep!r}'
        )


def assert_ir_capbc_mappings_have_no_capholderoffset(ir: dict[str, Any]) -> None:
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        subroutine = str(params.get("subroutine") or "")
        if CAPBC_SUBROUTINE_MARKER.casefold() not in subroutine.casefold():
            continue
        for section in ("variable_mappings_start", "variable_mappings_end"):
            mappings = params.get(section) or []
            for item in mappings:
                if isinstance(item, dict) and str(item.get("target") or "") == "capholderoffset":
                    raise AssertionError(
                        f"CapBC IR step still maps stale target capholderoffset in {section}"
                    )


def assert_capbc_prep_set_variable_commands(
    xscr_text: str,
    *,
    expected_close: float | None = None,
    expected_location: str | None = None,
) -> None:
    commands = xscr_text.split("<Commands>", 1)[1].split("</Commands>", 1)[0] if "<Commands>" in xscr_text else xscr_text
    close_match = re.search(
        r"<SetVariableStatement>\s*<Name>GripperClose</Name>\s*<Value>([^<]+)</Value>",
        commands,
        re.DOTALL,
    )
    if not close_match:
        raise AssertionError("expected compiled SetVariable GripperClose command in XSCR Commands")
    close_value = float(close_match.group(1).strip())
    if close_value == 0.0:
        raise AssertionError(f"compiled GripperClose SetVariable remains zero-like: {close_match.group(1)!r}")
    if expected_close is not None and close_value != float(expected_close):
        raise AssertionError(f"compiled GripperClose expected {expected_close}, got {close_match.group(1)!r}")

    location_match = re.search(
        r'<SetVariableStatement>\s*<Name>TubeLocationName</Name>\s*<Value>"([^"]+)"</Value>',
        commands,
        re.DOTALL,
    )
    if not location_match:
        raise AssertionError("expected compiled SetVariable TubeLocationName command in XSCR Commands")
    location = location_match.group(1).strip()
    if not location or location in {"0", "0.0"}:
        raise AssertionError(f"compiled TubeLocationName SetVariable invalid: {location!r}")
    if expected_location is not None and location != expected_location:
        raise AssertionError(f'compiled TubeLocationName expected "{expected_location}", got {location!r}')

    def _line_number(block: str) -> int | None:
        match = re.search(r"<LineNumber>(\d+)</LineNumber>", block)
        return int(match.group(1)) if match else None

    capbc_lines = [
        _line_number(block)
        for block in re.findall(r"<SubRoutineStatement>.*?</SubRoutineStatement>", commands, re.DOTALL | re.IGNORECASE)
        if "capbc" in block.casefold()
    ]
    gripper_blocks = re.findall(
        r"<SetVariableStatement>\s*<Name>GripperClose</Name>.*?</SetVariableStatement>",
        commands,
        re.DOTALL,
    )
    gripper_line = _line_number(gripper_blocks[0]) if gripper_blocks else None
    first_capbc_line = min(line for line in capbc_lines if line is not None) if capbc_lines else None
    if first_capbc_line is None or gripper_line is None or gripper_line >= first_capbc_line:
        raise AssertionError("expected CapBC prep SetVariable steps before first CapBC subroutine call")


def assert_ir_capbc_prep_set_variable_steps(ir: dict[str, Any]) -> None:
    emitted = (ir.get("source", {}).get("subroutine_deck_locations") or {}).get("prep_steps_emitted") or []
    if not emitted:
        raise AssertionError("expected auto-emitted CapBC prep set_variable steps in IR report")
    set_steps = [
        step
        for step in ir.get("steps") or []
        if isinstance(step, dict) and step.get("operation") == "set_variable"
    ]
    if not set_steps:
        raise AssertionError("expected set_variable IR steps for CapBC prep")
    variables = {step["parameters"]["variable"] for step in set_steps if isinstance(step.get("parameters"), dict)}
    for required in ("TubeLocationName", "GripperClose", "GripperOpen"):
        if required not in variables:
            raise AssertionError(f"missing CapBC prep set_variable step for {required}")


def assert_subroutine_load_review_identity_clean(validation_report: dict[str, Any]) -> None:
    gates = validation_report.get("gates") or []
    gate = next(
        (
            item
            for item in gates
            if item.get("id") in {"subroutine_calls_resolve", "subroutine_dependencies_valid"}
        ),
        None,
    )
    if gate is None:
        raise AssertionError("subroutine dependency validation gate missing from validation report")
    identity = (gate.get("details") or {}).get("subroutine_identity") or {}
    mismatch_count = int(identity.get("mismatch_count") or 0)
    if mismatch_count:
        findings = identity.get("findings") or []
        raise AssertionError(
            f"subroutine_load_review subroutine identity reported {mismatch_count} mismatch(es): {findings[:3]!r}"
        )


def assert_all_verification_v12_golden_xscr_checks(
    xscr_text: str,
    ir: dict[str, Any] | None = None,
    validation_report: dict[str, Any] | None = None,
    *,
    expected_close: float | None = None,
    expected_open: float | None = None,
    expected_location: str | None = None,
) -> None:
    assert_rup_configure_data_labware_booleans_are_lowercase(xscr_text)
    assert_gripper_prep_defaults(
        xscr_text,
        expected_close=expected_close,
        expected_open=expected_open,
    )
    assert_capbc_input_sub_location_literals(xscr_text, expected_location=expected_location)
    assert_capbc_prep_set_variable_commands(
        xscr_text,
        expected_close=expected_close,
        expected_location=expected_location,
    )
    if ir is not None:
        assert_ir_capbc_prep_metadata(
            ir,
            expected_close=expected_close,
            expected_open=expected_open,
            expected_location=expected_location,
        )
        assert_ir_capbc_prep_set_variable_steps(ir)
        assert_ir_capbc_mappings_have_no_capholderoffset(ir)
    if validation_report is not None:
        assert_subroutine_load_review_identity_clean(validation_report)
