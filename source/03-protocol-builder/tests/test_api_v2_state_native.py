from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import fluent_pipeline.api_v2.state as state_module
from fluent_pipeline.api_v2.types import StateMachineStates
from tecan_common import native_acceleration


class ApiV2StateNativeTests(unittest.TestCase):
    def test_state_label_casefold_uses_native_ascii_with_unicode_fallback(self):
        values = [" Ready ", "EDITMODE", "ſtartup", "Unrecognized"]
        expected = [value.strip().casefold() for value in values]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [state_module._casefold_state_text(value) for value in values]
        self.assertEqual(disabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                state_module,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                enabled = [state_module._casefold_state_text(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(enabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                state_module,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback = [state_module._casefold_state_text(value) for value in values]
        self.assertEqual(fallback, expected)
        self.assertIs(
            state_module.parse_state_machine_states("ſtartup"),
            StateMachineStates.STARTUP,
        )

    def test_compaction_uses_native_ascii_and_preserves_casefold_fallback(self):
        values = ["R U N! 9", "ſcript", "İD", "東京", ""]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [state_module._compact(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                state_module,
                "_native_ascii_lower_alnum_compact",
                wraps=native_acceleration.ascii_lower_alnum_compact,
            ) as native_call:
                actual = [state_module._compact(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(native_acceleration.ascii_lower_alnum_compact("i\u0307d"))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["run9", "script", "id", "", ""])

    def test_enum_state_label_comparison_uses_rust_with_unicode_fallback(self):
        values = ["READY", "ſtartup", "İD", "Unknown"]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [state_module.parse_state_machine_states(value) for value in values]

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                state_module,
                "_native_ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_call:
                actual = [state_module.parse_state_machine_states(value) for value in values]
                self.assertGreater(native_call.call_count, 0)
                native_call.assert_any_call("ready", "Ready")
                self.assertIsNone(native_acceleration.ascii_casefold_equals("i\u0307d", "Ready"))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                StateMachineStates.READY,
                StateMachineStates.STARTUP,
                StateMachineStates.UNKNOWN,
                StateMachineStates.UNKNOWN,
            ],
        )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                state_module, "_native_ascii_casefold_equals", return_value=None
            ):
                forced_fallback = [
                    state_module.parse_state_machine_states(value) for value in values
                ]
        self.assertEqual(forced_fallback, expected)

    def test_state_alias_mapping_remains_python_policy(self):
        self.assertIs(
            state_module.parse_state_machine_states("ready-mode"),
            StateMachineStates.READY,
        )
        self.assertIs(
            state_module.parse_state_machine_states("error mode"),
            StateMachineStates.ERROR,
        )
        self.assertIs(
            state_module.parse_state_machine_states("unrecognized state"),
            StateMachineStates.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
