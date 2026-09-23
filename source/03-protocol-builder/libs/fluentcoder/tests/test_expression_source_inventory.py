from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from fluentcoder.expressions import (
    expression_inventory_from_xscr_text,
    expression_inventory_from_zeia,
    load_expression_symbol_catalog,
)


def _xscr(value: str, *, name: str, type_name: str = "String") -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{name}</ObjectName>
    <VariableDeclarations>
      <anyType xmlns:i="http://www.w3.org/2001/XMLSchema-instance" i:type="VariableDefinitionHelper">
        <Name>Result</Name>
        <TypeName>{type_name}</TypeName>
        <QueryOnStartup>false</QueryOnStartup>
        <Scope>Script</Scope>
      </anyType>
    </VariableDeclarations>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.SetVariableStatement">
          <SetVariableStatement>
            <Name>Result</Name>
            <Value>{value}</Value>
            <LineNumber>65</LineNumber>
          </SetVariableStatement>
        </Object>
      </Objects>
      <Name>Steps</Name>
    </ScriptGroup>
  </Payload>
</VxData>
'''


def _versioned_catalog():
    return load_expression_symbol_catalog(
        {
            "schema": "tecan.expression_symbols.v1",
            "schemaVersion": 1,
            "symbols": [
                {
                    "name": "VersionedVendorFunction",
                    "kind": "function",
                    "contexts": ["script_expression"],
                    "versionRanges": [{"min": "3.0", "max": "3.8"}],
                    "provenance": [{"source": "fixture", "reference": "versioned-source"}],
                    "confidence": 0.9,
                    "signatures": [{"argumentTypes": ["number"], "returnType": "number"}],
                }
            ],
        }
    )


def test_imported_unknown_functions_are_stable_and_nonblocking() -> None:
    kwargs = {
        "script": "UnknownScript",
        "entry": "DataStore/UnknownScript.xscr",
        "source_kind": "imported",
        "source_version": "3.8",
        "version_evidence": {"build": "3.8.0", "source": "synthetic-fixture"},
    }
    first = expression_inventory_from_xscr_text(_xscr("VendorOnly(1)", name="UnknownScript"), **kwargs)
    second = expression_inventory_from_xscr_text(_xscr("VendorOnly(1)", name="UnknownScript"), **kwargs)

    assert first == second
    assert first["valid"]
    assert first["failure_count"] == 0
    assert first["unknown_source_function_count"] == 1
    finding = first["unknown_source_functions"][0]
    assert finding["function_name"] == "VendorOnly"
    assert finding["status"] == "catalog_unknown"
    assert finding["script"] == "UnknownScript"
    assert finding["entry"] == "DataStore/UnknownScript.xscr"
    assert finding["line"] == 65
    assert finding["field"] == "Value"
    assert finding["source_version"] == "3.8"
    assert finding["version_evidence"] == {"build": "3.8.0", "source": "synthetic-fixture"}
    assert finding["record_id"].startswith("sha256:")


def test_zeia_source_inventory_orders_multiple_scripts_and_keeps_known_symbols(tmp_path: Path) -> None:
    archive = tmp_path / "source.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DataStore/z_unknown.xscr", _xscr("OtherVendor(2)", name="Z Unknown"))
        zf.writestr("DataStore/a_known.xscr", _xscr("Round(2.1)", name="A Known", type_name="Number"))

    inventory = expression_inventory_from_zeia(
        archive,
        source_version="3.8",
        version_evidence={"export": "FluentControl", "build": "3.8.0"},
    )

    assert inventory["valid"]
    assert [script["records"][0]["entry"] for script in inventory["scripts"]] == [
        "DataStore/a_known.xscr",
        "DataStore/z_unknown.xscr",
    ]
    assert inventory["source_function_count"] == 2
    assert [item["function_name"] for item in inventory["source_functions"]] == [
        "OtherVendor",
        "Round",
    ]
    assert [item["function_name"] for item in inventory["unknown_source_functions"]] == ["OtherVendor"]
    assert inventory["unknown_source_functions"][0]["entry"] == "DataStore/z_unknown.xscr"


def test_imported_version_unsupported_function_is_reviewable_not_rejected() -> None:
    inventory = expression_inventory_from_xscr_text(
        _xscr("VersionedVendorFunction(1)", name="Versioned"),
        script="Versioned",
        entry="DataStore/Versioned.xscr",
        source_kind="imported",
        source_version="4.0",
        target_version="4.0",
        catalog=_versioned_catalog(),
    )

    assert inventory["valid"]
    finding = inventory["unknown_source_functions"][0]
    assert finding["status"] == "known_unsupported"
    assert inventory["records"][0]["semantic_issues"][0]["code"] == "source_function_unsupported_for_target"


def test_opaque_source_preserved_function_is_included_with_provenance() -> None:
    source = "VendorOnly(1"
    inventory = expression_inventory_from_xscr_text(
        _xscr(source, name="Opaque"),
        script="Opaque",
        entry="DataStore/Opaque.xscr",
        source_kind="imported",
        source_preserved_allowlist=[
            {
                "source": source,
                "source_hash": "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "command": "SetVariableStatement",
                "field": "Value",
                "entry": "DataStore/Opaque.xscr",
                "line": 65,
                "command_index": 1,
                "source_entry": "source.zeia!DataStore/Opaque.xscr",
                "provenance_policy": "source_preservation_allowed",
            }
        ],
    )

    assert inventory["valid"]
    assert inventory["records"][0]["source_preserved"] is True
    assert inventory["records"][0]["referenced_functions"] == ["VendorOnly"]
    finding = inventory["unknown_source_functions"][0]
    assert finding["function_name"] == "VendorOnly"
    assert finding["source_entry"] == "source.zeia!DataStore/Opaque.xscr"
