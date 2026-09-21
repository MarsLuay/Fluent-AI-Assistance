import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tecan_common.worklist_contract import (
    ExecuteWorklistContract,
    LoadWorklistContract,
    build_execution_contract,
    normalize_load_worklist,
)
from tecan_worklist.cli import main
from tecan_worklist.gwl import (
    Break,
    Comment,
    Flush,
    Pipette,
    RawRecord,
    SetDiTiType,
    parse_gwl_lines,
    parse_tip_selection,
)
from tecan_worklist.optimizer import analyze_worklist, optimize_worklist, semantic_equivalent
from tecan_worklist.transfer import Transfer, validate_transfers
from tecan_worklist.gwl import Worklist


def _transfer(index: int, *, tip: str, source: int | None = None, destination: int | None = None) -> tuple[Pipette, Pipette]:
    source = source or index
    destination = destination or index
    return (
        Pipette("A", "Source", "Plate", source, 10, liquid_class="Water", tip_mask=tip),
        Pipette("D", "Destination", "Plate", destination, 10, liquid_class="Water", tip_mask=tip),
    )


class WorklistSemanticsTests(unittest.TestCase):
    def test_tip_selection_is_automatic_or_one_hot_and_preserves_raw_spelling(self):
        automatic = parse_tip_selection("")
        self.assertEqual((automatic.mode, automatic.value, automatic.channel), ("automatic", None, None))
        self.assertTrue(automatic.valid)

        for raw, channel in (("1", 1), ("2", 2), ("4", 3), ("8", 4), ("16", 5), ("32", 6), ("64", 7), ("128", 8), ("01", 1)):
            selection = parse_tip_selection(raw)
            self.assertTrue(selection.valid, raw)
            self.assertEqual(selection.channel, channel)
            self.assertEqual(selection.raw, raw)

        spaced = parse_tip_selection(" 01 ")
        self.assertTrue(spaced.valid)
        self.assertEqual(spaced.raw, " 01 ")

        self.assertEqual(parse_tip_selection("not-an-int").diagnostic_code, "tip_mask_not_integer")
        self.assertEqual(parse_tip_selection("0").diagnostic_code, "tip_mask_non_positive")
        self.assertEqual(parse_tip_selection("3").diagnostic_code, "tip_mask_not_one_hot")
        self.assertEqual(parse_tip_selection("256").diagnostic_code, "tip_mask_channel_out_of_range")

    def test_typed_f_and_s_records_and_unknown_records_round_trip_losslessly(self):
        lines = [
            "F;  vendor-field ;two",
            "S;DiTiType; 42 ",
            "X;opaque;field",
        ]
        worklist = parse_gwl_lines(lines, permissive=True)
        self.assertIsInstance(worklist.records[0], Flush)
        self.assertIsInstance(worklist.records[1], SetDiTiType)
        self.assertIsInstance(worklist.records[2], RawRecord)
        self.assertEqual(worklist.to_text(), "\n".join(lines) + "\n")

    def test_csv_tip_mask_validation_rejects_non_one_hot(self):
        valid = Transfer("S", "Plate", 1, "D", "Plate", 1, 10, tip_mask="4")
        invalid = Transfer("S", "Plate", 2, "D", "Plate", 2, 10, tip_mask="3")
        result = validate_transfers([valid, invalid])
        self.assertFalse(result.ok)
        self.assertTrue(any("tip_mask_not_one_hot" in error for error in result.errors))

    def test_load_contract_normalizes_mask_and_preserves_unknown_fields(self):
        contract = normalize_load_worklist(
            {
                "WorklistName": "Transfers.gwl",
                "SelectedTipsIndexes": "5",
                "TipSystem": "Fixed Tips",
                "NewVendorField": "preserve-me",
            },
            command_id="LoadWorklistStatementDataV4",
        )
        self.assertIsInstance(contract, LoadWorklistContract)
        self.assertEqual(contract.worklist, "Transfers.gwl")
        self.assertEqual(contract.selected_tip_channels, (1, 3))
        self.assertEqual(contract.tip_system, "fixed_tip")
        self.assertEqual(contract.extra_fields, {"NewVendorField": "preserve-me"})
        self.assertEqual(contract.to_dict()["schema_version"], "tecan.worklist.contract.v2")

    def test_load_execute_contract_retains_chronological_load_boundary(self):
        result = build_execution_contract(
            [
                {"command_id": "LoadWorklistStatementDataV4", "parameters": {"WorklistName": "first.gwl"}},
                {"command_id": "LoadWorklistStatementDataV4", "parameters": {"WorklistName": "second.gwl"}},
                {"command_id": "ExecuteWorklistStatementDataV1", "parameters": {"Unknown": "kept"}},
                {"command_id": "LoadWorklistStatementDataV4", "parameters": {"WorklistName": "third.gwl"}},
            ]
        )
        self.assertIsInstance(result[2], ExecuteWorklistContract)
        self.assertEqual(result[2].loads_before_execute, ("first.gwl", "second.gwl"))
        self.assertEqual(result[2].extra_fields, {"Unknown": "kept"})
        self.assertIsInstance(result[3], LoadWorklistContract)

    def test_analysis_reports_explicit_and_automatic_units_and_barriers(self):
        first_a, first_d = _transfer(1, tip="1")
        second_a, second_d = _transfer(2, tip="")
        worklist = Worklist(records=[first_a, first_d, second_a, second_d, Break()])
        analysis = analyze_worklist(worklist, enabled_tip_mask="3")
        self.assertEqual(len(analysis.units), 2)
        self.assertEqual(analysis.units[0].tip_channel, 1)
        self.assertEqual(analysis.units[1].tip_selection.mode, "automatic")
        self.assertEqual(analysis.barriers[0]["type"], "B")
        self.assertEqual(analysis.enabled_tip_mask, 3)

    def test_optimizer_interleaves_only_independent_explicit_units(self):
        records = []
        for index, tip in ((1, "1"), (2, "1"), (3, "2"), (4, "2")):
            records.extend(_transfer(index, tip=tip))
        original = Worklist(records=records)
        result = optimize_worklist(original, enabled_tip_mask="3")
        self.assertTrue(result.changed)
        self.assertEqual(result.reordered_units, ((0, 2, 1, 3),))
        self.assertTrue(semantic_equivalent(original, result.worklist, enabled_tip_mask="3"))
        self.assertEqual([record.tip_mask for record in result.worklist.records if isinstance(record, Pipette) and record.operation == "A"], ["1", "2", "1", "2"])

    def test_optimizer_keeps_comments_with_units_and_respects_dependencies_and_barriers(self):
        first_a, first_d = _transfer(1, tip="1")
        second_a, second_d = _transfer(2, tip="1")
        third_a, third_d = _transfer(3, tip="2")
        dependent_a, dependent_d = _transfer(3, tip="1", source=1, destination=3)
        original = Worklist(
            records=[
                Comment("first"),
                first_a,
                first_d,
                second_a,
                second_d,
                third_a,
                third_d,
                Break(),
                dependent_a,
                dependent_d,
            ]
        )
        result = optimize_worklist(original, enabled_tip_mask="3")
        self.assertTrue(result.changed)
        self.assertEqual(result.worklist.records[0].to_line(), "C;first")
        self.assertEqual(result.worklist.records[1].to_line(), "A;Source;;Plate;1;;10;Water;;1;")
        self.assertEqual(result.worklist.records[7].type_character, "B")
        self.assertEqual(result.worklist.records[8].to_line(), dependent_a.to_line())

        shared_a, shared_d = _transfer(4, tip="2", source=1, destination=4)
        unsafe = Worklist(records=[first_a, first_d, shared_a, shared_d])
        unsafe_result = optimize_worklist(unsafe, enabled_tip_mask="3")
        self.assertFalse(unsafe_result.changed)
        self.assertFalse(unsafe_result.analysis.windows[0].independent)

    def test_optimizer_refuses_invalid_selection_instead_of_crashing(self):
        first_a, _ = _transfer(1, tip="3")
        invalid = Worklist(records=[first_a, Pipette("D", "Destination", "Plate", 1, 10, tip_mask="3")])
        result = optimize_worklist(invalid, enabled_tip_mask="3")
        self.assertFalse(result.changed)
        self.assertTrue(result.analysis.has_errors)

    def test_cli_analyze_and_optimize_are_explicit_and_json_inspectable(self):
        first_a, first_d = _transfer(1, tip="1")
        second_a, second_d = _transfer(2, tip="1")
        third_a, third_d = _transfer(3, tip="2")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.gwl"
            output = Path(tmp) / "optimized.gwl"
            Worklist(records=[first_a, first_d, second_a, second_d, third_a, third_d]).write(source)
            analyze_stdout = io.StringIO()
            with contextlib.redirect_stdout(analyze_stdout):
                self.assertEqual(main(["analyze", str(source), "--enabled-tip-mask", "3", "--json"]), 0)
            analysis_payload = json.loads(analyze_stdout.getvalue())
            self.assertEqual(analysis_payload["unit_count"], 3)
            optimize_stdout = io.StringIO()
            with contextlib.redirect_stdout(optimize_stdout):
                self.assertEqual(main(["optimize", str(source), "-o", str(output), "--enabled-tip-mask", "3", "--json"]), 0)
            self.assertTrue(output.is_file())
            self.assertTrue(json.loads(optimize_stdout.getvalue())["changed"])


if __name__ == "__main__":
    unittest.main()
