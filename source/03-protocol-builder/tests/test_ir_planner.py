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


if __name__ == "__main__":
    unittest.main()
