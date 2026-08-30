import tempfile
import unittest
from pathlib import Path

from tecan_worklist.gwl import parse_gwl, parse_gwl_lines
from tecan_worklist.transfer import (
    build_worklist,
    load_transfers,
    validate_transfers,
    well_to_position,
)

ROOT = Path(__file__).resolve().parents[1]


class WorklistGeneratorTests(unittest.TestCase):
    def test_well_to_position_96_well_column_major(self) -> None:
        self.assertEqual(well_to_position("A1"), 1)
        self.assertEqual(well_to_position("B1"), 2)
        self.assertEqual(well_to_position("H1"), 8)
        self.assertEqual(well_to_position("A2"), 9)
        self.assertEqual(well_to_position("H12"), 96)

    def test_simple_transfer_builds_expected_records(self) -> None:
        transfers = load_transfers(ROOT / "examples" / "simple_transfer.csv")
        result = validate_transfers(transfers)
        self.assertTrue(result.ok)
        worklist = build_worklist(transfers)
        text = worklist.to_text()
        self.assertIn("C;Simple 4-well transfer\n", text)
        self.assertIn("A;SourcePlate;;96 Well Flat;1;;20;Water Free Single;;;\n", text)
        self.assertIn("D;DestPlate;;96 Well Flat;1;;20;Water Free Single;;;\n", text)
        self.assertEqual(worklist.counts()["A"], 4)
        self.assertEqual(worklist.counts()["D"], 4)
        self.assertEqual(worklist.counts()["W"], 4)
        self.assertEqual(worklist.counts()["B"], 1)

    def test_roundtrip_parse(self) -> None:
        transfers = load_transfers(ROOT / "examples" / "simple_transfer.csv")
        worklist = build_worklist(transfers)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.gwl"
            worklist.write(path)
            parsed = parse_gwl(path)
        self.assertEqual(parsed.counts(), worklist.counts())

    def test_permissive_parse_preserves_unknown_records(self) -> None:
        worklist = parse_gwl_lines(
            [
                "A;Source;;96 Well Flat;1;;20;Water Free Single;;;",
                "W5;",
                "X;unsupported;field",
            ],
            name="sample",
            permissive=True,
        )

        self.assertEqual(worklist.counts()["A"], 1)
        self.assertEqual(worklist.counts()["W5"], 1)
        self.assertEqual(worklist.counts()["X"], 1)
        self.assertEqual(
            worklist.to_text(),
            "A;Source;;96 Well Flat;1;;20;Water Free Single;;;\nW5;\nX;unsupported;field\n",
        )

    def test_row_to_transfer_invalid_position(self) -> None:
        from tecan_worklist.transfer import _row_to_transfer

        row = {
            "source_position": "invalid",
            "dest_position": "A1",
            "volume_ul": "10",
        }
        with self.assertRaisesRegex(
            ValueError, "CSV row 2: Invalid well/position value: 'invalid'"
        ):
            _row_to_transfer(row, row_number=2, well_rows=8)

    def test_row_to_transfer_invalid_volume(self) -> None:
        from tecan_worklist.transfer import _row_to_transfer

        row = {
            "source_position": "A1",
            "dest_position": "A2",
            "volume_ul": "not_a_number",
        }
        with self.assertRaisesRegex(
            ValueError, "CSV row 3: volume_ul must be numeric."
        ):
            _row_to_transfer(row, row_number=3, well_rows=8)


if __name__ == "__main__":
    unittest.main()
