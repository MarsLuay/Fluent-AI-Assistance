from __future__ import annotations

import os
import re
import sqlite3
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_reader import common, pattern_library, project_index


_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class SqlIdentifierValidationTests(unittest.TestCase):
    def test_native_identifier_check_matches_python_regex_edge_cases(self) -> None:
        values = (
            "records",
            "_records2",
            "2records",
            "records-with-dash",
            "café",
            "records\n",
            "records\n\n",
            "records\r\n",
        )
        expected = [_IDENTIFIER_PATTERN.match(value) is not None for value in values]

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = [common._sql_identifier_is_valid(value) for value in values]
        self.assertEqual(fallback, expected)

        native_values: list[str] = []
        native_check = native_acceleration.ascii_variable_name_is_valid

        def track_native(value):
            native_values.append(value)
            return native_check(value)

        with (
            patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}),
            patch.object(common, "_native_ascii_variable_name_is_valid", side_effect=track_native),
        ):
            accelerated = [common._sql_identifier_is_valid(value) for value in values]

        self.assertEqual(accelerated, expected)
        self.assertEqual(native_values, [value for value in values if not value.endswith("\n")])

    def test_project_and_pattern_counts_preserve_sqlite_allowlist_and_regex(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE records (value INTEGER)")
        connection.execute('CREATE TABLE "line\n" (value INTEGER)')
        count_functions = (project_index._count, pattern_library._count)

        try:
            for disabled in ("0", "1"):
                with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": disabled}):
                    for count in count_functions:
                        self.assertEqual(count(connection, "records"), 0)
                        # The source regex uses ``$``, which accepts one final newline.
                        self.assertEqual(count(connection, "line\n"), 0)
                        with self.assertRaisesRegex(ValueError, "Invalid table name"):
                            count(connection, "records-with-dash")
                        with self.assertRaisesRegex(
                            ValueError, "Table name not found in schema"
                        ):
                            count(connection, "missing_table")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
