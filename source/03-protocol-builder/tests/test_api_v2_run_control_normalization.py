from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.api_v2.context_enrichment as context_enrichment
import fluent_pipeline.api_v2.run_control as run_control
from tecan_common import native_acceleration
from fluent_pipeline.api_v2.run_control import (
    FileOperatorAckSource,
    RunControlOptions,
    operator_ack_source_from_options,
    run_control_options_from_env,
)


class RunControlNormalizationTests(unittest.TestCase):
    def test_context_enrichment_reuses_run_control_environment_normalization(self) -> None:
        for disabled, expected in (("0", True), ("1", True), ("0", False)):
            raw = "Yeſ" if expected else "enabled"
            with self.subTest(disabled=disabled, raw=raw):
                env = {
                    "TECAN_RESUME_RUN_AFTER_PROMPTS": raw,
                    "TECAN_DISABLE_RUST_ACCELERATION": disabled,
                }
                with patch.dict(os.environ, env, clear=True):
                    report = context_enrichment.merge_api_v2_context_into_report(
                        {}, host="localhost", port=1
                    )
                resume = report["details"]["api_v2"]["resume_run"]
                self.assertEqual(resume["resume_run_after_prompts"], expected)

    def test_resume_environment_flag_native_and_fallback_parity(self) -> None:
        cases = [
            ("yes", True),
            (" TRUE ", True),
            ("1", True),
            ("yeſ", True),
            ("\u2003YES\u00a0", True),
            ("enabled", False),
            ("", False),
        ]
        for disabled in ("0", "1"):
            for raw, expected in cases:
                with self.subTest(disabled=disabled, raw=raw):
                    env = {
                        "TECAN_RESUME_RUN_AFTER_PROMPTS": raw,
                        "TECAN_DISABLE_RUST_ACCELERATION": disabled,
                    }
                    with patch.dict(os.environ, env, clear=True):
                        if disabled == "0":
                            with patch.object(
                                run_control,
                                "_native_ascii_casefold_trim",
                                wraps=native_acceleration.ascii_casefold_trim,
                            ) as native_call:
                                actual = run_control_options_from_env().resume_run_after_prompts
                                self.assertEqual(native_call.call_count, 1)
                        else:
                            actual = run_control_options_from_env().resume_run_after_prompts
                    self.assertEqual(actual, expected)

                    with patch.dict(os.environ, env, clear=True):
                        with patch.object(
                            run_control,
                            "_native_ascii_casefold_trim",
                            return_value=None,
                        ):
                            forced_fallback = run_control_options_from_env().resume_run_after_prompts
                    self.assertEqual(forced_fallback, expected)

    def test_callable_and_file_acknowledgements_keep_python_token_semantics(self) -> None:
        callable_cases = [(" ACK ", True), ("Reſume", True), ("no", False), ("", False)]
        file_cases = [
            ("ACK", True),
            ("Reſume", True),
            ("   ", True),
            ("invalid", False),
        ]

        for disabled in ("0", "1"):
            for raw, expected in callable_cases:
                with self.subTest(source="callable", disabled=disabled, raw=raw):
                    env = {
                        "TECAN_OPERATOR_ACK": raw,
                        "TECAN_DISABLE_RUST_ACCELERATION": disabled,
                    }
                    with patch.dict(os.environ, env, clear=True):
                        source = operator_ack_source_from_options(RunControlOptions())
                        actual = source.wait_for_ack(0.015, poll_interval_seconds=0.001)
                    self.assertEqual(actual, expected)

            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "operator-ack.txt"
                source = FileOperatorAckSource(path)
                for raw, expected in file_cases:
                    with self.subTest(source="file", disabled=disabled, raw=raw):
                        path.write_text(raw, encoding="utf-8")
                        with patch.dict(
                            os.environ,
                            {"TECAN_DISABLE_RUST_ACCELERATION": disabled},
                            clear=True,
                        ):
                            actual = source.wait_for_ack(
                                0.015, poll_interval_seconds=0.001
                            )
                        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
