import tempfile
import unittest
from fluent_pipeline import xml_compat as ET
from pathlib import Path

from fluent_pipeline.request_spec import build_request_validation_diff, render_request_validation_diff_markdown
from fluent_pipeline.runtime_variable_audit import (
    audit_runtime_variables_for_workflow,
    build_runtime_variable_audit,
    expected_variable_names_from_ir,
    expected_variable_names_from_xscr,
    live_variable_names_from_fluent_report,
    normalize_variable_names,
    render_fluent_variables_cli_output,
)


class RuntimeVariableAuditTests(unittest.TestCase):
    def test_normalize_variable_names_dedupes_and_sorts(self):
        names = normalize_variable_names(["PlateCount", "TubeCount", "PlateCount", ""])
        self.assertEqual(names, ("PlateCount", "TubeCount"))

    def test_expected_names_from_ir_variables_and_source(self):
        ir = {
            "variables": [{"name": "PlateCount"}],
            "source": {
                "selected_source_scripts": [
                    {"startup_variables": [{"name": "OperatorMode"}]},
                ]
            },
        }
        self.assertEqual(expected_variable_names_from_ir(ir), ("OperatorMode", "PlateCount"))

    def test_expected_names_from_xscr_variable_definitions(self):
        root = ET.Element("VxData")
        helper = ET.SubElement(root, "VariableDefinitionHelper")
        ET.SubElement(helper, "Name").text = "StartupVolume"
        ET.SubElement(helper, "TypeName").text = "Double"
        ET.SubElement(helper, "QueryOnStartup").text = "false"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "method.xscr"
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            self.assertEqual(expected_variable_names_from_xscr(path), ("StartupVolume",))

    def test_build_audit_passes_when_live_matches_offline(self):
        audit = build_runtime_variable_audit(
            protocol_ir={"variables": [{"name": "PlateCount"}], "source": {}},
            live_names=["PlateCount"],
            live_query_names=["PlateCount"],
        )
        self.assertEqual(audit.status, "passed")
        self.assertEqual(audit.live_names, ("PlateCount",))

    def test_build_audit_fails_when_live_missing_offline_names(self):
        audit = build_runtime_variable_audit(
            protocol_ir={"variables": [{"name": "PlateCount"}], "source": {}},
            live_names=[],
            live_query_names=[],
        )
        self.assertEqual(audit.status, "failed")
        self.assertEqual(audit.missing_from_live, ("PlateCount",))

    def test_live_names_from_fluent_report_details(self):
        live = live_variable_names_from_fluent_report(
            {
                "ok": True,
                "status": "passed",
                "details": {"variable_names": ["PlateCount"]},
            }
        )
        self.assertEqual(live, ("PlateCount",))

    def test_validation_diff_includes_runtime_variable_section(self):
        diff = build_request_validation_diff(
            request_spec={"request": {"intent": "demo"}, "source": {}, "generation": {}, "review": {}},
            protocol_ir={
                "protocol": {"name": "Demo"},
                "variables": [{"name": "PlateCount"}],
                "source": {},
            },
            request_spec_path=None,
            protocol_ir_path=None,
            generated_files={},
            worktable_diff=None,
            validation_report=None,
            fluent_report={
                "ok": True,
                "status": "passed",
                "details": {"variable_names": ["PlateCount"], "query_variable_names": ["PlateCount"]},
            },
        )
        markdown = render_request_validation_diff_markdown(diff)
        self.assertIn("runtime_variable_audit", markdown)
        self.assertIn("FluentControl variable inventory (GetVariableNames)", markdown)
        checks = {item["id"]: item for item in diff["checks"]}
        self.assertEqual(checks["runtime_variable_audit"]["status"], "passed")

    def test_prepare_report_variable_capture_via_audit(self):
        report = {
            "ok": True,
            "status": "passed",
            "details": {
                "variable_names": ["PlateCount"],
                "query_variable_names": ["PlateCount"],
            },
        }
        audit = audit_runtime_variables_for_workflow(
            protocol_ir={"variables": [{"name": "PlateCount"}], "source": {}},
            fluent_report=report,
        )
        self.assertEqual(audit.status, "passed")
        self.assertEqual(audit.live_names, ("PlateCount",))

    def test_fluent_variables_cli_output_contains_issue_id(self):
        audit = build_runtime_variable_audit(
            protocol_ir={"variables": [{"name": "PlateCount"}], "source": {}},
            live_names=["PlateCount"],
        ).as_dict()
        output = render_fluent_variables_cli_output(audit)
        self.assertIn("api-v2-027", output)
        self.assertIn("GetVariableNames", output)


if __name__ == "__main__":
    unittest.main()
