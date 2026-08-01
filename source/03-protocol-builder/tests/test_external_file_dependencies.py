import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fluent_pipeline.external_file_dependencies import (
    audit_external_file_dependencies,
    default_external_file_search_roots,
    render_external_file_dependencies_markdown,
    stage_found_external_files,
    write_external_file_install_bat,
)


class ExternalFileDependencyTests(unittest.TestCase):
    def test_extracts_tubeye_path_from_xscr(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "script.xscr"
            xscr.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <FileReference><File>Z:\\TubeEye\\bin\\TEyeClient.exe</File></FileReference>
  <Application>"Z:\\TubeEye\\bin\\TEyeClient.exe"</Application>
</VxData>
""",
                encoding="utf-8",
            )
            docs = Path(tmp) / "Documents"
            docs.mkdir()
            candidate = docs / "TEyeClient.exe"
            candidate.write_bytes(b"MZ")

            report = audit_external_file_dependencies(
                compiled_xscr=xscr,
                search_roots=[docs.resolve()],
            )
            teye = next(item for item in report["entries"] if "TEyeClient" in item["expected_path"])
            self.assertEqual(teye["status"], "found_elsewhere")
            self.assertEqual(report["declared_path_missing_count"], 1)
            self.assertEqual(report["missing_count"], 0)
            self.assertTrue(any("TEyeClient.exe" in path for path in teye["candidate_paths"]))
            self.assertIn("Copy `", teye["remediation"][0])
            self.assertTrue(teye["permission_sensitive"])

    def test_markdown_mentions_candidates(self):
        report = {
            "entry_count": 1,
            "declared_path_missing_count": 1,
            "missing_count": 0,
            "found_elsewhere_count": 1,
            "search_roots": ["C:\\Users\\me\\Documents"],
            "entries": [
                {
                    "expected_path": "C:\\TubeEye\\bin\\TEyeClient.exe",
                    "status": "found_elsewhere",
                    "referenced_by": ["Demo\\SUB_ScanTubes_50mL_v2"],
                    "permission_sensitive": True,
                    "candidate_paths": ["C:\\Users\\me\\OneDrive - Lab\\Documents\\TEyeClient.exe"],
                    "remediation": ["Copy the found file to C:\\TubeEye\\bin\\TEyeClient.exe"],
                }
            ],
        }
        md = render_external_file_dependencies_markdown(report)
        self.assertIn("TEyeClient.exe", md)
        self.assertIn("Missing at script-declared path: `1`", md)
        self.assertIn("OneDrive - Lab", md)
        self.assertIn("Administrator", md)

    def test_stage_found_external_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle" / "source"
            source = Path(tmp) / "Documents" / "TEyeClient.exe"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"MZ")
            report = {
                "entries": [
                    {
                        "status": "found_elsewhere",
                        "expected_path": "C:\\TubeEye\\bin\\TEyeClient.exe",
                        "candidate_paths": [str(source)],
                    }
                ]
            }
            staged = stage_found_external_files(report, bundle_source_dir=bundle)
            self.assertEqual(len(staged), 1)
            dest = Path(staged[0]["staged_path"])
            self.assertTrue(dest.is_file())
            self.assertIn("external-files", str(dest))

    def test_stage_present_external_files_for_portable_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle" / "source"
            source = Path(tmp) / "TubeEye" / "bin" / "GetLastBarcode.vb"
            source.parent.mkdir(parents=True)
            source.write_text("' helper", encoding="utf-8")
            report = {
                "entries": [
                    {
                        "status": "present",
                        "expected_path": r"C:\TubeEye\bin\GetLastBarcode.vb",
                        "candidate_paths": [str(source)],
                    }
                ]
            }
            staged = stage_found_external_files(report, bundle_source_dir=bundle)
            self.assertEqual(len(staged), 1)
            self.assertTrue(Path(staged[0]["staged_path"]).is_file())

    def test_write_external_file_install_bat(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_source = Path(tmp) / "bundle" / "source"
            staged_file = bundle_source / "external-files" / "bin" / "GetLastBarcode.vb"
            staged_file.parent.mkdir(parents=True)
            staged_file.write_text("' helper", encoding="utf-8")
            bat = write_external_file_install_bat(
                [
                    {
                        "expected_path": r"C:\TubeEye\bin\GetLastBarcode.vb",
                        "staged_path": str(staged_file),
                    }
                ],
                bundle_source_dir=bundle_source,
            )
            self.assertIsNotNone(bat)
            text = Path(bat).read_text(encoding="utf-8")
            self.assertIn("Run as administrator", text)
            self.assertIn(r"%ROOT%source\external-files\bin\GetLastBarcode.vb", text)
            self.assertIn(r"C:\TubeEye\bin\GetLastBarcode.vb", text)

    def test_default_search_roots_include_documents(self):
        with patch.dict("os.environ", {}, clear=False):
            roots = default_external_file_search_roots()
        self.assertTrue(any(path.name == "Documents" for path in roots))


if __name__ == "__main__":
    unittest.main()
