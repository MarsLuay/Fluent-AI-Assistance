from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tecan_reader.archive import inspect_archive
from tecan_reader.diagnostics import DiagnosticCode
from tecan_reader.project_index import build_project_index
from tecan_reader.zeia_adapters import ingest_zeia


SCRIPT_TEMPLATE = """<?xml version=\"1.0\"?>
<Root>
  <ObjectName>{name}</ObjectName>
  <Script version=\"2.0\" />
</Root>
"""
OBJECT_TEMPLATE = """<?xml version=\"1.0\"?>
<Root>
  <ObjectName>{name}</ObjectName>
  <TypeId>CatalogObject</TypeId>
</Root>
"""


class ArchiveCompletenessTests(unittest.TestCase):
    def test_preview_is_explicitly_incomplete_and_full_ingestion_is_uncapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "large.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                for index in range(51):
                    zf.writestr(
                        f"DataStore/UserSpecific/script-{index:03}.xscr",
                        SCRIPT_TEMPLATE.format(name=f"Script {index}"),
                    )
                for index in range(201):
                    zf.writestr(
                        f"DataStore/SystemSpecific/object-{index:03}.xcmp",
                        OBJECT_TEMPLATE.format(name=f"Object {index}"),
                    )

            preview = inspect_archive(archive)
            full = ingest_zeia(archive)
            database = root / "index.sqlite"
            indexed = build_project_index([archive], database, force=True)

        self.assertFalse(preview["complete"])
        self.assertEqual(preview["configured_limits"], {"scripts": 50, "objects": 200})
        self.assertEqual(preview["eligible_member_counts"]["scripts"], 51)
        self.assertEqual(preview["eligible_member_counts"]["objects"], 201)
        self.assertEqual(preview["summarized_counts"]["scripts"], 50)
        self.assertEqual(preview["summarized_counts"]["objects"], 200)
        self.assertEqual(preview["truncated_scripts"], 1)
        self.assertEqual(preview["truncated_objects"], 1)
        self.assertEqual(len(full.scripts), 51)
        self.assertEqual(len(full.objects), 201)
        self.assertTrue(full.completeness["complete"])
        self.assertEqual(indexed["script_count"], 51)
        self.assertEqual(indexed["catalog_object_count"], 201)
        self.assertTrue(indexed["files"][0]["completeness"]["complete"])

    def test_failed_member_is_classified_and_makes_full_ingestion_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "malformed.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "DataStore/UserSpecific/source.xscr",
                    SCRIPT_TEMPLATE.format(name="Source"),
                )
                zf.writestr("DataStore/SystemSpecific/Worktable/bad.xwsp", "<Root>")

            model = ingest_zeia(archive)

        self.assertFalse(model.completeness["complete"])
        self.assertEqual(len(model.objects), 0)
        self.assertEqual(len(model.errors), 1)
        error = model.errors[0]
        self.assertEqual(error["entry"], "DataStore/SystemSpecific/Worktable/bad.xwsp")
        self.assertEqual(error["parser"], "xml:xwsp")
        self.assertEqual(error["classification"], "malformed")
        self.assertEqual(error["severity"], "error")
        self.assertEqual(error["code"], DiagnosticCode.ZEIA_SCHEMA_MALFORMED)
        self.assertEqual(error["archive_path"], str(archive.resolve()))
        self.assertEqual(error["entry_path"], error["entry"])

    def test_known_irrelevant_metadata_failure_is_retained_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "metadata-warning.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "Scripts/source.xscr",
                    SCRIPT_TEMPLATE.format(name="Source"),
                )
                zf.writestr("DataStore/metadata.xml", "<Metadata>")

            model = ingest_zeia(archive)

        self.assertTrue(model.completeness["complete"])
        self.assertEqual(len(model.errors), 1)
        self.assertEqual(model.errors[0]["classification"], "known_irrelevant_metadata")
        self.assertEqual(model.errors[0]["severity"], "warning")
        self.assertEqual(model.errors[0]["code"], DiagnosticCode.PARSER_FAILED)

    def test_encoding_failure_is_retained_as_a_blocking_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "encoding-failure.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "Scripts/source.xscr",
                    SCRIPT_TEMPLATE.format(name="Source"),
                )
                zf.writestr("DataStore/SystemSpecific/object.xcmp", bytes([0xFF, 0xFE]))

            model = ingest_zeia(archive)

        self.assertFalse(model.completeness["complete"])
        self.assertEqual(model.errors[0]["classification"], "unreadable_or_encoding_failure")
        self.assertEqual(model.errors[0]["severity"], "error")
        self.assertEqual(model.errors[0]["code"], DiagnosticCode.ENCODING_DECODE_FAILED)
        self.assertEqual(model.errors[0]["entry_path"], "DataStore/SystemSpecific/object.xcmp")

    def test_unknown_xml_subtype_is_preserved_with_explicit_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "unknown-subtype.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "DataStore/UserSpecific/source.xscr",
                    SCRIPT_TEMPLATE.format(name="Source"),
                )
                zf.writestr(
                    "DataStore/SystemSpecific/unknown.xml",
                    "<FutureObject><FutureField>value</FutureField></FutureObject>",
                )

            model = ingest_zeia(archive)

        self.assertEqual(len(model.objects), 1)
        self.assertEqual(model.objects[0]["inspection_status"], "unsupported_subtype")
        self.assertEqual(
            model.objects[0]["source_metadata"]["unknown_fields"]["FutureField"],
            ["value"],
        )
        self.assertEqual(model.errors[0]["classification"], "unsupported_xml_subtype")
        self.assertEqual(model.errors[0]["code"], DiagnosticCode.ZEIA_SCHEMA_MALFORMED)
        self.assertFalse(model.completeness["complete"])


if __name__ == "__main__":
    unittest.main()
