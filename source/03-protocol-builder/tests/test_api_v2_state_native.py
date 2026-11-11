from __future__ import annotations

import unittest
from unittest.mock import patch

import fluent_pipeline.api_v2.state as state_module
from fluent_pipeline.api_v2.types import StateMachineStates
from tecan_common import native_acceleration


class ApiV2StateNativeTests(unittest.TestCase):
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
