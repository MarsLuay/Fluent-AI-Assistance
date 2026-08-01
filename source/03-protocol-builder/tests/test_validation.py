import hashlib
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fluent_pipeline.expression_provenance import (
    bind_protocol_ir_expression_provenance,
    build_expression_provenance_ledger,
    write_expression_provenance_ledger,
)
from fluent_pipeline.gates import ValidationContext, readiness_evaluator
from fluent_pipeline.protocol_ir import protocol_ir_from_python, write_protocol_ir
from fluent_pipeline.readiness import REVIEWABLE_READY, STRICT_READY
from fluent_pipeline.readiness_gates import readiness_gate, readiness_gates
from fluent_pipeline.generation_workflow import (
    _copy_source_workspace_data,
    _ensure_compiled_subroutine_references,
    _normalize_compiled_variable_declaration_namespaces,
)
from fluent_pipeline.validation import (
    REQUIRED_GATE_COUNT,
    _compiled_command_inventory,
    _gate_carriers,
    _gate_checksums,
    _gate_command_inventory,
    _gate_deck_layout,
    _gate_device_aliases,
    _gate_generated_zeia,
    _gate_ir_schema,
    _gate_zeia,
    _gate_subroutine_additions,
    _gate_subroutine_dependencies,
    _gate_liquid_class_compatibility,
    _gate_no_unapproved_raw_xml,
    _gate,
    _gate_tip_boxes,
    _gate_xscr_ir_roundtrip,
    _registered_step_expression_signatures,
    _supported_compiled_operation,
    render_validation_markdown,
    scaffold_validation_report,
    validate_ready_to_import,
    validation_failure_message,
)


DRAFT = """from fluentcoder import Plate96, Reagent, Worktable


def build_worktable():
    reagent = Reagent("Water")
    wt = Worktable.from_workspace("780_Empty", auto_place=False)
    source = wt.place(Plate96("SourcePlate", catalog="96 Well Flat"), "Site", 1)
    source.fill_all(reagent, 50.0)
    head = wt.mca96
    head.aspirate(source, 20.0, liquid_class="Water Free Single")
    return wt
"""

XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>protocol</ObjectName>
    <Reference>
      <Guid>workspace-guid</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <Reference>
      <Guid>lc-guid</Guid>
      <TypeId>LiquidClass</TypeId>
      <ObjectName>Water Free Single</ObjectName>
    </Reference>
    <PayloadData><Script><Commands><ScriptGroup><Objects>
      <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
        <ScriptGroupDataV1><Name>Protocol</Name><Data><Statements>
          <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
            <AddLabwareDataV1>
              <LabwareType>96 Well Flat</LabwareType>
              <LabwareLable>SourcePlate</LabwareLable>
              <Location>Site</Location>
              <Position>1</Position>
              <Rotation>0</Rotation>
              <HasLid>False</HasLid>
              <Data><LineNumber>1</LineNumber></Data>
            </AddLabwareDataV1>
          </Object>
          <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384AspirateScriptCommandDataV2">
            <Mca384AspirateScriptCommandDataV2>
              <LiquidClassName>Water Free Single</LiquidClassName>
              <Volume>20</Volume>
              <ScriptCommandCommonDataV2>
                <LabwareName>SourcePlate</LabwareName>
                <DeviceAlias>Instrument=1/Device=MCA384:1</DeviceAlias>
                <LineNumber>2</LineNumber>
              </ScriptCommandCommonDataV2>
            </Mca384AspirateScriptCommandDataV2>
          </Object>
        </Statements></Data></ScriptGroupDataV1>
      </Object>
    </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""


def _set_variable_xscr(
    value: str,
    *,
    variable: str = "FalconType",
    declarations: dict[str, str] | None = None,
) -> str:
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
    <ObjectName>protocol</ObjectName>
    <VariableDeclarations>{declaration_xml}</VariableDeclarations>
    <PayloadData><Script><Commands>
      <ScriptGroup>
        <Objects>
          <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
            <ScriptGroupDataV1>
              <Name>Protocol</Name>
              <Data><Statements>
                <Object Type="Tecan.Core.Scripting.SetVariableStatement">
                  <SetVariableStatement>
                    <Name>{variable}</Name>
                    <Value>{value}</Value>
                    <LineNumber>1</LineNumber>
                  </SetVariableStatement>
                </Object>
              </Statements></Data>
            </ScriptGroupDataV1>
          </Object>
        </Objects>
      </ScriptGroup>
    </Commands></Script></PayloadData>
  </Payload>
</VxData>
"""


def _source_preserved_ir(source: str, *, source_entry: str = "FullExport.zeia!DataStore/main.xscr") -> dict:
    return {
        "ir_version": "tecan.protocol_ir.v2",
        "expression_schema_version": "fluent_control.expression.v1",
        "id": "source_preserved_expression",
        "protocol": {"name": "Source preserved expression"},
        "source": {"format": "xscr", "path": source_entry},
        "worktable": {"name": "780_Empty"},
        "variables": [
            {
                "name": "FalconType",
                "type": "String",
                "default_expression": {"kind": "string_literal", "value": ""},
            }
        ],
        "steps": [
            {
                "id": "step_001",
                "index": 1,
                "operation": "set_variable",
                "command_id": "SetVariableStatement",
                "parameters": {
                    "variable": "FalconType",
                    "line_number": 1,
                    "command_index": 2,
                    "value_expression": {
                        "kind": "source_preserved_expression",
                        "source": source,
                        "source_hash": "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "source_entry": source_entry,
                        "byte_stable": True,
                    },
                },
            }
        ],
    }

RAW_XML_DRAFT = """from fluentcoder import Worktable


def build_worktable():
    wt = Worktable.from_workspace("780_Empty", auto_place=False)
    wt.raw_xml_step("Mca384CustomCommand", "<Object />")
    return wt
"""

TIP_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>tip_protocol</ObjectName>
    <Reference>
      <Guid>workspace-guid</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <Reference>
      <Guid>lc-guid</Guid>
      <TypeId>LiquidClass</TypeId>
      <ObjectName>Water Free Single</ObjectName>
    </Reference>
    <PayloadData><Script><Commands><ScriptGroup><Objects>
      <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
        <ScriptGroupDataV1><Name>Protocol</Name><Data><Statements>
          <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
            <AddLabwareDataV1>
              <LabwareType>MCA96, 100ul, Box</LabwareType>
              <LabwareLable>Tips</LabwareLable>
              <Location>Site</Location><Position>1</Position><Rotation>0</Rotation><HasLid>False</HasLid>
              <Data><LineNumber>1</LineNumber></Data>
            </AddLabwareDataV1>
          </Object>
          <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
            <AddLabwareDataV1>
              <LabwareType>96 Well Flat</LabwareType>
              <LabwareLable>SourcePlate</LabwareLable>
              <Location>Site</Location><Position>2</Position><Rotation>0</Rotation><HasLid>False</HasLid>
              <Data><LineNumber>2</LineNumber></Data>
            </AddLabwareDataV1>
          </Object>
          <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384PickUpTipsScriptCommandDataV5">
            <Mca384PickUpTipsScriptCommandDataV5>
              <ScriptCommandCommonDataV2><LabwareName>Tips</LabwareName><LineNumber>3</LineNumber></ScriptCommandCommonDataV2>
            </Mca384PickUpTipsScriptCommandDataV5>
          </Object>
          <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384AspirateScriptCommandDataV2">
            <Mca384AspirateScriptCommandDataV2>
              <LiquidClassName>Water Free Single</LiquidClassName>
              <Volume>150</Volume>
              <ScriptCommandCommonDataV2><LabwareName>SourcePlate</LabwareName><LineNumber>4</LineNumber></ScriptCommandCommonDataV2>
            </Mca384AspirateScriptCommandDataV2>
          </Object>
        </Statements></Data></ScriptGroupDataV1>
      </Object>
    </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""


def _gate_record(gate_id: str, status: str, summary: str, details: dict | None = None) -> dict:
    gate = readiness_gate(gate_id)
    return {
        "id": gate.id,
        "gate": gate.gate_label,
        "name": gate.name,
        "status": status,
        "summary": summary,
        "details": details or {},
    }


class ValidationTests(unittest.TestCase):
    def test_ready_validation_all_gates_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            zeia = root / "source.zeia"
            draft.write_text(DRAFT, encoding="utf-8")
            xscr.write_text(XSCR, encoding="utf-8")
            write_protocol_ir(protocol_ir_from_python(draft), ir_path)
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                draft_path=draft,
                protocol_ir=ir_path,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "780_Empty"}],
                    "labware_names": ["SourcePlate"],
                    "liquid_classes": ["Water Free Single"],
                    "rack_types": ["96 Well Flat"],
                    "device_aliases": ["Instrument=1/Device=MCA384:1"],
                    "worklist_paths": [],
                    "scripts": [],
                },
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                },
            )

            self.assertTrue(report["ready"])
            self.assertEqual(report["passed_count"], 26)
            self.assertEqual(
                [gate["id"] for gate in report["gates"]],
                [gate.id for gate in readiness_gates() if gate.is_required_offline_gate],
            )
            self.assertEqual(report["offline_validation"]["status"], "ready_to_import")
            self.assertEqual(report["review_state"]["status"], "import_ready_needs_review")
            self.assertIn("device_aliases_resolve", report["review_state"]["gates"])
            self.assertEqual(report["fluentcontrol_load_diagnostic"]["status"], "not_run")
            markdown = render_validation_markdown(report)
            self.assertIn("Gate 1. ZEIA parsed successfully", markdown)
            self.assertIn("Gate 18. robotools-style liquid state model is valid", markdown)
            self.assertIn("Gate 22. deck positions match the source worktable unless approved", markdown)
            self.assertIn("Gate 23. generated ZEIA entries carry valid FluentControl checksums", markdown)
            self.assertIn(
                "Gate 24. packaged generated ZEIA opens, resolves references, and matches its datastore metadata",
                markdown,
            )
            self.assertIn(
                "Gate 25. compiled command name strings resolve in the source context",
                markdown,
            )
            self.assertIn(
                "Gate 26. subroutine calls resolve to loadable Script dependencies",
                markdown,
            )
            self.assertIn("Readiness Boundaries", markdown)
            self.assertIn("FluentControl Script Editor open/load check", markdown)
            self.assertIn("hardware_review_required", markdown)

    def test_required_host_config_blocks_readiness_without_adding_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            zeia = root / "source.zeia"
            draft.write_text(DRAFT, encoding="utf-8")
            xscr.write_text(XSCR, encoding="utf-8")
            write_protocol_ir(protocol_ir_from_python(draft), ir_path)
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                draft_path=draft,
                protocol_ir=ir_path,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "780_Empty"}],
                    "labware_names": ["SourcePlate"],
                    "liquid_classes": ["Water Free Single"],
                    "rack_types": ["96 Well Flat"],
                    "device_aliases": ["Instrument=1/Device=MCA384:1"],
                    "worklist_paths": [],
                    "scripts": [],
                },
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                    "host_instrument_configuration": {
                        "status": "failed",
                        "summary": "Installed host configurations do not match the expected hints.",
                        "expected": {"exact_names": ["Required_RGA"], "required": True},
                    },
                },
            )

            self.assertFalse(report["ready"])
            self.assertEqual(report["passed_count"], 26)
            self.assertTrue(report["host_instrument_config_blocking"])
            self.assertIn("required host instrument configuration", validation_failure_message(report))

    def _validate_with_custom_zeia_status(self, zeia_status: str, *, ready_policy=STRICT_READY):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            zeia = root / "source.zeia"
            draft.write_text(DRAFT, encoding="utf-8")
            xscr.write_text(XSCR, encoding="utf-8")
            write_protocol_ir(protocol_ir_from_python(draft), ir_path)
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            custom_gate = _gate_record("zeia_parsed", zeia_status, f"Injected {zeia_status} gate.")
            with mock.patch("fluent_pipeline.validation._gate_zeia", return_value=custom_gate):
                return validate_ready_to_import(
                    compiled_xscr=xscr,
                    draft_path=draft,
                    protocol_ir=ir_path,
                    source_projects=[zeia],
                    source_manifest={
                        "name": "source",
                        "errors": [],
                        "workspaces": [{"object_name": "780_Empty"}],
                        "labware_names": ["SourcePlate"],
                        "liquid_classes": ["Water Free Single"],
                        "rack_types": ["96 Well Flat"],
                        "device_aliases": ["Instrument=1/Device=MCA384:1"],
                        "worklist_paths": [],
                        "scripts": [],
                    },
                    validation_context={
                        "simulation_passed": True,
                        "repair_plan": {"actions": []},
                        "compile_passed": True,
                        "checksums_recompute_waived": True,
                    },
                    ready_policy=ready_policy,
                )

    def test_ready_validation_blocks_non_reviewable_required_gate_statuses(self):
        for status in ("error", "skipped", "not_run", "unknown"):
            with self.subTest(status=status):
                report = self._validate_with_custom_zeia_status(status)
                self.assertFalse(report["ready"])
                self.assertEqual(report["offline_validation"]["status"], "validated_not_ready")
                self.assertEqual(report["required_failed_count"], 1)
                self.assertEqual(report["blocking_count"], 1)
                self.assertEqual(report["blocking_gates"], ["zeia_parsed"])
                self.assertIn(status, validation_failure_message(report))
                self.assertIn("STRICT_READY", validation_failure_message(report))

    def test_reviewable_policy_allows_needs_review_required_gate(self):
        report = self._validate_with_custom_zeia_status("needs_review", ready_policy=REVIEWABLE_READY)
        self.assertTrue(report["ready"])
        self.assertEqual(report["ready_policy"], "REVIEWABLE_READY")
        self.assertEqual(report["ready_policy_statuses"], ["needs_review", "passed"])
        self.assertEqual(report["offline_validation"]["status"], "ready_to_import")
        self.assertEqual(report["required_failed_count"], 0)
        self.assertEqual(report["blocking_count"], 0)
        self.assertEqual(report["review_state"]["status"], "import_ready_needs_review")
        self.assertGreaterEqual(report["review_state"]["needs_review_count"], 1)

    def test_trivial_passes_are_flagged_for_non_liquid_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            zeia = root / "source.zeia"
            draft.write_text(DRAFT, encoding="utf-8")
            xscr.write_text(XSCR, encoding="utf-8")
            write_protocol_ir(protocol_ir_from_python(draft), ir_path)
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                draft_path=draft,
                protocol_ir=ir_path,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "780_Empty"}],
                    "labware_names": ["SourcePlate"],
                    "liquid_classes": ["Water Free Single"],
                    "rack_types": ["96 Well Flat"],
                    "device_aliases": ["Instrument=1/Device=MCA384:1"],
                    "worklist_paths": [],
                    "scripts": [],
                },
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                },
            )

            self.assertTrue(report["ready"])
            # No explicit wells and no tip pickup in this IR -> trivial passes.
            self.assertIn("well_ranges_valid", report["trivial_pass_gates"])
            self.assertIn("tip_capacity_valid", report["trivial_pass_gates"])
            self.assertEqual(report["trivial_pass_count"], len(report["trivial_pass_gates"]))
            statuses = {gate["id"]: gate for gate in report["gates"]}
            self.assertTrue(statuses["well_ranges_valid"]["details"].get("trivial"))
            # The aspirate step is real liquid handling, so these are not trivial.
            self.assertFalse(statuses["volume_bounds_valid"]["details"].get("trivial"))
            self.assertFalse(statuses["liquid_class_compatible"]["details"].get("trivial"))
            markdown = render_validation_markdown(report)
            self.assertIn("Trivial passes:", markdown)

    def test_scaffold_validation_report_is_not_ready(self):
        report = scaffold_validation_report("Compile was skipped by request.")
        self.assertFalse(report["ready"])
        self.assertTrue(report["scaffold"])
        self.assertEqual(report["passed_count"], 0)
        self.assertEqual(report["gate_count"], REQUIRED_GATE_COUNT)
        markdown = render_validation_markdown(report)
        self.assertIn("not validated", markdown)
        self.assertIn("Not Ready To Import", markdown)
        self.assertIn("MUST NOT", markdown)

    def test_ready_validation_blocks_missing_simulation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            zeia = root / "source.zeia"
            draft.write_text(DRAFT, encoding="utf-8")
            xscr.write_text("<VxData />", encoding="utf-8")
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                draft_path=draft,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "780_Empty"}],
                    "labware_names": ["SourcePlate"],
                    "liquid_classes": ["Water Free Single"],
                    "rack_types": ["96 Well Flat"],
                    "device_aliases": ["Instrument=1/Device=MCA384:1"],
                    "worklist_paths": [],
                    "scripts": [],
                },
                validation_context={
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                },
            )

            self.assertFalse(report["ready"])
            self.assertIn("Gate 7", validation_failure_message(report))
            self.assertEqual(report["offline_validation"]["status"], "validated_not_ready")

    def test_optional_fluent_context_check_gate_passes_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            zeia = root / "source.zeia"
            draft.write_text(DRAFT, encoding="utf-8")
            xscr.write_text(XSCR, encoding="utf-8")
            write_protocol_ir(protocol_ir_from_python(draft), ir_path)
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                draft_path=draft,
                protocol_ir=ir_path,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "780_Empty"}],
                    "labware_names": ["SourcePlate"],
                    "liquid_classes": ["Water Free Single"],
                    "rack_types": ["96 Well Flat"],
                    "device_aliases": ["Instrument=1/Device=MCA384:1"],
                    "worklist_paths": [],
                    "scripts": [],
                },
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                    "fluent_context_check_required": True,
                    "fluent_context_check": {
                        "ok": True,
                        "status": "passed",
                        "summary": "FluentControl prepared the method in simulation mode.",
                        "provider": "external",
                        "method": "protocol",
                    },
                },
            )

            self.assertTrue(report["ready"])
            self.assertEqual(report["gate_count"], 27)
            self.assertEqual(report["passed_count"], 27)
            self.assertEqual(report["gates"][-1]["id"], "fluent_context_check")
            self.assertEqual(report["offline_validation"]["status"], "ready_to_import")
            self.assertEqual(report["fluentcontrol_load_diagnostic"]["status"], "load_clean")

    def test_optional_fluent_context_check_gate_does_not_block_offline_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            zeia = root / "source.zeia"
            draft.write_text(DRAFT, encoding="utf-8")
            xscr.write_text(XSCR, encoding="utf-8")
            write_protocol_ir(protocol_ir_from_python(draft), ir_path)
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                draft_path=draft,
                protocol_ir=ir_path,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "780_Empty"}],
                    "labware_names": ["SourcePlate"],
                    "liquid_classes": ["Water Free Single"],
                    "rack_types": ["96 Well Flat"],
                    "device_aliases": ["Instrument=1/Device=MCA384:1"],
                    "worklist_paths": [],
                    "scripts": [],
                },
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                    "fluent_context_check_required": True,
                    "fluent_context_check": {
                        "ok": False,
                        "status": "failed",
                        "summary": "FluentControl context check failed.",
                        "provider": "external",
                        "method": "protocol",
                        "runtime_errors": ["12: Invalid liquid class Water Free Single"],
                    },
                },
            )

            self.assertTrue(report["ready"])
            self.assertEqual(report["offline_validation"]["status"], "ready_to_import")
            self.assertEqual(report["fluentcontrol_load_diagnostic"]["status"], "load_failed")
            markdown = render_validation_markdown(report)
            self.assertIn("Invalid liquid class Water Free Single", markdown)

    def test_review_state_tracks_non_blocking_required_gate_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            zeia = root / "source.zeia"
            draft.write_text(DRAFT, encoding="utf-8")
            xscr.write_text(XSCR, encoding="utf-8")
            write_protocol_ir(protocol_ir_from_python(draft), ir_path)
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                draft_path=draft,
                protocol_ir=ir_path,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "780_Empty"}],
                    "labware_names": ["SourcePlate"],
                    "liquid_classes": ["Water Free Single"],
                    "rack_types": ["96 Well Flat"],
                    "worklist_paths": [],
                    "scripts": [],
                },
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                },
            )

            self.assertTrue(report["ready"])
            self.assertEqual(report["offline_validation"]["status"], "ready_to_import")
            self.assertEqual(report["review_state"]["status"], "import_ready_needs_review")
            self.assertGreaterEqual(report["review_state"]["needs_review_count"], 1)
            self.assertIn("device_aliases_resolve", report["review_state"]["gates"])

    def test_ready_validation_blocks_deeper_domain_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "protocol.py"
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            zeia = root / "source.zeia"
            draft.write_text(RAW_XML_DRAFT, encoding="utf-8")
            xscr.write_text(TIP_XSCR, encoding="utf-8")
            write_protocol_ir(_tip_capacity_ir(), ir_path)
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                draft_path=draft,
                protocol_ir=ir_path,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "780_Empty"}],
                    "labware_names": ["Tips", "SourcePlate"],
                    "liquid_classes": ["Water Free Single"],
                    "rack_types": ["MCA96, 100ul, Box", "96 Well Flat"],
                    "worklist_paths": [],
                    "scripts": [],
                    "liquid_class_compatibility": [
                        {"name": "Water Free Single", "operations": ["dispense"]},
                    ],
                },
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "max_volume_ul": 100,
                },
            )

            statuses = {gate["id"]: gate["status"] for gate in report["gates"]}
            self.assertFalse(report["ready"])
            self.assertEqual(statuses["post_compile_xscr_reinspect"], "passed")
            self.assertEqual(statuses["xscr_ir_roundtrip_matches"], "passed")
            self.assertEqual(statuses["volume_bounds_valid"], "failed")
            self.assertEqual(statuses["well_ranges_valid"], "failed")
            self.assertEqual(statuses["tip_capacity_valid"], "failed")
            self.assertEqual(statuses["liquid_class_compatible"], "failed")
            self.assertEqual(statuses["no_unapproved_raw_xml"], "failed")
            self.assertEqual(statuses["liquid_state_valid"], "failed")

    def test_liquid_class_compatibility_resolves_aliases(self):
        gate = _gate_liquid_class_compatibility(
            {
                "liquid_classes": [{"name": "Water Free Single[001]"}],
                "steps": [
                    {
                        "operation": "aspirate",
                        "liquid_class": "Water Free Single[001]",
                    }
                ],
            },
            {
                "liquid_classes": ["Water Free Single"],
                "liquid_class_compatibility": [
                    {"name": "Water Free Single", "operations": ["aspirate"]},
                ],
            },
        )

        self.assertEqual(gate["status"], "passed")

    def test_approved_passthrough_compiled_command_is_supported(self):
        self.assertEqual(_supported_compiled_operation("CgaDropFingersScriptCommand"), "approved_passthrough")
        self.assertIsNone(_supported_compiled_operation("CustomUnsupportedCommand"))

    def test_non_executable_compiled_metadata_is_not_unsupported_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "metadata.xscr"
            xscr.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <PayloadData>
    <Script><Commands><ScriptGroup><Objects>
      <Object Type="Tecan.Core.Scripting.Commands.LiHa.UI.LabwareSectionInfo">
        <LabwareSectionInfo><Name>metadata only</Name></LabwareSectionInfo>
      </Object>
    </Objects></ScriptGroup></Commands></Script>
  </PayloadData>
</VxData>
""",
                encoding="utf-8",
            )

            inventory = _compiled_command_inventory(xscr)

            self.assertEqual(inventory["unsupported_commands"], [])

    def test_post_compile_validation_allows_matching_source_preserved_expression(self):
        source = "Unsupported.Syntax(1)"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            source_archive = root / "FullExport.zeia"
            provenance_path = root / "expression_provenance.json"
            xscr.write_text(
                _set_variable_xscr(source, declarations={"FalconType": "String"}),
                encoding="utf-8",
            )
            with zipfile.ZipFile(source_archive, "w") as zf:
                zf.writestr(
                    "DataStore/main.xscr",
                    _set_variable_xscr(source, declarations={"FalconType": "String"}),
                )
            ledger = build_expression_provenance_ledger([source_archive])
            bound_ir = bind_protocol_ir_expression_provenance(_source_preserved_ir(source), ledger)
            self.assertTrue(
                bound_ir["steps"][0]["parameters"]["value_expression"].get("provenance_id")
            )
            ir_path.write_text(json.dumps(bound_ir), encoding="utf-8")
            write_expression_provenance_ledger(provenance_path, ledger)

            untrusted_inventory = _compiled_command_inventory(xscr)
            self.assertTrue(
                any(item.get("reason") == "invalid_expression" for item in untrusted_inventory["fluentcontrol_findings"])
            )

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                protocol_ir=ir_path,
                expression_provenance=provenance_path,
                source_projects=[source_archive],
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                },
            )

            post_compile = next(gate for gate in report["gates"] if gate["id"] == "post_compile_xscr_reinspect")
            self.assertEqual(post_compile["details"]["expression_inventory"]["failure_count"], 0)
            self.assertFalse(
                any(item.get("reason") == "invalid_expression" for item in post_compile["details"].get("findings", []))
            )
            provenance = post_compile["details"]["expression_provenance"]
            self.assertTrue(provenance["valid"])
            self.assertEqual(provenance["record_count"], 1)

    def test_post_compile_validation_blocks_self_attested_source_preserved_expression(self):
        source = "Unsupported.Syntax(1)"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xscr = root / "protocol.xscr"
            ir_path = root / "protocol.ir.json"
            xscr.write_text(
                _set_variable_xscr(source, declarations={"FalconType": "String"}),
                encoding="utf-8",
            )
            protocol_ir = _source_preserved_ir(source)
            protocol_ir["steps"][0]["parameters"]["value_expression"]["provenance_id"] = (
                "exprprov:" + ("1" * 64)
            )
            ir_path.write_text(json.dumps(protocol_ir), encoding="utf-8")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                protocol_ir=ir_path,
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                },
            )

            post_compile = next(gate for gate in report["gates"] if gate["id"] == "post_compile_xscr_reinspect")
            self.assertEqual(post_compile["details"]["expression_inventory"]["failure_count"], 1)
            self.assertFalse(post_compile["details"]["expression_provenance"]["valid"])

    def test_roundtrip_gate_normalizes_setup_before_runtime_steps(self):
        source_ir = {
            "steps": [
                {"operation": "comment"},
                {"operation": "prompt_user"},
            ]
        }
        compiled_ir = {
            "steps": [
                {"operation": "add_labware", "target_labware": "AdapterA200"},
                {"operation": "comment"},
                {"operation": "prompt_user"},
            ]
        }

        gate = _gate_xscr_ir_roundtrip(source_ir, compiled_ir)

        self.assertEqual(gate["status"], "passed")

    def test_roundtrip_gate_treats_first_instance_suffix_as_equivalent(self):
        source_ir = {
            "steps": [
                {
                    "operation": "prompt_user",
                    "target_labware": "CapHolder",
                    "parameters": {"prompt": "Check cap holder"},
                }
            ]
        }
        compiled_ir = {
            "steps": [
                {
                    "operation": "prompt_user",
                    "target_labware": "CapHolder[001]",
                    "parameters": {"prompt": "Check cap holder"},
                }
            ]
        }

        gate = _gate_xscr_ir_roundtrip(source_ir, compiled_ir)

        self.assertEqual(gate["status"], "passed")

    def test_roundtrip_gate_compares_rga_move_destinations(self):
        source_ir = {
            "steps": [
                {
                    "operation": "move_plate",
                    "target_labware": "FilterDWP[platecount]",
                    "parameters": {"labware": "FilterDWP[platecount]", "onto_labware": "AdapterA200"},
                }
            ]
        }
        compiled_ir = {
            "steps": [
                {
                    "operation": "move_plate",
                    "target_labware": "FilterDWP[platecount]",
                    "parameters": {
                        "labware": "FilterDWP[platecount]",
                        "destination_location": "Demo_Nest_Pos",
                        "destination_site": 1,
                    },
                }
            ]
        }

        gate = _gate_xscr_ir_roundtrip(source_ir, compiled_ir)

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["expected"][0]["onto_labware"], "AdapterA200")

    def test_roundtrip_gate_compares_registered_expression_fields(self):
        expected = {
            "variables": [
                {"name": "A", "type": "Integer"},
                {"name": "B", "type": "Integer"},
            ],
            "steps": [
                {
                    "operation": "set_variable",
                    "parameters": {
                        "variable": "B",
                        "value_expression": {
                            "kind": "binary_expression",
                            "operator": "+",
                            "left": {"kind": "variable_reference", "name": "A"},
                            "right": {"kind": "number_literal", "value": 1},
                        },
                    },
                }
            ]
        }
        actual = json.loads(json.dumps(expected))
        actual["steps"][0]["parameters"]["value_expression"]["right"]["value"] = 2

        gate = _gate_xscr_ir_roundtrip(expected, actual)

        self.assertEqual(gate["status"], "failed")

    def test_roundtrip_gate_compares_registered_setup_position_expressions(self):
        expected = {
            "variables": [
                {"name": "SourceSite", "type": "Integer"},
                {"name": "DestinationSite", "type": "Integer"},
            ],
            "steps": [
                {
                    "operation": "add_labware",
                    "target_labware": "Plate",
                    "parameters": {
                        "label": "Plate",
                        "position_expression": {
                            "kind": "variable_reference",
                            "name": "SourceSite",
                        },
                    },
                }
            ]
        }
        actual = json.loads(json.dumps(expected))
        actual["steps"][0]["parameters"]["position_expression"]["name"] = "DestinationSite"

        gate = _gate_xscr_ir_roundtrip(expected, actual)

        self.assertEqual(gate["status"], "failed")
        self.assertIn("Plate", gate["details"]["expected_setup_expressions"])

    def test_registered_step_expression_signatures_use_shared_walker_for_subroutine_mappings(self):
        step = {
            "operation": "call_subroutine",
            "parameters": {
                "subroutine": "sub.xscr",
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
            },
        }

        signatures = _registered_step_expression_signatures(step)

        self.assertEqual(signatures["source_expression"], ["(PlateCount + 1)"])

    def test_gate_17_accepts_registry_supported_raw_xml_command_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            draft.write_text(
                "def build_worktable():\n"
                "    wt.raw_xml_step('ConditionalGroup', '<Object />')\n",
                encoding="utf-8",
            )

            gate = _gate_no_unapproved_raw_xml(draft, {"unsupported_commands": []}, {})

            self.assertEqual(gate["status"], "passed")
            self.assertEqual(gate["details"]["approved_draft_raw_xml_calls"][0]["command_id"], "ConditionalGroup")

    def test_gate_17_rejects_unknown_raw_xml_command_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            draft.write_text(
                "def build_worktable():\n"
                "    wt.raw_xml_step('CustomUnsupportedCommand', '<Object />')\n",
                encoding="utf-8",
            )

            gate = _gate_no_unapproved_raw_xml(draft, {"unsupported_commands": []}, {})

            self.assertEqual(gate["status"], "failed")
            self.assertEqual(gate["details"]["draft_raw_xml_calls"][0]["command_id"], "CustomUnsupportedCommand")


def _tip_capacity_ir() -> dict:
    return {
        "ir_version": "tecan.protocol_ir.v1",
        "id": "tip_protocol",
        "protocol": {"name": "tip_protocol"},
        "source": {"format": "test"},
        "worktable": {"name": "780_Empty"},
        "labware": [
            {"label": "Tips", "catalog": "MCA96, 100ul, Box", "role": "tips"},
            {"label": "SourcePlate", "catalog": "96 Well Flat", "role": "source"},
        ],
        "reagents": [],
        "liquid_classes": [{"name": "Water Free Single"}],
        "variables": [],
        "worklists": [],
        "dependencies": [],
        "safety_assumptions": [{"id": "manual_validation_required", "text": "Review before use."}],
        "steps": [
            {"operation": "add_labware", "target_labware": "Tips"},
            {"operation": "add_labware", "target_labware": "SourcePlate"},
            {"operation": "pick_up_tips", "target_labware": "Tips"},
            {
                "operation": "aspirate",
                "target_labware": "SourcePlate",
                "volume_ul": 150,
                "liquid_class": "Water Free Single",
                "parameters": {"well": "Z99"},
            },
        ],
    }


class ChecksumGateTests(unittest.TestCase):
    def _zeia(self, tmp: str) -> Path:
        zeia = Path(tmp) / "source.zeia"
        with zipfile.ZipFile(zeia, "w") as zf:
            zf.writestr("Scripts/source.xscr", "<Root />")
        return zeia

    def test_no_project_archive_is_trivial_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "p.xscr"
            xscr.write_text("<VxData />", encoding="utf-8")
            gate = _gate_checksums(xscr, [], {})
            self.assertEqual(gate["status"], "passed")
            self.assertTrue(gate["details"].get("trivial"))

    def test_audit_with_blank_entries_fails_without_waiver(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "p.xscr"
            xscr.write_text("<VxData />", encoding="utf-8")
            gate = _gate_checksums(
                xscr,
                [self._zeia(tmp)],
                {"project_checksum_audit": {"blank_entries": ["Scripts/main.xscr"], "bridge_available": False}},
            )
            self.assertEqual(gate["status"], "failed")
            self.assertIn("Scripts/main.xscr", gate["details"]["blank_entries"])
            self.assertIn("checksums_recompute_waived", gate["details"]["approval_keys"])

    def test_audit_with_absent_entries_fails_without_waiver(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "p.xscr"
            xscr.write_text("<VxData />", encoding="utf-8")
            gate = _gate_checksums(
                xscr,
                [self._zeia(tmp)],
                {
                    "project_checksum_audit": {
                        "absent_entries": ["Scripts/main.xscr"],
                        "blank_entries": [],
                        "bridge_available": True,
                    }
                },
            )

        self.assertEqual(gate["status"], "failed")
        self.assertIn("Scripts/main.xscr", gate["details"]["absent_entries"])

    def test_audit_with_invalid_entries_fails_even_when_waived(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "p.xscr"
            xscr.write_text("<VxData />", encoding="utf-8")
            gate = _gate_checksums(
                xscr,
                [self._zeia(tmp)],
                {
                    "project_checksum_audit": {
                        "invalid_entries": ["Scripts/main.xscr"],
                        "bridge_available": True,
                    },
                    "checksums_recompute_waived": True,
                },
            )

        self.assertEqual(gate["status"], "failed")
        self.assertIn("Scripts/main.xscr", gate["details"]["invalid_entries"])
        self.assertIn("cannot be waived", gate["summary"])

    def test_audit_with_blank_entries_passes_when_waived(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "p.xscr"
            xscr.write_text("<VxData />", encoding="utf-8")
            gate = _gate_checksums(
                xscr,
                [self._zeia(tmp)],
                {
                    "project_checksum_audit": {"blank_entries": ["Scripts/main.xscr"]},
                    "checksums_recompute_waived": True,
                },
            )
            self.assertEqual(gate["status"], "passed")
            self.assertTrue(gate["details"].get("waived"))

    def test_audit_with_valid_entries_passes_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "p.xscr"
            xscr.write_text("<VxData />", encoding="utf-8")
            gate = _gate_checksums(
                xscr,
                [self._zeia(tmp)],
                {"project_checksum_audit": {"blank_entries": []}},
            )
            self.assertEqual(gate["status"], "passed")
            self.assertFalse(gate["details"].get("waived"))

    def test_predicts_failure_when_bridge_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "p.xscr"
            # Blank checksum, no audit, no bridge -> predicted not import-clean.
            xscr.write_text("<VxData><Checksum></Checksum></VxData>", encoding="utf-8")
            with mock.patch("fluent_pipeline.validation.checksum_bridge_available", return_value=False):
                gate = _gate_checksums(xscr, [self._zeia(tmp)], {})
            # Offline test env has no fluentcontrol_core bridge.
            self.assertEqual(gate["status"], "failed")
            self.assertFalse(gate["details"]["bridge_available"])


class RegisteredEvaluatorCompatibilityTests(unittest.TestCase):
    def test_registered_evaluators_match_legacy_facade_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            draft.write_text(DRAFT, encoding="utf-8")
            ir = protocol_ir_from_python(draft)
            diff = {
                "required_tip_boxes": [{"label": "Tips", "status": "available"}],
                "required_carriers": [{"name": "Carrier", "status": "unverified"}],
                "device_aliases": [{"name": "MCA", "status": "available"}],
                "changed_deck_positions": [],
            }
            cases = (
                (
                    "zeia_parsed",
                    ValidationContext(
                        make_gate=_gate,
                        source_manifest={"errors": []},
                    ),
                    lambda: _gate_zeia({"errors": []}, []),
                ),
                (
                    "protocol_ir_schema",
                    ValidationContext(make_gate=_gate, protocol_ir=ir),
                    lambda: _gate_ir_schema(ir, ""),
                ),
                (
                    "tip_boxes_resolve",
                    ValidationContext(make_gate=_gate, worktable_diff=diff),
                    lambda: _gate_tip_boxes(diff),
                ),
                (
                    "carriers_resolve",
                    ValidationContext(make_gate=_gate, worktable_diff=diff),
                    lambda: _gate_carriers(diff),
                ),
                (
                    "device_aliases_resolve",
                    ValidationContext(make_gate=_gate, worktable_diff=diff),
                    lambda: _gate_device_aliases(diff),
                ),
                (
                    "deck_layout_consistent",
                    ValidationContext(make_gate=_gate, worktable_diff=diff),
                    lambda: _gate_deck_layout(diff, {}),
                ),
            )
            for gate_id, context, legacy in cases:
                with self.subTest(gate=gate_id):
                    self.assertEqual(readiness_evaluator(gate_id).evaluate(context), legacy())


class WorktableResourceGateTests(unittest.TestCase):
    def test_tip_boxes_gate_fails_on_missing_box(self):
        diff = {"required_tip_boxes": [{"label": "Tips", "status": "missing"}]}
        gate = _gate_tip_boxes(diff)
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["tip_boxes"][0]["label"], "Tips")

    def test_tip_boxes_gate_passes_when_available(self):
        diff = {"required_tip_boxes": [{"label": "Tips", "status": "available"}]}
        gate = _gate_tip_boxes(diff)
        self.assertEqual(gate["status"], "passed")
        self.assertFalse(gate["details"].get("trivial"))

    def test_tip_boxes_gate_trivial_pass_when_none_required(self):
        gate = _gate_tip_boxes({"required_tip_boxes": []})
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("trivial"))

    def test_carriers_gate_fails_on_missing(self):
        diff = {"required_carriers": [{"name": "Carrier A", "status": "missing"}]}
        gate = _gate_carriers(diff)
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["carriers"][0]["name"], "Carrier A")

    def test_carriers_gate_passes_but_flags_unverified(self):
        diff = {"required_carriers": [{"name": "Carrier A", "status": "unverified"}]}
        gate = _gate_carriers(diff)
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("needs_review"))
        self.assertEqual(gate["details"]["carriers"][0]["name"], "Carrier A")

    def test_device_aliases_gate_passes_but_flags_unverified(self):
        diff = {"device_aliases": [{"name": "MCA96 head", "status": "unverified"}]}
        gate = _gate_device_aliases(diff)
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("needs_review"))

    def test_device_aliases_gate_fails_on_missing(self):
        diff = {"device_aliases": [{"name": "MCA384:1", "status": "missing"}]}
        gate = _gate_device_aliases(diff)
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["device_aliases"][0]["name"], "MCA384:1")

    def test_device_aliases_gate_trivial_pass_when_none_required(self):
        gate = _gate_device_aliases({"device_aliases": []})
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("trivial"))

    def test_deck_layout_gate_blocks_unapproved_move(self):
        diff = {
            "changed_deck_positions": [
                {
                    "label": "SourcePlate",
                    "source_deck_location": "Site 1",
                    "required_deck_location": "Site 3",
                    "status": "changed",
                }
            ]
        }
        gate = _gate_deck_layout(diff, {})
        deck_layout_gate = readiness_gate("deck_layout_consistent")
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["changed_deck_positions"][0]["label"], "SourcePlate")
        self.assertEqual(gate["details"]["approval_keys"], ["deck_layout_changes_approved", "deck_changes_approved"])
        self.assertEqual(gate["details"]["approval_key"], deck_layout_gate.approval_key)
        self.assertEqual(gate["details"]["cli_flag"], deck_layout_gate.cli_flag)
        self.assertEqual(gate["details"]["request_spec_path"], deck_layout_gate.request_spec_path)
        self.assertEqual(gate["details"]["remediation"], deck_layout_gate.remediation)

    def test_deck_layout_gate_passes_when_changes_approved(self):
        diff = {
            "changed_deck_positions": [
                {"label": "SourcePlate", "status": "changed"}
            ]
        }
        gate = _gate_deck_layout(diff, {"deck_layout_changes_approved": True})
        deck_layout_gate = readiness_gate("deck_layout_consistent")
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["details"]["approved_deck_changes"][0]["label"], "SourcePlate")
        self.assertEqual(gate["details"]["approval_key"], deck_layout_gate.approval_key)

    def test_deck_layout_gate_trivial_pass_when_no_changes(self):
        gate = _gate_deck_layout({"changed_deck_positions": []}, {})
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("trivial"))

    def test_worktable_resource_gates_fail_closed_without_ir(self):
        for gate in (
            _gate_tip_boxes(None),
            _gate_carriers(None),
            _gate_device_aliases(None),
            _gate_deck_layout(None, {}),
        ):
            self.assertEqual(gate["status"], "failed")


class GeneratedZeiaGateTests(unittest.TestCase):
    def _zeia(self, tmp: str) -> Path:
        zeia = Path(tmp) / "source.zeia"
        with zipfile.ZipFile(zeia, "w") as zf:
            zf.writestr("Scripts/source.xscr", "<Root />")
        return zeia

    def test_no_project_archive_is_trivial_pass(self):
        gate = _gate_generated_zeia([], {})
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("trivial"))

    def test_pre_flight_without_audit_defers(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_generated_zeia([self._zeia(tmp)], {})
            self.assertEqual(gate["status"], "passed")
            self.assertTrue(gate["details"].get("deferred"))

    def test_blocking_findings_fail_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_generated_zeia(
                [self._zeia(tmp)],
                {
                    "project_archive_audit": {
                        "zip_ok": True,
                        "blocking": [
                            {
                                "kind": "metadata_entry_missing",
                                "entry": "UserSpecific\\x.xscr",
                                "detail": "missing",
                            }
                        ],
                        "needs_review": [],
                    }
                },
            )
            self.assertEqual(gate["status"], "failed")
            self.assertEqual(len(gate["details"]["blocking"]), 1)

    def test_corrupt_zip_fails_even_without_explicit_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_generated_zeia(
                [self._zeia(tmp)],
                {"project_archive_audit": {"zip_ok": False, "blocking": [], "needs_review": []}},
            )
            self.assertEqual(gate["status"], "failed")

    def test_needs_review_findings_pass_but_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_generated_zeia(
                [self._zeia(tmp)],
                {
                    "project_archive_audit": {
                        "zip_ok": True,
                        "blocking": [],
                        "needs_review": [
                            {"kind": "unresolved_reference", "guid": "lc-guid", "type_id": "LiquidClass"}
                        ],
                    }
                },
            )
            self.assertEqual(gate["status"], "passed")
            self.assertEqual(gate["details"].get("needs_review_count"), 1)

    def test_clean_audit_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_generated_zeia(
                [self._zeia(tmp)],
                {"project_archive_audit": {"zip_ok": True, "blocking": [], "needs_review": []}},
            )
            self.assertEqual(gate["status"], "passed")
            self.assertNotIn("needs_review_count", gate["details"])


class CommandInventoryGateTests(unittest.TestCase):
    def _inventory(self, **fields) -> dict:
        name_fields = {
            "labware_types": [],
            "labware_names": [],
            "liquid_class_names": [],
            "device_aliases": [],
            "available_ids": [],
        }
        name_fields.update(fields)
        return {"command_ids": [], "unsupported_commands": [], "name_fields": name_fields}

    def test_extracts_name_fields_from_compiled_xscr(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "p.xscr"
            xscr.write_text(XSCR, encoding="utf-8")
            name_fields = _compiled_command_inventory(xscr)["name_fields"]
            self.assertIn("96 Well Flat", name_fields["labware_types"])
            self.assertIn("SourcePlate", name_fields["labware_names"])
            self.assertIn("Water Free Single", name_fields["liquid_class_names"])
            self.assertIn("Instrument=1/Device=MCA384:1", name_fields["device_aliases"])

    def test_compiled_inspection_reports_fluentcontrol_field_findings(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Verification_Script2</ObjectName>
    <Reference><TypeId>WorktableWorkspace</TypeId><ObjectName>WT</ObjectName></Reference>
    <PayloadData><Script><Properties><VariableDeclarations><VariableDeclarations /></VariableDeclarations></Properties>
      <Commands><ScriptGroup><Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>Adapter A200</LabwareType>
            <LabwareLable>AdapterA200</LabwareLable>
            <LineNumber>1</LineNumber>
          </AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>24 Filter Plate</LabwareType>
            <LabwareLable>FilterDWP[platecount]</LabwareLable>
            <LineNumber>2</LineNumber>
          </AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.UserPromptStatement">
          <UserPromptStatement><Prompt>Check</Prompt><Timeout>0</Timeout><LineNumber>3</LineNumber></UserPromptStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
          <SubRoutineStatement><SubRoutine>"Demo\\MissingSub"</SubRoutine><LineNumber>4</LineNumber></SubRoutineStatement>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaGetFingersScriptCommandDataV1">
          <CgaGetFingersScriptCommandDataV1><ScriptCommandCommonDataV2><LabwareName>AdapterA200</LabwareName><LineNumber>5</LineNumber></ScriptCommandCommonDataV2></CgaGetFingersScriptCommandDataV1>
        </Object>
      </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(
                path,
                preferred_label_catalogs={"adaptera200": "Adapter A200_ElutionRack"},
            )["fluentcontrol_findings"]

        reasons = {item["reason"] for item in findings}
        self.assertIn("label_catalog_mismatch", reasons)
        self.assertIn("prompt_timeout_out_of_range", reasons)
        self.assertIn("undeclared_variable", reasons)
        self.assertIn("subroutine_reference_missing", reasons)
        self.assertIn("rga_fingers_incompatible_labware", reasons)

    def test_compiled_inspection_ignores_disabled_add_labware_duplicates(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>SourceSubroutine</ObjectName>
    <PayloadData><Script>
      <Commands><ScriptGroup><Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>50ml Falcon</LabwareType>
            <LabwareLable>SampleTube[index]</LabwareLable>
            <Location>TubeLocationName</Location>
            <Position>index</Position>
            <Data><ProgrammingStatementBaseDataV1><IsDisabledForExecution>True</IsDisabledForExecution><LineNumber>1</LineNumber></ProgrammingStatementBaseDataV1></Data>
          </AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>50ml Falcon</LabwareType>
            <LabwareLable>SampleTube[index]</LabwareLable>
            <Location>TubeLocationName</Location>
            <Position>index+1</Position>
            <Data><ProgrammingStatementBaseDataV1><IsDisabledForExecution>True</IsDisabledForExecution><LineNumber>2</LineNumber></ProgrammingStatementBaseDataV1></Data>
          </AddLabwareDataV1>
        </Object>
      </Objects></ScriptGroup></Commands>
    </Script></PayloadData>
  </Payload>
</VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "disabled_add_labware.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertNotIn("duplicate_labware_label", {item["reason"] for item in findings})

    def test_compiled_inspection_accepts_a200_elutionrack_catalog(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Verification_Script2</ObjectName>
    <PayloadData><Script><Commands><ScriptGroup><Objects>
      <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
        <AddLabwareDataV1>
          <LabwareType>Adapter A200_ElutionRack</LabwareType>
          <LabwareLable>AdapterA200</LabwareLable>
          <LineNumber>1</LineNumber>
        </AddLabwareDataV1>
      </Object>
    </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "good_a200.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertNotIn(
            "label_catalog_mismatch",
            {item["reason"] for item in findings},
        )

    def test_ir_gate_blocks_a200_adapter_catalog_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft_path = Path(tmp) / "draft.py"
            draft_path.write_text(DRAFT, encoding="utf-8")
            ir = protocol_ir_from_python(draft_path)
        ir["protocol"]["name"] = "Demo_A200_Verification"
        ir["labware"].append(
            {
                "label": "AdapterA200",
                "catalog": "Adapter A200",
                "location": "Demo_Nest_Pos",
                "position": 1,
            }
        )

        gate = _gate_ir_schema(
            ir,
            "",
            source_manifest={
                "worktable_geometry": {
                    "workspaces": [
                        {
                            "name": "Demo_WT",
                            "placements": [
                                {
                                    "label": "AdapterA200",
                                    "catalog": "Adapter A200_ElutionRack",
                                }
                            ],
                        }
                    ]
                }
            },
        )

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(
            gate["details"]["issues"][0]["code"],
            "label_catalog_mismatch",
        )

    def test_rup_worktable_requires_valid_workspace_delta_and_fields(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><ObjectName>PromptTest</ObjectName><PayloadData><Script>
  <Properties><VxWorkspaceData><BaseWorkspaceName>workspace</BaseWorkspaceName>
    <WorkspaceDeltas /></VxWorkspaceData></Properties>
  <Commands><ScriptGroup><Objects>
    <Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPWorktableStatement">
      <RUPWorktableStatement>
        <RUPDisplayAndWait>not-a-bool</RUPDisplayAndWait>
        <RUPAutoClose>also-not-a-bool</RUPAutoClose>
        <RUPTimeOut>oops</RUPTimeOut>
      </RUPWorktableStatement>
    </Object>
  </Objects></ScriptGroup></Commands>
</Script></PayloadData></Payload></VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_rup.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        reasons = {item["reason"] for item in findings}
        self.assertIn("rup_auto_close_invalid", reasons)
        self.assertIn("rup_display_and_wait_invalid", reasons)
        self.assertIn("rup_timeout_out_of_range", reasons)
        self.assertIn("rup_worktable_grid_site_invalid", reasons)
        self.assertIn("rup_worktable_labware_name_missing", reasons)
        self.assertIn("rup_worktable_workspace_delta_missing", reasons)

    def test_rup_worktable_rejects_variable_indexed_labware_name(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><ObjectName>PromptTest</ObjectName><PayloadData><Script>
  <Properties><VxWorkspaceData><BaseWorkspaceName>workspace</BaseWorkspaceName>
    <WorkspaceDeltas><string>&lt;VxWorkspaceDelta&gt;&lt;Identifier&gt;delta-1&lt;/Identifier&gt;&lt;/VxWorkspaceDelta&gt;</string></WorkspaceDeltas>
  </VxWorkspaceData></Properties>
  <Commands><ScriptGroup><Objects>
    <Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPWorktableStatement">
      <RUPWorktableStatement>
        <WorktableProperties><WorktableStatementDataClass><SelectedLabware>
          <ConfigureDataLabwareDataModel>
            <LabwareName>SampleSourceTube[NumSourceTubes_Main]</LabwareName>
            <LabwareTypeName>Tube</LabwareTypeName>
            <Grid>31</Grid><Site>1</Site>
          </ConfigureDataLabwareDataModel>
        </SelectedLabware></WorktableStatementDataClass></WorktableProperties>
        <RUPDisplayAndWait>True</RUPDisplayAndWait><RUPAutoClose>False</RUPAutoClose><RUPTimeOut>1</RUPTimeOut>
      </RUPWorktableStatement>
    </Object>
  </Objects></ScriptGroup></Commands>
</Script></PayloadData></Payload></VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_variable_index_rup.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        reasons = {item["reason"] for item in findings}
        self.assertIn("rup_worktable_variable_labware_index_invalid", reasons)

    def test_workspace_metadata_fixup_copies_source_delta(self):
        target = """<VxData><Payload><PayloadData><Script><Properties>
<VxWorkspaceData><BaseWorkspaceName>old</BaseWorkspaceName><WorkspaceDeltas /></VxWorkspaceData>
</Properties><Commands><RUPWorktableStatement /></Commands></Script></PayloadData></Payload></VxData>"""
        source = """<VxData><Payload><PayloadData><Script><Properties>
<VxWorkspaceData><BaseWorkspaceName>source</BaseWorkspaceName><WorkspaceDeltas>
<string>&lt;VxWorkspaceDelta&gt;&lt;Identifier&gt;delta-123&lt;/Identifier&gt;&lt;/VxWorkspaceDelta&gt;</string>
</WorkspaceDeltas></VxWorkspaceData>
</Properties></Script></PayloadData></Payload></VxData>"""
        with tempfile.TemporaryDirectory() as tmp:
            target_path = Path(tmp) / "target.xscr"
            source_path = Path(tmp) / "source.xscr"
            target_path.write_text(target, encoding="utf-8")
            source_path.write_text(source, encoding="utf-8")

            fixup = _copy_source_workspace_data(target_path, [source_path])
            findings = _compiled_command_inventory(target_path)["fluentcontrol_findings"]

        self.assertEqual(fixup["status"], "replaced")
        self.assertNotIn(
            "rup_worktable_workspace_delta_missing",
            {item["reason"] for item in findings},
        )

    def test_compiled_inspection_blocks_stale_workspace_base_guid(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload>
  <Reference><Guid>packaged-worktable</Guid><TypeId>WorktableWorkspace</TypeId><ObjectName>WT</ObjectName></Reference>
  <PayloadData><Script><Properties>
    <VxWorkspaceData><BaseWorkspaceName>source-worktable</BaseWorkspaceName><WorkspaceDeltas>
      <string>&lt;VxWorkspaceDelta&gt;&lt;Identifier&gt;delta-123&lt;/Identifier&gt;&lt;/VxWorkspaceDelta&gt;</string>
    </WorkspaceDeltas></VxWorkspaceData>
  </Properties><Commands><ScriptGroup><Objects>
    <Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPWorktableStatement">
      <RUPWorktableStatement><RUPDisplayAndWait>True</RUPDisplayAndWait><RUPAutoClose>False</RUPAutoClose><RUPTimeOut>1</RUPTimeOut></RUPWorktableStatement>
    </Object>
  </Objects></ScriptGroup></Commands></Script></PayloadData>
</Payload></VxData>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stale_workspace.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertIn(
            "rup_worktable_base_workspace_mismatch",
            {item["reason"] for item in findings},
        )

    def test_compiled_inspection_accepts_declared_variable_and_referenced_subroutine(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <Reference><TypeId>Script</TypeId><ObjectName>ExistingSub</ObjectName></Reference>
    <PayloadData><Script>
      <Properties><VariableDeclarations><VariableDeclarations>
        <anyType xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper" xmlns:i="http://www.w3.org/2001/XMLSchema-instance"><d3p1:Name>platecount</d3p1:Name><d3p1:Values><string>1</string></d3p1:Values></anyType>
      </VariableDeclarations></VariableDeclarations></Properties>
      <Commands><ScriptGroup><Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1><LabwareType>24 Filter Plate</LabwareType><LabwareLable>FilterDWP[platecount]</LabwareLable></AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.UserPromptStatement">
          <UserPromptStatement><Prompt>Check</Prompt><Timeout>1</Timeout></UserPromptStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
          <SubRoutineStatement><SubRoutine>"Demo\\ExistingSub"</SubRoutine></SubRoutineStatement>
        </Object>
      </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "good.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertEqual(findings, [])

    def test_compiled_inspection_blocks_unbound_variable_definition_helper_prefix(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:ns5="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
  <Payload>
    <PayloadData><Script>
      <Properties><VariableDeclarations><VariableDeclarations>
        <anyType i:type="d3p1:VariableDefinitionHelper"><ns5:Name>platecount</ns5:Name><ns5:Values><string>1</string></ns5:Values></anyType>
      </VariableDeclarations></VariableDeclarations></Properties>
      <Commands />
    </Script></PayloadData>
  </Payload>
</VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_variable_namespace.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertIn(
            "variable_declaration_type_namespace_missing",
            {item["reason"] for item in findings},
        )

    def test_variable_namespace_normalization_repairs_elementtree_reserialized_xscr(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays" xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:ns3="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine" xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData">
  <Payload>
    <PayloadData><Script>
      <Properties><VariableDeclarations><ns3:VariableDeclarations>
        <ns3:VariableDeclarations>
          <d2p1:anyType xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper"><d3p1:Name>platecount</d3p1:Name><d3p1:Values><d2p1:string>1</d2p1:string></d3p1:Values></d2p1:anyType>
        </ns3:VariableDeclarations>
      </ns3:VariableDeclarations></VariableDeclarations></Properties>
      <Commands />
    </Script></PayloadData>
  </Payload>
</sd:VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repaired_variable_namespace.xscr"
            path.write_text(xscr, encoding="utf-8")

            fixups = _normalize_compiled_variable_declaration_namespaces(path)
            text = path.read_text(encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertGreaterEqual(len(fixups), 4)
        self.assertIn(
            'd2p1:anyType xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays" xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper"',
            text,
        )
        self.assertIn(
            '<ns3:VariableDeclarations xmlns:ns3="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine">',
            text,
        )
        root_tag = text.split(">", 1)[0]
        self.assertNotIn("xmlns:d2p1=", root_tag)
        self.assertNotIn("xmlns:d3p1=", root_tag)
        self.assertNotIn("xmlns:i=", root_tag)
        self.assertNotIn("xmlns:ns3=", root_tag)
        self.assertNotIn(
            "variable_declaration_type_namespace_missing",
            {item["reason"] for item in findings},
        )
        self.assertNotIn(
            "variable_declaration_type_namespace_not_local",
            {item["reason"] for item in findings},
        )
        self.assertNotIn(
            "variable_declaration_fragment_namespace_unbound",
            {item["reason"] for item in findings},
        )

    def test_variable_namespace_normalization_localizes_generated_scripting_alias(self):
        """A generated ns4 ScriptEngine alias must not remain root-only.

        ElementTree assigns aliases by document order.  When VxWorkspaceData
        occupies ns1, ScriptEngine can become ns4 instead of the native ns3.
        FluentControl later deserializes VariableDeclarations independently,
        so this alias must be canonicalized and declared on the fragment.
        """
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays" xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:ns4="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine" xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData">
  <Payload><PayloadData><Script><Properties>
    <VariableDeclarations><ns4:VariableDeclarations><ns4:VariableDeclarations>
      <d2p1:anyType i:type="d3p1:VariableDefinitionHelper"><d3p1:Name>TubeFixingCase</d3p1:Name><d3p1:Values><d2p1:string>1</d2p1:string></d3p1:Values></d2p1:anyType>
    </ns4:VariableDeclarations></ns4:VariableDeclarations></VariableDeclarations>
  </Properties><Commands /></Script></PayloadData></Payload>
</sd:VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generated_alias_variable_namespace.xscr"
            path.write_text(xscr, encoding="utf-8")

            fixups = _normalize_compiled_variable_declaration_namespaces(path)
            text = path.read_text(encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertTrue(any(item["from"] == "ns4" and item["to"] == "ns3" for item in fixups))
        self.assertIn(
            '<ns3:VariableDeclarations xmlns:ns3="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine">',
            text,
        )
        self.assertNotIn("ns4:VariableDeclarations", text)
        self.assertNotIn(
            "variable_declaration_fragment_namespace_unbound",
            {item["reason"] for item in findings},
        )
        self.assertNotIn(
            "variable_declaration_type_namespace_missing",
            {item["reason"] for item in findings},
        )

    def test_variable_namespace_normalization_preserves_root_alias_used_by_workspace_metadata(self):
        """Root cleanup must not unbind VxWorkspaceData's i:nil attribute."""
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays" xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:ns1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.SharedGlobal" xmlns:ns3="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine" xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData">
  <Payload><PayloadData><Script><Properties>
    <ns1:VxWorkspaceData><ns1:CameraView i:nil="true" /></ns1:VxWorkspaceData>
    <VariableDeclarations><ns3:VariableDeclarations><ns3:VariableDeclarations>
      <d2p1:anyType i:type="d3p1:VariableDefinitionHelper"><d3p1:Name>TubeFixingCase</d3p1:Name><d3p1:Values><d2p1:string>1</d2p1:string></d3p1:Values></d2p1:anyType>
    </ns3:VariableDeclarations></ns3:VariableDeclarations></VariableDeclarations>
  </Properties><Commands /></Script></PayloadData></Payload>
</sd:VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workspace_i_namespace.xscr"
            path.write_text(xscr, encoding="utf-8")

            _normalize_compiled_variable_declaration_namespaces(path)
            text = path.read_text(encoding="utf-8")
            inventory = _compiled_command_inventory(path)

        root_match = re.search(r"<(?:[\w.-]+:)?VxData\b[^>]*>", text)
        self.assertIsNotNone(root_match)
        root_tag = root_match.group(0) if root_match is not None else ""
        self.assertIn('xmlns:i="http://www.w3.org/2001/XMLSchema-instance"', root_tag)
        self.assertIn('<ns1:CameraView i:nil="true" />', text)
        self.assertIsNone(inventory.get("error"))
        self.assertNotIn(
            "variable_declaration_fragment_namespace_unbound",
            {item["reason"] for item in inventory["fluentcontrol_findings"]},
        )

    def test_variable_namespace_normalization_preserves_workspace_delta_arrays(self):
        xscr = """<?xml version='1.0' encoding='utf-8'?>
<sd:VxData xmlns:ns1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.SharedGlobal" xmlns:ns2="http://schemas.microsoft.com/2003/10/Serialization/Arrays" xmlns:ns3="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine" xmlns:ns5="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Payload><PayloadData><Script><Properties>
    <ns1:VxWorkspaceData><ns1:WorkspaceDeltas><ns2:string>&lt;VxWorkspaceDelta /&gt;</ns2:string></ns1:WorkspaceDeltas></ns1:VxWorkspaceData>
    <VariableDeclarations><ns3:VariableDeclarations><ns3:VariableDeclarations>
      <ns2:anyType xsi:type="d3p1:VariableDefinitionHelper"><ns5:Name>platecount</ns5:Name><ns5:Values><ns2:string>1</ns2:string></ns5:Values></ns2:anyType>
    </ns3:VariableDeclarations></ns3:VariableDeclarations></VariableDeclarations>
  </Properties><Commands /></Script></PayloadData></Payload>
</sd:VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workspace_and_variables.xscr"
            path.write_text(xscr, encoding="utf-8")

            fixups = _normalize_compiled_variable_declaration_namespaces(path)
            text = path.read_text(encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertGreaterEqual(len(fixups), 4)
        self.assertIn("<ns2:string>", text)
        self.assertIn('i:type="d3p1:VariableDefinitionHelper"', text)
        self.assertNotIn("variable_declaration_fragment_namespace_unbound", {item["reason"] for item in findings})
        self.assertNotIn("variable_declaration_type_namespace_missing", {item["reason"] for item in findings})

    def test_variable_namespace_normalization_canonicalizes_reserialized_prefix_aliases(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:ns2="http://schemas.microsoft.com/2003/10/Serialization/Arrays" xmlns:ns3="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine" xmlns:ns5="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Payload><PayloadData><Script><Properties>
    <VariableDeclarations><ns3:VariableDeclarations><ns3:VariableDeclarations>
      <ns2:anyType xsi:type="d3p1:VariableDefinitionHelper"><ns5:Name>A200Method</ns5:Name><ns5:Values><ns2:string>0.0</ns2:string></ns5:Values></ns2:anyType>
    </ns3:VariableDeclarations></ns3:VariableDeclarations></VariableDeclarations>
  </Properties><Commands /></Script></PayloadData></Payload>
</sd:VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliased_variable_namespaces.xscr"
            path.write_text(xscr, encoding="utf-8")

            fixups = _normalize_compiled_variable_declaration_namespaces(path)
            text = path.read_text(encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertGreaterEqual(len(fixups), 4)
        self.assertIn("<d2p1:anyType", text)
        self.assertIn("<d3p1:Name>A200Method</d3p1:Name>", text)
        self.assertIn('i:type="d3p1:VariableDefinitionHelper"', text)
        self.assertNotIn("variable_declaration_fragment_namespace_unbound", {item["reason"] for item in findings})
        self.assertNotIn("variable_declaration_type_namespace_missing", {item["reason"] for item in findings})

    def test_compiled_inspection_blocks_root_only_variable_fragment_namespaces(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays" xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:ns3="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine" xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData">
  <Payload><PayloadData><Script><Properties>
    <VariableDeclarations><ns3:VariableDeclarations><ns3:VariableDeclarations>
      <d2p1:anyType xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper"><d3p1:Name>platecount</d3p1:Name></d2p1:anyType>
    </ns3:VariableDeclarations></ns3:VariableDeclarations></VariableDeclarations>
  </Properties><Commands /></Script></PayloadData></Payload>
</sd:VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "root_only_variable_namespaces.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertIn(
            "variable_declaration_fragment_namespace_unbound",
            {item["reason"] for item in findings},
        )

    def test_compiled_inspection_blocks_ancestor_only_variable_definition_helper_prefix(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:ns5="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Payload>
    <PayloadData><Script>
      <Properties><VariableDeclarations><VariableDeclarations>
        <anyType xsi:type="ns5:VariableDefinitionHelper"><ns5:Name>platecount</ns5:Name><ns5:Values><string>1</string></ns5:Values></anyType>
      </VariableDeclarations></VariableDeclarations></Properties>
      <Commands />
    </Script></PayloadData>
  </Payload>
</VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ancestor_only_variable_namespace.xscr"
            path.write_text(xscr, encoding="utf-8")
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]

        self.assertIn(
            "variable_declaration_type_namespace_not_local",
            {item["reason"] for item in findings},
        )

    def test_subroutine_reference_fixup_adds_script_references_from_ir(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <Reference><TypeId>WorktableWorkspace</TypeId><ObjectName>WT</ObjectName></Reference>
    <PayloadData><Script>
      <Properties><VariableDeclarations><VariableDeclarations /></VariableDeclarations></Properties>
      <Commands><ScriptGroup><Objects>
        <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
          <SubRoutineStatement><SubRoutine>"Demo\\SUB_Get_Fingers_v1.0"</SubRoutine></SubRoutineStatement>
        </Object>
      </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""
        ir = {
            "dependencies": [
                {
                    "kind": "Script",
                    "name": "SUB_Get_Fingers_v1.0",
                    "guid": "bc667100-b840-4a78-8c36-f849849355c4",
                }
            ],
            "steps": [
                {
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_Get_Fingers_v1.0"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "needs_ref.xscr"
            path.write_text(xscr, encoding="utf-8")

            added = _ensure_compiled_subroutine_references(path, ir, None)
            findings = _compiled_command_inventory(path)["fluentcontrol_findings"]
            text = path.read_text(encoding="utf-8")

        self.assertEqual(len(added), 1)
        self.assertIn("<TypeId>Script</TypeId>", text)
        self.assertIn("<ObjectName>SUB_Get_Fingers_v1.0</ObjectName>", text)
        self.assertNotIn("subroutine_reference_missing", {item["reason"] for item in findings})

    def test_trivial_pass_when_nothing_to_check(self):
        gate = _gate_command_inventory(self._inventory(), {"liquid_classes": ["Water Free Single"]}, {})
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("trivial"))

    def test_clean_pass_when_all_names_known(self):
        manifest = {
            "labware_names": ["SourcePlate"],
            "rack_types": ["96 Well Flat"],
            "liquid_classes": ["Water Free Single"],
            "device_aliases": ["Instrument=1/Device=MCA384:1"],
        }
        inventory = self._inventory(
            labware_types=["96 Well Flat"],
            labware_names=["SourcePlate"],
            liquid_class_names=["Water Free Single"],
            device_aliases=["Instrument=1/Device=MCA384:1"],
        )
        gate = _gate_command_inventory(inventory, manifest, {})
        self.assertEqual(gate["status"], "passed")
        self.assertFalse(gate["details"].get("needs_review"))
        self.assertNotIn("unknown_labware_names", gate["details"])

    def test_clean_pass_resolves_bracketed_alias_against_manifest(self):
        manifest = {"liquid_classes": ["Water Free Single"]}
        inventory = self._inventory(liquid_class_names=["Water Free Single[001]"])
        gate = _gate_command_inventory(inventory, manifest, {})
        self.assertEqual(gate["status"], "passed")

    def test_blocking_when_name_absent_from_known_inventory(self):
        manifest = {
            "labware_names": ["SourcePlate"],
            "rack_types": ["96 Well Flat"],
            "liquid_classes": ["Water Free Single"],
        }
        inventory = self._inventory(
            labware_types=["96 Well Flat"],
            labware_names=["SourcePlate"],
            liquid_class_names=["Ghost Liquid Class"],
        )
        gate = _gate_command_inventory(inventory, manifest, {})
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["unknown_liquid_classes"][0]["name"], "Ghost Liquid Class")
        self.assertIn("command_inventory_approved", gate["details"]["approval_keys"])

    def test_blocking_labware_name_is_reported(self):
        manifest = {"labware_names": ["SourcePlate"], "liquid_classes": ["Water Free Single"]}
        inventory = self._inventory(
            labware_names=["MysteryPlate"],
            liquid_class_names=["Water Free Single"],
        )
        gate = _gate_command_inventory(inventory, manifest, {})
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["unknown_labware_names"][0]["name"], "MysteryPlate")

    def test_blocking_can_be_approved(self):
        manifest = {"liquid_classes": ["Water Free Single"]}
        inventory = self._inventory(liquid_class_names=["Ghost Liquid Class"])
        gate = _gate_command_inventory(inventory, manifest, {"command_inventory_approved": True})
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("approved"))
        self.assertTrue(gate["details"].get("needs_review"))

    def test_needs_review_when_manifest_inventory_empty(self):
        inventory = self._inventory(
            labware_names=["SourcePlate"],
            device_aliases=["Instrument=1/Device=MCA384:1"],
        )
        gate = _gate_command_inventory(inventory, {"name": "src"}, {})
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("needs_review"))
        self.assertIn("unknown_labware_names", gate["details"])

    def test_partial_inventory_blocks_known_and_flags_unverified(self):
        manifest = {"liquid_classes": ["Water Free Single"]}
        inventory = self._inventory(
            liquid_class_names=["Ghost LC"],
            device_aliases=["Unknown Device"],
        )
        gate = _gate_command_inventory(inventory, manifest, {})
        self.assertEqual(gate["status"], "failed")
        self.assertIn("unknown_liquid_classes", gate["details"])
        self.assertIn("unknown_device_aliases", gate["details"])
        self.assertTrue(gate["details"].get("needs_review"))


class SubroutineAdditionsGateTests(unittest.TestCase):
    def _zeia(self, tmp: str) -> Path:
        zeia = Path(tmp) / "source.zeia"
        with zipfile.ZipFile(zeia, "w") as zf:
            zf.writestr("Scripts/source.xscr", "<Root />")
        return zeia

    def test_no_project_archive_is_trivial_pass(self):
        gate = _gate_subroutine_additions([], {})
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["details"].get("trivial"))

    def test_pre_flight_without_audit_defers(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_additions([self._zeia(tmp)], {})
            self.assertEqual(gate["status"], "passed")
            self.assertTrue(gate["details"].get("deferred"))

    def test_no_additions_is_safe_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_additions(
                [self._zeia(tmp)],
                {"project_subroutine_audit": {"added": [], "replaced_count": 2, "blocking": []}},
            )
            self.assertEqual(gate["status"], "passed")
            self.assertTrue(gate["details"].get("trivial"))
            self.assertEqual(gate["details"].get("replaced_count"), 2)

    def test_additions_pass_as_needs_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_additions(
                [self._zeia(tmp)],
                {
                    "project_subroutine_audit": {
                        "added": [{"object_name": "Helper", "entry": "DataStore\\x.xscr"}],
                        "blocking": [],
                    }
                },
            )
            self.assertEqual(gate["status"], "passed")
            self.assertTrue(gate["details"].get("needs_review"))
            self.assertEqual(gate["details"].get("added_count"), 1)
            self.assertIn("subroutine_additions_acknowledged", gate["details"]["approval_keys"])

    def test_metadata_defect_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_additions(
                [self._zeia(tmp)],
                {
                    "project_subroutine_audit": {
                        "added": [{"object_name": "Helper", "entry": "DataStore\\x.xscr"}],
                        "blocking": [{"kind": "malformed_guid", "object_name": "Helper"}],
                    }
                },
            )
            self.assertEqual(gate["status"], "failed")
            self.assertEqual(len(gate["details"]["blocking"]), 1)

    def _ir(self, ref: str = "Subroutines\\Helper") -> dict:
        return {
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": ref},
                }
            ]
        }

    def _manifest(self, tmp: str, *, ambiguous: bool = False) -> dict:
        root = Path(tmp)
        helper = root / "Subroutines" / "Helper.xscr"
        helper.parent.mkdir()
        helper.write_text("<Root />", encoding="utf-8")
        scripts = [
            {
                "object_name": "Helper",
                "folder": "Subroutines",
                "guid": "helper-guid",
                "entry": "Subroutines/Helper.xscr",
                "extracted_path": "Subroutines/Helper.xscr",
                "script_version": "1.2",
            }
        ]
        if ambiguous:
            other = root / "Other" / "Helper.xscr"
            other.parent.mkdir()
            other.write_text("<Root />", encoding="utf-8")
            scripts.append(
                {
                    "object_name": "Helper",
                    "folder": "Other",
                    "guid": "other-guid",
                    "entry": "Other/Helper.xscr",
                    "extracted_path": "Other/Helper.xscr",
                    "script_version": "1.0",
                }
            )
        return {"root": str(root), "scripts": scripts}

    def _xscr(self, tmp: str, *, guid: str = "helper-guid", include_ref: bool = True) -> Path:
        ref = f"<Reference><Guid>{guid}</Guid><TypeId>Script</TypeId><ObjectName>Helper</ObjectName></Reference>" if include_ref else ""
        path = Path(tmp) / "compiled.xscr"
        path.write_text(
            f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    {ref}
    <PayloadData><Script><Commands><ScriptGroup><Objects>
      <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
        <SubRoutineStatement><SubRoutine>"Subroutines\\Helper"</SubRoutine></SubRoutineStatement>
      </Object>
    </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
""",
            encoding="utf-8",
        )
        return path

    def test_resolved_subroutine_dependency_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_dependencies(
                self._ir(),
                self._xscr(tmp),
                self._manifest(tmp),
                [],
                {},
            )
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["details"]["resolved"][0]["guid"], "helper-guid")

    def test_missing_subroutine_dependency_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_dependencies(self._ir("Missing"), self._xscr(tmp), self._manifest(tmp), [], {})
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["missing"][0]["ref"], "Missing")

    def test_ambiguous_subroutine_dependency_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_dependencies(self._ir(), self._xscr(tmp), self._manifest(tmp, ambiguous=True), [], {})
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["ambiguous"][0]["object_name"], "Helper")

    def test_compiled_subroutine_reference_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_dependencies(self._ir(), self._xscr(tmp, guid="wrong-guid"), self._manifest(tmp), [], {})
        self.assertEqual(gate["status"], "failed")
        reasons = {item["reason"] for item in gate["details"]["compiled_reference_findings"]}
        self.assertIn("compiled_subroutine_guid_mismatch", reasons)

    def test_packaged_subroutine_dependency_missing_blocks_after_packaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_dependencies(
                self._ir(),
                self._xscr(tmp),
                self._manifest(tmp),
                [],
                {"project_subroutine_audit": {"dependencies": []}},
            )
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["package_findings"][0]["reason"], "packaged_subroutine_dependency_missing")

    def test_packaged_subroutine_dependency_passes_when_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate_subroutine_dependencies(
                self._ir(),
                self._xscr(tmp),
                self._manifest(tmp),
                [],
                {"project_subroutine_audit": {"dependencies": [{"object_name": "Helper", "guid": "helper-guid"}]}},
            )
        self.assertEqual(gate["status"], "passed")


if __name__ == "__main__":
    unittest.main()
