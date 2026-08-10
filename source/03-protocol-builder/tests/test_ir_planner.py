import unittest

from fluent_pipeline.ir_planner import synthesize_seed_ir


class IrPlannerTests(unittest.TestCase):
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
