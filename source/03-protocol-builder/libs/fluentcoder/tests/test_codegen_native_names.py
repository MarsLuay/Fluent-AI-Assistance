from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from fluentcoder.decompiler import codegen


def _python_reference(label: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "_", label)
    if value and value[0].isdigit():
        value = "_" + value
    if not value:
        value = "lw"
    return value.lower() if value[:1].isupper() else value


class CodegenNativeNameTests(unittest.TestCase):
    def test_variable_name_replacement_matches_python_and_falls_back(self) -> None:
        class CustomString(str):
            pass

        values = [
            "Alpha Beta",
            "a..b",
            "A / B",
            "a-b_9",
            "123Flow",
            "",
            "café",
            "東京",
            CustomString("ignored"),
        ]
        expected = [_python_reference(value) for value in values]

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = [codegen._to_var_name(value) for value in values]
        self.assertEqual(fallback, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                codegen,
                "_native_ascii_replace_non_identifier_chars",
                wraps=native_acceleration.ascii_replace_non_identifier_chars,
            ) as native_call:
                actual = [codegen._to_var_name(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                codegen,
                "_native_ascii_replace_non_identifier_chars",
                return_value=None,
            ):
                unsupported_fallback = [codegen._to_var_name(value) for value in values]
        self.assertEqual(unsupported_fallback, expected)


if __name__ == "__main__":
    unittest.main()
