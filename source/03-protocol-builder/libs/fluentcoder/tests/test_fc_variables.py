"""Tests for FluentControl variable references in labware_type fields."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import FCVariableToken, Plate96, Reagent, Worktable  # noqa: E402
from fluentcoder.compiler import render_protocol  # noqa: E402
from fluentcoder.fc_variables import decode_fc_variable, encode_fc_variable  # noqa: E402
from fluentcoder.simulator.invariants import MissingSimValueError  # noqa: E402
from tests.conftest import bind_offline_authoring  # noqa: E402


def test_xscr_variable_declarations_use_native_candidate_scan_with_python_parity(
) -> None:
    from unittest.mock import patch

    from tecan_common import native_acceleration, xml_compat, xml_helpers
    from fluentcoder.decompiler import xscr_parser

    if not native_acceleration.available() or not hasattr(
        native_acceleration._native, "xml_elements_by_name"
    ):
        pytest.skip("optional Rust XML selector is not built")

    root = xml_compat.fromstring(
        """<Root><VariableDeclarations>
          <anyType>
            <Wrapper><Name> alpha </Name><TypeName>Integer</TypeName></Wrapper>
            <Name>later-name</Name><Scope> Run </Scope>
            <Values><string> 3.9 </string><string>9</string></Values>
          </anyType>
          <anyType>
            <Name>beta</Name><TypeName>String</TypeName><Scope>Script</Scope>
            <Values><string> </string><string>later-default</string></Values>
          </anyType>
          <anyType><Name> </Name><Name>must-not-be-selected</Name></anyType>
        </VariableDeclarations></Root>"""
    )

    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = xscr_parser._parse_variable_declarations(root)
    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            xml_helpers,
            "_native_xml_elements_by_name",
            wraps=xml_helpers._native_xml_elements_by_name,
        ) as native_call:
            with patch.object(
                native_acceleration,
                "xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as native_first:
                with patch.object(
                    native_acceleration,
                    "xml_first_direct_element_by_name",
                    wraps=native_acceleration.xml_first_direct_element_by_name,
                ) as native_direct_first:
                    actual = xscr_parser._parse_variable_declarations(root)
                    assert native_call.call_count == 4
                    native_first.assert_called_once()
                    assert native_direct_first.call_count == 2

    assert actual == expected
    assert actual == (
        ["alpha", "beta", ""],
        {"alpha": 3, "beta": "", "": ""},
        {
            "alpha": {"scope": "Run", "type_name": "Integer"},
            "beta": {"scope": "Script", "type_name": "String"},
            "": {"scope": "Script", "type_name": "Floating Point"},
        },
    )


def test_xscr_optional_float_uses_native_decimal_and_python_fallbacks() -> None:
    import math
    from unittest.mock import patch

    from tecan_common import native_acceleration
    from fluentcoder.decompiler import xscr_parser

    if not native_acceleration.available() or not hasattr(
        native_acceleration._native, "parse_f64_decimal_text"
    ):
        pytest.skip("optional Rust decimal parser is not built")

    values = [
        None,
        "",
        " \u2003 ",
        "1.25",
        " -0.0 ",
        "nan",
        "inf",
        "1_000",
        "bad",
    ]
    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = [xscr_parser._parse_optional_float(value) for value in values]
    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            xscr_parser,
            "_native_parse_f64_decimal_text",
            wraps=xscr_parser._native_parse_f64_decimal_text,
        ) as native_call:
            actual = [xscr_parser._parse_optional_float(value) for value in values]
            assert native_call.call_count == 6

    for index, (actual_value, expected_value) in enumerate(zip(actual, expected)):
        if index == 5:
            assert math.isnan(actual_value)
            assert math.isnan(expected_value)
        else:
            assert actual_value == expected_value
    assert actual[3] == 1.25
    assert math.copysign(1.0, actual[4]) == -1.0
    assert math.isinf(actual[6])


def test_xscr_int_cast_uses_native_float_parser_with_fallbacks() -> None:
    from unittest.mock import patch

    from tecan_common import native_acceleration
    from fluentcoder.decompiler import xscr_parser

    if not native_acceleration.available() or not hasattr(
        native_acceleration._native, "parse_f64_decimal_text"
    ):
        pytest.skip("optional Rust decimal parser is not built")

    values = [
        None,
        "",
        "3.9",
        "-2.8",
        "0",
        "1e2",
        "nan",
        "1_000",
        " bad ",
        "bad",
    ]
    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = [xscr_parser._parse_int(value, default=77) for value in values]
    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            xscr_parser,
            "_native_parse_f64_decimal_text",
            wraps=xscr_parser._native_parse_f64_decimal_text,
        ) as native_call:
            actual = [xscr_parser._parse_int(value, default=77) for value in values]
            assert native_call.call_count == 8

    assert actual == expected
    assert actual[:6] == [77, 77, 3, -2, 0, 100]
    assert actual[-1] == 77


def test_declare_fc_variable_returns_token() -> None:
    wt = Worktable(name="fc var")
    token = wt.declare_fc_variable("PlateType")
    assert isinstance(token, FCVariableToken)
    assert token.name == "PlateType"
    assert str(token) == "PlateType"


def test_declare_fc_variable_rejects_invalid_name() -> None:
    wt = Worktable(name="fc var")
    with pytest.raises(ValueError, match="Invalid FluentControl variable name"):
        wt.declare_fc_variable("1BadName")


@pytest.mark.usefixtures("synthetic_catalog")
def test_place_with_fc_variable_token_encodes_ir_labware_type() -> None:
    wt = Worktable(name="fc plate")
    plate_type = wt.declare_fc_variable("PlateType")
    wt.declare_variable("PlateType", "96 Well Flat")
    wt.group("Setup")
    wt.place(Plate96("Source", catalog=plate_type), "Nest", 1)

    add_step = wt.to_protocol().groups[0].steps[0]
    assert add_step.labware_type == encode_fc_variable("PlateType")
    assert decode_fc_variable(add_step.labware_type) == "PlateType"


@pytest.mark.usefixtures("synthetic_catalog")
def test_add_labware_accepts_fc_variable_token() -> None:
    wt = Worktable(name="fc add")
    plate_type = wt.declare_fc_variable("PlateType")
    wt.declare_variable("PlateType", "96 Well Flat")
    wt.group("Setup")
    wt.add_labware(plate_type, "Source", "Nest", 1)

    add_step = wt.to_protocol().groups[0].steps[0]
    assert decode_fc_variable(add_step.labware_type) == "PlateType"


@pytest.mark.usefixtures("synthetic_catalog")
def test_compile_emits_fc_variable_name_not_resolved_literal(tmp_path: Path) -> None:
    wt = Worktable.from_workspace(
        "SAT_Fluent_780_Rev3",
        workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        auto_place=False,
        protocol_name="fc variable plate",
    )
    plate_type = wt.declare_fc_variable("PlateType")
    wt.declare_variable("PlateType", "96 Well Flat")
    wt.group("Setup")
    wt.place(Plate96("Source", catalog=plate_type), "Nest61mm_Pos", 1)

    out = tmp_path / "fc_variable_plate.xscr"
    wt.compile(out)
    xml = out.read_text(encoding="utf-8")

    assert "<LabwareType>PlateType</LabwareType>" in xml
    assert "<LabwareType>96 Well Flat</LabwareType>" not in xml
    assert "<d3p1:Name>PlateType</d3p1:Name>" in xml
    assert "<d3p1:TypeName>String</d3p1:TypeName>" in xml


@pytest.mark.usefixtures("synthetic_catalog")
def test_simulate_resolves_fc_labware_type_from_protocol_default() -> None:
    wt = Worktable(name="fc sim default")
    plate_type = wt.declare_fc_variable("PlateType")
    wt.declare_variable("PlateType", "96 Well Flat")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog=plate_type), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.simulate()
    twin = wt.snapshots[-1].labware("Source")
    assert twin.catalog_name == "96 Well Flat"


@pytest.mark.usefixtures("synthetic_catalog")
def test_simulate_resolves_fc_labware_type_from_set_sim_value() -> None:
    wt = Worktable(name="fc sim override")
    plate_type = wt.declare_fc_variable("PlateType")
    wt.declare_variable("PlateType", "96 Well Flat")
    wt.set_sim_value("PlateType", "96 Well Flat")
    wt.group("Setup")
    wt.place(Plate96("Source", catalog=plate_type), "Nest", 1)

    wt.simulate()
    twin = wt.snapshots[-1].labware("Source")
    assert twin.catalog_name == "96 Well Flat"


@pytest.mark.usefixtures("synthetic_catalog")
def test_simulate_missing_fc_labware_type_value_raises() -> None:
    wt = Worktable(name="fc sim missing")
    plate_type = wt.declare_fc_variable("PlateType")
    wt.group("Setup")
    wt.place(Plate96("Source", catalog=plate_type), "Nest", 1)

    with pytest.raises(MissingSimValueError, match="PlateType"):
        wt.simulate()


def test_render_protocol_fc_variable_via_ir_only() -> None:
    from fluentcoder.ir.schema import AddLabwareStep, Group, Protocol

    protocol = Protocol(
        name="IR fc variable",
        variables=["PlateType"],
        variable_defaults={"PlateType": "96 Well Flat"},
        groups=[
            Group(
                name="Setup",
                steps=[
                    AddLabwareStep(
                        labware_type=encode_fc_variable("PlateType"),
                        label="Source",
                        location="Nest",
                        position=1,
                    )
                ],
            )
        ],
        worktable_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        worktable_name="SAT_Fluent_780_Rev3",
    )
    xml = render_protocol(protocol, deterministic=True)
    assert "<LabwareType>PlateType</LabwareType>" in xml
    assert "<LabwareType>96 Well Flat</LabwareType>" not in xml


def test_declare_variable_preserves_scope_and_type_metadata() -> None:
    wt = bind_offline_authoring(Worktable(name="metadata"), with_device=False)
    wt.declare_variable("LASTBARCODE", "NOBARCODE", scope="Run", type_name="String")
    protocol = wt.to_protocol()

    assert protocol.variable_metadata["LASTBARCODE"] == {"scope": "Run", "type_name": "String"}

    xml = render_protocol(protocol, deterministic=True)
    assert "<d3p1:Name>LASTBARCODE</d3p1:Name>" in xml
    assert "<d3p1:Scope>Run</d3p1:Scope>" in xml
    assert "<d3p1:TypeName>String</d3p1:TypeName>" in xml


def test_place_resolves_cover_site_expression_but_preserves_emitted_expression() -> None:
    wt = Worktable(name="cover site")
    wt.group("Setup")
    adapter_type = wt.declare_fc_variable("ParkAdapterType")
    rack_type = wt.declare_fc_variable("RackType")
    adapter = wt.place(Plate96("ParkAdapter", catalog=adapter_type), "Nest7mm_Pos", 17)
    rack = wt.place(
        Plate96("ElutionRack", catalog=rack_type),
        'GetCoverSiteName("ParkAdapter")',
        'GetCoverSiteIndex("ParkAdapter")',
    )

    assert rack.slot == ("Nest7mm_Pos", 17)
    assert rack.stack_below == [adapter]

    emitted = wt.to_protocol().groups[0].steps[-1]
    assert emitted.location == 'GetCoverSiteName("ParkAdapter")'
    assert emitted.position == 'GetCoverSiteIndex("ParkAdapter")'


def test_place_resolves_native_cover_site_name_with_numeric_position() -> None:
    wt = Worktable(name="cover site numeric")
    wt.group("Setup")
    adapter_type = wt.declare_fc_variable("ParkAdapterType")
    rack_type = wt.declare_fc_variable("RackType")
    adapter = wt.place(Plate96("ParkAdapter", catalog=adapter_type), "Nest7mm_Pos", 17)
    rack = wt.place(
        Plate96("ElutionRack", catalog=rack_type),
        'GetCoverSiteName("ParkAdapter")',
        1,
    )

    assert rack.slot == ("Nest7mm_Pos", 17)
    assert rack.stack_below == [adapter]
    emitted = wt.to_protocol().groups[0].steps[-1]
    assert emitted.location == 'GetCoverSiteName("ParkAdapter")'
    assert emitted.position == 1


def test_user_prompt_accepts_explicit_auto_close_false() -> None:
    wt = bind_offline_authoring(Worktable(name="prompt metadata"), with_device=False)
    wt.group("Prompts")
    wt.user_prompt("Review the deck", timeout=1, auto_close=False)

    step = wt.to_protocol().groups[0].steps[0]
    assert step.auto_close is False

    xml = render_protocol(wt.to_protocol(), deterministic=True)
    assert "<AutoClose>False</AutoClose>" in xml
    assert "<Timeout>1</Timeout>" in xml


def test_integer_variable_metadata_uses_integer_startup_default() -> None:
    from fluentcoder.ir.schema import Protocol

    protocol = Protocol(
        name="integer metadata",
        variables=["NumCycles"],
        variable_defaults={"NumCycles": 1.0},
        variable_metadata={"NumCycles": {"type_name": "Integer"}},
        worktable_guid="11111111-1234-aaaa-ffff-000000000222",
        worktable_name="Synthetic Offline Workspace",
    )

    xml = render_protocol(protocol, deterministic=True)

    assert "<d3p1:TypeName>Integer</d3p1:TypeName>" in xml
    assert "<d2p1:string>1</d2p1:string>" in xml
    assert "<d2p1:string>1.0</d2p1:string>" not in xml


def test_decompile_preserves_variable_metadata_and_array_declarations(tmp_path: Path) -> None:
    from fluentcoder.decompiler.xscr_parser import parse_xscr

    source = tmp_path / "variable_metadata.xscr"
    source.write_text(
        """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<VxData>
  <Payload><ObjectName>variable metadata</ObjectName></Payload>
  <PayloadData><VariableDeclarations><VariableDeclarations><VariableDeclarations>
    <anyType><Name>RunCount</Name><Scope>Run</Scope><TypeName>Integer</TypeName><Values><string>2</string></Values></anyType>
    <anyType><Name>WaterVol[]</Name><Scope>Script</Scope><TypeName>Floating Point</TypeName><Values><string>0</string></Values></anyType>
  </VariableDeclarations></VariableDeclarations></VariableDeclarations></PayloadData>
</VxData>""",
        encoding="utf-8",
    )

    protocol = parse_xscr(source)
    protocol.worktable_guid = "11111111-1234-aaaa-ffff-000000000222"
    protocol.worktable_name = "Synthetic Offline Workspace"

    assert protocol.variable_metadata == {
        "RunCount": {"scope": "Run", "type_name": "Integer"},
        "WaterVol[]": {"scope": "Script", "type_name": "Floating Point"},
    }
    xml = render_protocol(protocol, deterministic=True)
    assert "<d3p1:Name>RunCount</d3p1:Name>" in xml
    assert "<d3p1:Scope>Run</d3p1:Scope>" in xml
    assert "<d3p1:TypeName>Integer</d3p1:TypeName>" in xml
    assert "<d3p1:Name>WaterVol[]</d3p1:Name>" in xml


def test_decompiler_codegen_emits_fc_variable_catalog() -> None:
    from fluentcoder.decompiler import emit_python
    from fluentcoder.ir.schema import AddLabwareStep, Group, Protocol

    protocol = Protocol(
        name="Decompile fc variable",
        variables=["PlateType"],
        variable_defaults={"PlateType": "96 Well Flat"},
        groups=[
            Group(
                name="Setup",
                steps=[
                    AddLabwareStep(
                        labware_type=encode_fc_variable("PlateType"),
                        label="Source",
                        location="Nest61mm_Pos",
                        position=1,
                    )
                ],
            )
        ],
        worktable_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
        worktable_name="SAT_Fluent_780_Rev3",
    )
    rendered = emit_python(protocol, source_xscr="fc_variable_plate.xscr")
    assert "platetype = wt.declare_fc_variable('PlateType')" in rendered
    assert "source = wt.place(Plate96('Source', catalog=platetype)," in rendered
    assert 'catalog="@fc:PlateType"' not in rendered
    assert 'catalog="96 Well Flat"' not in rendered

