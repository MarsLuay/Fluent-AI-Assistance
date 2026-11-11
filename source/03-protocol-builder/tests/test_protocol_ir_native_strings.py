from __future__ import annotations

import unittest
from unittest.mock import patch

import fluent_pipeline.protocol_ir as protocol_ir_module
import fluent_pipeline.protocol_ir_compat as protocol_ir_compat_module
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

    def test_bracket_variable_extraction_uses_shared_rust_scanner(self):
        values = [
            "[Alpha] [beta_2] [Alpha] [] [_x] [9bad] [with-dash] [[nested]] [é] [Aé]",
            "before [name9] after",
            "unicode [é] [Aé] [okay_1]",
            "",
            None,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_module._bracket_variable_names(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "bracket_variable_names",
                wraps=native_acceleration.bracket_variable_names,
            ) as native_call:
                actual = [
                    protocol_ir_module._bracket_variable_names(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], {"Alpha", "beta_2", "_x", "nested"})
        self.assertEqual(actual[1], {"name9"})
        self.assertEqual(actual[2], {"okay_1"})
        self.assertEqual(actual[3:], [set(), set()])

    def test_variable_name_validation_uses_native_ascii_with_python_fallback(self):
        values = ["Name_2", "_x", "\u001cAlpha9\u001f", "2name", "A-B", "", "é", "ſcript"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_module._valid_variable_name(value) for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_variable_name_is_valid",
                wraps=native_acceleration.ascii_variable_name_is_valid,
            ) as native_call:
                actual = [
                    protocol_ir_module._valid_variable_name(value) for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(
                native_acceleration.ascii_variable_name_is_valid("ſcript")
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [True, True, True, False, False, False, False, False])

    def test_schema_fc_variable_name_validation_uses_native_ascii(self):
        values = [
            "Name_2",
            "_x",
            "9bad",
            "A-B",
            "",
            "Name\n",
            "é",
            "ſcript",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_schema_module._is_fc_variable_name(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_variable_name_is_valid",
                wraps=native_acceleration.ascii_variable_name_is_valid,
            ) as native_call:
                actual = [
                    protocol_ir_schema_module._is_fc_variable_name(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(
                native_acceleration.ascii_variable_name_is_valid("ſcript")
            )
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [True, True, False, False, False, False, False, False],
        )

    def test_setup_group_name_whitespace_normalization_uses_native_ascii(self):
        values = [
            "  Operator   Setup  ",
            "Instrument\tInitialization Setup",
            "setup current",
            "Set up",
            "Straße Setup",
            "Opérator init",
            "\u001cOperator\t\nSetup\u001f",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_compat_module.is_setup_group_name(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module,
                "_native_ascii_collapse_casefold_whitespace",
                wraps=native_acceleration.ascii_collapse_casefold_whitespace,
            ) as native_call:
                actual = [
                    protocol_ir_compat_module.is_setup_group_name(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(
                native_acceleration.ascii_collapse_casefold_whitespace(
                    "Opérator init"
                )
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [True, True, True, False, True, False, True])

    def test_rup_display_type_alias_normalization_uses_native_compaction(self):
        cases = [
            ("Combo Box", False),
            ("RADIO-BUTTON", False),
            ("Straße", True),
            ("東京", False),
            ("TextboxWithNumpad", False),
            ("not-a-kind", False),
            ("not-a-kind", True),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_module._rup_variable_display_type(
                    value, has_allowed_values=has_allowed_values
                )
                for value, has_allowed_values in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_lower_alnum_compact",
                wraps=native_acceleration.ascii_lower_alnum_compact,
            ) as native_call:
                actual = [
                    protocol_ir_module._rup_variable_display_type(
                        value, has_allowed_values=has_allowed_values
                    )
                    for value, has_allowed_values in cases
                ]
                self.assertEqual(native_call.call_count, len(cases) - 1)
            self.assertIsNone(native_acceleration.ascii_lower_alnum_compact("東京"))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            ["Combobox", "RadioButton", "Combobox", "Empty", "TextboxWithNumpad", "Empty", "Combobox"],
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
