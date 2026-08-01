from __future__ import annotations

import hashlib
import html
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from fluent_pipeline.expression_provenance import (
    bind_protocol_ir_expression_provenance,
    build_expression_provenance_ledger,
    write_expression_provenance_ledger,
)
from fluent_pipeline.exports import verify_generated_project_archive


def _xscr(
    value: str,
    *,
    name: str,
    variable: str = "FalconType",
    declarations: dict[str, str] | None = None,
) -> str:
    declaration_xml = "\n".join(
        f"""
        <anyType xmlns:i="http://www.w3.org/2001/XMLSchema-instance" i:type="VariableDefinitionHelper">
          <Name>{html.escape(var_name)}</Name>
          <TypeName>{html.escape(type_name)}</TypeName>
          <QueryOnStartup>false</QueryOnStartup>
          <Scope>Script</Scope>
        </anyType>
        """
        for var_name, type_name in (declarations or {}).items()
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{name}</ObjectName>
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


def _loop_xscr(number_of_loops: str, *, name: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{name}</ObjectName>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.LoopGroup">
          <LoopGroup>
            <Name>Loop</Name>
            <NumberOfLoops>{number_of_loops}</NumberOfLoops>
            <Objects />
            <LineNumber>4</LineNumber>
          </LoopGroup>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
"""


def _move_axis_xscr(charge_condition: str, *, name: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{name}</ObjectName>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.MoveAxisCommandScriptStatement">
          <MoveAxisCommandScriptStatement>
            <Position>1</Position>
            <ChargeCondition>
              <ChargeCondition>{charge_condition}</ChargeCondition>
            </ChargeCondition>
            <LineNumber>9</LineNumber>
          </MoveAxisCommandScriptStatement>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
"""


def _write_source_preserved_protocol_ir(
    bundle_root: Path,
    *,
    entry: str,
    source: str,
    command: str = "SetVariableStatement",
    field_key: str = "value_expression",
    source_hash: str | None = None,
    provenance_id: str | None = None,
) -> None:
    expression = {
        "kind": "source_preserved_expression",
        "source": source,
        "source_hash": source_hash
        or "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "source_entry": entry,
        "byte_stable": True,
    }
    if provenance_id:
        expression["provenance_id"] = provenance_id
    (bundle_root / "protocol.ir.json").write_text(
        json.dumps(
            {
                "ir_version": "tecan.protocol_ir.v2",
                "steps": [
                    {
                        "operation": "set_variable",
                        "command_id": command,
                        "parameters": {
                            field_key: expression,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_verified_source_preservation(
    bundle_root: Path,
    *,
    source: str,
    source_archive_entry: str = "DataStore/main.xscr",
    generated_command: str = "SetVariableStatement",
    field_key: str = "value_expression",
) -> tuple[Path, Path]:
    original_sources = bundle_root / "source" / "original-sources"
    reports = bundle_root / "source" / "reports"
    original_sources.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    source_archive = original_sources / "source_project_1.zeia"
    source_name = Path(source_archive_entry).stem
    with zipfile.ZipFile(source_archive, "w") as zf:
        zf.writestr(
            source_archive_entry,
            _xscr(source, name=source_name, declarations={"FalconType": "String"}),
        )
    ledger = build_expression_provenance_ledger([source_archive])
    assert len(ledger["records"]) == 1
    protocol_ir = {
        "ir_version": "tecan.protocol_ir.v2",
        "steps": [
            {
                "operation": "set_variable",
                "command_id": generated_command,
                "parameters": {
                    "line_number": 65,
                    "command_index": 1,
                    field_key: {
                        "kind": "source_preserved_expression",
                        "source": source,
                        "source_hash": "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "source_entry": f"{source_archive.name}!{source_archive_entry}",
                        "byte_stable": True,
                    },
                },
            }
        ],
    }
    bound_ir = bind_protocol_ir_expression_provenance(protocol_ir, ledger)
    provenance_id = bound_ir["steps"][0]["parameters"][field_key].get("provenance_id")
    assert provenance_id == ledger["records"][0]["record_id"]
    (bundle_root / "protocol.ir.json").write_text(json.dumps(bound_ir), encoding="utf-8")
    ledger_path = reports / "expression_provenance.json"
    write_expression_provenance_ledger(ledger_path, ledger)
    return source_archive, ledger_path


def _xscr_with_repeated_expression(value: str, *, name: str) -> str:
    first = _xscr(value, name=name, declarations={"FalconType": "String"})
    duplicate = f"""
        <Object Type="Tecan.Core.Scripting.SetVariableStatement">
          <SetVariableStatement>
            <Name>FalconType</Name>
            <Value>{value}</Value>
            <LineNumber>66</LineNumber>
          </SetVariableStatement>
        </Object>
"""
    return first.replace(
        "      </Objects>",
        duplicate + "      </Objects>",
        1,
    )


def test_generated_project_archive_blocks_malformed_loop_expression(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _loop_xscr("A +", name="Main"))

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["command"] == "LoopGroup"
    assert invalid[0]["field"] == "NumberOfLoops"
    assert audit["expression_inventory"]["failure_count"] == 1


def test_generated_project_archive_demotes_inherited_base_export_expressions(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/owned.xscr", _loop_xscr("1", name="Owned"))
        zf.writestr("DataStore/carryover.xscr", _loop_xscr("A +", name="CarryOver"))

    audit = verify_generated_project_archive(
        archive,
        bundle_root=tmp_path,
        owned_entries=["DataStore/owned.xscr"],
    )

    blocking_invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    review_invalid = [item for item in audit["needs_review"] if item.get("kind") == "invalid_expression"]
    assert blocking_invalid == []
    assert len(review_invalid) == 1
    assert review_invalid[0]["script"] == "CarryOver"
    assert review_invalid[0]["inherited_from_base_export"] is True
    # Inventory still reports the carry-over failure for diagnostics.
    assert audit["expression_inventory"]["failure_count"] == 1


def test_generated_project_archive_allows_matching_source_preserved_expression(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _xscr(source, name="Main", declarations={"FalconType": "String"}))
    _write_verified_source_preservation(tmp_path, source=source)

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid == []
    assert audit["expression_provenance"]["valid"] is True
    assert audit["expression_provenance"]["record_count"] == 1
    assert audit["expression_inventory"]["failure_count"] == 0
    record = audit["expression_inventory"]["scripts"][0]["records"][0]
    assert record["source_preserved"] is True
    assert record["source_hash"].startswith("sha256:")


def test_provenance_ledger_records_byte_and_occurrence_identity(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    source_archive, ledger_path = _write_verified_source_preservation(tmp_path, source=source)

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    record = ledger["records"][0]

    assert ledger["schema_version"] == "tecan.expression_provenance.v1"
    assert ledger["ledger_sha256"].startswith("sha256:")
    assert record["source_archive_sha256"].startswith("sha256:")
    assert record["source_xscr_sha256"].startswith("sha256:")
    assert record["source_entry"] == f"{source_archive.name}!DataStore/main.xscr"
    assert record["command"] == "SetVariableStatement"
    assert record["field"] == "Value"
    assert record["line_number"] == 65
    assert record["command_index"] == 1
    assert record["expression_sha256"].startswith("sha256:")
    assert record["reference_metadata_origin"] == "source_ingestion"
    assert record["referenced_variables"] == []
    assert record["referenced_functions"] == ["Unsupported.Syntax"]


def test_provenance_binding_requires_source_occurrence_coordinates(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    source_archive, ledger_path = _write_verified_source_preservation(tmp_path, source=source)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    protocol_ir = {
        "ir_version": "tecan.protocol_ir.v2",
        "steps": [
            {
                "operation": "set_variable",
                "command_id": "SetVariableStatement",
                "parameters": {
                    "value_expression": {
                        "kind": "source_preserved_expression",
                        "source": source,
                        "source_hash": "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "source_entry": f"{source_archive.name}!DataStore/main.xscr",
                        "byte_stable": True,
                        "provenance_id": ledger["records"][0]["record_id"],
                    },
                },
            }
        ],
    }

    bound_ir = bind_protocol_ir_expression_provenance(protocol_ir, ledger)

    assert "provenance_id" not in bound_ir["steps"][0]["parameters"]["value_expression"]


def test_standalone_source_provenance_survives_bundle_relocation(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    original = tmp_path / "selected-source.xscr"
    original.write_text(
        _xscr(source, name="SelectedSource", declarations={"FalconType": "String"}),
        encoding="utf-8",
    )
    ledger = build_expression_provenance_ledger([original])
    protocol_ir = {
        "ir_version": "tecan.protocol_ir.v2",
        "steps": [
            {
                "operation": "set_variable",
                "command_id": "SetVariableStatement",
                "parameters": {
                    "line_number": 65,
                    "command_index": 1,
                    "value_expression": {
                        "kind": "source_preserved_expression",
                        "source": source,
                        "source_hash": "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "source_entry": str(original.resolve()),
                        "byte_stable": True,
                    },
                },
            }
        ],
    }
    bound_ir = bind_protocol_ir_expression_provenance(protocol_ir, ledger)
    (tmp_path / "protocol.ir.json").write_text(json.dumps(bound_ir), encoding="utf-8")
    source_dir = tmp_path / "source" / "original-sources"
    reports_dir = tmp_path / "source" / "reports"
    source_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    shutil.copy2(original, source_dir / "source_script_1.xscr")
    write_expression_provenance_ledger(reports_dir / "expression_provenance.json", ledger)
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _xscr(source, name="Main", declarations={"FalconType": "String"}))

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid == []


def test_generated_project_archive_allows_matching_source_preserved_expression_in_subroutine(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _xscr('"50ml Falcon"', name="Main", declarations={"FalconType": "String"}),
        )
        zf.writestr(
            "DataStore/subroutine.xscr",
            _xscr(source, name="Subroutine", declarations={"FalconType": "String"}),
        )
    _write_verified_source_preservation(
        tmp_path,
        source=source,
        source_archive_entry="DataStore/subroutine.xscr",
    )

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid == []
    assert audit["expression_inventory"]["failure_count"] == 0


def test_generated_project_archive_uses_verified_prefixed_source_entry(
    tmp_path: Path,
) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _xscr(source, name="Main", declarations={"FalconType": "String"}))
    _write_verified_source_preservation(tmp_path, source=source)

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid == []
    assert audit["expression_inventory"]["failure_count"] == 0
    record = audit["expression_inventory"]["scripts"][0]["records"][0]
    assert record["source_preserved"] is True
    assert record["source_entry"] == "source_project_1.zeia!DataStore/main.xscr"


def test_generated_project_archive_blocks_self_attested_source_preservation(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _xscr(source, name="Main", declarations={"FalconType": "String"}))
    _write_source_preserved_protocol_ir(
        tmp_path,
        entry="FullExport.zeia!DataStore/main.xscr",
        source=source,
        provenance_id="exprprov:" + ("1" * 64),
    )

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["entry"] == "DataStore/main.xscr"
    assert audit["expression_provenance"]["valid"] is False


def test_generated_project_archive_blocks_source_preserved_expression_with_wrong_hash(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _xscr(source, name="Main", declarations={"FalconType": "String"}))
    _write_verified_source_preservation(tmp_path, source=source)
    ir_path = tmp_path / "protocol.ir.json"
    protocol_ir = json.loads(ir_path.read_text(encoding="utf-8"))
    protocol_ir["steps"][0]["parameters"]["value_expression"]["source_hash"] = "sha256:" + ("0" * 64)
    ir_path.write_text(json.dumps(protocol_ir), encoding="utf-8")

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["entry"] == "DataStore/main.xscr"


def test_generated_project_archive_blocks_forged_opaque_reference_metadata(
    tmp_path: Path,
) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _xscr(source, name="Main", declarations={"FalconType": "String"}),
        )
    _write_verified_source_preservation(tmp_path, source=source)
    ir_path = tmp_path / "protocol.ir.json"
    protocol_ir = json.loads(ir_path.read_text(encoding="utf-8"))
    expression = protocol_ir["steps"][0]["parameters"]["value_expression"]
    expression["referenced_variables"] = ["HiddenVariable"]
    ir_path.write_text(json.dumps(protocol_ir), encoding="utf-8")

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [
        item for item in audit["blocking"] if item.get("kind") == "invalid_expression"
    ]
    assert invalid
    assert audit["expression_provenance"]["valid"] is False
    assert audit["expression_provenance"]["errors"][0]["reason"] == (
        "expression_metadata_mismatch"
    )


def test_generated_project_archive_blocks_omitted_opaque_reference_metadata(
    tmp_path: Path,
) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _xscr(source, name="Main", declarations={"FalconType": "String"}),
        )
    _write_verified_source_preservation(tmp_path, source=source)
    ir_path = tmp_path / "protocol.ir.json"
    protocol_ir = json.loads(ir_path.read_text(encoding="utf-8"))
    expression = protocol_ir["steps"][0]["parameters"]["value_expression"]
    expression.pop("referenced_variables")
    ir_path.write_text(json.dumps(protocol_ir), encoding="utf-8")

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [
        item for item in audit["blocking"] if item.get("kind") == "invalid_expression"
    ]
    assert invalid
    assert audit["expression_provenance"]["valid"] is False
    assert audit["expression_provenance"]["errors"][0]["reason"] == (
        "expression_metadata_mismatch"
    )


def test_generated_project_archive_blocks_source_preservation_when_source_bytes_change(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _xscr(source, name="Main", declarations={"FalconType": "String"}))
    source_archive, _ = _write_verified_source_preservation(tmp_path, source=source)
    with zipfile.ZipFile(source_archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _xscr("Different.Unsupported.Syntax(1)", name="main", declarations={"FalconType": "String"}),
        )

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["entry"] == "DataStore/main.xscr"


def test_one_provenance_record_cannot_authorize_repeated_generated_expressions(tmp_path: Path) -> None:
    source = "Unsupported.Syntax(1)"
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _xscr_with_repeated_expression(source, name="Main"))
    _write_verified_source_preservation(tmp_path, source=source)

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert len(invalid) == 1
    assert invalid[0]["line"] == 66


def test_generated_project_archive_blocks_malformed_charge_condition_in_main_script(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _move_axis_xscr("ChargeReady AND", name="Main"))

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["entry"] == "DataStore/main.xscr"
    assert invalid[0]["command"] == "MoveAxisCommand"
    assert invalid[0]["field"] == "ChargeCondition"
    assert audit["expression_inventory"]["failure_count"] == 1


@pytest.mark.parametrize("charge_condition", ["Standard", "Maximum"])
def test_generated_project_archive_allows_vendor_charge_condition_literals(
    tmp_path: Path,
    charge_condition: str,
) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _move_axis_xscr(charge_condition, name="Main"),
        )

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [
        item for item in audit["blocking"] if item.get("kind") == "invalid_expression"
    ]
    assert invalid == []
    assert audit["expression_inventory"]["failure_count"] == 0


def test_generated_project_archive_blocks_malformed_expression_in_subroutine(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _xscr('"50ml Falcon"', name="Main", declarations={"FalconType": "String"}),
        )
        zf.writestr("DataStore/subroutine.xscr", _xscr('"""50ml Falcon"""', name="Subroutine"))

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["entry"] == "DataStore/subroutine.xscr"
    assert audit["expression_inventory"]["failure_count"] == 1


def test_generated_project_archive_blocks_malformed_charge_condition_in_subroutine(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _move_axis_xscr("True", name="Main"))
        zf.writestr("DataStore/subroutine.xscr", _move_axis_xscr("ChargeReady AND", name="Subroutine"))

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["entry"] == "DataStore/subroutine.xscr"
    assert invalid[0]["command"] == "MoveAxisCommand"
    assert invalid[0]["field"] == "ChargeCondition"
    assert audit["expression_inventory"]["failure_count"] == 1


def test_generated_project_archive_blocks_undefined_expression_variable(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _xscr(
                "MissingSource + 1",
                name="Main",
                variable="TubeCount",
                declarations={"TubeCount": "Integer"},
            ),
        )

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["reason"] == "undefined_variable"
    assert invalid[0]["semantic_issues"][0]["code"] == "undefined_variable"


def test_generated_project_archive_blocks_undefined_expression_variable_without_declarations(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/main.xscr", _loop_xscr("MissingLoopCount", name="Main"))

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["reason"] == "undefined_variable"
    assert invalid[0]["semantic_issues"][0]["code"] == "undefined_variable"


def test_generated_project_archive_blocks_assignment_type_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _xscr(
                '"50ml Falcon"',
                name="Main",
                variable="TubeCount",
                declarations={"TubeCount": "Integer"},
            ),
        )

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["reason"] == "assignment_type_mismatch"


def test_generated_project_archive_blocks_unknown_function_in_subroutine(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/main.xscr",
            _xscr('"50ml Falcon"', name="Main", declarations={"FalconType": "String"}),
        )
        zf.writestr(
            "DataStore/subroutine.xscr",
            _xscr(
                'UnknownFunction("50ml Falcon")',
                name="Subroutine",
                declarations={"FalconType": "String"},
            ),
        )

    audit = verify_generated_project_archive(archive, bundle_root=tmp_path)

    invalid = [item for item in audit["blocking"] if item.get("kind") == "invalid_expression"]
    assert invalid
    assert invalid[0]["entry"] == "DataStore/subroutine.xscr"
    assert invalid[0]["reason"] == "unknown_function"
