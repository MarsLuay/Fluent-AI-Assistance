from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_reader import pattern_library


class PatternSummaryHelperTests(unittest.TestCase):
    def test_join_summary_uses_rust_for_supported_ascii_and_falls_back(self) -> None:
        parts = (" \tAspirate\x1c", "", "Water\x1f ")
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = pattern_library._join_summary(*parts)
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                pattern_library,
                "_native_pattern_join_summary",
                wraps=native_acceleration.pattern_join_summary,
            ) as native_call:
                actual = pattern_library._join_summary(*parts)
                native_call.assert_called_once_with(parts)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "Aspirate Water")
        if native_acceleration.available() and hasattr(
            native_acceleration._native, "pattern_join_summary"
        ):
            with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertEqual(
                    native_acceleration.pattern_join_summary(parts), expected
                )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(pattern_library._join_summary(), "Command")
            self.assertIsNone(native_acceleration.pattern_join_summary((" café ",)))
            self.assertEqual(pattern_library._join_summary(" café "), "café")
            self.assertIsNone(
                native_acceleration.pattern_join_summary((chr(0xD800),))
            )

    def test_liquid_step_uses_rust_for_supported_ascii_and_falls_back(self) -> None:
        values = ("Aspirate", "Plate A", "10", "Water")
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = pattern_library._liquid_step(*values)
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                pattern_library,
                "_native_pattern_liquid_step",
                wraps=native_acceleration.pattern_liquid_step,
            ) as native_call:
                actual = pattern_library._liquid_step(*values)
                native_call.assert_called_once_with(*values)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "Aspirate 10 at Plate A using Water")
        if native_acceleration.available() and hasattr(
            native_acceleration._native, "pattern_liquid_step"
        ):
            with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertEqual(
                    native_acceleration.pattern_liquid_step(*values), expected
                )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                pattern_library._liquid_step("Aspirate", "Café", "10", "Water"),
                "Aspirate 10 at Café using Water",
            )
            self.assertIsNone(
                native_acceleration.pattern_liquid_step(
                    "Aspirate", "Café", "10", "Water"
                )
            )
            self.assertEqual(
                pattern_library._liquid_step("Aspirate", "Plate", 0, "Water"),
                "Aspirate at Plate using Water",
            )


if __name__ == "__main__":
    unittest.main()
