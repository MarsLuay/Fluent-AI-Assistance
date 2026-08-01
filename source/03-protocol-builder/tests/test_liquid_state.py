import unittest

from fluent_pipeline.liquid_state import render_liquid_state_markdown, validate_liquid_state


class LiquidStateTests(unittest.TestCase):
    def test_tracks_transfer_before_and_after_volumes(self):
        report = validate_liquid_state(_transfer_ir())

        self.assertEqual(report["status"], "passed")
        aspirate = next(item for item in report["transfers"] if item["operation"] == "aspirate")
        dispense = next(item for item in report["transfers"] if item["operation"] == "dispense")
        self.assertEqual(aspirate["labware"], "SourcePlate")
        self.assertEqual(aspirate["well"], "A1")
        self.assertEqual(aspirate["before_ul"], 50)
        self.assertEqual(aspirate["after_ul"], 30)
        self.assertEqual(dispense["labware"], "DestPlate")
        self.assertEqual(dispense["before_ul"], 0)
        self.assertEqual(dispense["after_ul"], 20)
        self.assertIn("destination_assumed_empty", {item["id"] for item in report["assumptions"]})

        markdown = render_liquid_state_markdown(report)
        self.assertIn("# Liquid State Validation", markdown)
        self.assertIn("Result: `passed`", markdown)

    def test_fails_when_aspirate_would_go_below_dead_volume(self):
        ir = _transfer_ir(source_volume=10, transfer_volume=9, dead_volume=2)
        report = validate_liquid_state(ir)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failures"][0]["reason"], "source_below_dead_volume")
        self.assertIn("below dead volume", report["failures"][0]["message"])

    def test_needs_user_when_source_volume_is_unknown(self):
        ir = _transfer_ir()
        for item in ir["labware"]:
            if item["label"] == "SourcePlate":
                item.pop("initial_contents")

        report = validate_liquid_state(ir)

        self.assertEqual(report["status"], "needs_user")
        self.assertEqual(report["unresolved"][0]["reason"], "source_volume_unknown")

    def test_fails_when_dispense_would_exceed_destination_max_volume(self):
        ir = _transfer_ir(destination_volume=190, transfer_volume=20, destination_max=200)
        report = validate_liquid_state(ir)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failures"][0]["reason"], "destination_exceeds_max_volume")


def _transfer_ir(
    *,
    source_volume: float = 50,
    destination_volume: float | None = None,
    transfer_volume: float = 20,
    dead_volume: float = 0,
    destination_max: float = 200,
) -> dict:
    dest_contents = (
        {"reagent": "Water", "volume_ul": destination_volume, "scope": "all_wells"}
        if destination_volume is not None
        else None
    )
    labware = [
        {"label": "Tips", "catalog": "MCA96, 100ul, Box", "role": "tips"},
        {
            "label": "SourcePlate",
            "catalog": "96 Well Flat",
            "role": "source",
            "dead_volume_ul": dead_volume,
            "initial_contents": {"reagent": "Water", "volume_ul": source_volume, "scope": "all_wells"},
        },
        {
            "label": "DestPlate",
            "catalog": "96 Well Flat",
            "role": "destination",
            "max_volume_ul": destination_max,
        },
    ]
    if dest_contents:
        labware[2]["initial_contents"] = dest_contents
    return {
        "ir_version": "tecan.protocol_ir.v1",
        "id": "transfer",
        "protocol": {"name": "transfer"},
        "source": {"format": "test"},
        "worktable": {"name": "Test"},
        "labware": labware,
        "reagents": [{"name": "Water"}],
        "liquid_classes": [{"name": "Water Free Single"}],
        "variables": [],
        "worklists": [],
        "dependencies": [],
        "safety_assumptions": [{"id": "manual_validation_required", "text": "Review before use."}],
        "steps": [
            _step(1, "pick_up_tips", "Tips"),
            _step(2, "aspirate", "SourcePlate", volume=transfer_volume, wells="A1"),
            _step(3, "dispense", "DestPlate", volume=transfer_volume, wells="A1"),
        ],
    }


def _step(index: int, operation: str, target: str, *, volume: float | None = None, wells: str | None = None) -> dict:
    step = {
        "id": f"step_{index:03d}",
        "index": index,
        "group": "Protocol",
        "operation": operation,
        "name": operation.replace("_", " ").title(),
        "target_labware": target,
        "parameters": {},
    }
    if volume is not None:
        step["volume_ul"] = volume
        step["liquid_class"] = "Water Free Single"
    if wells:
        step["parameters"]["wells"] = wells
    return step


if __name__ == "__main__":
    unittest.main()
