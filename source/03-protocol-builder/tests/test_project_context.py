import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import fluent_pipeline.project_context as pc
from fluent_pipeline.import_identity import build_source_import_identity
from fluent_pipeline.provenance import policy_profile_sha256s, sha256_path
from tecan_common.command_registry import command_registry_sha256


class ProjectContextTests(unittest.TestCase):
    def test_project_and_collection_roots_use_ready_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            pc.PROJECTS_DIR = tmp_path / "ready-to-import"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR
            try:
                self.assertEqual(
                    pc.project_dir("demo"),
                    (tmp_path / "ready-to-import" / "demo" / "temp_files").resolve(),
                )
                self.assertEqual(
                    pc.collection_dir("combined"),
                    (tmp_path / "ready-to-import" / "collection-combined" / "temp_files").resolve(),
                )
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir

    def test_context_workspace_is_the_project_temp_files_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context = pc.ProjectContext("demo", tmp_path / "ready-to-import" / "demo" / "temp_files", {})
            collection = pc.ProjectCollection(
                "combined",
                tmp_path / "ready-to-import" / "collection-combined" / "temp_files",
                {},
            )

            self.assertEqual(context.artifacts_root, context.root.resolve())
            self.assertEqual(context.drafts_dir, context.artifacts_root / "drafts")
            self.assertEqual(context.build_dir, context.artifacts_root / "build")
            self.assertEqual(context.reports_dir, context.artifacts_root / "reports")
            self.assertEqual(context.roundtrips_dir, context.artifacts_root / "roundtrips")
            self.assertEqual(
                collection.build_dir,
                (tmp_path / "ready-to-import" / "collection-combined" / "temp_files" / "build").resolve(),
            )
            self.assertEqual(
                pc.resolve_context_path(context, Path("build") / "output.xscr"),
                context.build_dir / "output.xscr",
            )
            self.assertFalse((context.root / "build").exists())

    def test_import_project_builds_manifest_and_resolves_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "demo.zeia"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr(
                        "Scripts/demo.xscr",
                        """<?xml version="1.0"?>
<Root>
  <ObjectName>DemoScript</ObjectName>
  <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
  <Reference>
    <Guid>sub-guid</Guid>
    <TypeId>Script</TypeId>
    <ObjectName>SubScript</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
  </Reference>
  <Script version="1.0" />
  <Object Type="Tecan.Fluent.Commands.MCA96.TransferDataV1">
    <LabwareName>SourcePlate</LabwareName>
    <LiquidClassName>Water Free Single</LiquidClassName>
  </Object>
</Root>
""",
                    )
                    zf.writestr(
                        "Worktables/base.xwsp",
                        """<?xml version="1.0"?>
<Workspace>
  <ObjectName>Base Worktable</ObjectName>
  <Name>Plexiglas Pane[002]</Name>
</Workspace>
""",
                    )
                    zf.writestr("Worklists/sample.gwl", "A;SourcePlate;;Plate Carrier 1;1;;10;Water Free Single")

                ctx = pc.import_project(archive, name="demo-project")

                self.assertEqual(ctx.name, "demo-project")
                self.assertEqual(len(ctx.manifest["scripts"]), 1)
                self.assertEqual(ctx.manifest["scripts"][0]["object_path"], "Demo")
                self.assertEqual(ctx.manifest["scripts"][0]["references"][0]["object_subfolder_path"], "Demo")
                self.assertEqual(len(ctx.manifest["workspaces"]), 1)
                self.assertEqual(ctx.manifest["liquid_classes"], ["Water Free Single"])
                self.assertEqual(ctx.manifest["worklist_paths"], ["Worklists/sample.gwl"])
                self.assertEqual(
                    ctx.manifest["catalog_alias_candidates"],
                    [{"base_name": "Plexiglas Pane", "project_name": "Plexiglas Pane[002]"}],
                )

                resolved = pc.resolve_context_script(ctx, "DemoScript")
                self.assertTrue(resolved.exists())
                self.assertEqual(resolved.name, "demo.xscr")

                self.assertGreaterEqual(len(pc.find_in_project(ctx, "Plexiglas")), 1)
                self.assertGreaterEqual(len(pc.find_in_project(ctx, "Water Free")), 1)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_treats_windows_zip_paths_as_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "windows-paths.zeia"
                _write_script_archive(
                    archive,
                    r"DataStore\UserSpecific\demo.xscr",
                    "WindowsPathScript",
                    "SourcePlate",
                )

                ctx = pc.import_project(archive, name="windows-paths")
                script_path = ctx.root / ctx.manifest["scripts"][0]["extracted_path"]

                self.assertTrue(script_path.exists())
                self.assertEqual(
                    script_path.relative_to(ctx.root).as_posix(),
                    "extracted/DataStore/UserSpecific/demo.xscr",
                )
                if os.name != "nt":
                    self.assertFalse((ctx.extracted_dir / r"DataStore\UserSpecific\demo.xscr").exists())
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_assesses_full_and_partial_zeia_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                full_archive = tmp_path / "full.zeia"
                _write_full_export_like_archive(full_archive)
                full_ctx = pc.import_project(full_archive, name="full-context")

                self.assertEqual(full_ctx.manifest["full_zeia_export"]["status"], "likely_full_export")
                self.assertTrue(full_ctx.manifest["full_zeia_export"]["accepted"])
                full_report = (full_ctx.root / "project_report.md").read_text(encoding="utf-8")
                self.assertIn("Full ZEIA Export Check", full_report)
                self.assertIn("likely_full_export", full_report)

                warning_archive = tmp_path / "warning-full.zeia"
                _write_full_export_like_archive_with_stale_warnings(warning_archive)
                warning_ctx = pc.import_project(warning_archive, name="warning-context")

                warning_assessment = warning_ctx.manifest["full_zeia_export"]
                self.assertEqual(warning_assessment["status"], "likely_full_export")
                self.assertTrue(warning_assessment["accepted"])
                self.assertEqual(warning_assessment["blocking_findings"], [])
                warning_ids = {warning["id"] for warning in warning_assessment["warnings"]}
                self.assertEqual(
                    warning_ids,
                    {
                        "missing_liquid_class_objects",
                        "missing_referenced_worktables",
                        "unresolved_script_references",
                    },
                )
                self.assertIn("stale references in unrelated scripts", warning_assessment["summary"])
                warning_report = (warning_ctx.root / "project_report.md").read_text(encoding="utf-8")
                self.assertIn("Warning signal `missing_liquid_class_objects`", warning_report)

                partial_archive = tmp_path / "partial.zeia"
                _write_script_archive(partial_archive, "Scripts/demo.xscr", "PartialScript", "SourcePlate")
                partial_ctx = pc.import_project(partial_archive, name="partial-context")

                self.assertEqual(partial_ctx.manifest["full_zeia_export"]["status"], "needs_user")
                self.assertFalse(partial_ctx.manifest["full_zeia_export"]["accepted"])
                finding_ids = {
                    finding["id"]
                    for finding in partial_ctx.manifest["full_zeia_export"]["blocking_findings"]
                }
                self.assertIn("missing_liquid_class_objects", finding_ids)
                self.assertIn("no_worktable_objects", finding_ids)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_project_collection_merges_contexts_and_resolves_qualified_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                first = tmp_path / "first.zeia"
                second = tmp_path / "second.zeia"
                _write_script_archive(first, "Scripts/demo.xscr", "SharedScript", "SourcePlate")
                _write_script_archive(second, "Scripts/demo.xscr", "SharedScript", "DestinationPlate")

                pc.import_project(first, name="first-project")
                pc.import_project(second, name="second-project")
                progress_events = []
                collection = pc.create_project_collection(
                    "combined-assay",
                    ["first-project", "second-project"],
                    progress_callback=progress_events.append,
                )

                self.assertEqual(collection.name, "combined-assay")
                self.assertEqual(len(collection.manifest["source_projects"]), 2)
                self.assertEqual(len(collection.manifest["scripts"]), 2)
                self.assertEqual(
                    [script["source_context"] for script in collection.manifest["scripts"]],
                    ["first-project", "second-project"],
                )

                with self.assertRaises(pc.PipelineError):
                    pc.resolve_context_script(collection, "SharedScript")

                resolved = pc.resolve_context_script(collection, "second-project:SharedScript")
                self.assertTrue(resolved.exists())
                self.assertIn("second-project", str(resolved))

                matches = pc.find_in_project(collection, "second-project:SharedScript")
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["source_context"], "second-project")
                terminal_stages = [
                    event.stage_id
                    for event in progress_events
                    if event.status in {"completed", "failed"}
                ]
                self.assertEqual(
                    terminal_stages,
                    [
                        "load_context_1",
                        "load_context_2",
                        "resolve_source_identities",
                        "merge_scripts",
                        "merge_objects",
                        "validate_collection",
                        "write_manifest",
                    ],
                )
                script_progress = [
                    event
                    for event in progress_events
                    if event.stage_id == "merge_scripts" and event.status == "running"
                ]
                self.assertEqual(script_progress[-1].completed_units, 2)
                self.assertEqual(script_progress[-1].total_units, 2)
                self.assertTrue(
                    all(event.operation_id == "create_collection" for event in progress_events)
                )
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_accepts_large_trusted_xml_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "large.zeia"
                large_payload = "x" * (4 * 1024 * 1024 + 1024)
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr(
                        "DataStore/nodedescription.xml",
                        f"<Root><ObjectName>LargeNodeDescription</ObjectName><Description>{large_payload}</Description></Root>",
                    )

                ctx = pc.import_project(archive, name="large-xml")

                self.assertEqual(ctx.manifest["errors"], [])
                self.assertIn("nodedescription", ctx.manifest["object_names"])
                self.assertTrue(ctx.manifest["objects"][0]["oversized_xml"])
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_project_collection_accepts_partial_companion_when_full_export_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                partial_archive = tmp_path / "partial.zeia"
                full_archive = tmp_path / "full.zeia"
                _write_script_archive(partial_archive, "Scripts/demo.xscr", "PartialScript", "SourcePlate")
                _write_full_export_like_archive(full_archive)

                pc.import_project(partial_archive, name="partial-context")
                pc.import_project(full_archive, name="full-context")
                collection = pc.create_project_collection(
                    "partial-plus-full",
                    ["partial-context", "full-context"],
                )

                assessment = collection.manifest["full_zeia_export"]
                self.assertEqual(assessment["status"], "likely_full_export")
                self.assertTrue(assessment["accepted"])
                self.assertEqual(assessment["warnings"][0]["id"], "partial_companion_contexts")
                self.assertEqual(assessment["warnings"][0]["items"][0]["source_context"], "partial-context")
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_indexes_subroutines_custom_assets_and_pin_connectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "custom.zeia"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr(
                        "Scripts/main.xscr",
                        """<?xml version="1.0"?>
<Root>
  <ObjectName>MainScript</ObjectName>
  <Script version="1.0" />
  <VariableDefinitionHelper>
    <Name>OperatorMode</Name>
    <QueryOnStartup>true</QueryOnStartup>
    <QueryOnStartupString>0=manual setup, 1=skip setup prompts</QueryOnStartupString>
    <ReadOnly>false</ReadOnly>
    <Scope>Run</Scope>
    <TypeName>Integer</TypeName>
    <Values><string>0</string></Values>
  </VariableDefinitionHelper>
  <Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPVariableStatement">
    <RUPVariableStatement>
      <VariableDatas>
        <VariableDataModel>
          <Instructions>Please confirm startup values</Instructions>
          <Variables>
            <RupVariableItem>
              <VariableName>OperatorMode</VariableName>
              <DisplayText>Operator mode</DisplayText>
              <DisplayType>Combobox</DisplayType>
              <AllowedValues>0;1</AllowedValues>
              <IsEnabled>true</IsEnabled>
            </RupVariableItem>
          </Variables>
        </VariableDataModel>
      </VariableDatas>
      <RUPScreenTitle>User Input</RUPScreenTitle>
      <RUPDisplayAndWait>True</RUPDisplayAndWait>
      <RUPAutoClose>False</RUPAutoClose>
      <RUPTimeOut>1</RUPTimeOut>
      <LineNumber>7</LineNumber>
    </RUPVariableStatement>
  </Object>
  <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
    <SubRoutine>"Subroutines\\GetFingers"</SubRoutine>
    <PinNumber>GIO1_Pin20</PinNumber>
    <Location>WorktablePin_MiddleFront</Location>
    <CustomDetailImageFilePath>C:\\ProgramData\\Tecan\\VisionX\\TouchToolsData\\Images\\sourcecapholder.jpg</CustomDetailImageFilePath>
    <Barcode>FakeBarcode</Barcode>
  </Object>
  <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
    <AddLabwareDataV1>
      <LabwareType>1x16 15ml Falcon Tube Runner</LabwareType>
      <LabwareLable>DemoTubeRunner[001]</LabwareLable>
    </AddLabwareDataV1>
  </Object>
  <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384AspirateScriptCommandDataV2">
    <Mca384AspirateScriptCommandDataV2>
      <ScriptCommandCommonDataV2>
        <DeviceAlias>Instrument=1/Device=MCA384:1</DeviceAlias>
        <AvailableID>USB:TECAN,FLUENT,1/MCA384:1</AvailableID>
      </ScriptCommandCommonDataV2>
    </Mca384AspirateScriptCommandDataV2>
  </Object>
</Root>
""",
                    )
                    zf.writestr(
                        "Scripts/get-fingers.xscr",
                        """<?xml version="1.0"?>
<Root>
  <ObjectName>GetFingers</ObjectName>
  <Script version="1.0" />
</Root>
""",
                    )
                    zf.writestr(
                        "Worktable/connector.xcon",
                        """<?xml version="1.0"?>
<VxData>
  <Payload>
    <ObjectName>Generated connector for Worktable_Segment_WorktablePin_MiddleFront and custom cap holder</ObjectName>
    <PayloadData>
      <WorktableConnector>
        <ConnectorTemplate>
          <ComponentGuid>component-guid</ComponentGuid>
          <SiteGuid>site-guid</SiteGuid>
          <Description>Worktable_Segment_WorktablePin_MiddleFront connector</Description>
        </ConnectorTemplate>
      </WorktableConnector>
    </PayloadData>
  </Payload>
</VxData>
""",
                    )
                    zf.writestr("fs/1/sourcecapholder.jpg", b"not really a jpeg")

                ctx = pc.import_project(archive, name="custom-project")
                main = ctx.manifest["scripts"][0]
                deps = main["dependencies"]

                self.assertEqual(deps["subroutine_refs"], ['"Subroutines\\GetFingers"'])
                self.assertIn("GIO1_Pin20", deps["pin_refs"])
                self.assertIn("WorktablePin_MiddleFront", deps["worktable_pin_locations"])
                self.assertIn("DemoTubeRunner[001]", deps["labware_names"])
                self.assertIn("1x16 15ml Falcon Tube Runner", deps["rack_types"])
                self.assertIn("Instrument=1/Device=MCA384:1", deps["device_aliases"])
                self.assertIn("USB:TECAN,FLUENT,1/MCA384:1", deps["available_ids"])
                self.assertIn("sourcecapholder.jpg", deps["custom_asset_refs"])
                self.assertIn("FakeBarcode", deps["barcode_refs"])
                self.assertIn("User Input", deps["touchtools_titles"])
                self.assertEqual(main["startup_variables"][0]["name"], "OperatorMode")
                self.assertTrue(main["startup_variables"][0]["query_on_startup"])
                self.assertEqual(
                    main["startup_variables"][0]["prompt"],
                    "0=manual setup, 1=skip setup prompts",
                )
                self.assertEqual(main["operator_prompts"][0]["title"], "User Input")
                self.assertEqual(
                    main["operator_prompts"][0]["variables"][0]["display_text"],
                    "Operator mode",
                )
                self.assertGreaterEqual(ctx.manifest["custom_part_summary"]["pin_connector_count"], 1)
                self.assertIn("sourcecapholder.jpg", ctx.manifest["custom_part_summary"]["asset_refs"])
                self.assertGreaterEqual(len(pc.find_in_project(ctx, "sourcecapholder")), 1)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_indexes_snapshot_evidence_and_collections_roll_it_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "demo.zeia"
                snapshot = tmp_path / "support-snapshot.zip"
                _write_script_archive(archive, "Scripts/demo.xscr", "DemoScript", "SourcePlate")
                _write_snapshot_archive(snapshot)

                ctx = pc.import_project(archive, name="snapshot-project", snapshot_archives=[snapshot])
                summary = ctx.manifest["snapshot_summary"]

                self.assertEqual(ctx.manifest["kind"], "project_with_snapshot")
                self.assertGreaterEqual(summary["role_counts"]["instrument_configuration"], 1)
                self.assertGreaterEqual(summary["role_counts"]["simulation_setup"], 1)
                self.assertGreaterEqual(summary["role_counts"]["hardware_details"], 1)
                self.assertGreaterEqual(summary["role_counts"]["troubleshooting_context"], 1)
                self.assertIn("SN-12345", summary["instrument_serial_numbers"])
                self.assertTrue(any(path.endswith("system.config") for path in summary["system_config_paths"]))
                self.assertGreaterEqual(len(pc.find_in_project(ctx, "SN-12345", kind="snapshot")), 1)

                report = (ctx.root / "project_report.md").read_text(encoding="utf-8")
                self.assertIn("## Snapshot Evidence", report)
                self.assertIn("system.config", report)

                collection = pc.create_project_collection("snapshot-collection", ["snapshot-project"])
                self.assertEqual(len(collection.manifest["snapshot_evidence"]), len(ctx.manifest["snapshot_evidence"]))
                self.assertIn("SN-12345", collection.manifest["snapshot_summary"]["instrument_serial_numbers"])
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_records_composite_import_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "identity.zeia"
                snapshot = tmp_path / "support-snapshot.zip"
                _write_script_archive(archive, "Scripts/demo.xscr", "IdentityScript", "SourcePlate")
                _write_snapshot_archive(snapshot)

                ctx = pc.import_project(archive, name="identity-project", snapshot_archives=[snapshot])
                identity = ctx.manifest["source_import_identity"]
                expected = build_source_import_identity(
                    archive,
                    [snapshot],
                    manifest_schema_version=pc.PROJECT_MANIFEST_SCHEMA_VERSION,
                )

                self.assertEqual(identity, expected)
                self.assertEqual(identity["source_archive_sha256"], sha256_path(archive))
                self.assertEqual(identity["snapshot_archive_sha256s"], [sha256_path(snapshot)])
                self.assertEqual(identity["command_registry_sha256"], command_registry_sha256())
                self.assertEqual(identity["policy_profile_sha256s"], policy_profile_sha256s())
                self.assertTrue(identity["import_options_sha256"])
                self.assertEqual(identity["manifest_schema_version"], pc.PROJECT_MANIFEST_SCHEMA_VERSION)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_reimports_when_snapshot_identity_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "snapshot-change.zeia"
                first_snapshot = tmp_path / "first-snapshot.zip"
                second_snapshot = tmp_path / "second-snapshot.zip"
                _write_script_archive(archive, "Scripts/demo.xscr", "SnapshotScript", "SourcePlate")
                _write_snapshot_archive(first_snapshot)
                _write_snapshot_archive(
                    second_snapshot,
                    instrument_serial="SN-54321",
                    issue_text="Issue Description: alternate snapshot for reuse test\n",
                    log_text="2026-06-08 ERROR alternate simulated deck mismatch\n",
                )

                first = pc.import_project(archive, name="snapshot-change", snapshot_archives=[first_snapshot])
                marker = first.extracted_dir / "reuse-marker.txt"
                marker.write_text("keep-me", encoding="utf-8")

                with self.assertRaises(pc.PipelineError):
                    pc.import_project(archive, name="snapshot-change", snapshot_archives=[second_snapshot])

                second = pc.import_project(
                    archive,
                    name="snapshot-change",
                    snapshot_archives=[second_snapshot],
                    force=True,
                )

                self.assertFalse(marker.exists())
                self.assertNotEqual(second.manifest["source_import_identity"], first.manifest["source_import_identity"])
                self.assertEqual(
                    second.manifest["source_import_identity"]["source_archive_sha256"],
                    first.manifest["source_import_identity"]["source_archive_sha256"],
                )
                self.assertNotEqual(
                    second.manifest["source_import_identity"]["snapshot_archive_sha256s"],
                    first.manifest["source_import_identity"]["snapshot_archive_sha256s"],
                )
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_parses_real_worktable_geometry_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "geometry.zeia"
                _write_geometry_archive(archive)

                ctx = pc.import_project(archive, name="geometry-project")
                geometry = ctx.manifest["worktable_geometry"]

                self.assertEqual(geometry["workspace_count"], 1)
                self.assertEqual(geometry["component_count"], 1)
                self.assertEqual(geometry["site_count"], 1)
                self.assertEqual(geometry["connector_count"], 1)
                self.assertEqual(geometry["pin_sites"][0]["pin_name"], "WorktablePin_MiddleFront")

                workspace = geometry["workspaces"][0]
                self.assertEqual(workspace["guid"], "workspace-guid")
                self.assertEqual(workspace["name"], "Pinned Worktable")
                self.assertEqual(workspace["placement_count"], 1)

                placement = workspace["placements"][0]
                self.assertEqual(placement["label"], "PinnedCarrier[001]")
                self.assertEqual(placement["catalog"], "5 Nest Hotel")
                self.assertEqual(placement["pin_name"], "WorktablePin_MiddleFront")
                self.assertEqual(placement["connector_guid"], "connector-guid")
                self.assertEqual(
                    placement["deck_location"],
                    "WorktablePin_MiddleFront via connector connector-guid at (12.5, -16.5, 3) mm",
                )
                self.assertEqual(
                    placement["connector_position_in_parent_mm"],
                    {"x": 12.5, "y": -16.5, "z": 3.0},
                )
                self.assertEqual(
                    placement["connector_orientation_euler_deg"],
                    {"phi": 90.0, "theta": 0.0, "psi": 180.0},
                )
                self.assertEqual(geometry["components"][0]["arrangements"][0]["site_offsets_mm"]["0"]["x"], 2.0)

                report = (ctx.root / "project_report.md").read_text(encoding="utf-8")
                self.assertIn("## Worktable Geometry", report)
                self.assertIn("WorktablePin_MiddleFront", report)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_project_collection_reuses_imported_worktable_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            original_builder = pc.build_worktable_geometry
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "geometry.zeia"
                _write_geometry_archive(archive)

                pc.import_project(archive, name="geometry-project")

                def fail_if_reparsed(manifest):
                    raise AssertionError("collection should reuse imported geometry")

                pc.build_worktable_geometry = fail_if_reparsed
                collection = pc.create_project_collection("geometry-collection", ["geometry-project"])
                geometry = collection.manifest["worktable_geometry"]

                self.assertEqual(geometry["workspace_count"], 1)
                self.assertEqual(geometry["component_count"], 1)
                self.assertEqual(geometry["site_count"], 1)
                self.assertEqual(geometry["connector_count"], 1)
                self.assertEqual(geometry["workspaces"][0]["source_context"], "geometry-project")
                self.assertEqual(geometry["pin_sites"][0]["pin_name"], "WorktablePin_MiddleFront")
            finally:
                pc.build_worktable_geometry = original_builder
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_reuses_extracted_context_when_archive_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "reuse.zeia"
                _write_script_archive(archive, "Scripts/demo.xscr", "ReuseScript", "SourcePlate")

                first = pc.import_project(archive, name="reuse-project")
                self.assertTrue(first.manifest.get("source_archive_fingerprint"))

                marker = first.extracted_dir / "reuse-marker.txt"
                marker.write_text("keep-me", encoding="utf-8")
                imported_at = first.manifest["imported_at"]

                second = pc.import_project(archive, name="reuse-project", force=True)
                self.assertTrue(marker.exists())
                self.assertEqual(second.manifest["imported_at"], imported_at)
                self.assertEqual(
                    second.manifest["source_archive_fingerprint"],
                    first.manifest["source_archive_fingerprint"],
                )

                third = pc.import_project(archive, name="reuse-project")
                self.assertTrue(marker.exists())
                self.assertEqual(third.manifest["imported_at"], imported_at)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_force_reimports_when_archive_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "changed.zeia"
                _write_script_archive(archive, "Scripts/demo.xscr", "ChangedScript", "SourcePlate")

                first = pc.import_project(archive, name="changed-project")
                marker = first.extracted_dir / "reuse-marker.txt"
                marker.write_text("remove-me", encoding="utf-8")

                with zipfile.ZipFile(archive, "a") as zf:
                    zf.writestr("Worklists/extra.gwl", "A;SourcePlate;;Plate Carrier 1;1;;10;Water Free Single")

                second = pc.import_project(archive, name="changed-project", force=True)
                self.assertFalse(marker.exists())
                self.assertNotEqual(
                    second.manifest["source_archive_fingerprint"],
                    first.manifest["source_archive_fingerprint"],
                )
                self.assertIn("Worklists/extra.gwl", second.manifest["worklist_paths"])
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_import_project_still_errors_when_archive_changes_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "blocked.zeia"
                _write_script_archive(archive, "Scripts/demo.xscr", "BlockedScript", "SourcePlate")
                pc.import_project(archive, name="blocked-project")

                with zipfile.ZipFile(archive, "a") as zf:
                    zf.writestr("Worklists/extra.gwl", "A;SourcePlate;;Plate Carrier 1;1;;10;Water Free Single")

                with self.assertRaises(pc.PipelineError):
                    pc.import_project(archive, name="blocked-project")
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file


def _write_script_archive(path: Path, entry: str, object_name: str, labware_name: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            entry,
            f"""<?xml version="1.0"?>
<Root>
  <ObjectName>{object_name}</ObjectName>
  <Script version="1.0" />
  <Object Type="Tecan.Fluent.Commands.MCA96.TransferDataV1">
    <LabwareName>{labware_name}</LabwareName>
    <LiquidClassName>Water Free Single</LiquidClassName>
  </Object>
</Root>
""",
        )


def _write_full_export_like_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Scripts/demo.xscr",
            """<?xml version="1.0"?>
<Root>
  <ObjectName>FullScript</ObjectName>
  <Reference>
    <Guid>workspace-guid</Guid>
    <TypeId>WorktableWorkspace</TypeId>
    <ObjectName>Base Worktable</ObjectName>
  </Reference>
  <Script version="1.0" />
  <Object Type="Tecan.Fluent.Commands.MCA96.TransferDataV1">
    <LabwareName>SourcePlate</LabwareName>
    <LiquidClassName>Water Free Single</LiquidClassName>
  </Object>
</Root>
""",
        )
        zf.writestr(
            "Worktables/base.xwsp",
            """<?xml version="1.0"?>
<Workspace>
  <ObjectName>Base Worktable</ObjectName>
  <Guid>workspace-guid</Guid>
</Workspace>
""",
        )
        zf.writestr(
            "LiquidClasses/water.xlqc",
            """<?xml version="1.0"?>
<LiquidClass>
  <ObjectName>Water Free Single</ObjectName>
  <Guid>water-free-single-guid</Guid>
</LiquidClass>
""",
        )


def _write_full_export_like_archive_with_stale_warnings(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Scripts/demo.xscr",
            """<?xml version="1.0"?>
<Root>
  <ObjectName>FullScript</ObjectName>
  <Reference>
    <Guid>workspace-guid</Guid>
    <TypeId>WorktableWorkspace</TypeId>
    <ObjectName>Base Worktable</ObjectName>
  </Reference>
  <Reference>
    <Guid>water-free-single-guid</Guid>
    <TypeId>LiquidClass</TypeId>
    <ObjectName>Water Free Single</ObjectName>
  </Reference>
  <Script version="1.0" />
  <Object Type="Tecan.Fluent.Commands.MCA96.TransferDataV1">
    <LabwareName>SourcePlate</LabwareName>
    <LiquidClassName>Water Free Single</LiquidClassName>
  </Object>
</Root>
""",
        )
        zf.writestr(
            "Scripts/stale.xscr",
            """<?xml version="1.0"?>
<Root>
  <ObjectName>StaleScript</ObjectName>
  <Reference>
    <Guid>workspace-guid</Guid>
    <TypeId>WorktableWorkspace</TypeId>
    <ObjectName>Base Worktable</ObjectName>
  </Reference>
  <Reference>
    <Guid>missing-worktable-guid</Guid>
    <TypeId>WorktableWorkspace</TypeId>
    <ObjectName>Missing Worktable</ObjectName>
  </Reference>
  <Reference>
    <Guid>missing-liq-guid</Guid>
    <TypeId>LiquidClass</TypeId>
    <ObjectName>Missing Liquid Class</ObjectName>
  </Reference>
  <Reference>
    <Guid>missing-sub-guid</Guid>
    <TypeId>Script</TypeId>
    <ObjectName>Missing Subroutine</ObjectName>
  </Reference>
  <Script version="1.0" />
  <Object Type="Tecan.Fluent.Commands.MCA96.TransferDataV1">
    <LabwareName>SourcePlate</LabwareName>
    <LiquidClassName>Missing Liquid Class</LiquidClassName>
  </Object>
</Root>
""",
        )
        zf.writestr(
            "Worktables/base.xwsp",
            """<?xml version="1.0"?>
<Workspace>
  <ObjectName>Base Worktable</ObjectName>
  <Guid>workspace-guid</Guid>
</Workspace>
""",
        )
        for index, name in enumerate(
            [
                "Water Free Single",
                "Standard Wash",
                "Water Mix",
                "Buffer Contact Wet",
                "Buffer Contact Dry",
            ],
            start=1,
        ):
            zf.writestr(
                f"LiquidClasses/{index}.xlqc",
                f"""<?xml version="1.0"?>
<LiquidClass>
  <ObjectName>{name}</ObjectName>
  <Guid>liquid-class-guid-{index}</Guid>
</LiquidClass>
""",
            )


def _write_snapshot_archive(
    path: Path,
    *,
    instrument_serial: str = "SN-12345",
    issue_text: str = "Issue Description: MCA arm error during simulation\n",
    log_text: str = "2026-06-08 ERROR simulated deck mismatch\n",
) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Instrument/system.config",
            """<?xml version="1.0"?>
<SystemConfiguration>
  <InstrumentSerialNumber>{instrument_serial}</InstrumentSerialNumber>
  <InstrumentSize>Fluent 780</InstrumentSize>
  <ArmOrder>LiHa,MCA384,RGA</ArmOrder>
  <TipConfiguration>Disposable tips</TipConfiguration>
  <DeviceName>MCA384</DeviceName>
  <FirmwareVersion>1.2.3</FirmwareVersion>
  <DriverVersion>4.5.6</DriverVersion>
</SystemConfiguration>
""".format(instrument_serial=instrument_serial),
        )
        zf.writestr("Firmware/FirmwareVersions.txt", "FirmwareVersion: 1.2.3\nDriverVersion: 4.5.6\n")
        zf.writestr("Issue/User Description.txt", issue_text)
        zf.writestr("Logs/FluentControl.log", log_text)
        zf.writestr("Screenshots/screenshot.png", b"not really a png")


def _write_geometry_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "DataStore/SystemSpecific/Worktable/Sites/site-pin-guid.xsit",
            """<?xml version="1.0"?>
<VxData>
  <Payload>
    <ObjectName>WorktablePin_MiddleFront Site</ObjectName>
    <Reference>
      <TypeId>WorktableConnector</TypeId>
      <Guid>connector-guid</Guid>
    </Reference>
    <PayloadData>
      <WorktableSite>
        <SiteTemplate>
          <GUID>site-pin-guid</GUID>
          <LocationGroupName>WorktablePin_MiddleFront</LocationGroupName>
          <TypeName>Worktable_Segment_WorktablePin_MiddleFront</TypeName>
          <Dimension><X>1</X><Y>2</Y><Z>3</Z></Dimension>
          <Orientation>
            <Mat>
              <ArrayOfdouble><double>1</double><double>0</double><double>0</double></ArrayOfdouble>
              <ArrayOfdouble><double>0</double><double>1</double><double>0</double></ArrayOfdouble>
              <ArrayOfdouble><double>0</double><double>0</double><double>1</double></ArrayOfdouble>
            </Mat>
          </Orientation>
        </SiteTemplate>
      </WorktableSite>
    </PayloadData>
  </Payload>
</VxData>
""",
        )
        zf.writestr(
            "DataStore/SystemSpecific/Worktable/Connectors/connector-guid.xcon",
            """<?xml version="1.0"?>
<VxData>
  <Payload>
    <ObjectName>Connector for WorktablePin_MiddleFront</ObjectName>
    <PayloadData>
      <WorktableConnector>
        <ConnectorTemplate>
          <GUID>connector-guid</GUID>
          <ComponentGuid>carrier-guid</ComponentGuid>
          <SiteGuid>site-pin-guid</SiteGuid>
          <Description>Phi=90, Theta=0, Psi=180</Description>
          <PositionInParent><X>12.5</X><Y>-16.5</Y><Z>3</Z></PositionInParent>
          <Orientation>
            <Mat>
              <ArrayOfdouble><double>0</double><double>-1</double><double>0</double></ArrayOfdouble>
              <ArrayOfdouble><double>1</double><double>0</double><double>0</double></ArrayOfdouble>
              <ArrayOfdouble><double>0</double><double>0</double><double>1</double></ArrayOfdouble>
            </Mat>
          </Orientation>
          <IsDefaultConnector>true</IsDefaultConnector>
        </ConnectorTemplate>
      </WorktableConnector>
    </PayloadData>
  </Payload>
</VxData>
""",
        )
        zf.writestr(
            "DataStore/SystemSpecific/Worktable/Components/carrier-guid.xcmp",
            """<?xml version="1.0"?>
<VxData>
  <Payload>
    <ObjectName>5 Nest Hotel</ObjectName>
    <Reference>
      <TypeId>WorktableSite</TypeId>
      <Guid>site-pin-guid</Guid>
    </Reference>
    <Reference>
      <TypeId>WorktableConnector</TypeId>
      <Guid>connector-guid</Guid>
    </Reference>
    <PayloadData>
      <CarrierOrLabware>
        <CarrierOrLabwareTemplate>
          <GUID>carrier-guid</GUID>
          <FunctionalGroup>Carrier</FunctionalGroup>
          <FootPrint>Worktable</FootPrint>
          <Renderer>CarrierRenderer</Renderer>
          <Dimension><X>10</X><Y>20</Y><Z>30</Z></Dimension>
          <Arrangements>
            <ArrangementTemplate>
              <SitesInX>1</SitesInX>
              <SitesInY>1</SitesInY>
              <SitesInZ>1</SitesInZ>
              <SiteSpacingInX>0</SiteSpacingInX>
              <SiteSpacingInY>0</SiteSpacingInY>
              <SiteSpacingInZ>0</SiteSpacingInZ>
              <PositionInParent><X>1</X><Y>2</Y><Z>3</Z></PositionInParent>
              <SiteOffsets>
                <KeyValueOfintVector>
                  <Key>0</Key>
                  <Value><X>2</X><Y>4</Y><Z>6</Z></Value>
                </KeyValueOfintVector>
              </SiteOffsets>
              <SiteTemplateIdentifiers>
                <KeyValueOfintguid>
                  <Key>0</Key>
                  <Value>site-pin-guid</Value>
                </KeyValueOfintguid>
              </SiteTemplateIdentifiers>
            </ArrangementTemplate>
          </Arrangements>
        </CarrierOrLabwareTemplate>
      </CarrierOrLabware>
    </PayloadData>
  </Payload>
</VxData>
""",
        )
        zf.writestr(
            "DataStore/SystemSpecific/Worktable/Workspaces/workspace-guid.xwsp",
            """<?xml version="1.0"?>
<VxData>
  <Payload>
    <ObjectName>Pinned Worktable</ObjectName>
    <Reference>
      <TypeId>WorktableComponent</TypeId>
      <Guid>base-worktable-guid</Guid>
      <ObjectName>Base Worktable</ObjectName>
    </Reference>
    <PayloadData>
      <WorktableWorkspace>
        <Workspace>
          <Worktables>
            <KeyValueOfstringWorktable1z8Zwvmi>
              <Key>default</Key>
              <Value>
                <Frame>
                  <Arrangements>
                    <Arrangement>
                      <Sites>
                        <KeyValueOfintSite1z8Zwvmi>
                          <Key>0</Key>
                          <Value>
                            <BaseTemplateGuid>site-pin-guid</BaseTemplateGuid>
                            <ConnectorTemplateGuid>connector-guid</ConnectorTemplateGuid>
                            <Adjustment>
                              <origin><X>0</X><Y>0</Y><Z>0</Z></origin>
                              <orientation>
                                <Mat>
                                  <ArrayOfdouble><double>1</double><double>0</double><double>0</double></ArrayOfdouble>
                                  <ArrayOfdouble><double>0</double><double>1</double><double>0</double></ArrayOfdouble>
                                  <ArrayOfdouble><double>0</double><double>0</double><double>1</double></ArrayOfdouble>
                                </Mat>
                              </orientation>
                            </Adjustment>
                            <ConnectedComponent>
                              <BaseLocationConnectorIdentifier>connector-guid</BaseLocationConnectorIdentifier>
                              <BaseLocationIdentifier>site-location-guid</BaseLocationIdentifier>
                              <CarrierOrLabwareTemplateGUID>carrier-guid</CarrierOrLabwareTemplateGUID>
                              <LabwareName>
                                <KeyValueOfstringstring>
                                  <Key>default</Key>
                                  <Value>PinnedCarrier[001]</Value>
                                </KeyValueOfstringstring>
                              </LabwareName>
                            </ConnectedComponent>
                          </Value>
                        </KeyValueOfintSite1z8Zwvmi>
                      </Sites>
                    </Arrangement>
                  </Arrangements>
                </Frame>
              </Value>
            </KeyValueOfstringWorktable1z8Zwvmi>
          </Worktables>
        </Workspace>
      </WorktableWorkspace>
    </PayloadData>
  </Payload>
</VxData>
""",
        )


_SAMPLE_XSCR_XML = """<?xml version="1.0"?>
<Root>
  <ObjectName>SUB_Get_Fingers_v1.0</ObjectName>
  <Script version="2.0" />
  <Object Type="Tecan.Core.Scripting.UserPromptStatement" />
</Root>
"""


class ScriptGuidCaptureTests(unittest.TestCase):
    """A script's own Script GUID is its datastore entry filename stem."""

    def test_entry_object_guid_extracts_guid_named_entries(self):
        guid = "fd461d1d-b4b4-52fe-abc8-6a030b971a29"
        self.assertEqual(pc._entry_object_guid(f"DataStore/UserSpecific/{guid}.xscr"), guid)
        self.assertEqual(pc._entry_object_guid(f"DataStore\\UserSpecific\\{guid}.xscr"), guid)

    def test_entry_object_guid_ignores_non_guid_and_zero(self):
        self.assertEqual(pc._entry_object_guid("source/original-sources/source_script_1.xscr"), "")
        self.assertEqual(
            pc._entry_object_guid("DataStore/00000000-0000-0000-0000-000000000000.xscr"), ""
        )
        self.assertEqual(pc._entry_object_guid(""), "")

    def _write(self, tmp, name):
        path = Path(tmp) / name
        path.write_text(_SAMPLE_XSCR_XML, encoding="utf-8")
        return path

    def test_inspect_xscr_captures_own_guid(self):
        guid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            entry = f"DataStore/UserSpecific/{guid}.xscr"
            record = pc._inspect_xscr(self._write(tmp, f"{guid}.xscr"), entry, entry)
        self.assertEqual(record["guid"], guid)
        self.assertEqual(record["script_guid"], guid)
        self.assertIn(guid, record["guids"])

    def test_inspect_xscr_fast_captures_own_guid(self):
        guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with tempfile.TemporaryDirectory() as tmp:
            entry = f"DataStore/UserSpecific/{guid}.xscr"
            record = pc._inspect_xscr_fast(self._write(tmp, f"{guid}.xscr"), entry, entry)
        self.assertEqual(record["guid"], guid)
        self.assertIn(guid, record["guids"])

    def test_inspect_xscr_no_guid_for_named_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = "source/original-sources/source_script_1.xscr"
            record = pc._inspect_xscr(self._write(tmp, "source_script_1.xscr"), entry, entry)
        self.assertEqual(record["guid"], "")
        self.assertEqual(record["guids"], [])


if __name__ == "__main__":
    unittest.main()
