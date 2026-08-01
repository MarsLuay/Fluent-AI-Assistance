import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluent_pipeline.instrument_config import (
    INSTRUMENT_CONFIG_DIR_ENV,
    infer_expected_host_config,
    inspect_host_instrument_configs,
    list_installed_config_names,
    render_host_instrument_config_markdown,
)


class InstrumentConfigTests(unittest.TestCase):
    def test_lists_config_file_stems_from_env_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "780_liqFCA_MCA_RGA.config").write_text("", encoding="utf-8")
            (root / "notes.txt").write_text("", encoding="utf-8")

            with mock.patch.dict("os.environ", {INSTRUMENT_CONFIG_DIR_ENV: str(root)}):
                self.assertEqual(list_installed_config_names(), ["780_liqFCA_MCA_RGA"])

    def test_infers_rga_hint_and_matches_installed_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "480_liqFCA_RGA.config").write_text("", encoding="utf-8")
            (root / "480_airFCA_FluentID.config").write_text("", encoding="utf-8")
            expected = infer_expected_host_config(
                intent="Create an RGA/A200 tube verification method",
                source_manifest={},
                selected_source_scripts=[],
            )

            report = inspect_host_instrument_configs(expected, config_dir=root)
            markdown = render_host_instrument_config_markdown(report)

            self.assertEqual(expected["patterns"], ["RGA"])
            self.assertEqual(report["status"], "matched")
            self.assertEqual(report["matches"], ["480_liqFCA_RGA"])
            self.assertIn("configuration dropdown", report["user_instruction"])
            self.assertIn("480_liqFCA_RGA", markdown)

    def test_missing_expected_match_needs_review_without_blocking_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "480_airFCA_FluentID.config").write_text("", encoding="utf-8")

            report = inspect_host_instrument_configs(
                {"patterns": ["RGA"], "required": False},
                config_dir=root,
            )

            self.assertEqual(report["status"], "needs_review")
            self.assertIn("switch via the FluentControl/VisionX configuration dropdown", report["user_instruction"])
            self.assertIn("480_airFCA_FluentID", report["user_instruction"])


if __name__ == "__main__":
    unittest.main()
