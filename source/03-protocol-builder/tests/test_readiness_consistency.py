import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.readiness import (
    build_canonical_readiness,
    embed_readiness,
    readiness_status_from_readiness,
)
from fluent_pipeline.simulator_scene import write_simulator_handoff


class ReadinessConsistencyTests(unittest.TestCase):
    def test_canonical_readiness_matches_across_json_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation_report = {
                "validation_version": "test",
                "ready": True,
                "offline_validation": {
                    "status": "ready_to_import",
                    "summary": "All required offline readiness gates passed.",
                    "required_gate_count": 26,
                    "passed_count": 26,
                    "failed_count": 0,
                    "failing_gates": [],
                    "host_instrument_config_blocking": False,
                },
                "review_state": {
                    "status": "import_ready_needs_review",
                    "summary": "One gate still needs review.",
                    "needs_review_count": 1,
                    "gates": ["device_aliases_resolve"],
                },
                "fluentcontrol_load_diagnostic": {
                    "status": "load_failed",
                    "summary": "FluentControl import/load diagnostic reported a load failure.",
                    "requested": True,
                    "gate": "Gate 27",
                    "gate_present": True,
                },
                "gates": [
                    {"id": "simulation_passes", "status": "passed"},
                    {"id": "checksums_valid", "status": "passed"},
                    {"id": "generated_zeia_valid", "status": "passed"},
                    {"id": "fluent_context_check", "status": "failed"},
                ],
            }
            readiness = build_canonical_readiness(
                validation_report=validation_report,
                package_outputs=["generated_project.zeia", "generated_script.xscr"],
            )
            readiness_status = readiness_status_from_readiness(
                readiness,
                workflow_status="ready_to_import",
            )
            embed_readiness(
                validation_report,
                readiness=readiness,
                readiness_status=readiness_status,
            )

            ir_path = root / "protocol.ir.json"
            ir_path.write_text(
                json.dumps(
                    {
                        "ir_version": "tecan.protocol_ir.v1",
                        "worktable_name": "WT_Test",
                        "worktable_guid": "worktable-guid",
                        "groups": [],
                        "labware": [],
                    }
                ),
                encoding="utf-8",
            )
            xscr_path = root / "generated_script.xscr"
            xscr_path.write_text("<Root />", encoding="utf-8")
            out_dir = root / "handoff"
            write_simulator_handoff(
                out_dir,
                base="demo",
                protocol_name="Demo",
                protocol_ir_path=ir_path,
                xscr_path=xscr_path,
                validation_report=validation_report,
                workflow_status="ready_to_import",
                readiness_status=readiness_status,
                readiness=readiness,
                ready_to_import=True,
            )

            manifest_path = root / "generation_manifest.json"
            metadata_path = root / "metadata.json"
            validation_report_path = root / "validation_report.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "workflow_status": "ready_to_import",
                        "ready_to_import": True,
                        "readiness_status": readiness_status,
                        "readiness": readiness,
                    }
                ),
                encoding="utf-8",
            )
            metadata_path.write_text(
                json.dumps(
                    {
                        "bundle_schema_version": "tecan.ready_to_import.bundle.v1",
                        "ready_to_import": True,
                        "readiness_status": readiness_status,
                        "readiness": readiness,
                    }
                ),
                encoding="utf-8",
            )
            validation_report_path.write_text(json.dumps(validation_report), encoding="utf-8")

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            validation_json = json.loads(validation_report_path.read_text(encoding="utf-8"))
            sim_scene = json.loads((out_dir / "sim_scene.json").read_text(encoding="utf-8"))
            simulator_project = json.loads((out_dir / "simulator-project.json").read_text(encoding="utf-8"))

            for payload in (manifest, metadata, validation_json, sim_scene["generation"], simulator_project["generation"]):
                self.assertEqual(payload["readiness_status"], readiness_status)
                self.assertEqual(payload["readiness"], readiness)

            self.assertEqual(readiness_status, "import_ready_needs_review")
            self.assertEqual(readiness["fluentcontrol_load_diagnostic"]["status"], "failed")
            self.assertEqual(readiness["script_editor_load"]["status"], "failed")
            self.assertEqual(readiness["script_editor_load"]["evidence"], "gate_27")
            self.assertEqual(readiness["hardware_run"]["status"], "needs_review")
            for key in ("fluentcontrol_load_diagnostic", "script_editor_load", "hardware_run"):
                self.assertTrue(readiness[key]["next_action"])

    def test_live_handoff_statuses_close_without_changing_offline_readiness(self):
        base_report = {
            "offline_validation": {"status": "ready_to_import"},
            "review_state": {"status": "hardware_review_required"},
            "gates": [{"id": "simulation_passes", "status": "passed"}],
        }
        for raw_status, expected_status in (
            (None, "not_run"),
            ("load_clean", "passed"),
            ("load_failed", "failed"),
            ("ambiguous", "needs_review"),
        ):
            report = dict(base_report)
            if raw_status is not None:
                report["fluentcontrol_load_diagnostic"] = {"status": raw_status}
            readiness = build_canonical_readiness(
                validation_report=report,
                package_outputs=["generated_project.zeia"],
            )

            with self.subTest(raw_status=raw_status):
                self.assertEqual(readiness["offline_validation"]["status"], "ready_to_import")
                self.assertEqual(readiness["generated_zeia_import"]["status"], "ready_to_import")
                self.assertEqual(readiness["fluentcontrol_load_diagnostic"]["status"], expected_status)
                self.assertIn(
                    readiness["script_editor_load"]["status"],
                    {"passed", "failed", "needs_review", "not_run"},
                )
                self.assertIn(
                    readiness["hardware_run"]["status"],
                    {"passed", "failed", "needs_review", "not_run"},
                )
                self.assertTrue(readiness["fluentcontrol_load_diagnostic"]["next_action"])
                self.assertTrue(readiness["script_editor_load"]["next_action"])
                self.assertTrue(readiness["hardware_run"]["next_action"])

    def test_hardware_handoff_stays_unrun_when_offline_validation_is_blocked(self):
        readiness = build_canonical_readiness(
            validation_report={
                "offline_validation": {"status": "validated_not_ready"},
                "review_state": {"status": "validated_not_ready"},
                "gates": [],
            },
            package_outputs=[],
        )

        self.assertEqual(readiness["hardware_run"]["status"], "not_run")
        self.assertIn("offline", readiness["hardware_run"]["next_action"].casefold())

    def test_nonexecuted_gate_details_do_not_fake_a_script_editor_failure(self):
        for detail_status in ("unavailable", "skipped", "not_configured"):
            report = {
                "offline_validation": {"status": "ready_to_import"},
                "review_state": {"status": "hardware_review_required"},
                "fluentcontrol_load_diagnostic": {
                    "status": "load_failed",
                    "summary": "Compatibility shim did not provide a live result.",
                    "requested": True,
                    "gate": "Gate 27",
                    "gate_present": True,
                },
                "gates": [
                    {
                        "id": "fluent_context_check",
                        "status": "failed",
                        "summary": "Optional diagnostic was unavailable.",
                        "details": {
                            "status": detail_status,
                            "provider": "offline-compatibility-shim",
                            "method": "protocol",
                        },
                    }
                ],
            }
            readiness = build_canonical_readiness(
                validation_report=report,
                package_outputs=["generated_project.zeia"],
            )

            with self.subTest(detail_status=detail_status):
                self.assertEqual(readiness["fluentcontrol_load_diagnostic"]["status"], "not_run")
                self.assertEqual(readiness["script_editor_load"]["status"], "not_run")
                self.assertNotEqual(readiness["script_editor_load"]["status"], "failed")
                self.assertIn("Open the generated script", readiness["script_editor_load"]["next_action"])

        report["gates"][0]["details"]["diagnostics"] = ["Provider result requires review."]
        readiness = build_canonical_readiness(
            validation_report=report,
            package_outputs=["generated_project.zeia"],
        )
        self.assertEqual(readiness["fluentcontrol_load_diagnostic"]["status"], "needs_review")
        self.assertEqual(readiness["script_editor_load"]["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
