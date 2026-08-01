from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.delivery_bundle import (
    DELIVERY_MANIFEST_SCHEMA_VERSION,
    READY_BUNDLE_SCHEMA_VERSION,
    validate_v2_delivery_bundle,
)


def _write_valid_bundle(root: Path, protocol_name: str = "demo") -> Path:
    bundle = root / protocol_name
    (bundle / "source").mkdir(parents=True)
    (bundle / "media").mkdir()
    (bundle / "reports").mkdir()
    (bundle / "generated").mkdir()
    (bundle / "support").mkdir()
    (bundle / f"{protocol_name}.zeia").write_bytes(b"zeia")
    (bundle / "run_tecan_bundle_setup.bat").write_text("@echo off\n", encoding="utf-8")
    (bundle / "RECREATE_SCRIPT.md").write_text("# Recreate\n", encoding="utf-8")
    (bundle / "support" / "request.spec.yaml").write_text("request: {}\n", encoding="utf-8")
    (bundle / "support" / "protocol.ir.json").write_text("{}\n", encoding="utf-8")
    (bundle / "generated" / "protocol.py").write_text("def build_worktable():\n    pass\n", encoding="utf-8")
    (bundle / "support" / "generation_manifest.json").write_text("{}\n", encoding="utf-8")
    (bundle / "support" / "GENERATION_WORKFLOW.md").write_text("# Workflow\n", encoding="utf-8")
    (bundle / "support" / "delivery_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": DELIVERY_MANIFEST_SCHEMA_VERSION,
                "bundle_schema_version": READY_BUNDLE_SCHEMA_VERSION,
                "protocol_name": protocol_name,
                "external_file_deployments": [],
                "deliverables": [
                    {
                        "kind": "fluent_project_archive",
                        "path": f"{protocol_name}.zeia",
                    }
                ],
                "companion_artifacts": [],
                "internal_artifacts": [{"kind": "compiled_xscr_intermediate", "published": False}],
            }
        ),
        encoding="utf-8",
    )
    return bundle


class DeliveryBundleValidationTests(unittest.TestCase):
    def test_valid_v2_delivery_bundle_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_valid_bundle(Path(tmp))

            result = validate_v2_delivery_bundle(bundle)

        self.assertTrue(result.ok, result.to_dict())

    def test_root_rejects_support_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_valid_bundle(Path(tmp))
            (bundle / "delivery_manifest.json").write_text("{}\n", encoding="utf-8")

            result = validate_v2_delivery_bundle(bundle)

        self.assertFalse(result.ok)
        self.assertIn("unexpected_root_file", {issue.code for issue in result.issues})

    def test_missing_required_v2_members_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_valid_bundle(Path(tmp))
            (bundle / "source").rmdir()

            result = validate_v2_delivery_bundle(bundle)

        self.assertFalse(result.ok)
        self.assertIn("missing_source_dir", {issue.code for issue in result.issues})

    def test_delivery_manifest_must_be_v2_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_valid_bundle(Path(tmp))
            manifest = json.loads((bundle / "support" / "delivery_manifest.json").read_text(encoding="utf-8"))
            manifest["schema_version"] = "tecan.protocol_delivery.v1"
            (bundle / "support" / "delivery_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            result = validate_v2_delivery_bundle(bundle)

        self.assertFalse(result.ok)
        self.assertIn("invalid_delivery_manifest_schema", {issue.code for issue in result.issues})

    def test_xscr_and_extra_bat_are_never_published_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_valid_bundle(Path(tmp))
            (bundle / "debug.xscr").write_text("<Root />\n", encoding="utf-8")
            (bundle / "deploy_touchtools_images.bat").write_text("@echo off\n", encoding="utf-8")

            result = validate_v2_delivery_bundle(bundle)

        codes = {issue.code for issue in result.issues}
        self.assertFalse(result.ok)
        self.assertIn("xscr_published", codes)
        self.assertIn("unexpected_bat", codes)

    def test_external_deployment_requires_exact_path_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_valid_bundle(Path(tmp))
            payload = bundle / "source" / "external-files" / "16" / "asset.dat"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"exact payload")
            manifest_path = bundle / "support" / "delivery_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["external_file_deployments"] = [
                {
                    "bundle_path": "source/external-files/16/asset.dat",
                    "target_path": r"C:\TecanInformation\asset.dat",
                    "sha256": "0cfefcacfe03534dd908444efd6e4d0d1075fd8cf59ac79bf956312385679cfe",
                }
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = validate_v2_delivery_bundle(bundle)

        self.assertTrue(result.ok, result.to_dict())

    def test_external_deployment_rejects_tampered_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_valid_bundle(Path(tmp))
            payload = bundle / "source" / "external-files" / "1" / "duplicate.dat"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"tampered")
            manifest_path = bundle / "support" / "delivery_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["external_file_deployments"] = [
                {
                    "bundle_path": "source/external-files/1/duplicate.dat",
                    "target_path": r"C:\one\duplicate.dat",
                    "sha256": "0" * 64,
                }
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = validate_v2_delivery_bundle(bundle)

        self.assertFalse(result.ok)
        self.assertIn("external_payload_hash_mismatch", {issue.code for issue in result.issues})

    def test_external_deployment_rejects_bundle_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_valid_bundle(Path(tmp))
            manifest_path = bundle / "support" / "delivery_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["external_file_deployments"] = [
                {
                    "bundle_path": "../payload.dat",
                    "target_path": r"C:\one\payload.dat",
                    "sha256": "0" * 64,
                }
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = validate_v2_delivery_bundle(bundle)

        self.assertFalse(result.ok)
        self.assertIn("invalid_external_bundle_path", {issue.code for issue in result.issues})


if __name__ == "__main__":
    unittest.main()
