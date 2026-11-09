import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tecan_common import native_acceleration
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
    parse_gwl,
    parse_gwl_lines,
    parse_tip_selection,
)
from tecan_worklist.optimizer import analyze_worklist, optimize_worklist, semantic_equivalent
from tecan_worklist.transfer import Transfer, load_transfers, validate_transfers
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

    def test_native_execution_contract_matches_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_execution_contract"
        ):
            self.skipTest("optional Rust worklist-contract kernel is not built")
        commands = [
            {
                "command_id": "LoadWorklist",
                "parameters": {
                    "WorklistName": "synthetic.gwl",
                    "SelectedTipsIndexes": [2, 0, 2],
                    "options": {"z_option": 1, "a_option": 2},
                    "VendorZ": {"z": 1, "a": 2},
                    "VendorA": "kept",
                },
                "provenance": {"z_source": "synthetic", "a_source": "test"},
            },
            {
                "command_id": "ExecuteWorklist",
                "parameters": {
                    "vendorZ": {"z": 1, "a": 2},
                    "vendorA": "kept",
                    "line_number": 4,
                    "empty": [],
                    "zero": 0,
                },
            },
            {"command_id": "Unknown", "parameters": {"z": 1, "a": 2}},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = build_execution_contract(commands)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_execution_contract",
                wraps=native_acceleration.worklist_execution_contract,
            ) as native_call:
                actual = build_execution_contract(commands)
        native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual[0].options), ["z_option", "a_option"])
        self.assertEqual(list(actual[0].extra_fields), ["VendorZ", "VendorA"])
        self.assertEqual(list(actual[1].extra_fields), ["vendorZ", "vendorA", "zero"])
        self.assertIs(actual[0].extra_fields["VendorZ"], commands[0]["parameters"]["VendorZ"])
        self.assertIs(actual[1].extra_fields["vendorZ"], commands[1]["parameters"]["vendorZ"])
        self.assertIs(actual[2], commands[2])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_load_contract",
                wraps=native_acceleration.worklist_load_contract,
            ) as native_load_call:
                load = normalize_load_worklist(
                    commands[0]["parameters"],
                    command_id="LoadWorklist",
                    provenance=commands[0]["provenance"],
                )
        native_load_call.assert_called_once()
        self.assertEqual(load, expected[0])
        self.assertIs(load.extra_fields["VendorZ"], commands[0]["parameters"]["VendorZ"])

    def test_native_csv_loader_matches_python_and_marks_logic_stage(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_load_transfers"
        ):
            self.skipTest("optional Rust CSV-loader kernel is not built")
        headers = [
            "source_label",
            "source_type",
            "source_position",
            "dest_label",
            "dest_type",
            "dest_position",
            "volume_ul",
            "liquid_class",
            "source_id",
            "dest_id",
            "source_tube_id",
            "dest_tube_id",
            "tip_mask",
            "forced_source_type",
            "forced_dest_type",
            "comment",
            "wash_after",
            "break_after",
        ]
        rows = [
            [
                "Source, A", "Plate", "A1", "Destination", "Plate", "B1", "10.5",
                "Water", "source-id", "dest-id", "source-tube", "dest-tube", "1",
                "forced-source", "forced-dest", "quoted, comment", "no", "true",
            ],
            [
                "Source2", "Plate", "H12", "Destination2", "Plate", "A2", "20.0",
                "", "", "", "", "", "2", "", "", "", "", "",
            ],
        ]
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "synthetic.csv"
            csv_text = io.StringIO(newline="")
            writer = csv.writer(csv_text)
            writer.writerow(headers)
            writer.writerows(rows)
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write("\ufeff" + csv_text.getvalue())

            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                expected = load_transfers(path)
            marker_output = io.StringIO()
            with patch.dict(
                "os.environ",
                {
                    "TECAN_DISABLE_RUST_ACCELERATION": "0",
                    "TECAN_PERFORMANCE_MARKERS": "1",
                },
            ):
                with contextlib.redirect_stderr(marker_output):
                    with patch.object(
                        native_acceleration,
                        "worklist_load_transfers",
                        wraps=native_acceleration.worklist_load_transfers,
                    ) as native_call:
                        actual = load_transfers(path)
            native_call.assert_called_once()
            self.assertEqual(actual, expected)
            self.assertEqual(actual[0].source_label, "Source, A")
            self.assertEqual(actual[0].comment, "quoted, comment")
            self.assertEqual(actual[0].wash_after, False)
            self.assertTrue(actual[0].break_after)
            self.assertEqual(actual[1].source_position, 96)
            markers = marker_output.getvalue().splitlines()
            self.assertEqual(len(markers), 1)
            marker = json.loads(markers[0][5:])
            self.assertEqual(marker["name"], "worklist.csv_load")
            self.assertEqual(marker["metrics"], {"transfer_count": 2})
            self.assertNotIn(str(path), markers[0])

            unicode_path = Path(raw) / "unicode-position.csv"
            unicode_text = io.StringIO(newline="")
            unicode_writer = csv.writer(unicode_text)
            unicode_writer.writerow(headers)
            unicode_writer.writerow(
                ["Source", "Plate", "١", "Destination", "Plate", "B1", "10"]
                + [""] * (len(headers) - 7)
            )
            with unicode_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(unicode_text.getvalue())
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                unicode_expected = load_transfers(unicode_path)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertIsNone(
                    native_acceleration.worklist_load_transfers(unicode_path, 8)
                )
                unicode_actual = load_transfers(unicode_path)
            self.assertEqual(unicode_actual, unicode_expected)
            self.assertEqual(unicode_actual[0].source_position, 1)

    def test_native_convert_cli_matches_python_fallback_and_markers(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_build_transfers"
        ):
            self.skipTest("optional Rust worklist-builder kernel is not built")
        source = Path(__file__).resolve().parents[1] / "examples" / "simple_transfer.csv"
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "generated.gwl"
            for options in ([], ["--wash-policy", "none", "--batch-size", "2"]):
                arguments = [
                    "convert",
                    str(source),
                    "--output",
                    str(output),
                    "--json",
                    *options,
                ]
                fallback_stdout = io.StringIO()
                with patch.dict(
                    "os.environ",
                    {
                        "TECAN_DISABLE_RUST_ACCELERATION": "1",
                        "TECAN_PERFORMANCE_MARKERS": "0",
                    },
                ):
                    with contextlib.redirect_stdout(fallback_stdout):
                        self.assertEqual(main(arguments), 0)
                fallback_text = output.read_bytes()

                native_stdout = io.StringIO()
                marker_output = io.StringIO()
                with patch.dict(
                    "os.environ",
                    {
                        "TECAN_DISABLE_RUST_ACCELERATION": "0",
                        "TECAN_PERFORMANCE_MARKERS": "1",
                    },
                ):
                    with contextlib.redirect_stdout(native_stdout):
                        with contextlib.redirect_stderr(marker_output):
                            with patch(
                                "tecan_worklist.cli.build_worklist",
                                side_effect=AssertionError("native builder fell back to Python"),
                            ) as python_fallback:
                                with patch.object(
                                    native_acceleration,
                                    "worklist_build_transfers",
                                    wraps=native_acceleration.worklist_build_transfers,
                                ) as native_call:
                                    self.assertEqual(main(arguments), 0)
                native_call.assert_called_once()
                python_fallback.assert_not_called()
                self.assertEqual(native_stdout.getvalue(), fallback_stdout.getvalue())
                self.assertEqual(output.read_bytes(), fallback_text)

                markers = [
                    json.loads(line[5:]) for line in marker_output.getvalue().splitlines()
                ]
                self.assertEqual(
                    [marker["name"] for marker in markers],
                    [
                        "worklist.csv_load",
                        "worklist.transfer_validation",
                        "worklist.gwl_record_assembly",
                        "worklist.gwl_write",
                    ],
                )
                self.assertNotIn(str(output), marker_output.getvalue())
                self.assertTrue(
                    all(
                        set(marker["metrics"]) <= {"transfer_count", "record_count"}
                        for marker in markers
                    )
                )

    def test_native_gwl_summary_matches_python_and_preserves_splitlines_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_parse_gwl"
        ):
            self.skipTest("optional Rust GWL parser kernel is not built")
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "synthetic.gwl"
            text = (
                "C;first comment\r\n"
                "a;Source;source-id;Plate;1;tube;10;Water;;1;forced\r\n"
                "D;Destination;dest-id;Plate;2;tube;10;Water;;1;forced\r\n"
                "W;\r\nB;\r\nF;opaque;field\r\nS;type\r\n"
            )
            with source.open("w", encoding="utf-8", newline="") as handle:
                handle.write("\ufeff" + text)
            arguments = ["summarize", str(source), "--json"]
            fallback_stdout = io.StringIO()
            with patch.dict(
                "os.environ",
                {
                    "TECAN_DISABLE_RUST_ACCELERATION": "1",
                    "TECAN_PERFORMANCE_MARKERS": "0",
                },
            ):
                with contextlib.redirect_stdout(fallback_stdout):
                    self.assertEqual(main(arguments), 0)

            native_stdout = io.StringIO()
            marker_output = io.StringIO()
            with patch.dict(
                "os.environ",
                {
                    "TECAN_DISABLE_RUST_ACCELERATION": "0",
                    "TECAN_PERFORMANCE_MARKERS": "1",
                },
            ):
                with contextlib.redirect_stdout(native_stdout):
                    with contextlib.redirect_stderr(marker_output):
                        with patch(
                            "tecan_worklist.cli.parse_gwl",
                            side_effect=AssertionError("native parser fell back to Python"),
                        ) as python_fallback:
                            with patch.object(
                                native_acceleration,
                                "worklist_parse_gwl",
                                wraps=native_acceleration.worklist_parse_gwl,
                            ) as native_call:
                                self.assertEqual(main(arguments), 0)
            native_call.assert_called_once()
            python_fallback.assert_not_called()
            self.assertEqual(native_stdout.getvalue(), fallback_stdout.getvalue())
            markers = [
                json.loads(line[5:]) for line in marker_output.getvalue().splitlines()
            ]
            self.assertEqual(
                [marker["name"] for marker in markers],
                ["worklist.gwl_parse", "worklist.gwl_summary"],
            )
            self.assertTrue(all(marker["metrics"] == {"record_count": 7} for marker in markers))
            self.assertNotIn(str(source), marker_output.getvalue())

            splitlines_source = Path(raw) / "python-splitlines.gwl"
            splitlines_source.write_text(
                "B;\vA;Source;source-id;Plate;1;tube;10;Water;;1;forced\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertIsNone(native_acceleration.worklist_parse_gwl(splitlines_source))
                expected_counts = parse_gwl(splitlines_source).counts()
            self.assertEqual(expected_counts, {"B": 1, "A": 1})

    def test_native_analyzer_matches_python_cli_and_markers(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_analyze_lines"
        ):
            self.skipTest("optional Rust worklist-analysis kernel is not built")
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "analysis.gwl"
            source.write_text(
                "C;first pair\n"
                "A;Source1;s1;Plate;1;t1;10;Water;;1;\n"
                "D;Destination1;d1;Plate;1;d1;10;Water;;1;\n"
                "C;second pair\n"
                "A;Source2;s2;Plate;1;t2;10;Water;;2;\n"
                "D;Destination2;d2;Plate;1;d2;10;Water;;2;\n"
                "W;\n"
                "A;Source3;s3;Plate;2;t3;10;Water;;3;\n"
                "D;Destination3;d3;Plate;2;d3;10;Water;;3;\n"
                "Z;unsupported\n",
                encoding="utf-8",
            )
            arguments = [
                "analyze",
                str(source),
                "--enabled-tip-mask",
                "7",
                "--supported-channels",
                "3",
                "--json",
            ]
            fallback_stdout = io.StringIO()
            with patch.dict(
                "os.environ",
                {
                    "TECAN_DISABLE_RUST_ACCELERATION": "1",
                    "TECAN_PERFORMANCE_MARKERS": "0",
                },
            ):
                with contextlib.redirect_stdout(fallback_stdout):
                    self.assertEqual(main(arguments), 1)

            native_stdout = io.StringIO()
            marker_output = io.StringIO()
            with patch.dict(
                "os.environ",
                {
                    "TECAN_DISABLE_RUST_ACCELERATION": "0",
                    "TECAN_PERFORMANCE_MARKERS": "1",
                },
            ):
                with contextlib.redirect_stdout(native_stdout):
                    with contextlib.redirect_stderr(marker_output):
                        with patch(
                            "tecan_worklist.cli.analyze_worklist",
                            side_effect=AssertionError("native analyzer fell back to Python"),
                        ) as python_fallback:
                            with patch.object(
                                native_acceleration,
                                "worklist_analyze_lines",
                                wraps=native_acceleration.worklist_analyze_lines,
                            ) as native_call:
                                self.assertEqual(main(arguments), 1)
            native_call.assert_called_once()
            python_fallback.assert_not_called()
            self.assertEqual(
                json.loads(native_stdout.getvalue()),
                json.loads(fallback_stdout.getvalue()),
            )
            markers = [
                json.loads(line[5:]) for line in marker_output.getvalue().splitlines()
            ]
            self.assertEqual(
                [marker["name"] for marker in markers],
                ["worklist.gwl_parse", "worklist.gwl_analysis"],
            )
            self.assertTrue(all(marker["metrics"] == {"record_count": 10} for marker in markers))
            self.assertNotIn(str(source), marker_output.getvalue())

    def test_native_transfer_validation_matches_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_validate_transfers"
        ):
            self.skipTest("optional Rust transfer-validation kernel is not built")
        transfers = [
            Transfer(
                source_label="",
                source_type="S" * 33,
                source_position=0,
                dest_label="destination",
                dest_type="",
                dest_position=1,
                volume_ul=1000.001,
                liquid_class="L" * 33,
                tip_mask="3",
                row_number=17,
            ),
            Transfer(
                source_label="source",
                source_type="plate",
                source_position=1,
                dest_label="destination",
                dest_type="plate",
                dest_position=1,
                volume_ul=10,
                tip_mask="1",
            ),
        ]
        for strict in (False, True):
            with self.subTest(strict=strict):
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                    expected = validate_transfers(transfers, strict=strict)
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                    with patch.object(
                        native_acceleration,
                        "worklist_validate_transfers",
                        wraps=native_acceleration.worklist_validate_transfers,
                    ) as native_call:
                        actual = validate_transfers(transfers, strict=strict)
                native_call.assert_called_once()
                self.assertEqual(actual, expected)
        self.assertEqual(len(actual.errors), 7)
        self.assertEqual(len(actual.warnings), 3)

    def test_transfer_validation_marker_is_opt_in_and_count_only(self):
        transfer = Transfer("source", "plate", 1, "destination", "plate", 1, 10.0)
        disabled = io.StringIO()
        with patch.dict("os.environ", {"TECAN_PERFORMANCE_MARKERS": "0"}):
            with contextlib.redirect_stderr(disabled):
                validate_transfers([transfer])
        self.assertEqual(disabled.getvalue(), "")

        output = io.StringIO()
        with patch.dict("os.environ", {"TECAN_PERFORMANCE_MARKERS": "1"}):
            with contextlib.redirect_stderr(output):
                validate_transfers([transfer])
        records = output.getvalue().splitlines()
        self.assertEqual(len(records), 1)
        marker = json.loads(records[0][5:])
        self.assertEqual(marker["name"], "worklist.transfer_validation")
        self.assertEqual(marker["metrics"], {"transfer_count": 1})

    def test_execution_contract_markers_are_opt_in_and_value_free(self):
        commands = [
            {"command_id": "LoadWorklist", "parameters": {"WorklistName": "synthetic.gwl", "private": "not-logged"}}
        ]
        disabled = io.StringIO()
        with patch.dict("os.environ", {"TECAN_PERFORMANCE_MARKERS": "0"}):
            with contextlib.redirect_stderr(disabled):
                build_execution_contract(commands)
        self.assertEqual(disabled.getvalue(), "")

        enabled = io.StringIO()
        with patch.dict("os.environ", {"TECAN_PERFORMANCE_MARKERS": "1"}):
            with contextlib.redirect_stderr(enabled):
                build_execution_contract(commands)
        lines = enabled.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("PERF "))
        marker = json.loads(lines[0][5:])
        self.assertEqual(marker["name"], "worklist.execution_contract")
        self.assertEqual(marker["metrics"], {"command_count": 1})
        self.assertNotIn("synthetic.gwl", lines[0])
        self.assertNotIn("not-logged", lines[0])

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
