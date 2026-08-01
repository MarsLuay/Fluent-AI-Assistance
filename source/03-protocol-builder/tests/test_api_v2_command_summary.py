import unittest

from fluent_pipeline.api_v2.command_summary import (
    enrich_simulation_subroutine_traces,
    enrich_subroutine_load_review_record,
    subroutine_call_summary,
    subroutine_path_from_opaque_message,
)


class ApiV2CommandSummaryTests(unittest.TestCase):
    def test_subroutine_call_summary_from_ir_step(self):
        step = {
            "operation": "call_subroutine",
            "parameters": {
                "subroutine": "Demo\\SUB_Get_Fingers_v1.0",
                "execution_mode": "JoinSubroutine",
            },
        }
        label = subroutine_call_summary(step)
        self.assertIn("SUB_Get_Fingers_v1.0", label)
        self.assertIn("mode=JoinSubroutine", label)

    def test_enrich_subroutine_load_review_record_adds_call_label(self):
        ir = {
            "steps": [
                {
                    "index": 2,
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_Missing"},
                }
            ]
        }
        record = enrich_subroutine_load_review_record({"subroutine": "Demo\\SUB_Missing", "step_index": 2}, ir)
        self.assertIn("call_label", record)
        self.assertIn("SUB_Missing", record["call_label"])

    def test_subroutine_path_from_opaque_message(self):
        path = subroutine_path_from_opaque_message(
            "subroutine 'Demo\\SUB_Does_Not_Exist' not found in registry"
        )
        self.assertEqual(path, "Demo\\SUB_Does_Not_Exist")

    def test_enrich_simulation_subroutine_traces_uses_ir_when_available(self):
        data = {
            "opaque_events": [
                {
                    "step_index": 3,
                    "command_id": "SubRoutineStatement",
                    "message": "external subroutine body is not available to the simulator",
                }
            ]
        }
        ir = {
            "steps": [
                {
                    "index": 3,
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_Get_Fingers_v1.0"},
                }
            ]
        }
        enrich_simulation_subroutine_traces(data, ir)
        self.assertIn("call_label", data["opaque_events"][0])
        self.assertIn("SUB_Get_Fingers_v1.0", data["opaque_events"][0]["call_label"])


if __name__ == "__main__":
    unittest.main()
