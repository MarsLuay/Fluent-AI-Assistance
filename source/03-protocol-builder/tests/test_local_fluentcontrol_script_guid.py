"""Local FluentControl name+folder GUID lookup for import-safe packaging."""

from __future__ import annotations

import unittest
from pathlib import Path

from fluent_pipeline.exports import _find_local_fluentcontrol_script_guid


def _write_script(root: Path, guid: str, name: str, folder: str) -> None:
    (root / f"{guid}.xscr").write_text(
        (
            '<?xml version="1.0"?>\n'
            "<VxData>\n"
            "  <Payload>\n"
            f"    <ObjectName>{name}</ObjectName>\n"
            f"    <ObjectSubfolderPath>{folder}</ObjectSubfolderPath>\n"
            "    <Checksum>00</Checksum>\n"
            "  </Payload>\n"
            "</VxData>\n"
        ),
        encoding="utf-8",
    )


class LocalFluentControlScriptGuidTests(unittest.TestCase):
    def test_matches_name_and_folder(self) -> None:
        import tempfile

        tmp = tempfile.TemporaryDirectory(prefix="fc_userspecific_")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _write_script(root, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "DemoScript", "Demo scripts")
        _write_script(root, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "DemoScript", "Demo")

        self.assertEqual(
            _find_local_fluentcontrol_script_guid(
                "DemoScript",
                "Demo scripts",
                userspecific_dir=root,
            ),
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        self.assertIsNone(
            _find_local_fluentcontrol_script_guid(
                "DemoScript",
                "Other",
                userspecific_dir=root,
            )
        )

    def test_ambiguous_same_name_folder_returns_none(self) -> None:
        import tempfile

        tmp = tempfile.TemporaryDirectory(prefix="fc_userspecific_")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _write_script(root, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "DemoScript", "Demo scripts")
        _write_script(root, "cccccccc-cccc-cccc-cccc-cccccccccccc", "DemoScript", "Demo scripts")

        self.assertIsNone(
            _find_local_fluentcontrol_script_guid(
                "DemoScript",
                "Demo scripts",
                userspecific_dir=root,
            )
        )


if __name__ == "__main__":
    unittest.main()
