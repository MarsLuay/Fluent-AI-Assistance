from __future__ import annotations

import unittest
from unittest.mock import patch

import fluent_pipeline.api_v2.run_control as run_control_module
import fluent_pipeline.api_v2.state as state_module
from tecan_common import native_acceleration


class ApiV2RunControlNativeTests(unittest.TestCase):
    def test_open_method_status_guard_reuses_rust_backed_state_compaction(self):
        values = ["READY", "ready-mode", "paused", "Startup", "mysterious"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [run_control_module._status_indicates_open_method(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                state_module,
                "_native_ascii_lower_alnum_compact",
                wraps=native_acceleration.ascii_lower_alnum_compact,
            ) as native_call:
                actual = [run_control_module._status_indicates_open_method(value) for value in values]
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [True, True, True, False, False])


if __name__ == "__main__":
    unittest.main()
