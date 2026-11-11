from __future__ import annotations

import unittest
from unittest.mock import patch

import fluent_pipeline.protocol_ir as protocol_ir_module
import fluent_pipeline.protocol_ir_schema as protocol_ir_schema_module
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

    def test_protocol_identifier_and_filename_components_match_python_fallback(self):
        values = ["__My-Protocol.Name__", "!!!", "Straße", "東京", " \u2003Mixed Name\u00a0 "]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_ids = [protocol_ir_module._safe_id(value) for value in values]
            expected_filenames = [protocol_ir_module._safe_filename(value) for value in values]
            expected_schema_ids = [protocol_ir_schema_module._safe_id(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_safe_component",
                wraps=native_acceleration.ascii_safe_component,
            ) as native_call:
                actual_ids = [protocol_ir_module._safe_id(value) for value in values]
                actual_filenames = [protocol_ir_module._safe_filename(value) for value in values]
                actual_schema_ids = [protocol_ir_schema_module._safe_id(value) for value in values]
                self.assertEqual(native_call.call_count, len(values) * 3)
            self.assertIsNone(native_acceleration.ascii_safe_component("東京", True))
        self.assertEqual(actual_ids, expected_ids)
        self.assertEqual(actual_filenames, expected_filenames)
        self.assertEqual(actual_schema_ids, expected_schema_ids)
        self.assertEqual(actual_ids, ["my-protocol.name", "protocol", "stra_e", "protocol", "mixed_name"])
        self.assertEqual(actual_filenames, ["My-Protocol.Name", "protocol", "Stra_e", "protocol", "Mixed_Name"])
        self.assertEqual(actual_schema_ids, expected_ids)


if __name__ == "__main__":
    unittest.main()
