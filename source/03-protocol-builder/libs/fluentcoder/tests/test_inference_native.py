from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from fluentcoder.catalog import inference


class TaxonomyTokenNativeTests(unittest.TestCase):
    def test_taxonomy_token_slug_matches_python_and_uses_ascii_rust_path(self) -> None:
        values = [
            "Alpha Beta",
            " alpha--Beta ",
            "___",
            "Mixed 42",
            "café",
            "Straße",
            "東京",
            "İ",
        ]

        class CustomString(str):
            def strip(self, chars=None):
                return "Custom Value"

        values.append(CustomString("ignored"))
        expected = [
            re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
            for value in values
        ]

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = [inference._normalize_taxonomy_token(value) for value in values]
        self.assertEqual(fallback, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inference,
                "_native_ascii_profile_slug",
                wraps=native_acceleration.ascii_profile_slug,
            ) as native_call:
                actual = [inference._normalize_taxonomy_token(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inference,
                "_native_ascii_profile_slug",
                return_value=None,
            ):
                unsupported_fallback = [
                    inference._normalize_taxonomy_token(value) for value in values
                ]
        self.assertEqual(unsupported_fallback, expected)


if __name__ == "__main__":
    unittest.main()
