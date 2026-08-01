import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.cli import main as cli_main
from fluent_pipeline.fluent_log_parser import (
    build_latest_fluent_log_report,
    build_fluent_log_report,
    diagnose_fluent_log_text,
    diagnostics_to_findings,
    parse_fluent_log_text,
)


class FluentLogParserTests(unittest.TestCase):
    def test_parses_script_editor_error_with_details(self):
        text = (
            "2026-06-11 10:45:00 [Script Editor] ERROR VX_SCEDT_001_005 failed during processing script commands\n"
            "System.Runtime.Serialization.SerializationException: VariableDefinitionHelper could not deserialize\n"
            "<d2p1:anyType xsi:type=\"VariableDefinitionHelper\">\n"
        )

        records = parse_fluent_log_text(text, source="FluentControl.log")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].timestamp, "2026-06-11 10:45:00")
        self.assertEqual(records[0].application, "Script Editor")
        self.assertEqual(records[0].error_id, "VX_SCEDT_001_005")
        self.assertTrue(any("VariableDefinitionHelper" in line for line in records[0].detail_lines))

    def test_parses_script_name_from_fluentcontrol_load_message(self):
        records = parse_fluent_log_text(
            "2026-07-10 10:45:00 ERROR The load operation for Script 'Verification Script A' failed: "
            "Mismatching If-Else branches"
        )

        self.assertEqual(records[0].script, "Verification Script A")

    def test_runtime_command_inherits_nearby_script_name(self):
        diagnostics = diagnose_fluent_log_text(
            "2026-07-10 10:45:00 ERROR Script 'Verification Script A' Mismatching If-Else branches\n"
            '2026-07-10 10:45:01 ERROR Command "ResolvexA200_Run" is unknown\n'
        )

        resolvex = next(item for item in diagnostics if item["id"] == "fluent_log.resolvex_a200_command_unknown")
        self.assertEqual(resolvex["records"][0]["script"], "Verification Script A")

    def test_audit_import_associates_a_following_unnamed_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "FluentControl.ulf"
            audit = root / "AuditTrail.csv"
            log.write_text(
                '<Log MsgID="1" TimeStamp="2026-07-09 17:30:22.328" '
                'Channel="Tecan/Fluent Control/Developer/Driver/Application Driver" Severity="Error" '
                'Message="Unable to start transfer because no gripper fingers are mounted." />\n',
                encoding="utf-8",
            )
            audit.write_text(
                "2026-07-09 17:28:20.721;No user logged in;Datastore Change;;;;"
                "Items imported from 'C:\\bundle\\full_export_command_corpus_v6.zeia': "
                "Full Export One Of Each Command Corpus;\n",
                encoding="utf-8",
            )

            report = build_fluent_log_report(log, audit_paths=[audit])

        gripper = next(item for item in report["diagnostics"] if item["id"] == "fluent_log.gripper_fingers_not_mounted")
        record = gripper["records"][0]
        self.assertEqual(record["script"], "Full Export One Of Each Command Corpus")
        self.assertEqual(record["script_source"], "audit_import_timeline")
        self.assertIn("full_export_command_corpus_v6.zeia", record["script_context"])

    def test_runtime_command_inherits_fluentcontrol_unloaded_script_name(self):
        instrumentation = "\n".join(
            '<Log MsgID="{0}" TimeStamp="2026-07-10 11:46:31.950" '
            'Channel="Tecan/Fluent Control/Instrument/FCA" Severity="Info" '
            'Message="Driver telemetry" />'.format(index)
            for index in range(3, 84)
        )
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="1" TimeStamp="2026-07-10 11:46:31.937" '
            'Channel="Tecan/Fluent Control/Scripting/CommandTool" Severity="Warning" '
            'Message="Error while creating the device"><Exception>'
            'MoveAxisCommandScriptStatement.get_AxisDrive()</Exception></Log>\n'
            + instrumentation
            + "\n"
            '<Log MsgID="2" TimeStamp="2026-07-10 11:46:31.977" '
            'Channel="Tecan/Fluent Control/Scripting" Severity="Info" '
            'Message="Unloaded script SUB_CapBCScanHandeling_v0.4." />\n'
        )

        move_axis = next(item for item in diagnostics if item["id"] == "fluent_log.move_axis_drive_unresolved")
        self.assertEqual(move_axis["records"][0]["script"], "SUB_CapBCScanHandeling_v0.4")

    def test_scope_stack_attributes_leaf_script_and_main_script(self):
        records = parse_fluent_log_text(
            '<Log MsgID="1" TimeStamp="2026-07-10 12:00:00.000" '
            'Channel="Tecan/Fluent Control/Scripting" Severity="Info" '
            'Message="Scope: Method|Verification_Script1|1|SUB_Get_Fingers_v1.0&#xD;&#xA;Value: 0">'
            "<ThreadId>66</ThreadId></Log>"
        )

        self.assertEqual(records[0].script, "SUB_Get_Fingers_v1.0")
        self.assertEqual(records[0].main_script, "Verification_Script1")
        self.assertEqual(records[0].script_source, "scope_stack")
        self.assertEqual(records[0].thread_id, "66")

    def test_thread_scope_carries_leaf_script_onto_later_error(self):
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="1" TimeStamp="2026-07-10 12:00:00.000" '
            'Channel="Tecan/Fluent Control/Scripting" Severity="Info" '
            'Message="Scope: Method|Verification_Script1|1|SUB_Spin_Tube_v0.1&#xD;&#xA;Value: 0">'
            "<ThreadId>66</ThreadId></Log>\n"
            '<Log MsgID="2" TimeStamp="2026-07-10 12:00:01.000" '
            'Channel="Tecan/Fluent Control/Scripting/CommandTool" Severity="Warning" '
            'Message="Error while creating the device"><Exception>'
            "MoveAxisCommandScriptStatement.get_AxisDrive()</Exception>"
            "<ThreadId>66</ThreadId></Log>"
        )

        move_axis = next(item for item in diagnostics if item["id"] == "fluent_log.move_axis_drive_unresolved")
        record = move_axis["records"][0]
        self.assertEqual(record["script"], "SUB_Spin_Tube_v0.1")
        self.assertEqual(record["main_script"], "Verification_Script1")
        self.assertEqual(record["script_source"], "thread_scope")
        self.assertEqual(record["command_hint"], "MoveAxisCommandScriptStatement")
        # Names mined from this log scope — not baked into the static rule text.
        self.assertIn("SUB_Spin_Tube_v0.1", move_axis["suggested_fix"])
        self.assertIn("Verification_Script1", move_axis["suggested_fix"])

    def test_rup_message_captures_script_line_number(self):
        records = parse_fluent_log_text(
            '<Log MsgID="1" TimeStamp="2026-07-10 12:00:00.000" '
            'Channel="Tecan/Fluent Control/Scripting" Severity="Info" '
            'Message="RUP Statement in Execute: User Prompt Line number: 13" />'
        )

        self.assertEqual(records[0].script_line, 13)

    def test_xscr_command_index_pins_unique_script_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "SUB_Spin_Tube_v0.1.xscr"
            xscr.write_text(
                "<Script><Statements>"
                "<MoveAxisCommandDataV1><LineNumber>42</LineNumber></MoveAxisCommandDataV1>"
                "</Statements></Script>",
                encoding="utf-8",
            )
            log = Path(tmp) / "FluentControl.ulf"
            log.write_text(
                '<Log MsgID="1" TimeStamp="2026-07-10 12:00:00.000" '
                'Channel="Tecan/Fluent Control/Scripting" Severity="Info" '
                'Message="Scope: Method|Verification_Script1|1|SUB_Spin_Tube_v0.1">'
                "<ThreadId>66</ThreadId></Log>\n"
                '<Log MsgID="2" TimeStamp="2026-07-10 12:00:01.000" '
                'Channel="Tecan/Fluent Control/Scripting/CommandTool" Severity="Warning" '
                'Message="Error while creating the device"><Exception>'
                "MoveAxisCommandScriptStatement.get_AxisDrive()</Exception>"
                "<ThreadId>66</ThreadId></Log>\n",
                encoding="utf-8",
            )
            report = build_fluent_log_report(log, xscr_paths=[xscr])

        move_axis = next(
            item for item in report["diagnostics"] if item["id"] == "fluent_log.move_axis_drive_unresolved"
        )
        record = move_axis["records"][0]
        self.assertEqual(record["script"], "SUB_Spin_Tube_v0.1")
        self.assertEqual(record["script_line"], 42)
        self.assertEqual(record["command_hint"], "MoveAxisCommandScriptStatement")

    def test_maps_variable_definition_namespace_failure(self):
        diagnostics = diagnose_fluent_log_text(
            "2026-06-11 10:45:00 VX_SCEDT_001_005 failed to load script\n"
            "VariableDefinitionHelper namespace missing d3p1 and invalid xsi:type\n"
        )

        ids = {item["id"] for item in diagnostics}
        self.assertIn("fluent_log.variable_definition_namespace", ids)
        diagnostic = next(item for item in diagnostics if item["id"] == "fluent_log.variable_definition_namespace")
        self.assertEqual(diagnostic["severity"], "blocking")
        self.assertIn("namespace normalization", diagnostic["suggested_fix"])

    def test_maps_rup_worktable_variable_index_deserialize_failure(self):
        diagnostics = diagnose_fluent_log_text(
            "ErrorID: VX_SCEDT_001_005, Description: The load operation failed during processing script commands.\n"
            "System.FormatException: Input string was not in a correct format.\n"
            "at System.Number.ParseInt32(String s, NumberStyles style, NumberFormatInfo info)\n"
            "at Microsoft.Xml.Serialization.GeneratedAssembly.XmlSerializationReaderWorktableStatementDataClass."
            "Read5_ConfigureDataLabwareDataModel(Boolean isNullable, Boolean checkType)\n"
        )

        ids = {item["id"] for item in diagnostics}
        self.assertIn("fluent_log.rup_worktable_variable_labware_index", ids)
        self.assertNotIn("fluent_log.variable_definition_namespace", ids)
        diagnostic = next(
            item for item in diagnostics if item["id"] == "fluent_log.rup_worktable_variable_labware_index"
        )
        self.assertIn("numeric instances", diagnostic["suggested_fix"])

    def test_maps_split_script_editor_dialog_to_nearby_worktable_stack(self):
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="11900" TimeStamp="2026-07-09 09:04:04.506" '
            'Channel="Tecan/Fluent Control/Developer/Application Frame/ScriptEditor" '
            'Type="GeneralMessage" Severity="Error" '
            'Message="LoadWorker(Verification_Script1) got ScriptReadStatementsException: '
            'Failed to deserialize statements while loading Script."><Exception><![CDATA['
            "System.FormatException: Input string was not in a correct format.\n"
            "   at System.Number.ParseInt32(String s, NumberStyles style, NumberFormatInfo info)\n"
            "   at Microsoft.Xml.Serialization.GeneratedAssembly.XmlSerializationReaderWorktableStatementDataClass."
            "Read5_ConfigureDataLabwareDataModel(Boolean isNullable, Boolean checkType)"
            ']]></Exception></Log>\n'
            '<Log MsgID="11901" TimeStamp="2026-07-09 09:04:04.640" '
            'Channel="Tecan/Fluent Control/Worktable" Type="GeneralMessage" Severity="Info" '
            'Message="ErrorID: VX_SCEDT_001_005, Description: The load operation for Script '
            'Verification_Script1 failed during processing the script commands." />\n'
        )

        ids = {item["id"] for item in diagnostics}
        self.assertIn("fluent_log.rup_worktable_variable_labware_index", ids)
        self.assertNotIn("fluent_log.script_editor_load_failed", ids)

    def test_maps_scriptgroup_invalid_xml_node_failure(self):
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="13122" TimeStamp="2026-07-09 14:54:27.479" '
            'Channel="Tecan/Fluent Control/Developer/Application Frame/ScriptEditor" '
            'Type="GeneralMessage" Severity="Error" '
            'Message="LoadWorker(Full Export One Of Each Command Corpus) got '
            'ScriptReadStatementsException: Failed to deserialize statements while loading Script.">'
            "<Exception><![CDATA[Tecan.VisionX.SharedGlobal.ScriptReadStatementsException: "
            "Failed to deserialize statements while loading Script. ---> System.Xml.XmlException: "
            "'Element' is an invalid XmlNodeType. Line 1, position 266.\n"
            "   at System.Xml.XmlReader.ReadEndElement()\n"
            "   at Tecan.CommonComponents.DriverManagement.Serialization."
            "DefaultSerializerImplementation.DeserializeListElements(XmlReader reader, IList target)\n"
            "   at Microsoft.Xml.Serialization.GeneratedAssembly.XmlSerializationReaderScriptGroup."
            "Read1_ScriptGroup()]]></Exception></Log>\n"
            '<Log MsgID="13123" TimeStamp="2026-07-09 14:54:27.580" '
            'Channel="Tecan/Fluent Control/Worktable" Type="GeneralMessage" Severity="Info" '
            'Message="ErrorID: VX_SCEDT_001_005, Description: The load operation for Script '
            'Full Export One Of Each Command Corpus failed during processing the script commands." />\n'
        )

        ids = {item["id"] for item in diagnostics}
        self.assertIn("fluent_log.scriptgroup_statement_list_invalid_xml_node", ids)
        self.assertNotIn("fluent_log.script_editor_load_failed", ids)
        diagnostic = next(
            item for item in diagnostics if item["id"] == "fluent_log.scriptgroup_statement_list_invalid_xml_node"
        )
        self.assertIn("directly under ScriptGroup/Objects", diagnostic["likely_workflow_defect"])

    def test_prunes_older_script_editor_failure_when_newer_runtime_session_exists(self):
        diagnostics = diagnose_fluent_log_text(
            "2026-07-09 16:31:13 ERROR VX_SCEDT_001_005 Failed to deserialize statements "
            "invalid XmlNodeType at DeserializeListElements Read1_ScriptGroup\n"
            "2026-07-09 17:11:02 WARNING Error while creating the device "
            "MoveAxisCommandScriptStatement.get_AxisDrive\n"
        )

        ids = {item["id"] for item in diagnostics}
        self.assertIn("fluent_log.move_axis_drive_unresolved", ids)
        self.assertNotIn("fluent_log.scriptgroup_statement_list_invalid_xml_node", ids)

    def test_maps_value_type_variable_default_failure(self):
        diagnostics = diagnose_fluent_log_text(
            "ErrorID: VX_ESHRD_001_009\n"
            "System.InvalidCastException: Null object cannot be converted to a value type.\n"
            "at Tecan.Core.VariableHandling.VariableContainer.Declare\n"
            "at Tecan.VisionX.Scripting.Engine.Script.DeclareScriptScopeVariable\n"
        )

        ids = {item["id"] for item in diagnostics}
        self.assertIn("fluent_log.invalid_variable_default", ids)

    def test_maps_missing_script_workspace_data_failure(self):
        diagnostics = diagnose_fluent_log_text(
            "Error message for Error ID: VX_ESHRD_001_009\n"
            "Application:\tFluentControl Application, Module: Script Editor\n"
            "Description:\tThe load operation for Script 'Full Export One Of Each Command Corpus' "
            "failed with Exception:\n"
            "Failed to recreate the script specific part of Worktable Unset Variable value for "
            "'workspaceName' while loading Script.\n"
            "Error in line 1 position 109. Expecting element 'VxWorkspaceData' from namespace "
            "'http://schemas.datacontract.org/2004/07/Tecan.VisionX.SharedGlobal'.. "
            "Encountered 'Element' with name 'VariableDeclarations', namespace ''.\n"
        )

        diagnostic = next(item for item in diagnostics if item["id"] == "fluent_log.script_workspace_data_missing")
        self.assertEqual(diagnostic["category"], "worktable_metadata")
        self.assertIn("before VariableDeclarations", diagnostic["suggested_fix"])

    def test_datastore_no_checksum_scan_status_is_not_checksum_failure(self):
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="498" TimeStamp="2026-07-01 21:48:33.586" '
            'Channel="Tecan/Fluent Control/Developer/DataStore" Type="GeneralMessage" Severity="Info" '
            'Message="Complete scan for 16030 entries took 1132.0006975 s. '
            'Settings: without schema validation / no checksum calculation here" />\n'
            '<Log MsgID="502" TimeStamp="2026-07-01 21:48:36.382" '
            'Channel="Tecan/Fluent Control/Developer/DataStore" Type="GeneralMessage" Severity="Info" '
            'Message="VX_DTAST_001_043: DataStore has been configured for SVN audit trail mode." />\n'
        )

        self.assertNotIn("fluent_log.checksum_recalculate", {item["id"] for item in diagnostics})

    def test_maps_move_axis_drive_unresolved_warning(self):
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="12705" TimeStamp="2026-07-09 12:35:20.332" '
            'Channel="Tecan/Fluent Control/Scripting/CommandTool" Type="GeneralMessage" '
            'Severity="Warning" Message="Error while creating the device "><Exception><![CDATA['
            "System.NullReferenceException: Object reference not set to an instance of an object.\n"
            "   at Tecan.Core.Scripting.Movement.MoveAxisCommandScriptStatement.get_AxisDrive()"
            "]]></Exception><ThreadId>198</ThreadId></Log>"
        )

        diagnostic = next(item for item in diagnostics if item["id"] == "fluent_log.move_axis_drive_unresolved")
        self.assertEqual(diagnostic["category"], "device_binding")
        self.assertIn("MoveAxis", diagnostic["suggested_fix"])
        self.assertNotIn("SUB_Spin_Tube", diagnostic["suggested_fix"])
        self.assertNotIn("SUB_CapBCScanHandeling_50mL", diagnostic["suggested_fix"])
        self.assertNotIn("Script1", diagnostic["suggested_fix"])

    def test_maps_unsupported_datastore_key_import_warning(self):
        diagnostics = diagnose_fluent_log_text(
            "Application: FluentControl Application, Module: Main\n"
            "Description: DataStoreKey '12' is not supported anymore. "
            "e80ad9f8-534b-41b7-96fb-c2c81cfc5c03 will be ignored for import.\n"
            "DataStoreKey '5' is not supported anymore. "
            "11111111-1234-aaaa-ffff-000000000004 will be ignored for import.\n"
        )

        diagnostic = next(
            item for item in diagnostics if item["id"] == "fluent_log.unsupported_datastore_key_import_ignored"
        )
        self.assertEqual(diagnostic["category"], "import")
        self.assertIn("dependencies_not_packaged", diagnostic["suggested_fix"])

    def test_diagnostics_prefer_newest_matching_records(self):
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="759" TimeStamp="2026-07-01 21:56:36.607" '
            'Channel="Tecan/Fluent Control/Scripting/CommandTool" Type="GeneralMessage" '
            'Severity="Warning" Message="Error while creating the device "><Exception><![CDATA['
            "System.NullReferenceException: Object reference not set to an instance of an object.\n"
            "   at Tecan.Core.Scripting.Movement.MoveAxisCommandScriptStatement.get_AxisDrive()"
            "]]></Exception><ThreadId>66</ThreadId></Log>\n"
            '<Log MsgID="12705" TimeStamp="2026-07-09 12:35:20.332" '
            'Channel="Tecan/Fluent Control/Scripting/CommandTool" Type="GeneralMessage" '
            'Severity="Warning" Message="Error while creating the device "><Exception><![CDATA['
            "System.NullReferenceException: Object reference not set to an instance of an object.\n"
            "   at Tecan.Core.Scripting.Movement.MoveAxisCommandScriptStatement.get_AxisDrive()"
            "]]></Exception><ThreadId>198</ThreadId></Log>"
        )

        diagnostic = next(item for item in diagnostics if item["id"] == "fluent_log.move_axis_drive_unresolved")
        self.assertEqual(diagnostic["records"][0]["timestamp"], "2026-07-09 12:35:20.332")
        self.assertIn("2026-07-09", diagnostic["evidence"][0])

    def test_new_runtime_cluster_prunes_stale_older_diagnostics(self):
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="11774" TimeStamp="2026-07-09 08:57:01.298" '
            'Channel="Tecan/Fluent Control/Scripting/Common Dialog" Type="GeneralMessage" '
            'Severity="Info" Message="ErrorID: VX_IMP_001_001, Description: The items with '
            'the following IDs are referenced by at least one of the imported components, but '
            'not part of the import file: 11111111-1234-aaaa-ffff-000000000004 (96 Well Flat)" />\n'
            '<Log MsgID="11900" TimeStamp="2026-07-09 09:04:04.506" '
            'Channel="Tecan/Fluent Control/Developer/Application Frame/ScriptEditor" '
            'Type="GeneralMessage" Severity="Error" '
            'Message="LoadWorker(Verification_Script1) got ScriptReadStatementsException: '
            'Failed to deserialize statements while loading Script."><Exception><![CDATA['
            "System.FormatException: Input string was not in a correct format.\n"
            "   at System.Number.ParseInt32(String s, NumberStyles style, NumberFormatInfo info)\n"
            "   at Microsoft.Xml.Serialization.GeneratedAssembly.XmlSerializationReaderWorktableStatementDataClass."
            "Read5_ConfigureDataLabwareDataModel(Boolean isNullable, Boolean checkType)"
            ']]></Exception></Log>\n'
            '<Log MsgID="12752" TimeStamp="2026-07-09 12:35:20.960" '
            'Channel="Tecan/Fluent Control/Worktable" Type="GeneralMessage" Severity="Warning" '
            'Message="WorktableVXDataStoreManager.Loading workspace delta got ArgumentNullException: '
            'Value cannot be null.&#xD;&#xA;Parameter name: deltaId"><Exception><![CDATA['
            "System.ArgumentNullException: Value cannot be null.\n"
            "Parameter name: deltaId\n"
            "   at Tecan.VisionX.Worktable.Core.WorkspaceApi.LoadWorkspaceDelta(String deltaId)"
            "]]></Exception></Log>\n"
        )

        ids = {item["id"] for item in diagnostics}
        self.assertEqual(ids, {"fluent_log.worktable_workspace_delta_missing"})

    def test_maps_known_runtime_error_families(self):
        cases = {
            "fluent_log.undefined_variable": "Runtime error: undefined variable platecount in labware expression",
            "fluent_log.prompt_timeout_range": "Close prompt after is below lower range 1-7200",
            "fluent_log.missing_subroutine": "Unable to load selected subroutine Demo\\SUB_Get_Fingers_v1.0",
            "fluent_log.missing_referenced_files": "Missing referenced file for labware object in imported worktable dependency",
            "fluent_log.checksum_recalculate": (
                "LoadData( 4 / 'Verification_v6') InvalidChecksumException: XML checksum error "
                "indicates unauthorized modification of Script with name \"Verification_v6\"."
            ),
            "fluent_log.zeia_import_failed": "ZEIA import failed in ExportImportArchive with invalid datastore payload",
            "fluent_log.adapter_rga_command": "Invalid adapter labware for RGA finger move command",
            "fluent_log.unknown_driver_command": (
                'Command "RGA1 TransferLabware" is unknown. Please check that the corresponding driver '
                "is available and configured properly."
            ),
            "fluent_log.gripper_fingers_not_mounted": (
                "Unable to start transfer because no gripper fingers are mounted. Please mount fingers to the arm."
            ),
            "fluent_log.if_else_branches_mismatched": "Mismatching If-Else branches - please check the script.",
            "fluent_log.vb_script_compile_failed": (
                "Unable to load and compile VB script 'C:\\Tecan\\Scripts\\CalculateVolume.vb'"
            ),
            "fluent_log.resolvex_a200_command_unknown": 'Command "ResolvexA200_Run" is unknown.',
            "fluent_log.invalid_labware_selection": "Select a valid labware.",
            "fluent_log.scanner_instance_binding": (
                "Unhandled exception in script command: USB:TECAN,FLUENT2405000993/CGA:1 "
                "is not associated with a scanner instance."
            ),
            "fluent_log.missing_worktable_labware": "Required labware FilterDWP is missing on worktable deck",
            "fluent_log.worktable_workspace_delta_missing": (
                "WorktableVXDataStoreManager.Loading workspace delta got ArgumentNullException: "
                "Value cannot be null. Parameter name: deltaId at WorkspaceApi.LoadWorkspaceDelta"
            ),
            "fluent_log.unsupported_datastore_key_import_ignored": (
                "DataStoreKey '9' is not supported anymore. "
                "4af53cca-0536-45f1-ba26-bf2b5ead68f2 will be ignored for import."
            ),
            "fluent_log.script_workspace_data_missing": (
                "VX_ESHRD_001_009 Expecting element 'VxWorkspaceData' but encountered "
                "VariableDeclarations while loading workspaceName."
            ),
            "fluent_log.scriptgroup_statement_list_invalid_xml_node": (
                "VX_SCEDT_001_005 ScriptReadStatementsException Failed to deserialize statements "
                "invalid XmlNodeType at DefaultSerializerImplementation.DeserializeListElements "
                "Read1_ScriptGroup"
            ),
        }
        for expected_id, text in cases.items():
            with self.subTest(expected_id=expected_id):
                diagnostics = diagnose_fluent_log_text(text)
                self.assertIn(expected_id, {item["id"] for item in diagnostics})

    def test_missing_referenced_file_diagnosis_covers_disabled_command_corpus_samples(self):
        diagnostics = diagnose_fluent_log_text(
            "Failed to open file C:\\Users\\Tecan\\Desktop\\cryoEM\\CalculateAspirateVolume.vb for reading"
        )

        diagnostic = next(item for item in diagnostics if item["id"] == "fluent_log.missing_referenced_files")
        self.assertIn("command corpus", diagnostic["likely_workflow_defect"])
        self.assertIn("every sample disabled", diagnostic["suggested_fix"])

    def test_maps_import_error_variants_from_ulf_and_audit_text(self):
        cases = {
            "fluent_log.checksum_recalculate": (
                "Audit: Import generated_project.zeia failed with ChecksumException while loading datastore object"
            ),
            "fluent_log.missing_subroutine": (
                "ULF: subroutine Demo\\SUB_Does_Not_Exist could not be found during imported method load"
            ),
            "fluent_log.missing_referenced_files": (
                "System.IO.FileNotFoundException: Could not find file 'C:\\Tecan\\Prompts\\verify.png'"
            ),
            "fluent_log.zeia_import_failed": (
                "VisionX Audit: ExportImportArchive rejected generated_project.zeia with VX_APPFR_016_005"
            ),
        }
        for expected_id, text in cases.items():
            with self.subTest(expected_id=expected_id):
                diagnostics = diagnose_fluent_log_text(text)
                self.assertIn(expected_id, {item["id"] for item in diagnostics})

    def test_rga_motion_telemetry_is_not_an_adapter_command_error(self):
        diagnostics = diagnose_fluent_log_text(
            '<Log MsgID="375557" TimeStamp="2026-07-06 16:14:51.544" '
            'Channel="Tecan/Fluent Control/Instrument/PathFinding/RGA 1" '
            'Type="GeneralMessage" Severity="Info" '
            'Message="Move is finished. Current position after move: X: 989.26 Y: 119.06 Z: 225.00" />\n'
            '<Log MsgID="376558" TimeStamp="2026-07-06 16:15:11.762" '
            'Channel="Tecan/Fluent Control/Instrument/RGA" Type="GeneralMessage" Severity="Info" '
            'Message="RGA,1;R,0;MoveCount=23526;MoveDistance=2259451(degree)" />\n'
        )

        self.assertNotIn("fluent_log.adapter_rga_command", {item["id"] for item in diagnostics})

    def test_diagnostics_convert_to_static_findings(self):
        diagnostics = diagnose_fluent_log_text("Runtime error: undefined variable platecount")

        findings = diagnostics_to_findings(diagnostics)

        self.assertEqual(findings[0]["id"], "fluent_log.undefined_variable")
        self.assertIn("likely_workflow_defect", findings[0]["details"])
        self.assertTrue(findings[0]["next_steps"])

    def test_build_report_and_cli_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "FluentControl.log"
            log.write_text(
                "2026-06-11 10:45:00 VX_SCEDT_001_005 failed to load script\n"
                "VariableDefinitionHelper namespace missing d3p1\n",
                encoding="utf-8",
            )

            report = build_fluent_log_report(log)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli_main(["parse-fluent-log", str(log), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(report["record_count"], 1)
        self.assertIn("fluent_log.variable_definition_namespace", stdout.getvalue())

    def test_build_latest_report_from_common_log_directory_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "LogFile 2026-06-16 09.36.55.000.ulf"
            log.write_text(
                'MCS connected to Logging Server '
                '<Log MsgID="1" TimeStamp="2026-06-16 09:35:55.000" '
                'Channel="Tecan/Fluent Control/Startup" Type="GeneralMessage" '
                'Severity="Info" Message="Startup complete" />'
                '<Log MsgID="3648" TimeStamp="2026-06-16 09:36:55.394" '
                'Channel="Tecan/Fluent Control/Developer/DataStore" '
                'Type="GeneralMessage" Severity="Error" '
                'Message="LoadData( 4 / &quot;Verification_v6&quot;) InvalidChecksumException: '
                'XML checksum error indicates unauthorized modification of Script with name '
                '&quot;Verification_v6&quot;." />\n',
                encoding="utf-8",
            )

            report = build_latest_fluent_log_report(
                locations=[(Path(tmp), "*.ulf")],
                since_hours=24,
                max_files=5,
                max_records=10,
            )

        self.assertEqual(report["file_count"], 1)
        self.assertEqual(report["record_count"], 1)
        self.assertIn("fluent_log.checksum_recalculate", {item["id"] for item in report["diagnostics"]})


if __name__ == "__main__":
    unittest.main()
