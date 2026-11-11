from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import fluent_pipeline.api_v2.runtime_session as runtime_session
from tecan_common import native_acceleration


class RuntimeSessionIdentityTests(unittest.TestCase):
    def test_inventory_lookup_uses_native_ascii_and_unicode_fallback(self) -> None:
        runnable = ["MethodA", "Straße"]
        maintenance = ["Maintenance"]
        methods = ["methoda", "STRAẞE", "maintenance", "Missing"]
        expected = [[], [], [], [
            "Method 'Missing' was not reported by GetAllRunnableMethods() or GetAllMaintenanceMethods()."
        ]]

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [
                runtime_session.validate_method_in_inventory(
                    method,
                    runnable_methods=runnable,
                    maintenance_methods=maintenance,
                )
                for method in methods
            ]
        self.assertEqual(disabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                runtime_session,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                enabled = [
                    runtime_session.validate_method_in_inventory(
                        method,
                        runnable_methods=runnable,
                        maintenance_methods=maintenance,
                    )
                    for method in methods
                ]
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(enabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                runtime_session,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback = [
                    runtime_session.validate_method_in_inventory(
                        method,
                        runnable_methods=runnable,
                        maintenance_methods=maintenance,
                    )
                    for method in methods
                ]
        self.assertEqual(fallback, expected)

    def test_method_inventory_uses_native_ascii_and_python_unicode_fallback(self) -> None:
        runnable = [" MethodA ", "Café", "Straße", "Runner", "methoda", ""]
        maintenance = ["methoda", "CAFÉ", "STRASSE", "Maintenance", "Straße", ""]
        expected = {
            "runnable_count": 5,
            "maintenance_count": 5,
            "assay_runnable": ["Runner"],
            "maintenance_methods": [
                "methoda", "CAFÉ", "STRASSE", "Maintenance", "Straße"
            ],
            "maintenance_only": ["Maintenance"],
        }

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = runtime_session.partition_method_inventory(runnable, maintenance)
        self.assertEqual(disabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                runtime_session,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                enabled = runtime_session.partition_method_inventory(runnable, maintenance)
                self.assertEqual(native_call.call_count, 10)
        self.assertEqual(enabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                runtime_session,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback = runtime_session.partition_method_inventory(runnable, maintenance)
        self.assertEqual(fallback, expected)

    def test_identity_match_uses_native_ascii_and_python_unicode_fallback(self) -> None:
        cases = [
            (" User@example.test ", "user@EXAMPLE.TEST", True),
            ("Straße", "STRAẞE", True),
            ("Straße", "STRASSE", True),
            ("User", "Other", False),
            ("", "", False),
            (None, "user", False),
        ]
        expected = [matched for _, _, matched in cases]

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [
                runtime_session.record_session_identity(
                    configured_username=configured,
                    observed_username=observed,
                )
                for configured, observed, _ in cases
            ]
        self.assertEqual(
            [record["login_verified"] for record in disabled], expected
        )
        self.assertEqual(disabled[0]["configured_username"], "User@example.test")
        self.assertEqual(disabled[0]["current_username"], "user@EXAMPLE.TEST")

        native_eligible_count = sum(
            bool(str(configured or "").strip() and str(observed or "").strip())
            for configured, observed, _ in cases
        )
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                runtime_session,
                "_native_ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_call:
                enabled = [
                    runtime_session.record_session_identity(
                        configured_username=configured,
                        observed_username=observed,
                    )
                    for configured, observed, _ in cases
                ]
                self.assertEqual(native_call.call_count, native_eligible_count)
        self.assertEqual([record["login_verified"] for record in enabled], expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                runtime_session,
                "_native_ascii_casefold_equals",
                return_value=None,
            ):
                fallback = [
                    runtime_session.record_session_identity(
                        configured_username=configured,
                        observed_username=observed,
                    )
                    for configured, observed, _ in cases
                ]
        self.assertEqual([record["login_verified"] for record in fallback], expected)


if __name__ == "__main__":
    unittest.main()
