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

    def test_ascii_trimmed_lower_matches_python_for_override_and_taxonomy(self) -> None:
        override_name, category = next(iter(inference._EXACT_CATEGORY_OVERRIDES.items()))
        group = next(iter(inference._FG_TO_CATEGORY))
        major, _, subtype = group.partition(".")
        functional_group = f"\t{major.upper()} \t.{subtype}\n"
        expected_group = functional_group.strip()
        expected_kind = major.lower() if major.lower() in {"carrier", "labware"} else "unknown"
        expected_taxonomy = (
            expected_group,
            expected_kind,
            inference._normalize_taxonomy_token(subtype),
        )

        def check_results() -> None:
            self.assertEqual(inference._trimmed_lower("  MIXED  "), "mixed")
            self.assertEqual(inference._trimmed_lower("  Straße  "), "straße")
            self.assertEqual(
                inference._lookup_builtin_category_override(
                    f"\t{override_name.upper()} \n"
                ),
                category,
            )
            self.assertEqual(
                inference.component_taxonomy(functional_group),
                expected_taxonomy,
            )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            check_results()

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inference,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                check_results()
                self.assertEqual(native_call.call_count, 4)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                inference,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                check_results()


if __name__ == "__main__":
    unittest.main()
