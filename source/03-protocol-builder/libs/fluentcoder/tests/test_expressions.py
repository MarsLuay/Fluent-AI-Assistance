from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fluentcoder import Worktable, xml_compat as ET
from fluentcoder.compiler import render_protocol
from fluentcoder.decompiler import emit_python, parse_xscr
from fluentcoder.expressions import (
    EXPRESSION_FIELDS,
    BinaryExpression,
    BooleanLiteral,
    FunctionCall,
    NumberLiteral,
    SourcePreservedExpression,
    StringLiteral,
    UnaryExpression,
    VariableReference,
    check_expression_semantics,
    coerce_literal_expression,
    expression_from_mapping,
    expression_to_mapping,
    expression_inventory_from_xscr_text,
    expression_inventory_from_zeia,
    parse_or_preserve_source_expression,
    parse_expression,
    render_expression,
    semantic_context_from_variables,
    walk_expression_values,
)
from fluentcoder.ir.schema import (
    AddLabwareStep,
    AspirateStep,
    CalculateVariableStep,
    ConditionalStep,
    DelayStep,
    Group,
    LihaAspirateStep,
    LihaMixStep,
    LoopStep,
    MoveAxisCommandStep,
    Protocol,
    RgaTransferLabwareStep,
    SetLocationStep,
    SetVariableStep,
    SubRoutineStep,
    TeGioSetPwmOutputStep,
    VariableMapping,
    WaitForTimerStep,
    WaitStep,
)


def _xscr(value: str, *, variable: str = "FalconType", declarations: dict[str, str] | None = None) -> str:
    declaration_xml = "\n".join(
        f"""
        <anyType xmlns:i="http://www.w3.org/2001/XMLSchema-instance" i:type="VariableDefinitionHelper">
          <Name>{name}</Name>
          <TypeName>{type_name}</TypeName>
          <QueryOnStartup>false</QueryOnStartup>
          <Scope>Script</Scope>
        </anyType>
        """
        for name, type_name in (declarations or {}).items()
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Expression Test</ObjectName>
    <Comment />
    <VariableDeclarations>{declaration_xml}</VariableDeclarations>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.SetVariableStatement">
          <SetVariableStatement>
            <Name>{variable}</Name>
            <Value>{value}</Value>
            <LineNumber>65</LineNumber>
          </SetVariableStatement>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
"""


def _expression_field_xscr(command: str, field_path: str, value: str) -> str:
    parts = field_path.split("/")
    field_xml = value
    for part in reversed(parts):
        field_xml = f"<{part}>{field_xml}</{part}>"
    target = "<Name>TargetVariable</Name>" if command == "SetVariableStatement" else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Expression Field Test</ObjectName>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Test.{command}">
          <{command}>{target}{field_xml}<LineNumber>7</LineNumber></{command}>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
"""


@pytest.mark.parametrize(
    ("command", "field_path"),
    [
        (command, field_path)
        for command, field_paths in EXPRESSION_FIELDS.items()
        for field_path in field_paths
    ],
)
def test_expression_inventory_covers_every_registered_command_field(
    command: str,
    field_path: str,
) -> None:
    inventory = expression_inventory_from_xscr_text(
        _expression_field_xscr(command, field_path, "A +")
    )

    assert inventory["failure_count"] == 1
    assert inventory["failures"][0]["command"] == command
    assert inventory["failures"][0]["field"] == field_path


@pytest.mark.parametrize(
    ("expression", "rendered"),
    [
        (StringLiteral(value="50ml Falcon"), '"50ml Falcon"'),
        (NumberLiteral(value=1), "1"),
        (NumberLiteral(value=1.5), "1.5"),
        (BooleanLiteral(value=True), "True"),
        (VariableReference(name="Demo_Tube_Pos_1"), "Demo_Tube_Pos_1"),
        (FunctionCall(name="GetCoverSiteName", arguments=(StringLiteral(value="Magnet"),)), 'GetCoverSiteName("Magnet")'),
        (NumberLiteral(value=-1), "-1"),
        (NumberLiteral(value=-1.5), "-1.5"),
        (VariableReference(name="WaterVol[0]"), "WaterVol[0]"),
        (UnaryExpression(operator="-", operand=VariableReference(name="A")), "(-A)"),
        (
            UnaryExpression(
                operator="-",
                operand=BinaryExpression(
                    operator="+",
                    left=VariableReference(name="A"),
                    right=NumberLiteral(value=1),
                ),
            ),
            "(-(A + 1))",
        ),
        (
            BinaryExpression(
                operator="+",
                left=VariableReference(name="CurrentPosition"),
                right=NumberLiteral(value=1),
            ),
            "(CurrentPosition + 1)",
        ),
        (
            BinaryExpression(
                operator="AND",
                left=BinaryExpression(
                    operator="=",
                    left=VariableReference(name="InputScanBarcode"),
                    right=NumberLiteral(value=0),
                ),
                right=BinaryExpression(
                    operator="=",
                    left=VariableReference(name="InputLidRemove"),
                    right=NumberLiteral(value=0),
                ),
            ),
            "((InputScanBarcode = 0) AND (InputLidRemove = 0))",
        ),
    ],
)
def test_expression_ast_renders_and_parses(expression, rendered: str) -> None:
    assert render_expression(expression) == rendered
    assert parse_expression(rendered) == expression


@pytest.mark.parametrize(
    "legacy_value",
    ["1x10 50ml Falcon Tube", "Demo_Tube_Pos_1", "50ml Falcon"],
)
def test_legacy_set_variable_strings_render_as_string_literals(legacy_value: str) -> None:
    expression = coerce_literal_expression(legacy_value)
    assert isinstance(expression, StringLiteral)
    assert render_expression(expression) == f'"{legacy_value}"'


@pytest.mark.parametrize("source", ['"""50ml Falcon"""', '""50ml Falcon""', '"50ml Falcon', '50ml Falcon"'])
def test_malformed_expressions_are_rejected(source: str) -> None:
    with pytest.raises(Exception):
        parse_expression(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("-1", NumberLiteral(value=-1)),
        ("-1.5", NumberLiteral(value=-1.5)),
        ("-A", UnaryExpression(operator="-", operand=VariableReference(name="A"))),
        (
            "-(A + 1)",
            UnaryExpression(
                operator="-",
                operand=BinaryExpression(
                    operator="+",
                    left=VariableReference(name="A"),
                    right=NumberLiteral(value=1),
                ),
            ),
        ),
        ("1e-3", NumberLiteral(value=0.001)),
    ],
)
def test_unary_and_exponent_expressions_parse(source: str, expected) -> None:
    assert parse_expression(source) == expected


def test_expression_walker_discovers_plural_lists_and_nested_mappings() -> None:
    payload = {
        "dependencies": [{"kind": "dependency", "name": "not an expression"}],
        "variables": [
            {
                "name": "Choice",
                "value_expressions": [
                    {"kind": "string_literal", "value": "A"},
                    {"kind": "source_preserved_expression", "source": "Unsupported.Syntax(1)", "source_hash": "sha256:" + "0" * 64},
                ],
            }
        ],
        "steps": [
            {
                "parameters": {
                    "volume_expressions": [
                        {"kind": "variable_reference", "name": "WaterVol[0]"},
                        {
                            "kind": "binary_expression",
                            "operator": "+",
                            "left": {"kind": "variable_reference", "name": "WaterVol[1]"},
                            "right": {"kind": "number_literal", "value": 1},
                        },
                    ],
                    "variable_mappings_start": [
                        {
                            "target": "LocalCount",
                            "source_expression": {
                                "kind": "binary_expression",
                                "operator": "+",
                                "left": {"kind": "variable_reference", "name": "PlateCount"},
                                "right": {"kind": "number_literal", "value": 1},
                            },
                        }
                    ],
                    "variables": [
                        {
                            "name": "RuntimeChoice",
                            "value_expression": {"kind": "variable_reference", "name": "Choice"},
                        }
                    ],
                }
            }
        ],
    }

    records = list(walk_expression_values(payload))
    paths = {record.path for record in records}

    assert "$.variables[0].value_expressions[0]" in paths
    assert "$.variables[0].value_expressions[1]" in paths
    assert "$.steps[0].parameters.volume_expressions[0]" in paths
    assert "$.steps[0].parameters.volume_expressions[1]" in paths
    assert "$.steps[0].parameters.variable_mappings_start[0].source_expression" in paths
    assert "$.steps[0].parameters.variables[0].value_expression" in paths
    assert "$.steps[0].parameters.volume_expressions[1].left" not in paths
    assert all(record.expression != payload["dependencies"][0] for record in records)


def test_set_variable_step_value_is_typed_expression() -> None:
    step = SetVariableStep(variable_name="FalconType", value="50ml Falcon")
    assert isinstance(step.value, StringLiteral)
    assert step.value == "50ml Falcon"


def test_renderer_uses_expression_ast_for_set_variable() -> None:
    protocol = Protocol(
        name="Expression Protocol",
        groups=[
            Group(
                name="Steps",
                steps=[
                    SetVariableStep(variable_name="FalconType", value=StringLiteral(value="50ml Falcon")),
                    SetVariableStep(
                        variable_name="NextPosition",
                        value=BinaryExpression(
                            operator="+",
                            left=VariableReference(name="CurrentPosition"),
                            right=NumberLiteral(value=1),
                        ),
                    ),
                ],
            )
        ],
        worktable_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        worktable_name="SAT_Fluent_780_Rev3",
    )

    xml = render_protocol(protocol, deterministic=True)

    assert '<Value>"50ml Falcon"</Value>' in xml
    assert "<Value>(CurrentPosition + 1)</Value>" in xml


def test_simulator_evaluates_sequential_set_variable_expressions() -> None:
    wt = Worktable(name="Sequential expressions")
    wt.group("Assignments")
    wt.set_variable("A", 4)
    wt.set_variable("B", parse_expression("A + 1"))
    wt.set_variable("C", parse_expression("B * 2"))

    wt.simulate()

    assert wt.protocol_variables == {"A": 4, "B": 5.0, "C": 10.0}
    assert wt.sim_values == {"A": 4, "B": 5.0, "C": 10.0}


def test_parser_and_simulator_support_compact_fluent_and_condition() -> None:
    """Source XSCR commonly uses `&` instead of the textual AND token."""
    condition = parse_expression("res>1000 & simulation=0")
    assert render_expression(condition) == "((res > 1000) AND (simulation = 0))"

    wt = Worktable(name="Compact Fluent condition")
    wt.group("Setup")
    wt.set_variable("res", 1001)
    wt.set_variable("simulation", 0)
    with wt.conditional(condition=condition):
        wt.set_variable("condition_ran", True)

    wt.simulate()

    assert wt.sim_values["condition_ran"] is True


def test_simulator_evaluates_generic_set_variable_expressions() -> None:
    wt = Worktable(name="Sequential generic expressions")
    wt.group("Assignments")
    wt.generic_step("SetVariable", variable_name="A", value=4)
    wt.generic_step("SetVariable", variable_name="B", value="A + 1")
    wt.generic_step("SetVariable", variable_name="C", value="B * 2")
    wt.generic_step("SetVariable", variable_name="Zero", value=0)

    wt.simulate()

    assert wt.protocol_variables == {"A": 4, "B": 5.0, "C": 10.0, "Zero": 0}
    assert wt.sim_values == {"A": 4, "B": 5.0, "C": 10.0, "Zero": 0}


def test_simulator_string_plus_uses_fluent_string_coercion() -> None:
    wt = Worktable(name="String concatenation expressions")
    wt.group("Assignments")
    wt.set_variable("TubeNumber", 7)
    wt.set_variable("TubeLabel", parse_expression('"Tube_" + TubeNumber'))
    wt.set_variable("NextTubeLabel", parse_expression("TubeLabel + 1"))

    wt.simulate()

    assert wt.protocol_variables == {
        "TubeNumber": 7,
        "TubeLabel": "Tube_7",
        "NextTubeLabel": "Tube_71",
    }
    assert wt.sim_values == {
        "TubeNumber": 7,
        "TubeLabel": "Tube_7",
        "NextTubeLabel": "Tube_71",
    }


def test_renderer_xml_escapes_expression_text_nodes() -> None:
    protocol = Protocol(
        name="Expression XML Escape",
        groups=[
            Group(
                name="Steps",
                steps=[
                    SetVariableStep(variable_name="TextValue", value=StringLiteral(value="A & B < C > D")),
                ],
            )
        ],
        worktable_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        worktable_name="SAT_Fluent_780_Rev3",
    )

    xml = render_protocol(protocol, deterministic=True)

    assert '<Value>"A &amp; B &lt; C &gt; D"</Value>' in xml
    assert ET.fromstring(xml) is not None


def test_calculate_variable_operands_are_typed_expressions() -> None:
    step = CalculateVariableStep(target_variable="Result", operation="add", operand_a="-A", operand_b="1e-3")

    assert isinstance(step.operand_a, UnaryExpression)
    assert isinstance(step.operand_b, NumberLiteral)


def test_calculate_variable_rejects_unparseable_operands() -> None:
    with pytest.raises(Exception):
        CalculateVariableStep(target_variable="Result", operation="add", operand_a="50ml Falcon", operand_b=1)


def test_every_expression_bearing_step_field_coerces_to_ast() -> None:
    add_labware = AddLabwareStep(
        labware_type="96 Well Flat",
        label="Plate",
        location="Carrier",
        position="slot + 1",
    )
    aspirate = AspirateStep(labware_name="Plate", volume="TransferVolume")
    liha_aspirate = LihaAspirateStep(
        labware_name="Plate",
        volume="WaterVol[0]",
        well_offset="column_index * 8",
        volumes=["WaterVol[0]", "WaterVol[1]"],
    )
    liha_mix = LihaMixStep(labware_name="Plate", volume="MixVolume", cycles="MixCycles")
    rga = RgaTransferLabwareStep(
        labware_name="Plate",
        destination_location='GetCoverSiteName("Magnet")',
        destination_site='GetCoverSiteIndex("Magnet")',
    )
    wait = WaitStep(duration_seconds="DelaySeconds + 5")
    wait_for_timer = WaitForTimerStep(timer=1, duration_seconds="TimerSeconds")
    delay = DelayStep(delay="DelayMs")
    set_location = SetLocationStep(labware="Plate", location="Carrier", site="DestinationSite")
    move_axis = MoveAxisCommandStep(position="ZTarget", charge_condition="ChargeReady = 1")
    pwm = TeGioSetPwmOutputStep(pin_number="1", duty_cycle="DutyCycle", duration="PulseMs")
    loop = LoopStep(number_of_loops="cycles", steps=[])
    conditional = ConditionalStep(
        condition="InputScanBarcode = 0 AND InputLidRemove = 0",
        left_variable="InputScanBarcode",
        operator="==",
        right_value=0,
        then_steps=[],
    )
    mapping = VariableMapping(target="LocalCount", source="PlateCount + 1")

    assert isinstance(add_labware.position, BinaryExpression)
    assert isinstance(aspirate.volume, VariableReference)
    assert isinstance(liha_aspirate.volume, VariableReference)
    assert isinstance(liha_aspirate.well_offset, BinaryExpression)
    assert all(isinstance(volume, VariableReference) for volume in liha_aspirate.volumes or [])
    assert isinstance(liha_mix.volume, VariableReference)
    assert isinstance(liha_mix.cycles, VariableReference)
    assert isinstance(rga.destination_site, FunctionCall)
    assert isinstance(wait.duration_seconds, BinaryExpression)
    assert isinstance(wait_for_timer.duration_seconds, VariableReference)
    assert isinstance(delay.delay, VariableReference)
    assert isinstance(set_location.site, VariableReference)
    assert isinstance(move_axis.position, VariableReference)
    assert isinstance(move_axis.charge_condition, BinaryExpression)
    assert isinstance(pwm.duty_cycle, VariableReference)
    assert isinstance(pwm.duration, VariableReference)
    assert isinstance(loop.number_of_loops, VariableReference)
    assert isinstance(conditional.condition, BinaryExpression)
    assert isinstance(conditional.right_value, NumberLiteral)
    assert isinstance(mapping.source, BinaryExpression)


def test_decompiler_codegen_emits_expression_arguments_not_dataclass_reprs() -> None:
    protocol = Protocol(
        name="Expression Fields",
        groups=[
            Group(
                name="Steps",
                steps=[
                    WaitStep(duration_seconds="DelaySeconds + 5"),
                    LoopStep(
                        name="Repeat",
                        number_of_loops="cycles",
                        steps=[WaitStep(duration_seconds="StepDelay")],
                    ),
                    ConditionalStep(
                        name="Inputs enabled",
                        condition="InputScanBarcode = 0 AND InputLidRemove = 0",
                        left_variable="InputScanBarcode",
                        operator="==",
                        right_value=0,
                        then_steps=[WaitStep(duration_seconds=1)],
                    ),
                    LihaAspirateStep(
                        labware_name="Plate",
                        volume="WaterVol[0]",
                        well_offset="column_index * 8",
                        volumes=["WaterVol[0]", "WaterVol[1]"],
                    ),
                    SubRoutineStep(
                        subroutine="sub.xscr",
                        variable_mappings_start=[
                            VariableMapping(target="LocalCount", source="PlateCount + 1"),
                        ],
                    ),
                ],
            )
        ],
        worktable_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        worktable_name="SAT_Fluent_780_Rev3",
    )

    py_src = emit_python(protocol, source_xscr="expressions.xscr")

    assert "parse_expression('(DelaySeconds + 5)')" in py_src
    assert "condition=parse_expression('((InputScanBarcode = 0) AND (InputLidRemove = 0))')" in py_src
    assert "well_offset=parse_expression('(column_index * 8)')" in py_src
    assert "VariableMapping(target='LocalCount', source=parse_expression('(PlateCount + 1)'))" in py_src
    assert "NumberLiteral(" not in py_src
    assert "VariableReference(" not in py_src
    assert "BinaryExpression(" not in py_src


def test_loop_count_preserves_integer_arithmetic_expression() -> None:
    step = LoopStep(iterations=2, number_of_loops="TubeCount - 1", steps=[])

    assert render_expression(step.number_of_loops) == "(TubeCount - 1)"


def test_loop_renderer_preserves_declared_count_variable_exactly() -> None:
    protocol = Protocol(
        name="Loop count",
        variables=["TubeCount"],
        variable_defaults={"TubeCount": 4},
        groups=[
            Group(
                name="Steps",
                steps=[LoopStep(iterations=2, number_of_loops="TubeCount", steps=[])],
            )
        ],
        worktable_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        worktable_name="SAT_Fluent_780_Rev3",
    )

    xml = render_protocol(protocol, deterministic=True)

    assert "<NumberOfLoops>TubeCount</NumberOfLoops>" in xml
    assert "<NumberOfLoops>2</NumberOfLoops>" not in xml


def test_renderer_does_not_mutate_protocol_ir() -> None:
    step = SetVariableStep(variable_name="FalconType", value=StringLiteral(value="50ml Falcon"))
    group = Group(name="Steps", steps=[step])
    protocol = Protocol(
        name="Expression Protocol",
        groups=[group],
        worktable_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        worktable_name="SAT_Fluent_780_Rev3",
    )

    render_protocol(protocol, deterministic=True)

    assert group.line_number is None
    assert step.line_number is None
    assert step.value == StringLiteral(value="50ml Falcon")


def test_decompiler_parses_set_variable_expression_kinds(tmp_path: Path) -> None:
    xscr = tmp_path / "expressions.xscr"
    xscr.write_text(
        _xscr('"50ml Falcon"', variable="FalconType")
        .replace("</Objects>", _xscr("CurrentPosition + 1", variable="NextPosition").split("<Objects>", 1)[1].split("</Objects>", 1)[0] + "</Objects>"),
        encoding="utf-8",
    )

    steps = parse_xscr(xscr).groups[0].steps

    assert isinstance(steps[0].value, StringLiteral)
    assert steps[0].value.value == "50ml Falcon"
    assert isinstance(steps[1].value, BinaryExpression)


def test_source_preserved_expression_is_not_reviewed_raw_approval() -> None:
    expression = parse_or_preserve_source_expression("Unsupported.Syntax(1)")
    serialized = expression_to_mapping(expression)

    assert isinstance(expression, SourcePreservedExpression)
    assert expression.source_hash.startswith("sha256:")
    assert render_expression(expression) == "Unsupported.Syntax(1)"
    assert expression.referenced_variables == ()
    assert expression.referenced_functions == ("Unsupported.Syntax",)
    assert serialized["reference_metadata_origin"] == "source_ingestion"
    assert serialized["referenced_variables"] == []
    assert serialized["referenced_functions"] == ["Unsupported.Syntax"]


def test_source_preserved_expression_tracks_explicit_opaque_references() -> None:
    expression = parse_or_preserve_source_expression("Unsupported.Syntax(A, Nested.Other(B))")

    assert isinstance(expression, SourcePreservedExpression)
    assert expression.referenced_variables == ("A", "B")
    assert expression.referenced_functions == ("Unsupported.Syntax", "Nested.Other")


def test_expression_mapping_rejects_malformed_internal_states() -> None:
    with pytest.raises(ValueError, match="function_call arguments"):
        expression_from_mapping({"kind": "function_call", "name": "F", "arguments": ["not-an-expression"]})
    with pytest.raises(ValueError, match="invalid boolean_literal"):
        expression_from_mapping({"kind": "boolean_literal", "value": "maybe"})


def test_expression_semantics_detect_undefined_variables() -> None:
    context = semantic_context_from_variables({"Defined": "Integer"})

    result = check_expression_semantics(parse_expression("Missing + 1"), context)

    assert not result.valid
    assert [issue.code for issue in result.issues] == ["undefined_variable"]


def test_expression_inventory_detects_undefined_variables_without_declarations() -> None:
    inventory = expression_inventory_from_xscr_text(
        _expression_field_xscr("LoopGroup", "NumberOfLoops", "MissingVariable")
    )

    assert inventory["failure_count"] == 1
    assert inventory["failures"][0]["reason"] == "undefined_variable"
    assert inventory["failures"][0]["semantic_issues"][0]["code"] == "undefined_variable"


def test_expression_inventory_allows_explicit_permitted_host_variable_without_declarations() -> None:
    inventory = expression_inventory_from_xscr_text(
        _expression_field_xscr("LoopGroup", "NumberOfLoops", "HostLoopCount"),
        permitted_variables={"HostLoopCount": "Integer"},
    )

    assert inventory["valid"]
    assert inventory["failure_count"] == 0
    assert inventory["declaration_count"] == 0


def test_expression_semantics_resolves_indexed_variable_from_base_declaration() -> None:
    context = semantic_context_from_variables({"WaterVol": "Double"})

    result = check_expression_semantics(parse_expression("WaterVol[0] + 1"), context)

    assert result.valid
    assert result.type_name == "number"


def test_expression_semantics_detect_assignment_type_mismatch() -> None:
    context = semantic_context_from_variables({"TubeCount": "Integer"})

    result = check_expression_semantics(
        parse_expression('"50ml Falcon"'),
        context,
        expected_type="number",
        assignment_target="TubeCount",
    )

    assert not result.valid
    assert result.issues[0].code == "assignment_type_mismatch"


def test_expression_semantics_validate_function_registry_and_arguments() -> None:
    context = semantic_context_from_variables({})

    unknown = check_expression_semantics(parse_expression('UnknownFunction("x")'), context)
    bad_argument = check_expression_semantics(parse_expression("GetCoverSiteName(1)"), context)
    ceil = check_expression_semantics(parse_expression("ceil(2.1)"), context)

    assert not unknown.valid
    assert unknown.issues[0].code == "unknown_function"
    assert not bad_argument.valid
    assert bad_argument.issues[0].code == "function_argument_type_mismatch"
    assert ceil.valid
    assert ceil.type_name == "number"


def test_expression_parser_supports_fluentcontrol_exponentiation() -> None:
    expression = parse_expression("tipselection+2^(loopnum-1)")
    context = semantic_context_from_variables({"tipselection": "Integer", "loopnum": "Integer"})

    assert render_expression(expression) == "(tipselection + (2 ^ (loopnum - 1)))"
    assert check_expression_semantics(expression, context).valid


def test_expression_semantics_reject_numeric_operations_on_strings() -> None:
    result = check_expression_semantics(parse_expression('"Falcon" * 2'))

    assert not result.valid
    assert result.issues[0].code == "invalid_binary_operand"


def test_expression_semantics_allow_fluent_string_plus_numeric_coercion() -> None:
    context = semantic_context_from_variables({"tube_pos": "Integer"})

    result = check_expression_semantics(parse_expression('"bc"+tube_pos'), context, expected_type="string")

    assert result.valid
    assert result.type_name == "string"


def test_zeia_expression_inventory_scans_subroutine_entries(tmp_path: Path) -> None:
    zeia = tmp_path / "bad_expression.zeia"
    with zipfile.ZipFile(zeia, "w") as zf:
        zf.writestr("DataStore/main.xscr", _xscr('"50ml Falcon"', declarations={"FalconType": "String"}))
        zf.writestr("DataStore/subroutine.xscr", _xscr('"""50ml Falcon"""'))

    inventory = expression_inventory_from_zeia(zeia)

    assert not inventory["valid"]
    assert inventory["script_count"] == 2
    assert inventory["failures"][0]["entry"] == "DataStore/subroutine.xscr"
    assert inventory["failures"][0]["reason"] == "unexpected_quote_after_string_literal"
