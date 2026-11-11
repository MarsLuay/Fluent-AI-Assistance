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
    _normalize_tip_system as normalize_tip_system,
)
from tecan_worklist.cli import main
from tecan_common.gwl import (
    _clean as clean_gwl_field,
    _format_volume as format_gwl_volume,
)
from tecan_worklist.gwl import (
    Break,
    Comment,
    Flush,
    Pipette,
    RawRecord,
    SetDiTiType,
    Wash,
    parse_gwl,
    parse_gwl_line,
    parse_gwl_lines,
    parse_gwl_text,
    parse_tip_selection,
    serialize_gwl,
)
from tecan_worklist.optimizer import (
    analyze_worklist,
    optimize_worklist,
    parse_enabled_tip_mask,
    semantic_equivalent,
    _append_tip_diagnostic,
    _barrier_reason,
    _window_properties,
)
from tecan_worklist.transfer import (
    Transfer,
    build_worklist,
    load_transfers,
    validate_transfers,
    well_to_position,
    _parse_bool as parse_transfer_bool,
    _row_name_to_index as transfer_row_name_to_index,
)
from tecan_worklist.gwl import Worklist


def _transfer(index: int, *, tip: str, source: int | None = None, destination: int | None = None) -> tuple[Pipette, Pipette]:
    source = source or index
    destination = destination or index
    return (
        Pipette("A", "Source", "Plate", source, 10, liquid_class="Water", tip_mask=tip),
        Pipette("D", "Destination", "Plate", destination, 10, liquid_class="Water", tip_mask=tip),
    )


class WorklistSemanticsTests(unittest.TestCase):
    def test_pipette_operation_normalization_matches_python(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_ascii_uppercase"
        ):
            self.skipTest("optional Rust operation normalizer is not built")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(native_acceleration.worklist_ascii_uppercase("ad"), "AD")
            self.assertEqual(native_acceleration.worklist_ascii_uppercase(""), "")
            self.assertIsNone(native_acceleration.worklist_ascii_uppercase("ſ"))

        for operation in (" a ", "d", "X", "ſ", " "):
            outcomes = []
            for disabled in ("1", "0"):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": disabled}
                ):
                    try:
                        outcomes.append(
                            Pipette(operation, "Rack", "Plate", 1, 1).type_character
                        )
                    except ValueError as error:
                        outcomes.append((type(error), str(error)))
            self.assertEqual(outcomes[0], outcomes[1], operation)

    def test_wash_scheme_normalization_matches_python(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_wash_type"
        ):
            self.skipTest("optional Rust wash formatter is not built")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(native_acceleration.worklist_wash_type(None), "W")
            self.assertEqual(native_acceleration.worklist_wash_type(1), "W1")
            self.assertEqual(native_acceleration.worklist_wash_type(4), "W4")
            for unsupported in (True, 1.0, 0, 5, 2**100):
                self.assertIsNone(native_acceleration.worklist_wash_type(unsupported))

        class IntegerScheme(int):
            pass

        for scheme in (None, 1, 4, 0, 5, True, False, 1.0, 2.0, IntegerScheme(3)):
            outcomes = []
            for disabled in ("1", "0"):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": disabled}
                ):
                    try:
                        outcomes.append(Wash(scheme).type_character)
                    except ValueError as error:
                        outcomes.append((type(error), str(error)))
            self.assertEqual(outcomes[0], outcomes[1], repr(scheme))

    def test_transfer_boolean_parser_matches_python_and_falls_back(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_parse_bool"
        ):
            self.skipTest("optional Rust transfer boolean parser is not built")

        class StringSubclass(str):
            pass

        cases = (
            "",
            "1",
            " true ",
            "YES",
            "y",
            "0",
            " FALSE ",
            "No",
            "n",
            "\tTrue\r\n",
            "\x1cY",
            "\u00a0yes",
            "on",
            "maybe",
            StringSubclass("true"),
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            for value in cases:
                with self.subTest(value=repr(value)):
                    if type(value) is str and value.isascii() and value.strip().lower() in {
                        "1", "true", "yes", "y", "0", "false", "no", "n"
                    }:
                        self.assertIsNotNone(
                            native_acceleration.worklist_parse_bool(value)
                        )
                    else:
                        self.assertIsNone(native_acceleration.worklist_parse_bool(value))
                    outcomes = []
                    for disabled in ("1", "0"):
                        with patch.dict(
                            "os.environ",
                            {"TECAN_DISABLE_RUST_ACCELERATION": disabled},
                        ):
                            try:
                                outcomes.append(parse_transfer_bool(value))
                            except ValueError as error:
                                outcomes.append((type(error), str(error)))
                    self.assertEqual(outcomes[0], outcomes[1])

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

    def test_native_enabled_tip_mask_matches_python_and_falls_back(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_parse_enabled_tip_mask"
        ):
            self.skipTest("optional Rust command-mask parser is not built")
        cases = [
            ("1", 8),
            (" 03 ", 8),
            ("1_0", 8),
            ("1", 1),
            (str((1 << 127) - 1), 127),
        ]
        for value, channels in cases:
            with self.subTest(value=value, channels=channels):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = parse_enabled_tip_mask(value, supported_channels=channels)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    native = native_acceleration.worklist_parse_enabled_tip_mask(
                        value, channels
                    )
                    self.assertIsNotNone(native)
                    actual = parse_enabled_tip_mask(value, supported_channels=channels)
                self.assertEqual(native, expected)
                self.assertEqual(actual, expected)

        fallback_cases = [
            ("256", 8, False),
            ("not-an-int", 8, False),
            (str(1 << 127), 127, False),
            ("١", 8, True),
            ("1", 0, False),
        ]
        for value, channels, valid in fallback_cases:
            with self.subTest(fallback=value, channels=channels):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    self.assertIsNone(
                        native_acceleration.worklist_parse_enabled_tip_mask(
                            value, channels
                        )
                    )
                    if valid:
                        self.assertEqual(
                            parse_enabled_tip_mask(value, supported_channels=channels),
                            1,
                        )
                    else:
                        with self.assertRaises(ValueError):
                            parse_enabled_tip_mask(value, supported_channels=channels)

    def test_native_tip_selection_matches_python_and_falls_back_on_edges(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_parse_tip_selection"
        ):
            self.skipTest("optional Rust tip-mask parser is not built")
        common_cases = [
            ("", "", 8),
            (" 01 ", " 01 ", 8),
            ("1_0", "1_0", None),
            ("256", "256", 8),
            ("0", "0", None),
            ("-1", "-1", 8),
            ("3", "3", 8),
            ("not-an-int", "not-an-int", 8),
            (None, "", 8),
            (17, "17", 8),
        ]
        for raw, rust_raw, channels in common_cases:
            with self.subTest(raw=raw, channels=channels):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = parse_tip_selection(raw, supported_channels=channels)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    native = native_acceleration.worklist_parse_tip_selection(
                        rust_raw, channels
                    )
                    self.assertIsNotNone(native)
                    actual = parse_tip_selection(raw, supported_channels=channels)
                self.assertEqual(native, expected.to_dict())
                self.assertEqual(actual, expected)

        fallback_cases = [
            ("١", "١", 8),
            (str(1 << 130), str(1 << 130), 8),
            ("\x1c", "\x1c", 8),
            ("1", "1", -1),
            ("1", "1", True),
            ("1", "1", 10**100),
        ]
        for raw, rust_raw, channels in fallback_cases:
            with self.subTest(fallback=raw, channels=channels):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = parse_tip_selection(raw, supported_channels=channels)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    self.assertIsNone(
                        native_acceleration.worklist_parse_tip_selection(
                            rust_raw, channels
                        )
                    )
                    actual = parse_tip_selection(raw, supported_channels=channels)
                self.assertEqual(actual, expected)

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

    def test_native_worklist_counts_preserve_first_seen_order(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_count_record_types"
        ):
            self.skipTest("optional Rust record counter is not built")
        worklist = Worklist(
            records=[
                Comment("note"),
                Pipette("A", "Source", "Plate", 1, 10),
                Pipette("D", "Destination", "Plate", 1, 10),
                Comment("second"),
                Wash(),
                Break(),
                RawRecord("X", "X;opaque"),
            ]
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = worklist.counts()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_count_record_types",
                wraps=native_acceleration.worklist_count_record_types,
            ) as native_call:
                actual = worklist.counts()
        native_call.assert_called_once()
        self.assertEqual(list(actual.items()), list(expected.items()))
        self.assertEqual(actual, {"C": 2, "A": 1, "D": 1, "W": 1, "B": 1, "X": 1})

        non_string_types = Worklist(records=[Comment("note", type_character=7)])
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_count_record_types",
                side_effect=AssertionError("non-string record types must stay on Python"),
            ) as native_call:
                self.assertEqual(non_string_types.counts(), {7: 1})
            native_call.assert_not_called()

    def test_native_gwl_field_cleaner_matches_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_clean_field"
        ):
            self.skipTest("optional Rust GWL field cleaner is not built")
        class StringSubclass(str):
            pass

        values = [
            None,
            "",
            "  rack label  ",
            "\t plate\r\n",
            "café",
            "\x1c edge ",
            StringSubclass("  subclass  "),
            42,
        ]
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = clean_gwl_field(value)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    native = native_acceleration.worklist_clean_field(value)
                    if value is None or (
                        type(value) is str
                        and value.isascii()
                        and not any(0x1C <= ord(character) <= 0x1F for character in value)
                    ):
                        self.assertIsNotNone(native)
                    else:
                        self.assertIsNone(native)
                    self.assertEqual(clean_gwl_field(value), expected)
                    if native is not None:
                        self.assertEqual(native, expected)

    def test_native_gwl_volume_formatter_matches_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_format_volume"
        ):
            self.skipTest("optional Rust GWL volume formatter is not built")
        class StringSubclass(str):
            pass

        values = [
            0,
            10,
            9_007_199_254_740_993,
            -0.0,
            1.25,
            1.2345675,
            -0.0000001,
            float("nan"),
            float("inf"),
            " 12.5 ",
            "1e2",
            ".5",
            "1.",
            "1e999",
            "1_000",
            "Infinity",
            "nan",
            "café",
            "\x1c3",
            StringSubclass(" 2.5 "),
            True,
        ]
        for value in values:
            with self.subTest(value_type=type(value).__name__, value=str(value)[:20]):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = format_gwl_volume(value)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    native = native_acceleration.worklist_format_volume(value)
                    if type(value) in (int, float):
                        self.assertIsNotNone(native)
                    elif type(value) is str and value.strip() in {
                        "12.5",
                        "1e2",
                        ".5",
                        "1.",
                        "1e999",
                    }:
                        self.assertIsNotNone(native)
                    else:
                        self.assertIsNone(native)
                    actual = format_gwl_volume(value)
                self.assertEqual(actual, expected)
                if native is not None:
                    self.assertEqual(native, expected)

        oversized_integer = 10**400
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.worklist_format_volume(oversized_integer))
            with self.assertRaises(OverflowError):
                format_gwl_volume(oversized_integer)

    def test_native_gwl_type_only_records_match_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_record_type_line"
        ):
            self.skipTest("optional Rust GWL record-type formatter is not built")
        records = [Wash(), Wash(scheme=2), Wash(scheme=4), Break(), Break("ignored")]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [record.to_line() for record in records]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_record_type_line",
                wraps=native_acceleration.worklist_record_type_line,
            ) as native_call:
                actual = [record.to_line() for record in records]
            self.assertEqual(actual, expected)
            self.assertEqual(native_call.call_count, len(records))

        class TypeCharacter(str):
            pass

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.worklist_record_type_line(TypeCharacter("W"))
            )

    def test_native_gwl_comment_serializer_matches_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_comment_line"
        ):
            self.skipTest("optional Rust GWL comment serializer is not built")
        texts = [
            "",
            "plain text",
            "first\nsecond",
            "first\r\nsecond",
            "literal \\\\n sequence",
            "naïve\n✓",
            "\n",
            "trailing\r",
        ]
        for text in texts:
            with self.subTest(text=text):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = Comment(text).to_line()
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    native = native_acceleration.worklist_comment_line(text)
                    actual = Comment(text).to_line()
                self.assertEqual(native, expected)
                self.assertEqual(actual, expected)

        class TextSubclass(str):
            def replace(self, old, new):
                return "custom replacement"

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.worklist_comment_line(TextSubclass("text"))
            )
            self.assertEqual(Comment(TextSubclass("text")).to_line(), "C;custom replacement")

    def test_native_gwl_record_field_join_matches_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_join_gwl_fields"
        ):
            self.skipTest("optional Rust GWL field joiner is not built")
        records = [
            Pipette(
                "A",
                " Source ",
                "plàte",
                " A1 ",
                "1e1",
                rack_id=" R1 ",
                tip_mask=" 01 ",
                forced_rack_type=" forced ",
            ),
            Flush(parameters=(" alpha ", None, "β;tail")),
            SetDiTiType(parameters=("\tA\r", " one\n two ")),
            Flush(raw_line="F; raw ;line "),
        ]
        with patch.dict(
            "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
        ):
            expected = [record.to_line() for record in records]
        class FieldText(str):
            pass

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_join_gwl_fields",
                wraps=native_acceleration.worklist_join_gwl_fields,
            ) as native_call:
                actual = [record.to_line() for record in records]
            self.assertEqual(actual, expected)
            self.assertEqual(native_call.call_count, 3)
            self.assertIsNone(
                native_acceleration.worklist_join_gwl_fields(["F", FieldText("value")])
            )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_subclass = Flush(
                parameters=(" field ",), type_character=FieldText("F")
            ).to_line()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_subclass = Flush(
                parameters=(" field ",), type_character=FieldText("F")
            ).to_line()
        self.assertEqual(actual_subclass, expected_subclass)

    def test_native_gwl_serializer_matches_python_output(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_serialize_gwl_lines"
        ):
            self.skipTest("optional Rust GWL serializer is not built")
        records = [
            Pipette("A", " Source ", "Plate", 1, 10, rack_id="R1", tip_mask=" 01 "),
            Comment("first line\nsecond line"),
            Wash(scheme=2),
            Break(),
            Flush(("vendor", "field"), raw_line="F; raw ;line "),
            SetDiTiType(("type", "42"), raw_line="S;type;42"),
            RawRecord("X", "X;opaque;field"),
        ]
        with patch.dict(
            "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
        ):
            expected = serialize_gwl(records)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_serialize_gwl_lines",
                wraps=native_acceleration.worklist_serialize_gwl_lines,
            ) as native_call:
                actual = serialize_gwl(records)
            self.assertEqual(actual, expected)
            native_call.assert_called_once()
            self.assertEqual(serialize_gwl([]), "\n")

    def test_native_gwl_text_parser_preserves_python_splitlines_behavior(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_parse_gwl_text_records"
        ):
            self.skipTest("optional Rust GWL text parser is not built")
        cases = [
            ("C;first\r\nC;second\nW1;\n", False, True),
            ("A;src;;plate;1;;10;;;1;\nD;dst;;plate;2;;10;;;1;", False, True),
            ("C;first\vC;second", False, False),
            ("C;first\rC;second", False, False),
            ("X;opaque;field\nW5;extra", True, True),
            ("C;naïve", False, True),
            ("A;Réagent;id;plàte;A1;;10;liquïde;;1;", False, True),
            ("C;first\u2028C;second", False, False),
            ("ſ;opaque", True, False),
            ("", False, True),
        ]
        for text, permissive, native_supported in cases:
            with self.subTest(permissive=permissive, length=len(text)):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = parse_gwl_text(
                        text, name="text-parse", permissive=permissive
                    )
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    native_payload = native_acceleration.worklist_parse_gwl_text_records(
                        text, permissive
                    )
                    if native_supported:
                        self.assertIsNotNone(native_payload)
                    else:
                        self.assertIsNone(native_payload)
                    actual = parse_gwl_text(
                        text, name="text-parse", permissive=permissive
                    )
                self.assertEqual(actual, expected)
                self.assertEqual(actual.to_text(), expected.to_text())

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.worklist_parse_gwl_text_records("A;short", False)
            )
            with self.assertRaises(ValueError) as native_error:
                parse_gwl_text("A;short", name="error")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with self.assertRaises(ValueError) as python_error:
                parse_gwl_text("A;short", name="error")
        self.assertEqual(str(native_error.exception), str(python_error.exception))

    def test_native_gwl_file_parser_matches_python_newline_and_bom_behavior(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_read_gwl_records"
        ):
            self.skipTest("optional Rust GWL file reader is not built")
        cases = [
            (b"\xef\xbb\xbfC;first\r\nC;second\rW1;\n", False, True),
            (b"C;one\rC;two\r", False, True),
            ("C;one\u2028C;two\n".encode("utf-8"), False, False),
            (b"X;opaque\n", True, True),
            (b"", False, True),
        ]
        with tempfile.TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "sample.gwl"
            for content, permissive, native_supported in cases:
                path.write_bytes(content)
                with self.subTest(content=content, permissive=permissive):
                    with patch.dict(
                        "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                    ):
                        expected = parse_gwl(path, permissive=permissive)
                    with patch.dict(
                        "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                    ):
                        native = native_acceleration.worklist_read_gwl_records(
                            path, permissive
                        )
                        if native_supported:
                            self.assertIsNotNone(native)
                        else:
                            self.assertIsNone(native)
                        actual = parse_gwl(path, permissive=permissive)
                    self.assertEqual(actual.name, expected.name)
                    self.assertEqual(actual.to_text(), expected.to_text())
                    self.assertEqual(actual.counts(), expected.counts())

            path.write_bytes(b"\xff")
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertIsNone(native_acceleration.worklist_read_gwl_records(path, False))
                with self.assertRaises(UnicodeDecodeError):
                    parse_gwl(path)

            path.write_bytes(b"A;short\n")
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                with self.assertRaises(ValueError) as python_error:
                    parse_gwl(path)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertIsNone(native_acceleration.worklist_read_gwl_records(path, False))
                with self.assertRaises(ValueError) as native_error:
                    parse_gwl(path)
            self.assertEqual(str(native_error.exception), str(python_error.exception))

    def test_native_single_gwl_record_parser_matches_python(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_parse_gwl_records"
        ):
            self.skipTest("optional Rust GWL parser is not built")
        cases = [
            ("A;src;;plate;1;;10;;;1;", False, 3),
            ("W3;ignored-extra", True, 4),
            ("C;comment;tail", False, None),
            ("F; vendor ;field", False, 1),
            ("X;opaque", True, None),
            ("   ", False, None),
        ]
        for raw_line, permissive, line_no in cases:
            with self.subTest(raw_line=raw_line, permissive=permissive):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = parse_gwl_line(
                        raw_line, line_no=line_no, permissive=permissive
                    )
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    with patch.object(
                        native_acceleration,
                        "worklist_parse_gwl_records",
                        wraps=native_acceleration.worklist_parse_gwl_records,
                    ) as native_call:
                        actual = parse_gwl_line(
                            raw_line, line_no=line_no, permissive=permissive
                        )
                native_call.assert_called_once()
                self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.worklist_parse_gwl_records(["A;short"], False)
            )
            with self.assertRaises(ValueError) as native_error:
                parse_gwl_line("A;short", line_no=9)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with self.assertRaises(ValueError) as python_error:
                parse_gwl_line("A;short", line_no=9)
        self.assertEqual(str(native_error.exception), str(python_error.exception))

    def test_native_gwl_parser_matches_typed_python_records(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_parse_gwl_records"
        ):
            self.skipTest("optional Rust GWL parser is not built")
        inputs = [
            (
                [
                    "  a; src ;id; plate ;01;tube;10.00;water;reserved; 01 ;forced  ",
                    "D;dst;did;plate;B1;tube;10;water;;1;",
                    "W1;",
                    "B;",
                    "C;comment;with;semicolons",
                    "F;  vendor-field ;two",
                    "S;DiTiType; 42 ",
                    "",
                    "   ",
                ],
                False,
            ),
            (
                [
                    "a;short",
                    "W4;ignored-extra",
                    "B;ignored-extra",
                    "C;permissive;comment",
                    "X;opaque;field",
                    "W5;unrecognized-wash",
                ],
                True,
            ),
        ]
        for lines, permissive in inputs:
            with self.subTest(permissive=permissive):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = parse_gwl_lines(
                        lines, name="typed-parse", permissive=permissive
                    )
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    with patch.object(
                        native_acceleration,
                        "worklist_parse_gwl_records",
                        wraps=native_acceleration.worklist_parse_gwl_records,
                    ) as native_call:
                        actual = parse_gwl_lines(
                            lines, name="typed-parse", permissive=permissive
                        )
                native_call.assert_called_once()
                self.assertEqual(actual, expected)
                self.assertEqual(actual.to_text(), expected.to_text())
                self.assertEqual(actual.counts(), expected.counts())

        invalid_lines = ["A;short"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.worklist_parse_gwl_records(invalid_lines, False)
            )
            with self.assertRaises(ValueError) as expected_error:
                parse_gwl_lines(invalid_lines, name="invalid")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with self.assertRaises(ValueError) as python_error:
                parse_gwl_lines(invalid_lines, name="invalid")
        self.assertEqual(str(expected_error.exception), str(python_error.exception))

    def test_csv_tip_mask_validation_rejects_non_one_hot(self):
        valid = Transfer("S", "Plate", 1, "D", "Plate", 1, 10, tip_mask="4")
        invalid = Transfer("S", "Plate", 2, "D", "Plate", 2, 10, tip_mask="3")
        result = validate_transfers([valid, invalid])
        self.assertFalse(result.ok)
        self.assertTrue(any("tip_mask_not_one_hot" in error for error in result.errors))

    def test_tip_system_alias_normalization_uses_native_for_ascii_and_falls_back(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_normalize_tip_system"
        ):
            self.skipTest("optional Rust tip-system normalizer is not built")

        class CustomValue:
            def __str__(self):
                return "Fixed Tips"

        values = [
            "fixed", "fixed_tip", "FIXEDTIPS", " Fixed Tips ", "fixed\ttips",
            "diti", "disposable_tip", "Disposable Tips", "DISPOSABLE TIP ADAPTER",
            "unknown", "Fíxed Tips", None, "", CustomValue(),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [normalize_tip_system(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_normalize_tip_system",
                wraps=native_acceleration.worklist_normalize_tip_system,
            ) as native_call:
                actual = [normalize_tip_system(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(native_acceleration.worklist_normalize_tip_system("Fíxed Tips"))
            self.assertEqual(
                native_acceleration.worklist_normalize_tip_system("  Fixed Tips  "),
                "fixed_tip",
            )
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                "fixed_tip", "fixed_tip", "fixed_tip", "fixed_tip", "unknown",
                "diti", "diti", "diti", "diti", "unknown", "unknown",
                "unknown", "unknown", "fixed_tip",
            ],
        )

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

    def test_native_row_name_index_matches_python_and_falls_back(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_row_name_to_index"
        ):
            self.skipTest("optional Rust alphabetic-row kernel is not built")

        class CustomRowName(str):
            pass

        values = ["", "A", "a", "Z", "AA", "AZ", "BA", "ZZ", "AAA", "ß", "é", "A!", "Z" * 100, CustomRowName("BC")]

        def result(function, value):
            try:
                return ("value", function(value))
            except ValueError:
                return ("ValueError", None)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [result(transfer_row_name_to_index, value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(native_acceleration.worklist_row_name_to_index("AA"), 26)
            self.assertIsNone(native_acceleration.worklist_row_name_to_index("ß"))
            with patch.object(
                native_acceleration,
                "worklist_row_name_to_index",
                wraps=native_acceleration.worklist_row_name_to_index,
            ) as native_call:
                actual = [result(transfer_row_name_to_index, value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)

    def test_native_well_to_position_matches_python_and_falls_back(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_well_to_position"
        ):
            self.skipTest("optional Rust well-coordinate kernel is not built")
        cases = [
            ("A1", 8),
            ("h12", 8),
            ("AA3", 8),
            ("  B5\t", 1),
            ("0", 8),
            ("000", 8),
            ("A1", 0),
            ("H12", -1),
        ]
        for value, rows in cases:
            with self.subTest(value=value, rows=rows):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = well_to_position(value, rows=rows)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    native = native_acceleration.worklist_well_to_position(value, rows)
                    self.assertIsNotNone(native)
                    actual = well_to_position(value, rows=rows)
                self.assertEqual(actual, expected)
                self.assertEqual(native, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.worklist_well_to_position("١", 8))
            self.assertEqual(well_to_position("١"), 1)
            huge_position = "18446744073709551616"
            self.assertIsNone(
                native_acceleration.worklist_well_to_position(huge_position, 8)
            )
            self.assertEqual(well_to_position(huge_position), int(huge_position))
            self.assertIsNone(native_acceleration.worklist_well_to_position("A0", 8))
            with self.assertRaisesRegex(ValueError, "Invalid well/position value"):
                well_to_position("A0")

    def test_native_build_schedule_preserves_python_record_objects(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_build_schedule"
        ):
            self.skipTest("optional Rust worklist-scheduler kernel is not built")
        transfers = [
            Transfer(
                "Source 1", "Plate", 1, "Destination 1", "Plate", 1, 10,
                comment="first line\nsecond line",
            ),
            Transfer(
                "Source 2", "Plate", 2, "Destination 2", "Plate", 2, 20.5,
                wash_after=False,
                break_after=True,
            ),
            Transfer(
                "Source 3", "Plate", 3, "Destination 3", "Plate", 3, 30,
                wash_after=True,
            ),
        ]
        configurations = [("each", None), ("none", 2)]
        for wash_policy, batch_size in configurations:
            with self.subTest(wash_policy=wash_policy, batch_size=batch_size):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = build_worklist(
                        transfers,
                        name="typed-records",
                        wash_policy=wash_policy,
                        batch_size=batch_size,
                    )
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    with patch.object(
                        native_acceleration,
                        "worklist_build_schedule",
                        wraps=native_acceleration.worklist_build_schedule,
                    ) as native_call:
                        actual = build_worklist(
                            transfers,
                            name="typed-records",
                            wash_policy=wash_policy,
                            batch_size=batch_size,
                        )
                native_call.assert_called_once()
                self.assertEqual(actual, expected)
                self.assertEqual(actual.to_text(), expected.to_text())
                self.assertEqual(actual.counts(), expected.counts())
                self.assertEqual(actual.records[0].text, "first line\nsecond line")
                self.assertIsInstance(actual.records[1].volume, int)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            expected = build_worklist(transfers)
            with patch.object(
                native_acceleration,
                "worklist_build_schedule",
                side_effect=AssertionError("one-shot iterables must stay on Python"),
            ) as native_call:
                actual = build_worklist((transfer for transfer in transfers))
            native_call.assert_not_called()
            self.assertEqual(actual, expected)
            with self.assertRaisesRegex(ValueError, "batch_size must be positive"):
                build_worklist(transfers, batch_size=0)

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

    def test_native_optimizer_matches_python_cli_and_verifies_before_write(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_optimize_lines"
        ):
            self.skipTest("optional Rust worklist-optimizer kernel is not built")
        lines = []
        for index, tip in enumerate(("1", "1", "2", "2"), start=1):
            lines.extend(
                [
                    f"A;Source{index};s{index};Plate;{index};t{index};10;Water;;{tip};",
                    f"D;Destination{index};d{index};Plate;{index};d{index};10;Water;;{tip};",
                ]
            )
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "grouped.gwl"
            output = Path(raw) / "optimized.gwl"
            source.write_text("\n".join(lines) + "\n", encoding="utf-8")
            arguments = [
                "optimize",
                str(source),
                "--output",
                str(output),
                "--enabled-tip-mask",
                "3",
                "--supported-channels",
                "2",
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
                    self.assertEqual(main(arguments), 0)
            fallback_text = output.read_bytes()
            fallback_payload = json.loads(fallback_stdout.getvalue())
            self.assertTrue(fallback_payload["changed"])

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
                            "tecan_worklist.cli.optimize_worklist",
                            side_effect=AssertionError("native optimizer fell back to Python"),
                        ) as python_fallback:
                            with patch.object(
                                native_acceleration,
                                "worklist_optimize_lines",
                                wraps=native_acceleration.worklist_optimize_lines,
                            ) as native_call:
                                self.assertEqual(main(arguments), 0)
            native_call.assert_called_once()
            python_fallback.assert_not_called()
            self.assertEqual(
                json.loads(native_stdout.getvalue()), fallback_payload
            )
            self.assertEqual(output.read_bytes(), fallback_text)
            markers = [
                json.loads(line[5:]) for line in marker_output.getvalue().splitlines()
            ]
            self.assertEqual(
                [marker["name"] for marker in markers],
                [
                    "worklist.gwl_parse",
                    "worklist.gwl_optimization",
                    "worklist.gwl_equivalence_verification",
                    "worklist.gwl_write",
                ],
            )
            self.assertTrue(all(marker["metrics"] == {"record_count": 8} for marker in markers))
            self.assertNotIn(str(source), marker_output.getvalue())
            self.assertNotIn(str(output), marker_output.getvalue())

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

    def test_native_window_properties_match_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_window_properties"
        ):
            self.skipTest("optional Rust window classifier is not built")

        independent = []
        for index, tip in ((1, "1"), (2, "2")):
            independent.extend(_transfer(index, tip=tip))
        shared = []
        shared.extend(_transfer(1, tip="1"))
        shared.extend(_transfer(2, tip="2", source=1, destination=2))
        automatic = []
        automatic.extend(_transfer(1, tip=""))
        invalid = list(_transfer(1, tip="3"))
        worklists = [
            Worklist(records=independent),
            Worklist(records=shared),
            Worklist(records=automatic),
            Worklist(records=invalid),
        ]
        units_by_case = []
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            for worklist in worklists:
                units_by_case.append(list(analyze_worklist(worklist).units))

        for units in units_by_case:
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                expected = _window_properties(units)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(
                    native_acceleration,
                    "worklist_window_properties",
                    wraps=native_acceleration.worklist_window_properties,
                ) as native_call:
                    actual = _window_properties(units)
                    self.assertEqual(native_call.call_count, 1)
            self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.worklist_window_properties(
                    [[]], [], [True], ["explicit"], [1]
                )
            )
            self.assertIsNone(
                native_acceleration.worklist_window_properties(
                    [[]], [[]], [True], ["explicit"], [True]
                )
            )

            class UnitLike:
                source_key = ("source",)
                destination_key = ("destination",)
                tip_selection = parse_tip_selection("1")

                @property
                def tip_channel(self):
                    return self.tip_selection.channel

            with patch.object(
                native_acceleration,
                "worklist_window_properties",
                side_effect=AssertionError("unsupported objects must use Python"),
            ):
                self.assertEqual(
                    _window_properties([UnitLike()]),
                    (
                        True,
                        True,
                        (1,),
                        "independent explicit-tip units may be interleaved deterministically",
                    ),
                )

    def test_native_barrier_reasons_match_python_classification(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_barrier_reason"
        ):
            self.skipTest("optional Rust barrier classifier is not built")
        records = [
            Break(),
            Wash(2),
            Flush(("x",)),
            SetDiTiType(("x",)),
            Comment("synthetic marker"),
            RawRecord("Q", "Q;opaque"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [_barrier_reason(record) for record in records]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_barrier_reason",
                wraps=native_acceleration.worklist_barrier_reason,
            ) as native_call:
                actual = [_barrier_reason(record) for record in records]
                self.assertEqual(native_call.call_count, len(records))
            self.assertIsNone(native_acceleration.worklist_barrier_reason("unexpected"))
        self.assertEqual(actual, expected)

    def test_native_tip_diagnostic_matches_python_repr_and_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_tip_diagnostic"
        ):
            self.skipTest("optional Rust tip diagnostic formatter is not built")
        cases = [
            ("3", 1),
            ("not-an-int", 2),
            ("1'2", 3),
            ('1"2', 4),
            ("both'\"quotes", 5),
            ("white\nspace\t", 6),
            ("café", 7),
        ]
        expected = []
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            for raw, line in cases:
                diagnostics = []
                _append_tip_diagnostic(diagnostics, parse_tip_selection(raw), line)
                expected.append(diagnostics)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "worklist_tip_diagnostic",
                wraps=native_acceleration.worklist_tip_diagnostic,
            ) as native_call:
                actual = []
                for raw, line in cases:
                    diagnostics = []
                    _append_tip_diagnostic(diagnostics, parse_tip_selection(raw), line)
                    actual.append(diagnostics)
                self.assertEqual(native_call.call_count, len(cases))
            self.assertIsNone(
                native_acceleration.worklist_tip_diagnostic("café", "code", 1)
            )
            valid = []
            _append_tip_diagnostic(valid, parse_tip_selection("1"), 8)
            self.assertEqual(valid, [])
        self.assertEqual(actual, expected)

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
