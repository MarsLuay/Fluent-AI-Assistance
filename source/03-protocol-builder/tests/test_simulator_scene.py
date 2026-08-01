import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.simulator_scene import (
    SIM_SCENE_KIND,
    SIMULATOR_PROJECT_KIND,
    write_simulator_handoff,
)


class SimulatorSceneTests(unittest.TestCase):
    def test_write_simulator_handoff_emits_scene_and_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "build"
            ir_path = root / "protocol.ir.json"
            ir_path.write_text(
                json.dumps(
                    {
                        "ir_version": "tecan.protocol_ir.v1",
                        "worktable_name": "Demo_WT_Test",
                        "worktable_guid": "aaaaaaaa-bbbb-4ccc-8ddd-222222222222",
                        "groups": [
                            {
                                "name": "Arm verification",
                                "steps": [
                                    {
                                        "id": "step_001",
                                        "operation": "prompt_user",
                                        "command_id": "UserPrompt",
                                        "parameters": {"prompt": "Confirm the RGA fingers are parallel."},
                                    }
                                ],
                            }
                        ],
                        "labware": [],
                    }
                ),
                encoding="utf-8",
            )
            xscr_path = root / "generated_script.xscr"
            xscr_path.write_text("<Root />", encoding="utf-8")
            validation_diff = {
                "status": "passed",
                "checks": [
                    {
                        "id": "ready_validation",
                        "status": "passed",
                        "details": {
                            "gates": [
                                {
                                    "id": "xscr_compiles",
                                    "gate": "Gate 9",
                                    "name": ".xscr compiles",
                                    "status": "passed",
                                    "summary": "Compile succeeded.",
                                }
                            ]
                        },
                    }
                ],
            }
            validation_diff_path = root / "validation_diff.json"
            validation_diff_path.write_text(json.dumps(validation_diff), encoding="utf-8")
            request_spec = {
                "verification_recipe": {
                    "groups": [
                        {
                            "name": "Operator setup",
                            "steps": [{"prompt": "Confirm the A200 is connected."}],
                        }
                    ],
                },
                "acceptance": {"required_checks": ["Confirm the A200 is connected."]},
            }
            readiness = {
                "offline_validation": {"status": "ready_to_import", "summary": "offline ready"},
                "review_state": {"status": "hardware_review_required", "summary": "hardware review required"},
                "fluentcontrol_load_diagnostic": {"status": "load_failed", "summary": "load failed"},
                "generated_zeia_import": {"status": "ready_to_import", "summary": "archive ready"},
                "script_editor_load": {"status": "load_failed", "summary": "load failed"},
                "simulation": {"status": "passed", "summary": "simulation passed"},
                "hardware_run": {"status": "hardware_review_required", "summary": "hardware review required"},
            }

            paths = write_simulator_handoff(
                out_dir,
                base="demo_verification",
                protocol_name="Demo Verification",
                protocol_ir_path=ir_path,
                xscr_path=xscr_path,
                request_spec=request_spec,
                validation_diff_json_path=validation_diff_path,
                validation_diff=validation_diff,
                workflow_status="scaffold_not_validated",
                readiness_status="load_failed",
                readiness=readiness,
                ready_to_import=False,
            )

            self.assertTrue(paths["sim_scene"].exists())
            self.assertTrue(paths["simulator_project"].exists())

            scene = json.loads(paths["sim_scene"].read_text(encoding="utf-8"))
            project = json.loads(paths["simulator_project"].read_text(encoding="utf-8"))

            self.assertEqual(scene["app"], "tecan-protocol-simulator")
            self.assertEqual(scene["kind"], SIM_SCENE_KIND)
            self.assertEqual(project["kind"], SIMULATOR_PROJECT_KIND)
            self.assertEqual(scene["generation"]["worktable_name"], "Demo_WT_Test")
            self.assertTrue(scene["generation"]["verification_script"])
            self.assertEqual(
                scene["generation"]["verification_steps"][0]["prompt"],
                "Confirm the A200 is connected.",
            )
            self.assertEqual(scene["generation"]["validation_gates"][0]["status"], "passed")
            self.assertEqual(scene["generation"]["readiness_status"], "load_failed")
            self.assertEqual(scene["generation"]["readiness"], readiness)
            embedded_roles = {artifact["role"] for artifact in project["artifacts"]}
            self.assertIn("protocol_ir", embedded_roles)
            self.assertIn("xscr", embedded_roles)
            protocol_ir_artifact = next(
                artifact for artifact in project["artifacts"] if artifact["role"] == "protocol_ir"
            )
            self.assertTrue(protocol_ir_artifact["embedded"])
            self.assertIn("prompt_user", protocol_ir_artifact["text"])


if __name__ == "__main__":
    unittest.main()
