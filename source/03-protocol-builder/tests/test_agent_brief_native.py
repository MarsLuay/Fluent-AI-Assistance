from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from fluent_pipeline import agent_brief


class AgentBriefNativeTests(unittest.TestCase):
    def test_intent_normalization_uses_ascii_rust_path_and_unicode_fallback(self) -> None:
        intents = [
            "  unrelated\trequest\n",
            "Straße",
            "\u017fIMULATOR",
            "",
            "\u2003\t",
        ]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [agent_brief.resolve_agent_brief_mode(intent) for intent in intents]
        self.assertEqual(expected[2]["mode"], "simulator")

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                agent_brief,
                "_native_ascii_collapse_casefold_whitespace",
                wraps=native_acceleration.ascii_collapse_casefold_whitespace,
            ) as native_call:
                actual = [
                    agent_brief.resolve_agent_brief_mode(intent) for intent in intents
                ]
                self.assertEqual(native_call.call_count, len(intents))
        self.assertEqual(actual, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                agent_brief,
                "_native_ascii_collapse_casefold_whitespace",
                return_value=None,
            ):
                unsupported_fallback = [
                    agent_brief.resolve_agent_brief_mode(intent) for intent in intents
                ]
        self.assertEqual(unsupported_fallback, expected)


if __name__ == "__main__":
    unittest.main()
