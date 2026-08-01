import tempfile
import unittest
import zipfile
from pathlib import Path

from fluent_pipeline.protocol_ir import protocol_ir_from_xscr, render_python_draft, write_protocol_ir
from fluent_pipeline.traceability import (
    build_traceability_map,
    render_traceability_markdown,
    trace_reference_for_error,
)
from fluent_pipeline.validation import validate_ready_to_import


XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Trace protocol</ObjectName>
    <Reference><TypeId>WorktableWorkspace</TypeId><ObjectName>WT</ObjectName></Reference>
    <Reference><TypeId>LiquidClass</TypeId><ObjectName>Water Free Single</ObjectName></Reference>
    <PayloadData><Script><Commands><ScriptGroup><Objects>
      <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
        <ScriptGroupDataV1><Name>Protocol</Name><Data><Statements>
          <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
            <AddLabwareDataV1>
              <LabwareType>96 Well Flat</LabwareType>
              <LabwareLable>SourcePlate</LabwareLable>
              <Location>Site</Location><Position>1</Position>
              <Data><LineNumber>1</LineNumber></Data>
            </AddLabwareDataV1>
          </Object>
          <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384AspirateScriptCommandDataV2">
            <Mca384AspirateScriptCommandDataV2>
              <LiquidClassName>Water Free Single</LiquidClassName>
              <Volume>20</Volume>
              <ScriptCommandCommonDataV2><LabwareName>SourcePlate</LabwareName><LineNumber>2</LineNumber></ScriptCommandCommonDataV2>
            </Mca384AspirateScriptCommandDataV2>
          </Object>
        </Statements></Data></ScriptGroupDataV1>
      </Object>
    </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""


BAD_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Bad trace protocol</ObjectName>
    <Reference><TypeId>WorktableWorkspace</TypeId><ObjectName>WT</ObjectName></Reference>
    <PayloadData><Script><Properties><VariableDeclarations><VariableDeclarations /></VariableDeclarations></Properties>
      <Commands><ScriptGroup><Objects>
        <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
          <ScriptGroupDataV1><Name>Protocol</Name><Data><Statements>
            <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
              <AddLabwareDataV1>
                <LabwareType>24 Filter Plate</LabwareType>
                <LabwareLable>FilterDWP[platecount]</LabwareLable>
                <Data><LineNumber>3</LineNumber></Data>
              </AddLabwareDataV1>
            </Object>
          </Statements></Data></ScriptGroupDataV1>
        </Object>
      </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""


class TraceabilityTests(unittest.TestCase):
    def test_traceability_map_links_request_ir_python_and_compiled_xscr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xscr = root / "trace.xscr"
            xscr.write_text(XSCR, encoding="utf-8")
            ir = protocol_ir_from_xscr(xscr)
            prompt = "  Use the SOURCE assay setup.\n\tAspiratte from SourcePlate!!!  "
            request_spec = {
                "request": {
                    "intent": "Use the source assay setup. Aspirate from SourcePlate.",
                    "verbatim_prompt": prompt,
                },
                "source": {"source_scripts": ["SourceScript"], "pattern_refs": []},
            }
            ir_path = root / "trace.protocol-ir.json"
            write_protocol_ir(ir, ir_path)
            python = root / "trace.py"
            python.write_text(render_python_draft(ir), encoding="utf-8")

            trace_map = build_traceability_map(
                request_spec=request_spec,
                request_spec_path=root / "request.spec.yaml",
                protocol_ir=ir,
                protocol_ir_path=ir_path,
                python_path=python,
                compiled_xscr_path=xscr,
            )

            aspirate = next(entry for entry in trace_map["entries"] if (entry["ir"]["operation"] == "aspirate"))
            self.assertEqual(trace_map["request"]["verbatim_prompt"], prompt)
            self.assertEqual(aspirate["request"]["verbatim_prompt"], prompt)
            self.assertEqual(aspirate["trace_id"], "step_002")
            self.assertEqual(aspirate["python"]["operation"], "aspirate")
            self.assertEqual(aspirate["compiled_xscr"]["line_number"], "2")
            self.assertIn("Aspirate from SourcePlate.", aspirate["request"]["clauses"])
            markdown = render_traceability_markdown(trace_map)
            self.assertIn("`step_002`", markdown)
            self.assertIn("line `2`", markdown)
            self.assertIn(prompt, markdown)

            ref = trace_reference_for_error("FluentControl failed at line 002", trace_map)
            self.assertEqual(ref["trace_id"], "step_002")

    def test_validation_findings_include_trace_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xscr = root / "bad.xscr"
            xscr.write_text(BAD_XSCR, encoding="utf-8")
            ir = protocol_ir_from_xscr(xscr)
            ir_path = root / "bad.protocol-ir.json"
            write_protocol_ir(ir, ir_path)
            trace_map = build_traceability_map(
                request_spec={"request": {"intent": "Load the variable-indexed filter plate."}},
                request_spec_path=root / "request.spec.yaml",
                protocol_ir=ir,
                protocol_ir_path=ir_path,
                compiled_xscr_path=xscr,
            )
            zeia = root / "source.zeia"
            with zipfile.ZipFile(zeia, "w") as zf:
                zf.writestr("Scripts/source.xscr", "<Root />")

            report = validate_ready_to_import(
                compiled_xscr=xscr,
                protocol_ir=ir_path,
                source_projects=[zeia],
                source_manifest={
                    "name": "source",
                    "errors": [],
                    "workspaces": [{"object_name": "WT"}],
                    "labware_names": ["FilterDWP[platecount]"],
                    "rack_types": ["24 Filter Plate"],
                    "liquid_classes": [],
                    "device_aliases": [],
                    "worklist_paths": [],
                    "scripts": [],
                },
                validation_context={
                    "simulation_passed": True,
                    "repair_plan": {"actions": []},
                    "compile_passed": True,
                    "checksums_recompute_waived": True,
                    "traceability": trace_map,
                },
            )

            post_compile = next(gate for gate in report["gates"] if gate["id"] == "post_compile_xscr_reinspect")
            finding = next(item for item in post_compile["details"]["findings"] if item["reason"] == "undeclared_variable")
            self.assertEqual(finding["line_number"], "3")
            self.assertEqual(finding["trace_reference"]["trace_id"], "step_001")
            self.assertEqual(finding["trace_reference"]["compiled_line"], "3")


if __name__ == "__main__":
    unittest.main()
