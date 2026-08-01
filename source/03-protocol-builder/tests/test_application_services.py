import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock

from fluent_pipeline.application_services import (
    BundleVerificationRequest,
    GenerationResult,
    LogAnalysisRequest,
    ProjectImportRequest,
    ProjectInspectionRequest,
    RepairApplyRequest,
    RepairPlanRequest,
    RequestSpecCreateRequest,
    RequestSpecValidationRequest,
    analyze_logs,
    apply_repair,
    create_request_spec,
    generate_protocol,
    import_project,
    inspect_project,
    plan_repair,
    validate_request_spec,
    verify_bundle,
)
from fluent_pipeline.generation_options import GenerationOptions
from fluent_pipeline.generation_workflow import ApprovalSet, GenerationRequest
from fluent_pipeline.repair import RepairAction, RepairPlan
from fluent_pipeline.spec_lint import LintResult


class ApplicationServicesTests(unittest.TestCase):
    def test_generate_protocol_wraps_workflow_request(self):
        request = GenerationRequest(
            intent="Generate a script",
            output_directory=Path("build/output"),
            options=GenerationOptions(simulate=False, compile_xscr=False),
            approvals=ApprovalSet(),
        )
        manifest = {"workflow_status": "scaffold_not_validated"}

        with mock.patch(
            "fluent_pipeline.application_services.run_generation_workflow",
            return_value=manifest,
        ) as workflow:
            result = generate_protocol(request)

        self.assertEqual(result, GenerationResult(request=request, manifest=manifest))
        workflow.assert_called_once_with(request)

    def test_import_project_can_activate_context(self):
        context = SimpleNamespace(name="demo", root=Path("projects/demo"), manifest={"scripts": []})

        with mock.patch(
            "fluent_pipeline.application_services.import_project_context",
            return_value=context,
        ) as import_context, mock.patch(
            "fluent_pipeline.application_services.set_active_project",
            return_value=context,
        ) as activate:
            result = import_project(
                ProjectImportRequest(
                    archive=Path("demo.zeia"),
                    name="demo",
                    force=True,
                    snapshot_archives=(Path("snap.zip"),),
                    activate=True,
                )
            )

        import_context.assert_called_once_with(
            Path("demo.zeia"),
            name="demo",
            force=True,
            snapshot_archives=[Path("snap.zip")],
        )
        activate.assert_called_once_with("demo")
        self.assertEqual(result.context, context)
        self.assertEqual(result.active_context_name, "demo")

    def test_inspect_project_returns_existing_report_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "project_report.md"
            report.write_text("# Demo\n", encoding="utf-8")
            context = SimpleNamespace(name="demo", root=root, manifest={"scripts": []})

            with mock.patch(
                "fluent_pipeline.application_services.load_project",
                return_value=context,
            ):
                result = inspect_project(ProjectInspectionRequest(context_name="demo"))

        self.assertEqual(result.context, context)
        self.assertEqual(result.report_path, report)

    def test_create_request_spec_writes_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "request.spec.yaml"
            result = create_request_spec(
                RequestSpecCreateRequest(
                    intent="Generate a script",
                    output_path=output,
                    generation_options=GenerationOptions(simulate=False, compile_xscr=False),
                )
            )
            self.assertEqual(result.output_path, output)
            self.assertTrue(output.exists())
            self.assertIn("request:", output.read_text(encoding="utf-8"))

    def test_validate_request_spec_wraps_lint_result(self):
        lint_result = LintResult()
        lint_result.add("warning", "request.intent", "Check the intent")

        with mock.patch(
            "fluent_pipeline.application_services.lint_request_spec_file",
            return_value=lint_result,
        ):
            result = validate_request_spec(RequestSpecValidationRequest(spec_path=Path("request.spec.yaml")))

        self.assertEqual(result.result, lint_result)

    def test_plan_and_apply_repair_share_service_path(self):
        context = SimpleNamespace(name="demo")
        action = RepairAction(kind="fix", status="ready", summary="Applied a fix", line=10)
        plan = RepairPlan(
            draft_path=Path("draft.py"),
            context_name="demo",
            simulation_json_path=Path("simulation.json"),
            actions=[action],
        )

        with mock.patch(
            "fluent_pipeline.application_services.load_project",
            return_value=context,
        ), mock.patch(
            "fluent_pipeline.application_services.build_repair_plan",
            return_value=plan,
        ) as build_plan, mock.patch(
            "fluent_pipeline.application_services.apply_repair_plan",
            return_value=[action],
        ) as apply_plan:
            plan_result = plan_repair(
                RepairPlanRequest(
                    draft_path=Path("draft.py"),
                    context_name="demo",
                    simulation_json_path=Path("simulation.json"),
                )
            )
            apply_result = apply_repair(
                RepairApplyRequest(
                    draft_path=Path("draft.py"),
                    output_path=Path("repaired.py"),
                    context_name="demo",
                    simulation_json_path=Path("simulation.json"),
                    apply_modeling=True,
                )
            )

        self.assertEqual(plan_result.plan, plan)
        self.assertEqual(apply_result.plan, plan)
        self.assertEqual(apply_result.applied_actions, (action,))
        self.assertEqual(build_plan.call_count, 2)
        apply_plan.assert_called_once_with(plan, Path("repaired.py"), apply_modeling=True)

    def test_verify_bundle_routes_to_validator(self):
        report = {"status": "passed"}
        request = BundleVerificationRequest(
            compiled_xscr=Path("compiled.xscr"),
            draft_path=Path("draft.py"),
            source_projects=(Path("project.zeia"),),
            source_scripts=(Path("source.xscr"),),
            validation_context={"traceability": {"request": "test"}},
        )

        with mock.patch(
            "fluent_pipeline.application_services.validate_ready_to_import",
            return_value=report,
        ) as validate:
            result = verify_bundle(request)

        validate.assert_called_once()
        self.assertEqual(result.report, report)

    def test_verify_bundle_writes_optional_artifacts(self):
        report = {"ready": True, "gates": [], "offline_validation": {"status": "ready_to_import"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = BundleVerificationRequest(
                compiled_xscr=Path("compiled.xscr"),
                report_path=root / "ready_validation.md",
                json_path=root / "ready_validation.json",
            )
            with mock.patch(
                "fluent_pipeline.application_services.validate_ready_to_import",
                return_value=report,
            ):
                result = verify_bundle(request)
            self.assertTrue(result.report_path.exists())
            self.assertTrue(result.json_path.exists())

    def test_analyze_logs_supports_explicit_and_latest_modes(self):
        explicit_report = {"source": "explicit"}
        latest_report = {"source": "latest"}

        with mock.patch(
            "fluent_pipeline.application_services.build_fluent_log_report",
            return_value=explicit_report,
        ) as explicit, mock.patch(
            "fluent_pipeline.application_services.build_latest_fluent_log_report",
            return_value=latest_report,
        ) as latest:
            explicit_result = analyze_logs(LogAnalysisRequest(log_path=Path("test.log")))
            latest_result = analyze_logs(LogAnalysisRequest(latest=True, since_hours=12.0, max_files=2, max_records=5))

        explicit.assert_called_once_with(Path("test.log"), audit_paths=(), xscr_paths=())
        latest.assert_called_once_with(since_hours=12.0, max_files=2, max_records=5)
        self.assertEqual(explicit_result.report, explicit_report)
        self.assertEqual(latest_result.report, latest_report)

    def test_analyze_logs_writes_optional_artifacts(self):
        report = {"diagnostic_count": 1, "record_count": 2, "diagnostics": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "fluent_pipeline.application_services.build_fluent_log_report",
                return_value=report,
            ):
                result = analyze_logs(
                    LogAnalysisRequest(
                        log_path=Path("test.log"),
                        report_path=root / "fluent_log_report.md",
                        json_path=root / "fluent_log_report.json",
                    )
                )
            self.assertTrue(result.report_path.exists())
            self.assertTrue(result.json_path.exists())

    def test_analyze_logs_requires_a_mode(self):
        with self.assertRaisesRegex(ValueError, "latest=True or log_path"):
            analyze_logs(LogAnalysisRequest())


if __name__ == "__main__":
    unittest.main()
