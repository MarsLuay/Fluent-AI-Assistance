import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.bundle_lifecycle import (
    archive_recommended_bundles,
    render_bundle_index,
    scan_bundle_lifecycle,
    source_export_kind,
    verification_state_from_readiness,
)


class BundleLifecycleTests(unittest.TestCase):
    def test_classifies_source_export_and_verification_state(self):
        self.assertEqual(source_export_kind({"accepted": True, "status": "likely_full_export"}), "full")
        self.assertEqual(source_export_kind({"accepted": False}, approved_partial=True), "approved_partial")
        self.assertEqual(source_export_kind({"accepted": False}), "partial")
        self.assertEqual(
            verification_state_from_readiness(
                ready_to_import=True,
                readiness={"fluentcontrol_load_diagnostic": {"status": "load_clean"}},
                workflow_status="ready_to_import",
            ),
            "load_tested",
        )
        self.assertEqual(
            verification_state_from_readiness(
                ready_to_import=True,
                readiness={"script_editor_load": {"status": "passed"}},
                workflow_status="ready_to_import",
            ),
            "load_tested",
        )
        self.assertEqual(
            verification_state_from_readiness(
                ready_to_import=True,
                readiness={
                    "offline_validation": {"status": "ready_to_import"},
                    "review_state": {"status": "import_ready_needs_review"},
                    "fluentcontrol_load_diagnostic": {"status": "not_run"},
                    "generated_zeia_import": {"status": "import_ready_needs_review"},
                    "script_editor_load": {"status": "not_run"},
                    "hardware_run": {"status": "hardware_review_required"},
                },
                workflow_status="ready_to_import",
            ),
            "offline_validated",
        )
        self.assertEqual(
            verification_state_from_readiness(
                ready_to_import=False,
                readiness={},
                workflow_status="scaffold_not_validated",
            ),
            "not_validated",
        )

    def test_dry_run_recommends_archiving_superseded_and_probe_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ready-to-import"
            probes = Path(tmp) / "build" / "fluent_import_probe"
            self._write_bundle(root / "assay_old", created_at="2026-06-10T12:00:00+00:00")
            self._write_bundle(root / "assay_new", created_at="2026-06-10T14:00:00+00:00")
            probes.mkdir(parents=True)
            (probes / "import_probe.zeia").write_bytes(b"probe")

            records = scan_bundle_lifecycle(ready_root=root, probe_roots=[probes])
            by_name = {record.name: record for record in records}

        self.assertEqual(by_name["assay_new"].recommendation, "keep")
        self.assertEqual(by_name["assay_old"].recommendation, "archive")
        self.assertEqual(by_name["assay_old"].superseded_by, "assay_new")
        self.assertEqual(by_name["import_probe.zeia"].bundle_role, "probe")
        self.assertEqual(by_name["import_probe.zeia"].recommendation, "archive")
        report = render_bundle_index(records)
        self.assertIn("Bundle Index", report)
        self.assertIn("assay_old", report)
        self.assertIn("superseded by", report)

    def test_archive_operation_moves_only_recommended_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ready-to-import"
            self._write_bundle(root / "assay_old", created_at="2026-06-10T12:00:00+00:00")
            self._write_bundle(root / "assay_new", created_at="2026-06-10T14:00:00+00:00")
            records = scan_bundle_lifecycle(ready_root=root, probe_roots=[])

            moved = archive_recommended_bundles(records, archive_root=root / "archive")

            self.assertEqual(len(moved), 1)
            self.assertFalse((root / "assay_old").exists())
            self.assertTrue((root / "assay_new").exists())
            archived_metadata = list((root / "archive").glob("*/assay_old/source/metadata.json"))
            self.assertEqual(len(archived_metadata), 1)
            metadata = json.loads(archived_metadata[0].read_text(encoding="utf-8"))
            self.assertEqual(metadata["bundle_role"], "archive")
            self.assertEqual(metadata["lifecycle"]["bundle_role"], "archive")

    def _write_bundle(self, bundle: Path, *, created_at: str) -> None:
        source = bundle / "source"
        source.mkdir(parents=True)
        metadata = {
            "bundle_schema_version": "tecan.ready_to_import.bundle.v1",
            "script_name": "assay",
            "context_name": "ctx",
            "exported_at": created_at,
            "bundle_role": "ready",
            "source_export_kind": "full",
            "verification_state": "offline_validated",
            "lifecycle": {
                "bundle_role": "ready",
                "source_export_kind": "full",
                "verification_state": "offline_validated",
            },
        }
        manifest = {
            "workflow_status": "ready_to_import",
            "ready_to_import": True,
            "generated_at": created_at,
            "context": "ctx",
            "full_zeia_export": {"accepted": True, "status": "likely_full_export"},
            "readiness": {
                "offline_validation": {"status": "ready_to_import"},
                "review_state": {"status": "hardware_review_required"},
                "fluentcontrol_load_diagnostic": {"status": "not_run"},
                "generated_zeia_import": {"status": "ready_to_import"},
                "script_editor_load": {"status": "not_run"},
            },
        }
        (source / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (source / "generation_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
