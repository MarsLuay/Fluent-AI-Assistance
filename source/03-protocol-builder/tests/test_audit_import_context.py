from unittest import TestCase
from unittest.mock import patch

import fluent_pipeline.audit_import_context as audit_import_context
from tecan_common import native_acceleration


class PrimaryScriptSelectionTests(TestCase):
    def test_ascii_marker_scan_uses_rust_and_preserves_python_fallback(self):
        cases = [
            ([" MAIN SCRIPT.XSCR; ", "miscellaneous"], "MAIN SCRIPT.XSCR"),
            (["ſcript", "example METHOD"], "example METHOD"),
            (["script", "method"], ""),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [audit_import_context._primary_script(items) for items, _ in cases]
        self.assertEqual(expected, [value for _, value in cases])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                audit_import_context,
                "_native_ascii_substring_match_groups",
                wraps=native_acceleration.ascii_case_insensitive_substring_match_groups,
            ) as native_call:
                actual = [audit_import_context._primary_script(items) for items, _ in cases]
                self.assertEqual(native_call.call_count, 6)
            self.assertIsNone(
                native_acceleration.ascii_case_insensitive_substring_match_groups(
                    ["ſcript"], [("script", "corpus", "verification", "method")]
                )
            )
        self.assertEqual(actual, expected)
