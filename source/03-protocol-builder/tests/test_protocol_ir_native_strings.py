from __future__ import annotations

import unittest
from unittest.mock import patch

import fluent_pipeline.protocol_ir as protocol_ir_module
from tecan_common import native_acceleration


class ProtocolIrNativeStringsTests(unittest.TestCase):
    def test_safe_variable_base_matches_python_with_collision_policy_retained(self):
        cases = [
            ("Tecan Pump", set()),
            ("__Alpha__", set()),
            ("alpha__beta", set()),
            ("123Flow", set()),
            ("Straße", set()),
            ("ſcript", set()),
            ("東京", set()),
            ("...", set()),
            ("Tecan Pump", {"tecan_pump", "tecan_pump_2"}),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_module._safe_var(value, used=used)
                for value, used in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_safe_variable_base",
                wraps=native_acceleration.ascii_safe_variable_base,
            ) as native_call:
                actual = [
                    protocol_ir_module._safe_var(value, used=used)
                    for value, used in cases
                ]
                self.assertEqual(native_call.call_count, len(cases))
            self.assertIsNone(native_acceleration.ascii_safe_variable_base("Straße"))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                "tecan_pump",
                "alpha",
                "alpha__beta",
                "item_123flow",
                "stra_e",
                "cript",
                "item",
                "item",
                "tecan_pump_3",
            ],
        )


if __name__ == "__main__":
    unittest.main()
