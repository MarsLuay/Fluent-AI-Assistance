from __future__ import annotations

import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from fluent_pipeline.delivery_bundle import validate_v2_delivery_bundle
from fluent_pipeline.runner import PipelineError
from tools.full_export_command_corpus import (
    CommandSample,
    READY_BUNDLE_NAME,
    build_synthetic_xscr,
    collect_workspace_data,
    default_project_root,
    publish_ready_to_import_bundle,
    write_outputs,
)


class FullExportCommandCorpusTests(unittest.TestCase):
    def test_full_export_one_of_each_command_corpus_matches_source_shapes(self):
        project_root = default_project_root()
        if not (project_root / "extracted").exists():
            self.skipTest(f"Imported full export is not present: {project_root}")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            debug_dir = tmp_root / "temporary-debug-artifacts"
            report = write_outputs(project_root, debug_dir, publish_ready_bundle=False)
            synthetic = debug_dir / "one_of_each_command.xscr"

            self.assertTrue(report["ok"], report.get("failures") or report.get("parse_errors"))
            self.assertFalse(report["ready_to_import"])
            self.assertGreaterEqual(report["command_type_count"], 50)
            self.assertGreater(report["variable_declaration_count"], 0)
            self.assertTrue(synthetic.exists())
            self.assertIn("VariableDefinitionHelper", synthetic.read_text(encoding="utf-8"))

            command_ids = {item["command_id"] for item in report["commands"]}
            for expected in (
                "ApplicationDriverMacro",
                "LegacyDriverMacro",
                "LihaAspirateScriptCommandDataV5",
                "RUPStandardStatement",
                "RUPWorktableStatement",
                "CgaDropFingersScriptCommandDataV1",
            ):
                self.assertIn(expected, command_ids)

    def test_ready_to_import_publish_is_zeia_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "project"
            source_zeia = project_root / "source" / "source.zeia"
            source_zeia.parent.mkdir(parents=True)
            with zipfile.ZipFile(source_zeia, "w") as zf:
                zf.writestr("meta/content.xml", "<ArchiveContent />")

            ready_root = tmp_root / "ready-to-import"
            report = {
                "ok": True,
                "project_root": str(project_root),
                "command_type_count": 1,
                "command_instance_count": 1,
                "commands": [],
                "parse_errors": [],
                "failures": [],
            }

            def fake_archive(source_project, destination, **kwargs):
                self.assertEqual(source_project, source_zeia)
                self.assertEqual(kwargs.get("target_script_folder"), "Demo scripts")
                with zipfile.ZipFile(destination, "w") as zf:
                    zf.writestr("DataStore/nodedescription.xml", "<NodeDescription />")
                return {"archive_audit": {"blocking": [], "needs_review": []}}

            with mock.patch("tools.full_export_command_corpus._write_generated_project_archive", side_effect=fake_archive):
                bundle = publish_ready_to_import_bundle(
                    report,
                    "<VxData />",
                    ready_root=ready_root,
                )

            self.assertEqual(bundle.name, f"{READY_BUNDLE_NAME}_v1")
            zeia = bundle / f"{bundle.name}.zeia"
            self.assertTrue(zeia.exists())
            self.assertFalse((bundle / "direct-imports").exists())
            self.assertEqual(list(bundle.rglob("*.xscr")), [])
            self.assertFalse((bundle / "support").exists())
            self.assertFalse((bundle / "generated").exists())
            self.assertFalse((bundle / "reports").exists())
            self.assertTrue((bundle / "source" / "generation_manifest.json").is_file())
            self.assertTrue((bundle / "source" / "GENERATION_WORKFLOW.md").is_file())
            self.assertTrue((bundle / "source" / "delivery_manifest.json").is_file())
            self.assertTrue((bundle / "source" / "generated" / "protocol.py").is_file())
            self.assertTrue((bundle / "source" / "reports" / "command_corpus_report.json").is_file())
            self.assertTrue((bundle / "source" / "collect_tecan_diagnostic_bundle.ps1").is_file())
            result = validate_v2_delivery_bundle(bundle, protocol_name=bundle.name)
            self.assertTrue(result.ok, result.to_dict())

    def test_ready_to_import_publish_counts_unversioned_bundle_as_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "project"
            source_zeia = project_root / "source" / "source.zeia"
            source_zeia.parent.mkdir(parents=True)
            with zipfile.ZipFile(source_zeia, "w") as zf:
                zf.writestr("meta/content.xml", "<ArchiveContent />")

            ready_root = tmp_root / "ready-to-import"
            legacy = ready_root / READY_BUNDLE_NAME
            legacy.mkdir(parents=True)
            (legacy / f"{READY_BUNDLE_NAME}.zeia").write_bytes(b"old")
            report = {
                "ok": True,
                "project_root": str(project_root),
                "command_type_count": 1,
                "command_instance_count": 1,
                "commands": [],
                "parse_errors": [],
                "failures": [],
            }

            def fake_archive(_source_project, destination, **_kwargs):
                with zipfile.ZipFile(destination, "w") as zf:
                    zf.writestr("DataStore/nodedescription.xml", "<NodeDescription />")
                return {"archive_audit": {"blocking": [], "needs_review": []}}

            with mock.patch("tools.full_export_command_corpus._write_generated_project_archive", side_effect=fake_archive):
                bundle = publish_ready_to_import_bundle(
                    report,
                    "<VxData />",
                    ready_root=ready_root,
                )

            self.assertEqual(bundle.name, f"{READY_BUNDLE_NAME}_v2")
            self.assertTrue((bundle / f"{READY_BUNDLE_NAME}_v2.zeia").exists())
            self.assertTrue((legacy / f"{READY_BUNDLE_NAME}.zeia").exists())

    def test_synthetic_xscr_copies_source_variable_declarations(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.xscr"
            source.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <PayloadData>
      <Script>
        <Properties>
          <VxWorkspaceData xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.SharedGlobal">
            <BaseWorkspaceName>4af53cca-0536-45f1-ba26-bf2b5ead68f2</BaseWorkspaceName>
            <CameraView i:nil="true" />
            <WorkspaceDeltas xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays">
              <d2p1:string>&lt;VxWorkspaceDelta xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Worktable.Core"&gt;
  &lt;Identifier&gt;13ae61cf-161e-4709-b400-d461aee37cb0&lt;/Identifier&gt;
  &lt;Jobs xmlns:d2p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Worktable.Core.Update" /&gt;
&lt;/VxWorkspaceDelta&gt;</d2p1:string>
            </WorkspaceDeltas>
          </VxWorkspaceData>
          <VariableDeclarations>
            <VariableDeclarations xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine">
              <VariableDeclarations xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays">
                <d2p1:anyType xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper">
                  <d3p1:Name>tube_pos</d3p1:Name>
                  <d3p1:TypeName>Integer</d3p1:TypeName>
                </d2p1:anyType>
              </VariableDeclarations>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
""",
                encoding="utf-8",
            )
            sample = CommandSample(
                command_id="SetVariableStatement",
                object_type="Tecan.Core.Scripting.Commands.SetVariableStatement",
                xml=(
                    '<Object><SetVariableStatement IsDisabledForExecution="false">'
                    "<VariableName>tube_pos</VariableName><Expression>1</Expression>"
                    "<IsDisabledForExecution>False</IsDisabledForExecution>"
                    "</SetVariableStatement></Object>"
                ),
                source_path=str(source),
                count=1,
            )

            xscr = build_synthetic_xscr([sample])
            workspace = collect_workspace_data([sample])

            self.assertIsNotNone(workspace)
            self.assertEqual(workspace.base_workspace_name, "4af53cca-0536-45f1-ba26-bf2b5ead68f2")
            self.assertIn("VariableDefinitionHelper", xscr)
            self.assertIn("tube_pos", xscr)
            self.assertIn("<ObjectSubfolderPath>Demo scripts</ObjectSubfolderPath>", xscr)
            self.assertIn("<PayloadData>", xscr)
            self.assertIn("<Commands>", xscr)
            self.assertIn("VxWorkspaceData", xscr)
            self.assertLess(xscr.index("VxWorkspaceData"), xscr.index("<VariableDeclarations>"))
            root = ET.fromstring(xscr)
            script_group = root.find("./Payload/PayloadData/Script/Commands/ScriptGroup")
            self.assertIsNotNone(script_group)
            self.assertEqual(["Objects"], [child.tag for child in script_group])
            objects = script_group.find("Objects")
            self.assertIsNotNone(objects)
            self.assertEqual(["Object"], [child.tag for child in objects])
            self.assertEqual("SetVariableStatement", objects[0][0].tag)
            self.assertNotIn("<ScriptGroupDataV1>", xscr)
            self.assertEqual("true", objects[0][0].attrib["IsDisabledForExecution"])
            self.assertEqual("True", objects[0][0].findtext("IsDisabledForExecution"))
            generated_workspace = next(
                element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "VxWorkspaceData"
            )
            generated_deltas = next(
                element
                for element in generated_workspace.iter()
                if element.tag.rsplit("}", 1)[-1] == "WorkspaceDeltas"
            )
            self.assertEqual([], list(generated_deltas))

            runnable_xscr = build_synthetic_xscr([sample], sample_execution="source-behavior")
            runnable_root = ET.fromstring(runnable_xscr)
            runnable_object = runnable_root.find(
                "./Payload/PayloadData/Script/Commands/ScriptGroup/Objects/Object"
            )
            self.assertEqual("false", runnable_object[0].attrib["IsDisabledForExecution"])
            self.assertEqual("False", runnable_object[0].findtext("IsDisabledForExecution"))

    def test_synthetic_xscr_requires_workspace_data_before_variable_declarations(self):
        sample = CommandSample(
            command_id="SetVariableStatement",
            object_type="Tecan.Core.Scripting.Commands.SetVariableStatement",
            xml="<Object><SetVariableStatement><VariableName>tube_pos</VariableName><Expression>1</Expression></SetVariableStatement></Object>",
            source_path="",
            count=1,
        )

        with self.assertRaises(PipelineError):
            build_synthetic_xscr([sample], variable_declarations=[])

    def test_ready_to_import_publish_refuses_dirty_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            project_root = tmp_root / "project"
            source_zeia = project_root / "source" / "source.zeia"
            source_zeia.parent.mkdir(parents=True)
            with zipfile.ZipFile(source_zeia, "w") as zf:
                zf.writestr("meta/content.xml", "<ArchiveContent />")

            ready_root = tmp_root / "ready-to-import"
            report = {
                "ok": True,
                "project_root": str(project_root),
                "command_type_count": 1,
                "command_instance_count": 1,
                "variable_declaration_count": 0,
                "commands": [],
                "parse_errors": [],
                "failures": [],
            }

            def fake_archive(source_project, destination, **kwargs):
                with zipfile.ZipFile(destination, "w") as zf:
                    zf.writestr("DataStore/nodedescription.xml", "<NodeDescription />")
                return {
                    "archive_audit": {
                        "zip_ok": True,
                        "blocking": [{"kind": "invalid_expression"}],
                        "needs_review": [],
                    },
                    "warnings": ["BROKEN IMPORT ARTIFACT: invalid expression"],
                }

            with mock.patch("tools.full_export_command_corpus._write_generated_project_archive", side_effect=fake_archive):
                with self.assertRaises(PipelineError):
                    publish_ready_to_import_bundle(
                        report,
                        "<VxData />",
                        ready_root=ready_root,
                    )

            self.assertFalse((ready_root / READY_BUNDLE_NAME).exists())
            self.assertFalse((ready_root / f"{READY_BUNDLE_NAME}_v1").exists())
            self.assertEqual(list(ready_root.glob("*.staging")), [])


if __name__ == "__main__":
    unittest.main()
