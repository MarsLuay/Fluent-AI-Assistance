from __future__ import annotations

import os
import random
import re
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from fluentcoder.expressions import catalog


_VERSION_PATTERN = re.compile(r"\b\d+(?:\.\d+)*\b")


def _python_target_version(value: object) -> str | None:
    text = str(value or "").strip()
    match = _VERSION_PATTERN.search(text)
    if match is None:
        return None
    candidate = match.group(0)
    try:
        catalog._parse_version(candidate)
    except catalog.CatalogValidationError:
        return None
    return candidate


class CatalogVersionNativeTests(unittest.TestCase):
    def test_version_extraction_matches_python_regex_on_ascii_and_unicode(self) -> None:
        randomizer = random.Random(0xCA7A10)
        alphabet = "abcXYZ_0123456789.+- /()"
        values = [
            "release 2.3",
            "!1.2beta",
            "version1.2",
            "1.2.3x",
            "a_2.3",
            "-12.4.5; later 7",
            "x1.2 y3",
            "1..2",
            "Version ٢.3",
            "Straße 4.5",
            "é1.2",
        ]
        values.extend(
            "".join(randomizer.choice(alphabet) for _ in range(randomizer.randrange(40)))
            for _ in range(256)
        )
        expected = [_python_target_version(value) for value in values]

        if native_acceleration.available():
            with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                for value in values:
                    text = str(value or "").strip()
                    if not text.isascii():
                        continue
                    match = _VERSION_PATTERN.search(text)
                    native_expected = match.group(0) if match is not None else None
                    self.assertEqual(
                        native_acceleration.ascii_first_version_token(text),
                        native_expected,
                        text,
                    )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = [catalog._target_version(value) for value in values]
        self.assertEqual(fallback, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                catalog,
                "_native_ascii_first_version_token",
                wraps=native_acceleration.ascii_first_version_token,
            ) as native_call:
                actual = [catalog._target_version(value) for value in values]
                self.assertEqual(
                    native_call.call_count,
                    sum(bool(str(value or "").strip()) for value in values),
                )
        self.assertEqual(actual, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                catalog,
                "_native_ascii_first_version_token",
                return_value=None,
            ):
                unsupported_fallback = [catalog._target_version(value) for value in values]
        self.assertEqual(unsupported_fallback, expected)


if __name__ == "__main__":
    unittest.main()
