import unittest

from fluent_pipeline.query_variable_audit import (
    audit_query_variables_for_workflow,
    build_query_variable_audit,
    expected_query_names_from_ir,
    expected_query_names_from_spec,
    live_query_names_from_fluent_report,
    normalize_query_variable_names,
)
from fluent_pipeline.request_spec import build_request_validation_diff, render_request_validation_diff_markdown


class QueryVariableAuditTests(unittest.TestCase):
    def test_normalize_query_variable_names_dedupes_and_sorts(self):
        names = normalize_query_variable_names(["TubeCount", "StartupVolume", "TubeCount", ""])
        self.assertEqual(names, ("StartupVolume", "TubeCount"))

    def test_expected_names_from_ir_query_steps_and_startup_flags(self):
        ir = {
            "steps": [
                {
                    "operation": "query_variable",
                    "parameters": {"variable": "StartupVolume", "prompt": "Confirm volume"},
                }
            ],
            "variables": [
                {"name": "TubeCount", "query_at_startup": True},
                {"name": "Bad-Name", "query_at_startup": True},
            ],
        }
        self.assertEqual(expected_query_names_from_ir(ir), ("StartupVolume", "TubeCount"))

    def test_expected_names_from_spec_simulation_values(self):
        spec = {
            "request": {"intent": "demo"},
            "source": {},
            "generation": {},
            "review": {},
            "verification_recipe": {
                "simulation_values": [
                    {"name": "PlateCount", "value": 1},
                    {"name": 'MountedFESfinger()<>"Eccentric[001]"', "value": 0},
                ]
            },
        }
        self.assertEqual(expected_query_names_from_spec(spec), ("PlateCount",))

    def test_build_audit_passes_when_live_matches_ir(self):
        ir = {
            "steps": [
                {
                    "operation": "query_variable",
                    "parameters": {"variable": "StartupVolume"},
                }
            ],
            "variables": [],
        }
        spec = {"request": {"intent": "demo"}, "source": {}, "generation": {}, "review": {}}
        audit = build_query_variable_audit(
            protocol_ir=ir,
            request_spec=spec,
            live_names=["StartupVolume"],
        )
        self.assertEqual(audit.status, "passed")
        self.assertEqual(audit.live_names, ("StartupVolume",))
        self.assertEqual(audit.missing_from_live, ())

    def test_build_audit_fails_when_live_missing_modeled_names(self):
        audit = build_query_variable_audit(
            protocol_ir={
                "steps": [{"operation": "query_variable", "parameters": {"variable": "TubeCount"}}],
                "variables": [],
            },
            request_spec={"request": {"intent": "demo"}, "source": {}, "generation": {}, "review": {}},
            live_names=[],
        )
        self.assertEqual(audit.status, "failed")
        self.assertEqual(audit.missing_from_live, ("TubeCount",))

    def test_offline_audit_needs_review_when_ir_models_queries(self):
        audit = build_query_variable_audit(
            protocol_ir={
                "steps": [{"operation": "query_variable", "parameters": {"variable": "StartupVolume"}}],
                "variables": [],
            },
            request_spec={"request": {"intent": "demo"}, "source": {}, "generation": {}, "review": {}},
            live_names=None,
        )
        self.assertEqual(audit.status, "needs-review")
        self.assertFalse(audit.runtime_available)

    def test_live_names_from_fluent_report_details(self):
        live = live_query_names_from_fluent_report(
            {
                "ok": True,
                "status": "passed",
                "details": {"query_variable_names": ["StartupVolume"]},
            }
        )
        self.assertEqual(live, ("StartupVolume",))

    def test_validation_diff_includes_query_audit_section(self):
        diff = build_request_validation_diff(
            request_spec={"request": {"intent": "demo"}, "source": {}, "generation": {}, "review": {}},
            protocol_ir={
                "protocol": {"name": "Demo"},
                "steps": [{"operation": "query_variable", "parameters": {"variable": "StartupVolume"}}],
                "variables": [],
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
                "details": {"query_variable_names": ["StartupVolume"]},
            },
        )
        markdown = render_request_validation_diff_markdown(diff)
        self.assertIn("query_variable_runtime_audit", markdown)
        self.assertIn("Query-at-startup variable audit", markdown)
        self.assertIn("`StartupVolume`", markdown)
        checks = {item["id"]: item for item in diff["checks"]}
        self.assertEqual(checks["query_variable_runtime_audit"]["status"], "passed")

    def test_workflow_audit_from_fluent_context_report(self):
        audit = audit_query_variables_for_workflow(
            protocol_ir={
                "steps": [
                    {"operation": "query_variable", "parameters": {"variable": "StartupVolume"}},
                ],
                "variables": [{"name": "TubeCount", "query_at_startup": True}],
            },
            request_spec={"request": {"intent": "demo"}, "source": {}, "generation": {}, "review": {}},
            fluent_report={
                "ok": True,
                "status": "passed",
                "details": {"query_variable_names": ["StartupVolume", "TubeCount"]},
            },
        )
        self.assertEqual(audit.status, "passed")
        self.assertEqual(audit.live_names, ("StartupVolume", "TubeCount"))


if __name__ == "__main__":
    unittest.main()
