from __future__ import annotations

import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import tecan_reader.zeia_adapters as zeia_adapters_module
from tecan_common import native_acceleration
from tecan_reader.zeia_adapters import (
    ZeiaFormatError,
    ingest_zeia,
    probe_zeia,
)


LEGACY_SCRIPT = """<?xml version="1.0"?>
<Root>
  <ObjectName>LegacyScript</ObjectName>
  <Script version="1.0" />
  <FutureField>retained</FutureField>
</Root>
"""

VISIONX_SCRIPT = """<?xml version="1.0"?>
<VxData xmlns="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="3">
  <Payload><ObjectName>VisionXScript</ObjectName><Script version="3.0" /></Payload>
</VxData>
"""


class ZeiaAdapterTests(unittest.TestCase):
    def _archive(self, root: Path, name: str, entries: dict[str, str]) -> Path:
        path = root / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, content in entries.items():
                archive.writestr(entry, content)
        return path

    def test_structurally_different_exports_share_canonical_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = self._archive(root, "legacy.zeia", {"Scripts/main.xscr": LEGACY_SCRIPT})
            visionx = self._archive(root, "visionx.zeia", {"DataStore/UserSpecific/main.xscr": VISIONX_SCRIPT})

            legacy_model = ingest_zeia(legacy)
            visionx_model = ingest_zeia(visionx)

        self.assertEqual(legacy_model.schema_version, visionx_model.schema_version)
        self.assertEqual(legacy_model.adapter_id, "structured-zeia")
        self.assertEqual(visionx_model.adapter_id, "visionx-datastore-v3")
        self.assertEqual(set(legacy_model.scripts[0]), set(visionx_model.scripts[0]))
        self.assertEqual(legacy_model.scripts[0]["kind"], "script")
        self.assertEqual(visionx_model.scripts[0]["kind"], "script")
        self.assertEqual(
            legacy_model.scripts[0]["source_metadata"]["unknown_fields"]["FutureField"],
            ["retained"],
        )
        self.assertEqual(
            legacy_model.scripts[0]["provenance"]["entry_path"],
            "Scripts/main.xscr",
        )

    def test_versioned_visionx_variants_are_selected_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v2 = self._archive(
                root,
                "visionx-v2.zeia",
                {"DataStore/v2.xscr": '<VxData dataStoreVersion="2"><ObjectName>Two</ObjectName><Script /></VxData>'},
            )
            v3 = self._archive(
                root,
                "visionx-v3.zeia",
                {"DataStore/v3.xscr": '<VxData dataStoreVersion="3"><ObjectName>Three</ObjectName><Script /></VxData>'},
            )

            v2_detection = probe_zeia(v2)
            v3_detection = probe_zeia(v3)

        self.assertEqual(v2_detection.status, "supported")
        self.assertEqual(v2_detection.selected.adapter_id, "visionx-datastore-v2")
        self.assertEqual(v3_detection.status, "supported")
        self.assertEqual(v3_detection.selected.adapter_id, "visionx-datastore-v3")

    def test_gwl_probe_uses_native_ascii_match_and_python_unicode_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ascii_archive = self._archive(
                root,
                "ascii-worklist.zeia",
                {"Worklists/records.gwl": "  d1;destination\r\n"},
            )
            unicode_archive = self._archive(
                root,
                "unicode-worklist.zeia",
                {"Worklists/records.gwl": "\u00a0A;source\n"},
            )
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(
                    zeia_adapters_module,
                    "_native_has_gwl_record_prefix",
                    wraps=native_acceleration.has_gwl_record_prefix,
                ) as native_call:
                    ascii_probe = probe_zeia(ascii_archive)
                    unicode_probe = probe_zeia(unicode_archive)
                    self.assertEqual(native_call.call_count, 2)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                ascii_fallback_probe = probe_zeia(ascii_archive)
                unicode_fallback_probe = probe_zeia(unicode_archive)

        self.assertEqual(ascii_probe.status, ascii_fallback_probe.status)
        self.assertEqual(unicode_probe.status, unicode_fallback_probe.status)
        self.assertEqual(ascii_probe.status, "supported")
        self.assertEqual(unicode_probe.status, "supported")
        if native_acceleration.available():
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                pattern = r"^\s*[ADWB](?:[1-4])?;"
                flags = re.IGNORECASE | re.MULTILINE
                for codepoint in range(128):
                    whitespace = chr(codepoint)
                    if whitespace.isspace():
                        candidate = f"{whitespace}A;"
                        self.assertEqual(
                            native_acceleration.has_gwl_record_prefix(candidate),
                            re.search(pattern, candidate, flags) is not None,
                            repr(candidate),
                        )
                for candidate in (
                    "A;source",
                    "d1;destination",
                    "foo\n \tW4;wash",
                    "foo\rD;not-a-line-start",
                    "A5;wrong-digit",
                    "A ;missing-semicolon-position",
                    "B2x;extra-character",
                ):
                    self.assertEqual(
                        native_acceleration.has_gwl_record_prefix(candidate),
                        re.search(pattern, candidate, flags) is not None,
                        repr(candidate),
                    )
                self.assertIsNone(native_acceleration.has_gwl_record_prefix("\u00a0A;source"))

    def test_worklists_are_canonical_entities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(
                Path(tmp),
                "worklist-only.zeia",
                {
                    "Worklists/transfers.gwl": (
                        "A;Source;;Plate Carrier 1;1;;10;Water Free Single\n"
                        "D;Destination;;Plate Carrier 1;1;;10;Water Free Single\n"
                    )
                },
            )
            model = ingest_zeia(archive)

        self.assertEqual(model.adapter_id, "structured-zeia")
        self.assertEqual(len(model.worklists), 1)
        self.assertEqual(model.worklists[0]["kind"], "worklist")
        self.assertEqual(
            model.worklists[0]["provenance"]["entry_path"],
            "Worklists/transfers.gwl",
        )

    def test_unsupported_and_ambiguous_exports_fail_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsupported = self._archive(root, "unknown.zeia", {"README.txt": "not a ZEIA"})
            ambiguous = self._archive(
                root,
                "ambiguous.zeia",
                {
                    "DataStore/v2.xscr": '<VxData dataStoreVersion="2"><ObjectName>Two</ObjectName><Script /></VxData>',
                    "DataStore/v3.xscr": '<VxData dataStoreVersion="3"><ObjectName>Three</ObjectName><Script /></VxData>',
                },
            )

            unsupported_result = probe_zeia(unsupported)
            ambiguous_result = probe_zeia(ambiguous)

            with self.assertRaises(ZeiaFormatError) as error:
                ingest_zeia(ambiguous)

        self.assertEqual(unsupported_result.status, "unsupported")
        self.assertIn("no supported ZEIA structural evidence", unsupported_result.diagnostics[0])
        self.assertEqual(ambiguous_result.status, "ambiguous")
        self.assertEqual(error.exception.result.status, "ambiguous")
        self.assertIn("visionx-datastore-v2", str(error.exception))
        self.assertIn("visionx-datastore-v3", str(error.exception))

    def test_known_malformed_export_is_reported_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(
                Path(tmp),
                "malformed.zeia",
                {"DataStore/UserSpecific/bad.xscr": "<broken"},
            )
            model = ingest_zeia(archive)

        self.assertEqual(model.detection["status"], "supported")
        self.assertEqual(len(model.scripts), 0)
        self.assertEqual(model.errors[0]["entry"], "DataStore/UserSpecific/bad.xscr")
        self.assertIn("ParseError", model.errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
