from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from fluentcoder.catalog import database as database_module


_XML = (
    '<Object Type="Example.AlphaDataV1" />'
    '<Object Type="Example.CommandDataV1" />'
    '<Object Type="Example.ScriptCommandDataV2" />'
    '<Object Type="Example.ScriptGroupDataV1" />'
    '<Object Type="Example.ScriptDataV1" />'
    '<Object Type="Example.BrokenDataV" />'
    '<Object Type="Example.UnicodeDataV١" />'
    '<Object Type="Example.Plain" />'
)
_EXPECTED = ["Alpha", "Command", "", "BrokenDataV", "Unicode", "Plain"]


class CatalogDatabaseNativeTests(unittest.TestCase):
    def _extract(self) -> list[str]:
        database = object.__new__(database_module.TecanDatabase)
        return database.extract_command_ids_from_xscr(_XML)

    def test_command_id_extraction_matches_with_enabled_disabled_and_fallback(self) -> None:
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            self.assertEqual(self._extract(), _EXPECTED)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                database_module,
                "_native_command_short_name",
                wraps=native_acceleration.command_short_name,
            ) as native_call:
                self.assertEqual(self._extract(), _EXPECTED)
                self.assertEqual(native_call.call_count, 8)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                database_module,
                "_native_command_short_name",
                return_value=None,
            ):
                self.assertEqual(self._extract(), _EXPECTED)


if __name__ == "__main__":
    unittest.main()
