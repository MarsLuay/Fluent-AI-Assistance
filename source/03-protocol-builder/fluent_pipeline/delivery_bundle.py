"""Strict validation for published V2 ready-to-import protocol folders."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal


READY_BUNDLE_SCHEMA_VERSION = "tecan.ready_to_import.bundle.v2"
DELIVERY_MANIFEST_SCHEMA_VERSION = "tecan.protocol_delivery.v2"


@dataclass(frozen=True)
class DeliveryBundleIssue:
    code: str
    message: str
    path: str | None = None
    severity: Literal["error", "warning"] = "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeliveryBundleValidationResult:
    bundle_dir: Path
    protocol_name: str
    ok: bool
    issues: tuple[DeliveryBundleIssue, ...]
    manifest_path: Path | None
    zeia_path: Path | None
    schema_version: str | None
    bundle_schema_version: str | None
    require_final_reports: bool

    @property
    def errors(self) -> tuple[DeliveryBundleIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "bundle_dir": str(self.bundle_dir),
            "protocol_name": self.protocol_name,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "zeia_path": str(self.zeia_path) if self.zeia_path else None,
            "schema_version": self.schema_version,
            "bundle_schema_version": self.bundle_schema_version,
            "require_final_reports": self.require_final_reports,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_v2_delivery_bundle(
    bundle_dir: Path,
    *,
    protocol_name: str | None = None,
    require_final_reports: bool = True,
) -> DeliveryBundleValidationResult:
    """Validate the complete V2 delivery-folder contract.

    This checks the final human/AI delivery bundle, not the older compiled-XCSR
    readiness gates. A standalone XSCR is never accepted as a deliverable here.
    """
    bundle_dir = Path(bundle_dir)
    expected_name = protocol_name or bundle_dir.name
    issues: list[DeliveryBundleIssue] = []
    manifest: dict[str, Any] = {}
    support_dir = bundle_dir / "support"
    metadata_dir = support_dir if support_dir.is_dir() else bundle_dir
    manifest_path = metadata_dir / "delivery_manifest.json"
    zeia_path = bundle_dir / f"{expected_name}.zeia"

    def add(code: str, message: str, path: Path | str | None = None) -> None:
        issues.append(
            DeliveryBundleIssue(
                code=code,
                message=message,
                path=str(path) if path is not None else None,
            )
        )

    if not bundle_dir.is_dir():
        add("missing_bundle_dir", "delivery bundle folder does not exist", bundle_dir)
        return DeliveryBundleValidationResult(
            bundle_dir=bundle_dir,
            protocol_name=expected_name,
            ok=False,
            issues=tuple(issues),
            manifest_path=manifest_path,
            zeia_path=zeia_path,
            schema_version=None,
            bundle_schema_version=None,
            require_final_reports=require_final_reports,
        )

    required_paths = [
        (zeia_path, "missing_zeia", "validated FluentControl ZEIA is missing"),
        (manifest_path, "missing_delivery_manifest", f"{manifest_path.relative_to(bundle_dir)} is missing"),
        (bundle_dir / "run_tecan_bundle_setup.bat", "missing_setup_bat", "run_tecan_bundle_setup.bat is missing"),
        (bundle_dir / "source", "missing_source_dir", "source/ companion tree is missing"),
        (bundle_dir / "media", "missing_media_dir", "media/ payload tree is missing"),
        (bundle_dir / "reports", "missing_reports_dir", "reports/ validation tree is missing"),
        (bundle_dir / "RECREATE_SCRIPT.md", "missing_recreate_script", "RECREATE_SCRIPT.md is missing"),
        (metadata_dir / "request.spec.yaml", "missing_request_spec", f"{(metadata_dir / 'request.spec.yaml').relative_to(bundle_dir)} is missing"),
        (metadata_dir / "protocol.ir.json", "missing_protocol_ir", f"{(metadata_dir / 'protocol.ir.json').relative_to(bundle_dir)} is missing"),
        (bundle_dir / "generated" / "protocol.py", "missing_generated_python", "generated/protocol.py is missing"),
    ]
    if require_final_reports:
        required_paths.extend(
            [
                (metadata_dir / "generation_manifest.json", "missing_generation_manifest", f"{(metadata_dir / 'generation_manifest.json').relative_to(bundle_dir)} is missing"),
                (metadata_dir / "GENERATION_WORKFLOW.md", "missing_workflow_report", f"{(metadata_dir / 'GENERATION_WORKFLOW.md').relative_to(bundle_dir)} is missing"),
            ]
        )
    for path, code, message in required_paths:
        if not path.exists():
            add(code, message, path)

    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            add("invalid_delivery_manifest_json", f"delivery_manifest.json is not valid JSON: {exc}", manifest_path)
        else:
            if isinstance(loaded, dict):
                manifest = loaded
            else:
                add("invalid_delivery_manifest_shape", "delivery_manifest.json must contain a JSON object", manifest_path)

    schema_version = str(manifest.get("schema_version")) if manifest.get("schema_version") else None
    bundle_schema_version = str(manifest.get("bundle_schema_version")) if manifest.get("bundle_schema_version") else None
    if manifest:
        if schema_version != DELIVERY_MANIFEST_SCHEMA_VERSION:
            add(
                "invalid_delivery_manifest_schema",
                f"delivery manifest schema_version must be {DELIVERY_MANIFEST_SCHEMA_VERSION}",
                manifest_path,
            )
        if bundle_schema_version != READY_BUNDLE_SCHEMA_VERSION:
            add(
                "invalid_ready_bundle_schema",
                f"bundle_schema_version must be {READY_BUNDLE_SCHEMA_VERSION}",
                manifest_path,
            )
        if str(manifest.get("protocol_name") or "") != expected_name:
            add("protocol_name_mismatch", "delivery manifest protocol_name does not match the folder/ZEIA stem", manifest_path)
        _validate_manifest_artifacts(
            manifest,
            bundle_dir=bundle_dir,
            protocol_name=expected_name,
            add_issue=add,
        )
        _validate_external_file_deployments(
            manifest,
            bundle_dir=bundle_dir,
            add_issue=add,
        )

    _validate_no_unpublished_artifacts(bundle_dir, expected_name, add)

    return DeliveryBundleValidationResult(
        bundle_dir=bundle_dir,
        protocol_name=expected_name,
        ok=not any(issue.severity == "error" for issue in issues),
        issues=tuple(issues),
        manifest_path=manifest_path,
        zeia_path=zeia_path,
        schema_version=schema_version,
        bundle_schema_version=bundle_schema_version,
        require_final_reports=require_final_reports,
    )


def render_delivery_bundle_validation(result: DeliveryBundleValidationResult) -> str:
    status = "passed" if result.ok else "failed"
    lines = [
        f"V2 delivery bundle validation {status}: {result.bundle_dir}",
        f"Protocol: {result.protocol_name}",
    ]
    if result.schema_version:
        lines.append(f"Schema: {result.schema_version}")
    for issue in result.issues:
        location = f" ({issue.path})" if issue.path else ""
        lines.append(f"[{issue.severity}] {issue.code}: {issue.message}{location}")
    return "\n".join(lines)


def delivery_bundle_failure_message(result: DeliveryBundleValidationResult) -> str:
    if result.ok:
        return "V2 delivery bundle validation passed"
    first = result.errors[0] if result.errors else result.issues[0]
    return f"V2 delivery bundle validation failed: {first.code}: {first.message}"


def _validate_manifest_artifacts(
    manifest: dict[str, Any],
    *,
    bundle_dir: Path,
    protocol_name: str,
    add_issue: Any,
) -> None:
    deliverables = manifest.get("deliverables")
    if not isinstance(deliverables, list):
        add_issue("missing_manifest_deliverables", "delivery manifest must list deliverables", bundle_dir / "support" / "delivery_manifest.json")
        return
    expected = f"{protocol_name}.zeia"
    zeia_deliverables = [
        item
        for item in deliverables
        if isinstance(item, dict)
        and item.get("kind") == "fluent_project_archive"
        and item.get("path") == expected
    ]
    if len(zeia_deliverables) != 1:
        add_issue("invalid_manifest_zeia_deliverable", f"delivery manifest must list exactly one ZEIA deliverable at {expected}", bundle_dir / "support" / "delivery_manifest.json")
    for item in deliverables:
        if not isinstance(item, dict):
            add_issue("invalid_manifest_deliverable", "delivery manifest deliverables must be objects", bundle_dir / "support" / "delivery_manifest.json")
            continue
        path = str(item.get("path") or "")
        if path.lower().endswith(".xscr"):
            add_issue("xscr_manifest_deliverable", "standalone XSCR files may not be manifest deliverables", path)
        if path and not (bundle_dir / path).exists():
            add_issue("manifest_deliverable_missing", "manifest deliverable path is missing from the bundle", bundle_dir / path)


def _validate_external_file_deployments(
    manifest: dict[str, Any],
    *,
    bundle_dir: Path,
    add_issue: Any,
) -> None:
    deployments = manifest.get("external_file_deployments")
    manifest_path = bundle_dir / "support" / "delivery_manifest.json"
    if not isinstance(deployments, list):
        add_issue(
            "missing_external_file_deployments",
            "delivery manifest must contain an external_file_deployments array",
            manifest_path,
        )
        return

    seen_bundle_paths: set[str] = set()
    seen_target_paths: set[str] = set()
    for index, item in enumerate(deployments):
        label = f"external_file_deployments[{index}]"
        if not isinstance(item, dict):
            add_issue("invalid_external_deployment", f"{label} must be an object", manifest_path)
            continue
        bundle_path = str(item.get("bundle_path") or "").strip()
        target_path = str(item.get("target_path") or "").strip()
        expected_sha256 = str(item.get("sha256") or "").strip().lower()
        if not bundle_path or not target_path or not expected_sha256:
            add_issue(
                "incomplete_external_deployment",
                f"{label} must contain bundle_path, target_path, and sha256",
                manifest_path,
            )
            continue

        normalized_bundle_path = bundle_path.replace("\\", "/")
        relative = PurePosixPath(normalized_bundle_path)
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.parts[:2] != ("source", "external-files")
        ):
            add_issue(
                "invalid_external_bundle_path",
                f"{label}.bundle_path must be a safe path under source/external-files/",
                bundle_path,
            )
            continue
        bundle_key = normalized_bundle_path.casefold()
        if bundle_key in seen_bundle_paths:
            add_issue("duplicate_external_bundle_path", f"{label}.bundle_path is duplicated", bundle_path)
        seen_bundle_paths.add(bundle_key)

        if not PureWindowsPath(target_path).is_absolute():
            add_issue(
                "invalid_external_target_path",
                f"{label}.target_path must be an absolute Windows path",
                target_path,
            )
        target_key = str(PureWindowsPath(target_path)).casefold()
        if target_key in seen_target_paths:
            add_issue("duplicate_external_target_path", f"{label}.target_path is duplicated", target_path)
        seen_target_paths.add(target_key)

        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            add_issue("invalid_external_sha256", f"{label}.sha256 must be 64 hexadecimal characters", manifest_path)
            continue
        source = bundle_dir.joinpath(*relative.parts)
        if not source.is_file():
            add_issue("missing_external_payload", f"{label}.bundle_path does not exist", source)
            continue
        actual_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            add_issue(
                "external_payload_hash_mismatch",
                f"{label}.sha256 does not match the packaged payload",
                source,
            )


def _validate_no_unpublished_artifacts(bundle_dir: Path, protocol_name: str, add_issue: Any) -> None:
    expected_zeia = bundle_dir / f"{protocol_name}.zeia"
    if (bundle_dir / "support").is_dir():
        allowed_root_files = {
            expected_zeia.name,
            "run_tecan_bundle_setup.bat",
            "RECREATE_SCRIPT.md",
        }
        for item in bundle_dir.iterdir():
            if item.is_file() and item.name not in allowed_root_files:
                add_issue(
                    "unexpected_root_file",
                    "only the ZEIA, run_tecan_bundle_setup.bat, and RECREATE_SCRIPT.md may be files at the delivery root",
                    item,
                )
    for xscr in bundle_dir.rglob("*.xscr"):
        add_issue("xscr_published", "standalone XSCR files are internal artifacts and may not be published in V2 bundles", xscr)
    for zeia in bundle_dir.rglob("*.zeia"):
        if zeia != expected_zeia:
            add_issue("unexpected_zeia", "V2 bundles may publish only the protocol ZEIA at the bundle root", zeia)
    bat_files = list(bundle_dir.rglob("*.bat"))
    expected_bat = bundle_dir / "run_tecan_bundle_setup.bat"
    external_files_root = (bundle_dir / "source" / "external-files").resolve()
    for bat in bat_files:
        if bat == expected_bat:
            continue
        try:
            bat.resolve().relative_to(external_files_root)
        except (ValueError, OSError):
            add_issue(
                "unexpected_bat",
                "run_tecan_bundle_setup.bat is the only BAT allowed in a V2 delivery bundle "
                "(except payloads under source/external-files/)",
                bat,
            )
