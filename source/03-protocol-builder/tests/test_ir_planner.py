import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
import fluent_pipeline.ir_planner as ir_planner
from fluent_pipeline.ir_planner import synthesize_seed_ir


class IrPlannerTests(unittest.TestCase):
    def test_first_field_uses_native_ascii_lower_with_unicode_fallback(self):
        fields = {
            "LabwareName": "first",
            "LABWAREname": "last",
            "Straße": "unicode",
        }
        names = ("labwarename", "STRAẞE", "STRASSE")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [ir_planner._first_field(fields, (name,)) for name in names]
        self.assertEqual(expected, ["last", "unicode", None])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                ir_planner,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = [ir_planner._first_field(fields, (name,)) for name in names]
                self.assertEqual(native_call.call_count, 12)
            native_call.assert_any_call("Straße")
            native_call.assert_any_call("STRAẞE")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(ir_planner, "_native_ascii_lower", return_value=None):
                forced_fallback = [
                    ir_planner._first_field(fields, (name,)) for name in names
                ]
        self.assertEqual(forced_fallback, expected)

    def test_operation_inference_uses_native_ascii_lower_with_unicode_fallback(self):
        cases = [
            ({"pattern_type": "  GRIPPER ", "name": "Ignored"}, {}, None),
            (
                {"pattern_type": "OTHER", "name": "ADD LABWARE"},
                {"command_family": "OTHER", "summary": "OTHER"},
                "add_labware",
            ),
            (
                {"pattern_type": "OTHER", "name": "ADD LABWARE"},
                {"command_family": "OTHER", "summary": "Straße"},
                "add_labware",
            ),
        ]
        fields = {}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(ir_planner, "registry_command_operation", return_value=None):
                expected = [
                    ir_planner._infer_operation(window, step, fields)
                    for window, step, _ in cases
                ]
        self.assertEqual(expected, [case[2] for case in cases])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(ir_planner, "registry_command_operation", return_value=None):
                with patch.object(
                    ir_planner,
                    "_native_ascii_lower",
                    wraps=native_acceleration.ascii_lower,
                ) as native_call:
                    actual = [
                        ir_planner._infer_operation(window, step, fields)
                        for window, step, _ in cases
                    ]
                    self.assertEqual(native_call.call_count, 7)
                native_call.assert_any_call("OTHER Straße")
                native_call.assert_any_call("OTHER ADD LABWARE other straße")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(ir_planner, "registry_command_operation", return_value=None):
                with patch.object(ir_planner, "_native_ascii_lower", return_value=None):
                    forced_fallback = [
                        ir_planner._infer_operation(window, step, fields)
                        for window, step, _ in cases
                    ]
        self.assertEqual(forced_fallback, expected)

    def test_worklist_suffix_detection_uses_native_lower_with_unicode_fallback(self):
        values = [" transfers.GWL ", "run.CsV", "data.TSV", "data.txt", "läufe.GWL"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [ir_planner._looks_like_worklist(value) for value in values]
        self.assertEqual(expected, [True, True, True, False, True])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                ir_planner,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = [ir_planner._looks_like_worklist(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
            native_call.assert_any_call("läufe.GWL")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(ir_planner, "_native_ascii_lower", return_value=None):
                forced_fallback = [ir_planner._looks_like_worklist(value) for value in values]
        self.assertEqual(forced_fallback, expected)

    def test_script_rack_types_are_dependencies_not_labware_labels(self):
        ir = {
            "protocol": {"name": "Rack type dependency test"},
            "source": {},
            "dependencies": [],
            "labware": [],
            "liquid_classes": [],
            "worklists": [],
            "steps": [],
        }
        selected_scripts = [
            {
                "name": "SourceScript",
                "dependencies": {
                    "labware_names": ["FilterDWP[platecount]"],
                    "rack_types": ["24 Filter Plate", "Adapter A200"],
                },
            }
        ]

        synthesize_seed_ir(ir, selected_scripts=selected_scripts, pattern_windows=[])

        labware_labels = {item["label"] for item in ir["labware"]}
        rack_type_dependencies = {
            item["name"]
            for item in ir["dependencies"]
            if item.get("kind") == "rack_type"
        }

        self.assertEqual(labware_labels, {"FilterDWP[platecount]"})
        self.assertEqual(rack_type_dependencies, {"24 Filter Plate", "Adapter A200"})

    def test_incomplete_pattern_step_infers_selected_source_fields(self):
        ir = {
            "protocol": {"name": "Inferred pattern test"},
            "source": {},
            "dependencies": [],
            "labware": [],
            "liquid_classes": [],
            "worklists": [],
            "steps": [],
        }
        selected_scripts = [
            {
                "name": "Synthetic source",
                "dependencies": {
                    "labware_names": ["Synthetic source plate"],
                    "liquid_classes": ["Synthetic liquid class"],
                },
            }
        ]
        windows = [
            {
                "id": 1,
                "name": "Synthetic aspirate",
                "steps": [
                    {
                        "command_index": 1,
                        "command_name": "Aspirate",
                        "summary": "Aspirate the inferred draft volume",
                        "fields": {},
                    }
                ],
            }
        ]

        report = synthesize_seed_ir(
            ir,
            selected_scripts=selected_scripts,
            pattern_windows=windows,
        )

        aspirate = next(step for step in ir["steps"] if step["operation"] == "aspirate")
        self.assertEqual(aspirate["target_labware"], "Synthetic source plate")
        self.assertEqual(aspirate["liquid_class"], "Synthetic liquid class")
        self.assertEqual(aspirate["volume_ul"], 1)
        self.assertEqual(len(aspirate["parameters"]["inference"]), 3)
        self.assertEqual(report["inferred_field_count"], 3)
        self.assertNotIn("incomplete_pattern_step", {item["kind"] for item in report["warnings"]})


if __name__ == "__main__":
    unittest.main()
