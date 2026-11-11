import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fluent_pipeline.compiled_xscr_finalizer as finalizer
from tecan_common import native_acceleration


class CompiledXscrFinalizerTests(unittest.TestCase):
    def test_first_xml_lookups_use_native_selectors_and_fallback(self):
        root = finalizer.ET.fromstring(
            '<Root xmlns:x="urn:test"><x:Name> </x:Name><Name>later</Name>'
            "<x:Direct>first</x:Direct><Direct>second</Direct></Root>"
        )
        with mock.patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_text = finalizer._first_xml_text(root, "Name")
            expected_child = finalizer._first_xml_child(root, "Direct")
        with mock.patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with mock.patch.object(
                finalizer,
                "_native_xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as first_call, mock.patch.object(
                finalizer,
                "_native_xml_first_direct_child_by_name",
                wraps=native_acceleration.xml_first_direct_child_by_name,
            ) as direct_call:
                actual_text = finalizer._first_xml_text(root, "Name")
                actual_child = finalizer._first_xml_child(root, "Direct")
                self.assertEqual(first_call.call_count, 1)
                self.assertEqual(direct_call.call_count, 1)
        self.assertEqual(actual_text, expected_text)
        self.assertEqual(actual_text, "")
        self.assertIs(actual_child, expected_child)
        self.assertIs(actual_child, root[2])

        class StringTag(str):
            pass

        custom_root = finalizer.ET.Element("Root")
        custom_name = finalizer.ET.SubElement(custom_root, StringTag("Name"))
        custom_name.text = "Custom"
        custom_child = finalizer.ET.SubElement(custom_root, StringTag("Direct"))
        with mock.patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_text = finalizer._first_xml_text(custom_root, "Name")
            fallback_child = finalizer._first_xml_child(custom_root, "Direct")
        with mock.patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_first_element_by_name(
                    custom_root, "Name", finalizer.ET.Element
                )
            )
            self.assertIsNone(
                native_acceleration.xml_first_direct_child_by_name(
                    custom_root, "Direct", finalizer.ET.Element
                )
            )
            actual_fallback_text = finalizer._first_xml_text(custom_root, "Name")
            actual_fallback_child = finalizer._first_xml_child(custom_root, "Direct")
        self.assertEqual(actual_fallback_text, fallback_text)
        self.assertEqual(actual_fallback_text, "Custom")
        self.assertIs(actual_fallback_child, fallback_child)
        self.assertIs(actual_fallback_child, custom_child)

    def test_aligns_workspace_base_name_to_worktable_reference(self):
        xscr = """<VxData><Payload>
<Reference><Guid>worktable-guid</Guid><TypeId>WorktableWorkspace</TypeId><ObjectName>WT</ObjectName></Reference>
<PayloadData><Script><Properties>
<VxWorkspaceData><BaseWorkspaceName>old-guid</BaseWorkspaceName><WorkspaceDeltas>
<string>&lt;VxWorkspaceDelta&gt;&lt;Identifier&gt;delta-123&lt;/Identifier&gt;&lt;/VxWorkspaceDelta&gt;</string>
</WorkspaceDeltas></VxWorkspaceData>
</Properties><Commands><RUPWorktableStatement /></Commands></Script></PayloadData>
</Payload></VxData>"""
        with tempfile.TemporaryDirectory() as tmp:
            xscr_path = Path(tmp) / "compiled.xscr"
            xscr_path.write_text(xscr, encoding="utf-8")

            fixup = finalizer._align_workspace_base_name_to_reference(xscr_path)

            text = xscr_path.read_text(encoding="utf-8")
        self.assertEqual(fixup["from"], "old-guid")
        self.assertEqual(fixup["to"], "worktable-guid")
        self.assertIn("<BaseWorkspaceName>worktable-guid</BaseWorkspaceName>", text)

    def test_workspace_metadata_transplant_prefers_matching_worktable_base(self):
        target = """<VxData><Payload>
<Reference><Guid>worktable-guid</Guid><TypeId>WorktableWorkspace</TypeId><ObjectName>WT</ObjectName></Reference>
<PayloadData><Script><Properties>
<VxWorkspaceData><BaseWorkspaceName>placeholder</BaseWorkspaceName><WorkspaceDeltas /></VxWorkspaceData>
</Properties><Commands><RUPWorktableStatement /></Commands></Script></PayloadData>
</Payload></VxData>"""
        stale_source = """<VxData><Payload><PayloadData><Script><Properties>
<VxWorkspaceData><BaseWorkspaceName>stale-guid</BaseWorkspaceName><WorkspaceDeltas>
<string>&lt;VxWorkspaceDelta&gt;&lt;Identifier&gt;stale-delta&lt;/Identifier&gt;&lt;/VxWorkspaceDelta&gt;</string>
</WorkspaceDeltas></VxWorkspaceData>
</Properties></Script></PayloadData></Payload></VxData>"""
        matching_source = """<VxData><Payload><PayloadData><Script><Properties>
<VxWorkspaceData><BaseWorkspaceName>worktable-guid</BaseWorkspaceName><WorkspaceDeltas>
<string>&lt;VxWorkspaceDelta&gt;&lt;Identifier&gt;matching-delta&lt;/Identifier&gt;&lt;/VxWorkspaceDelta&gt;</string>
</WorkspaceDeltas></VxWorkspaceData>
</Properties></Script></PayloadData></Payload></VxData>"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_path = root / "compiled.xscr"
            stale_path = root / "stale.xscr"
            matching_path = root / "matching.xscr"
            target_path.write_text(target, encoding="utf-8")
            stale_path.write_text(stale_source, encoding="utf-8")
            matching_path.write_text(matching_source, encoding="utf-8")

            fixup = finalizer._copy_source_workspace_data(target_path, [stale_path, matching_path])

            text = target_path.read_text(encoding="utf-8")
        self.assertEqual(fixup["base_workspace"], "worktable-guid")
        self.assertEqual(fixup["delta_identifier"], "matching-delta")
        self.assertEqual(fixup["matched_worktable_reference"], "True")
        self.assertIn("<BaseWorkspaceName>worktable-guid</BaseWorkspaceName>", text)
        self.assertIn("matching-delta", text)
        self.assertNotIn("stale-delta", text)

    def test_resolved_source_scripts_rehydrate_context_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.xscr"
            source.write_text("<VxData />", encoding="utf-8")
            stale = root / "missing.xscr"
            record = {
                "resolved_path": str(stale),
                "context_root": str(root),
                "entry": "source.xscr",
            }
            resolved = finalizer._resolved_source_scripts([], {"source": {"selected_source_scripts": [record]}})
        self.assertEqual(resolved, [source.resolve()])

    def test_resolved_source_scripts_prefer_request_source_over_baseline(self):
        from fluent_pipeline.project_context import filter_generation_source_script_records

        records = [
            {"object_name": "Verification_Script1", "resolved_path": __file__},
            {"object_name": "Demo_Script_2_50mL_v3.2", "resolved_path": __file__},
        ]
        protocol_ir = {
            "protocol": {"name": "Verification_Script1"},
            "source": {"source_scripts": ["Demo_Script_2_50mL_v3.2"]},
        }
        filtered = filter_generation_source_script_records(records, protocol_ir)
        self.assertEqual([item["object_name"] for item in filtered], ["Demo_Script_2_50mL_v3.2"])

    def test_finalizer_inserts_checksum_and_records_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr_path = Path(tmp) / "compiled.xscr"
            xscr_path.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Demo</ObjectName>
    <PayloadData>
      <Script>
        <Commands />
      </Script>
    </PayloadData>
  </Payload>
</VxData>
""",
                encoding="utf-8",
            )
            inventory = {
                "command_validation": {"failure_count": 0, "failures": []},
                "generic_command_validation": {"failure_count": 0, "failures": []},
                "command_ids": [],
                "unsupported_commands": [],
                "fluentcontrol_findings": [],
                "error": None,
            }
            protocol_ir = {"protocol": {"name": "Demo"}, "steps": []}

            with mock.patch.object(finalizer, "_inspect_compiled_xscr", return_value=({"steps": []}, "", inventory)), mock.patch.object(
                finalizer, "_compiled_xsi_type_namespace_findings", return_value=[]
            ), mock.patch.object(
                finalizer, "validate_xscr_variable_declarations", return_value=[]
            ), mock.patch.object(
                finalizer, "ensure_compiled_prompt_media_references", return_value=[]
            ), mock.patch.object(
                finalizer, "ensure_script_file_references", return_value=[]
            ), mock.patch.object(
                finalizer, "validate_compiled_subroutine_references", return_value=[]
            ):
                report = finalizer.finalize_compiled_xscr(
                    xscr_path,
                    protocol_ir,
                    None,
                    None,
                    {"source_ir_origin": "unit_test"},
                )

            self.assertTrue(report.ok)
            self.assertEqual(report.checksum_before, "absent")
            self.assertEqual(report.checksum_after, "valid")
            self.assertTrue(report.roundtrip.get("matched"))
            self.assertIn("<Checksum>", xscr_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["id"] for item in report.changes],
                ["checksum_element_insertion", "checksum_recompute"],
            )

    def test_source_inherited_command_findings_do_not_hide_new_failures(self):
        compiled = {
            "failure_count": 2,
            "failures": [
                {
                    "command_index": 12,
                    "command_type": "RUPWorktableStatement",
                    "reason": "variable_index",
                    "message": "generated wording",
                    "labware_name": "Tube[count]",
                },
                {
                    "command_index": 13,
                    "command_type": "UserPrompt",
                    "reason": "empty_prompt",
                    "message": "new generated prompt error",
                },
            ],
        }
        baseline = [
            {
                "command_index": -1,
                "command_type": "RUPWorktableStatement",
                "reason": "variable_index",
                "message": "source wording",
                "labware_name": "Tube[count]",
            }
        ]

        result, inherited = finalizer._subtract_inherited_validation_failures(compiled, baseline)

        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["inherited_failure_count"], 1)
        self.assertEqual(len(inherited), 1)
        self.assertEqual(result["failures"][0]["reason"], "empty_prompt")

