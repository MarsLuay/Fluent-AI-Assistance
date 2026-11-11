from __future__ import annotations

import unittest
from unittest.mock import patch

from fluent_pipeline.gates import worktable
from fluent_pipeline.gates.models import ValidationContext
from tecan_common import native_acceleration


class WorktableGateNativeTests(unittest.TestCase):
    def test_tip_box_evaluator_preserves_missing_status_policy(self):
        context = ValidationContext(
            make_gate=lambda gate_id, status, message, details=None: {
                "gate_id": gate_id,
                "status": status,
                "message": message,
                "details": details,
            },
            worktable_diff={
                "required_tip_boxes": [
                    {"status": " Missing ", "name": "synthetic tip box"}
                ]
            },
        )

        result = worktable.evaluate_tip_boxes(context)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["details"],
            {"tip_boxes": [{"status": " Missing ", "name": "synthetic tip box"}]},
        )

    def test_status_normalization_uses_native_ascii_and_python_fallback(self):
        values = [
            {"status": " MISSING "},
            {"status": " UnVerified "},
            {"status": "Missing"},
            {"status": "Straße"},
            {"status": "\u2003Missing\u2003"},
            {"status": None},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable._norm_status(value) for value in values]

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                worktable,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [worktable._norm_status(value) for value in values]
                self.assertEqual(native_call.call_count, 3)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual, ["missing", "unverified", "missing", "strasse", "missing", ""]
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                worktable,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback = [worktable._norm_status(value) for value in values]
        self.assertEqual(fallback, expected)


if __name__ == "__main__":
    unittest.main()
