"""Tests for extract_fluent_textures.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tecan_tools import extract_fluent_textures as textures

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_READY = PROJECT_ROOT / "source/03-protocol-builder/ready-to-import"
EXTRACTED_ROOT = next(
    iter(
        sorted(
            path
            for path in _READY.glob("*/temp_files/extracted/DataStore")
            if path.is_dir()
        )
    ),
    _READY / "_missing" / "temp_files" / "extracted" / "DataStore",
)
HOST_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
SAMPLE_XTX = (
    EXTRACTED_ROOT / "SystemSpecific/Worktable/Textures/e500c9d7-322e-4d32-8e25-baeaeff42d1f.xtx"
)


class ExtractFluentTexturesTests(unittest.TestCase):
    def test_parse_xtx_barcode_plate(self) -> None:
        if not SAMPLE_XTX.exists():
            self.skipTest("sample xtx fixture not available")
        decoded = textures.parse_xtx_file(SAMPLE_XTX, "zeia")
        self.assertEqual(decoded.texture_guid, "e500c9d7-322e-4d32-8e25-baeaeff42d1f")
        self.assertEqual(decoded.object_name, "Barcodeplate_Top")
        self.assertEqual(decoded.image_format, "jpeg")
        self.assertGreater(len(decoded.image_bytes), 1000)
        self.assertTrue(decoded.priority)

    def test_detect_image_format(self) -> None:
        self.assertEqual(textures.detect_image_format(b"\xff\xd8\xff\x00"), "jpeg")
        self.assertEqual(textures.detect_image_format(b"\x89PNG\r\n\x1a\n"), "png")

    def test_extract_textures_from_host_or_fixture(self) -> None:
        install = HOST_INSTALL if (HOST_INSTALL / "SystemSpecific/Worktable/Textures").exists() else EXTRACTED_ROOT
        if not (install / "SystemSpecific/Worktable/Textures").exists():
            self.skipTest("texture fixtures not available")
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            report = textures.extract_textures(
                install_path=install,
                out_dir=out_dir,
                registry_path=None,
                priority_only=False,
                attach_glbs=False,
                beside_glbs=False,
                overwrite=True,
            )
            self.assertEqual(report["kind"], textures.TEXTURE_MANIFEST_KIND)
            self.assertGreaterEqual(report["summary"]["decodedCount"], 1)
            manifest_path = out_dir / "textures/manifest.json"
            self.assertTrue(manifest_path.exists())
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            names = {row["objectName"] for row in payload["textures"]}
            self.assertTrue(names & {"Barcodeplate_Top", "ReferencePlate_top"})


if __name__ == "__main__":
    unittest.main()
