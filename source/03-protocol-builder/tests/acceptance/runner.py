"""Deterministic, test-only runner for the synthetic local acceptance corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import fluent_pipeline.project_context as project_context
from fluent_pipeline.application_services import (
    BundleVerificationRequest,
    ProjectImportRequest,
    ProjectInspectionRequest,
    RequestSpecValidationRequest,
    import_project,
    inspect_project,
    validate_request_spec,
    verify_bundle,
)
from fluent_pipeline.determinism import compare_run_dirs
from fluent_pipeline.protocol_ir import protocol_ir_from_gwl, protocol_ir_from_xscr


RESULT_SCHEMA_VERSION = "tecan.local_acceptance_result.v1"
ZEIA_RECIPE_SCHEMA_VERSION = "tecan.synthetic_zeia_recipe.v1"
CHECK_NAMES = (
    "import",
    "inspect_provenance",
    "spec_lint",
    "determinism",
    "validation",
    "publication",
)
EXPECTED_VALUES = {
    "import": {"pass", "fail"},
    "inspect_provenance": {"pass", "fail", "not_run"},
    "spec_lint": {"pass", "fail", "not_run"},
    "determinism": {"pass", "fail"},
    "validation": {"ready", "not_ready", "not_run", "error"},
    "publication": {"allowed", "blocked", "not_applicable"},
}
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
PROTOCOL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROTOCOL_ROOT / "corpus" / "local_corpus_manifest.json"


def run_acceptance(manifest_path: Path, work_dir: Path) -> dict[str, Any]:
    """Run the public corpus twice and compare all normalized artifacts."""
    manifest_path = Path(manifest_path).resolve()
    work_dir = Path(work_dir).resolve()
    manifest = _read_json(manifest_path)
    validate_manifest_paths(manifest_path)
    _validate_expectations(manifest)
    if _is_relative_to(work_dir, PROTOCOL_ROOT):
        raise ValueError("acceptance work_dir must be outside the source tree")

    run_a = work_dir / "run-a"
    run_b = work_dir / "run-b"
    for run_dir in (run_a, run_b):
        if run_dir.exists():
            raise ValueError(f"acceptance run directory already exists: {run_dir}")

    observations_a = _execute_manifest(manifest, run_a)
    _execute_manifest(manifest, run_b)
    cases: list[dict[str, Any]] = []
    matched_checks = 0
    expected_failures_matched = 0

    for case in manifest["acceptance_cases"]:
        case_id = case["id"]
        determinism = compare_run_dirs(
            run_a / "cases" / case_id,
            run_b / "cases" / case_id,
            extra_roots=(str(work_dir),),
        )
        actuals = dict(observations_a[case_id])
        actuals["determinism"] = {
            "status": "pass" if determinism["deterministic"] else "fail",
            "details": determinism,
        }
        checks: dict[str, dict[str, Any]] = {}
        for check_name in CHECK_NAMES:
            expected = case["expectations"][check_name]
            actual = actuals[check_name]["status"]
            matched = expected == actual
            matched_checks += int(matched)
            if expected == "fail" and matched:
                expected_failures_matched += 1
            checks[check_name] = {
                "expected": expected,
                "actual": actual,
                "matched": matched,
                "details": actuals[check_name].get("details") or {},
            }
        cases.append(
            {
                "id": case_id,
                "status": "passed" if all(item["matched"] for item in checks.values()) else "failed",
                "expected_boundary": case.get("expected_boundary"),
                "checks": checks,
            }
        )

    total_checks = len(cases) * len(CHECK_NAMES)
    passed_cases = sum(1 for case in cases if case["status"] == "passed")
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "manifest": {
            "name": manifest["name"],
            "schema_version": manifest["schema_version"],
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "classification": manifest["privacy"]["classification"],
        },
        "status": "passed" if passed_cases == len(cases) else "failed",
        "metrics": {
            "total_cases": len(cases),
            "passed_cases": passed_cases,
            "failed_cases": len(cases) - passed_cases,
            "total_checks": total_checks,
            "matched_checks": matched_checks,
            "mismatched_checks": total_checks - matched_checks,
            "expected_failures_matched": expected_failures_matched,
        },
        "cases": cases,
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    _write_json(work_dir / "acceptance-result.json", result)
    (work_dir / "acceptance-summary.md").write_text(
        render_acceptance_summary(result),
        encoding="utf-8",
    )
    return result


def validate_manifest_paths(manifest_path: Path) -> tuple[Path, ...]:
    """Validate every source, case, and ZEIA-recipe path without writing files."""
    manifest_path = Path(manifest_path).resolve()
    manifest = _read_json(manifest_path)
    if manifest.get("path_base") != "source/03-protocol-builder":
        raise ValueError("manifest path_base must be source/03-protocol-builder")
    resolved: list[Path] = []
    relative_paths: list[str] = []
    for source in manifest.get("sources") or []:
        for asset in source.get("assets") or []:
            relative_paths.append(str(asset.get("path") or ""))
    for case in manifest.get("acceptance_cases") or []:
        relative_paths.append(str((case.get("input") or {}).get("path") or ""))
        relative_paths.append(str(case.get("request_spec") or ""))

    for relative in relative_paths:
        path = _resolve_corpus_path(relative)
        if not path.is_file():
            raise ValueError(f"manifest path does not exist: {relative}")
        resolved.append(path)
        if path.name.endswith(".zeia.json"):
            recipe = _read_json(path)
            if recipe.get("schema_version") != ZEIA_RECIPE_SCHEMA_VERSION:
                raise ValueError(f"unsupported ZEIA recipe schema: {relative}")
            for entry in recipe.get("entries") or []:
                archive_path = PurePosixPath(str(entry.get("archive_path") or ""))
                if archive_path.is_absolute() or ".." in archive_path.parts or not archive_path.name:
                    raise ValueError(f"unsafe ZEIA recipe archive path: {archive_path}")
                source_path = _resolve_corpus_path(str(entry.get("source") or ""))
                if not source_path.is_file():
                    raise ValueError(f"ZEIA recipe source does not exist: {entry.get('source')}")
                resolved.append(source_path)
    return tuple(dict.fromkeys(resolved))


def render_acceptance_summary(result: dict[str, Any]) -> str:
    """Render a compact human-readable result summary."""
    metrics = result["metrics"]
    lines = [
        "# Local acceptance corpus",
        "",
        f"Status: **{result['status'].upper()}**",
        "",
        f"Cases: {metrics['passed_cases']}/{metrics['total_cases']} passed",
        f"Checks: {metrics['matched_checks']}/{metrics['total_checks']} matched",
        f"Expected failures matched: {metrics['expected_failures_matched']}",
        "",
        "| Case | Status | Validation | Publication |",
        "|---|---|---|---|",
    ]
    for case in result["cases"]:
        lines.append(
            f"| `{case['id']}` | {case['status']} | "
            f"{case['checks']['validation']['actual']} | {case['checks']['publication']['actual']} |"
        )
    boundaries = [case for case in result["cases"] if case.get("expected_boundary")]
    if boundaries:
        lines.extend(["", "## Expected publication boundaries", ""])
        for case in boundaries:
            lines.append(f"- `{case['id']}`: {case['expected_boundary']}")
    return "\n".join(lines) + "\n"


def _execute_manifest(manifest: dict[str, Any], run_dir: Path) -> dict[str, dict[str, Any]]:
    observations: dict[str, dict[str, Any]] = {}
    for case in manifest["acceptance_cases"]:
        case_dir = run_dir / "cases" / case["id"]
        case_dir.mkdir(parents=True, exist_ok=False)
        observation = _execute_case(case, case_dir)
        observations[case["id"]] = observation
        _write_json(case_dir / "observation.json", observation)
    return observations


def _execute_case(case: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    checks = {
        "import": {"status": "fail", "details": {}},
        "inspect_provenance": {"status": "not_run", "details": {}},
        "spec_lint": {"status": "not_run", "details": {}},
        "validation": {"status": "not_run", "details": {}},
        "publication": {"status": "not_applicable", "details": {}},
    }
    input_kind = case["input"]["kind"]
    input_path = _resolve_corpus_path(case["input"]["path"])
    ir: dict[str, Any] | None = None
    ir_path: Path | None = None
    compiled_xscr: Path | None = None
    source_archive: Path | None = None
    source_manifest: dict[str, Any] | None = None

    with _isolated_project_store(case_dir / "project-store"):
        try:
            if input_kind == "gwl":
                ir = protocol_ir_from_gwl(input_path)
            elif input_kind == "xscr":
                ir = protocol_ir_from_xscr(input_path)
                compiled_xscr = input_path
            elif input_kind == "zeia_recipe":
                source_archive = _materialize_zeia(input_path, case_dir / "inputs")
                imported = import_project(
                    ProjectImportRequest(
                        archive=source_archive,
                        name=f"acceptance-{case['id']}",
                        force=True,
                        activate=True,
                    )
                )
                inspected = inspect_project(ProjectInspectionRequest(context_name=imported.context.name))
                _write_json(case_dir / "project-inspection.json", inspected.to_dict())
                source_manifest = imported.context.manifest
                script = source_manifest["scripts"][0]
                compiled_xscr = imported.context.root / script["extracted_path"]
                ir = protocol_ir_from_xscr(
                    compiled_xscr,
                    source_name=f"{source_archive.name}!/{script['entry']}",
                )
                checks["inspect_provenance"] = _inspect_project_provenance(
                    inspected.to_dict(),
                    source_manifest,
                    ir,
                )
            else:
                raise ValueError(f"unsupported acceptance input kind: {input_kind}")
        except Exception as exc:  # expected negative fixtures use the real parser exception
            checks["import"] = {
                "status": "fail",
                "details": {"error_type": type(exc).__name__, "message": str(exc)},
            }
            _write_json(case_dir / "import-failure.json", checks["import"]["details"])
        else:
            checks["import"] = {
                "status": "pass",
                "details": {
                    "input_kind": input_kind,
                    "protocol": str((ir or {}).get("protocol", {}).get("name") or ""),
                    "step_count": len((ir or {}).get("steps") or []),
                },
            }
            ir_path = case_dir / "imported.protocol-ir.json"
            _write_json(ir_path, ir)
            if input_kind != "zeia_recipe":
                checks["inspect_provenance"] = _inspect_ir_provenance(ir or {}, input_kind)
                _write_json(case_dir / "provenance-inspection.json", checks["inspect_provenance"])

        spec_source = _resolve_corpus_path(case["request_spec"])
        spec_path = case_dir / "request.spec.yaml"
        shutil.copyfile(spec_source, spec_path)
        lint_result = validate_request_spec(RequestSpecValidationRequest(spec_path=spec_path)).to_dict()
        _write_json(case_dir / "spec-lint.json", lint_result)
        checks["spec_lint"] = {
            "status": "pass" if lint_result["ok"] else "fail",
            "details": {
                "error_count": sum(1 for item in lint_result["findings"] if item["severity"] == "error"),
                "warning_count": sum(1 for item in lint_result["findings"] if item["severity"] == "warning"),
                "estimated_ir_body_steps": lint_result["estimated_ir_body_steps"],
            },
        }

        if case.get("run_ready_validation") and compiled_xscr is not None and ir_path is not None:
            try:
                verification = verify_bundle(
                    BundleVerificationRequest(
                        compiled_xscr=compiled_xscr,
                        protocol_ir=ir_path,
                        source_projects=(source_archive,) if source_archive is not None else (),
                        source_scripts=(compiled_xscr,),
                        source_xscr=compiled_xscr,
                        source_manifest=source_manifest,
                        validation_context={
                            "full_zeia_export": (source_manifest or {}).get("full_zeia_export"),
                            "partial_zeia_export_approved": False,
                        },
                        report_path=case_dir / "ready-validation.md",
                        json_path=case_dir / "ready-validation.json",
                    )
                )
            except Exception as exc:
                checks["validation"] = {
                    "status": "error",
                    "details": {"error_type": type(exc).__name__, "message": str(exc)},
                }
                checks["publication"] = {"status": "blocked", "details": {"reason": "validation_error"}}
            else:
                report = verification.report
                ready = bool(report.get("ready"))
                checks["validation"] = {
                    "status": "ready" if ready else "not_ready",
                    "details": {
                        "offline_status": (report.get("offline_validation") or {}).get("status"),
                        "required_gate_count": report.get("required_gate_count"),
                        "blocking_gates": report.get("blocking_gates") or [],
                    },
                }
                checks["publication"] = {
                    "status": "allowed" if ready else "blocked",
                    "details": {
                        "derived_from": "ready validation",
                        "ready": ready,
                        "boundary": case.get("expected_boundary"),
                    },
                }
    return checks


def _inspect_ir_provenance(ir: dict[str, Any], expected_format: str) -> dict[str, Any]:
    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    steps = ir.get("steps") if isinstance(ir.get("steps"), list) else []
    missing_step_provenance: list[int] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            missing_step_provenance.append(index)
            continue
        if expected_format == "gwl":
            has_provenance = bool(str(step.get("source_path") or "").strip())
        else:
            parameters = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
            has_provenance = parameters.get("command_index") is not None
        if not has_provenance:
            missing_step_provenance.append(index)
    ok = (
        source.get("format") == expected_format
        and bool(str(source.get("path") or "").strip())
        and bool(steps)
        and not missing_step_provenance
    )
    return {
        "status": "pass" if ok else "fail",
        "details": {
            "source_format": source.get("format"),
            "source_path": source.get("path"),
            "step_count": len(steps),
            "missing_step_provenance_indexes": missing_step_provenance,
        },
    }


def _inspect_project_provenance(
    inspection: dict[str, Any],
    manifest: dict[str, Any],
    ir: dict[str, Any],
) -> dict[str, Any]:
    ir_check = _inspect_ir_provenance(ir, "xscr")
    full_export = manifest.get("full_zeia_export") or {}
    ok = (
        bool(inspection.get("ok"))
        and bool(inspection.get("report_path"))
        and len(manifest.get("scripts") or []) == 1
        and full_export.get("status") == "needs_user"
        and ir_check["status"] == "pass"
    )
    return {
        "status": "pass" if ok else "fail",
        "details": {
            "inspection_ok": bool(inspection.get("ok")),
            "script_count": len(manifest.get("scripts") or []),
            "full_zeia_export_status": full_export.get("status"),
            "ir_provenance": ir_check["details"],
        },
    }


def _materialize_zeia(recipe_path: Path, output_dir: Path) -> Path:
    recipe = _read_json(recipe_path)
    if recipe.get("schema_version") != ZEIA_RECIPE_SCHEMA_VERSION:
        raise ValueError(f"unsupported ZEIA recipe schema: {recipe.get('schema_version')!r}")
    archive_name = str(recipe.get("archive_name") or "")
    if not archive_name.endswith(".zeia") or Path(archive_name).name != archive_name:
        raise ValueError(f"unsafe ZEIA recipe archive name: {archive_name!r}")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / archive_name
    with zipfile.ZipFile(archive, "w") as zf:
        for entry in sorted(recipe.get("entries") or [], key=lambda item: item["archive_path"]):
            archive_path = PurePosixPath(str(entry["archive_path"]))
            if archive_path.is_absolute() or ".." in archive_path.parts or not archive_path.name:
                raise ValueError(f"unsafe ZEIA recipe archive path: {archive_path}")
            source = _resolve_corpus_path(str(entry["source"]))
            info = zipfile.ZipInfo(archive_path.as_posix(), date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, source.read_bytes())
    return archive


@contextmanager
def _isolated_project_store(root: Path) -> Iterator[None]:
    old_projects = project_context.PROJECTS_DIR
    old_collections = project_context.COLLECTIONS_DIR
    old_active = project_context.ACTIVE_CONTEXT_FILE
    project_context.PROJECTS_DIR = root / "projects"
    project_context.COLLECTIONS_DIR = root / "collections"
    project_context.ACTIVE_CONTEXT_FILE = root / ".active_context"
    try:
        yield
    finally:
        project_context.PROJECTS_DIR = old_projects
        project_context.COLLECTIONS_DIR = old_collections
        project_context.ACTIVE_CONTEXT_FILE = old_active


def _validate_expectations(manifest: dict[str, Any]) -> None:
    case_ids: set[str] = set()
    for case in manifest.get("acceptance_cases") or []:
        case_id = str(case.get("id") or "")
        if not case_id or case_id in case_ids:
            raise ValueError(f"invalid or duplicate acceptance case id: {case_id!r}")
        case_ids.add(case_id)
        expectations = case.get("expectations") or {}
        if set(expectations) != set(CHECK_NAMES):
            raise ValueError(f"acceptance case {case_id} must define exactly {CHECK_NAMES}")
        for check_name, expected in expectations.items():
            if expected not in EXPECTED_VALUES[check_name]:
                raise ValueError(f"unsupported {check_name} expectation for {case_id}: {expected!r}")


def _resolve_corpus_path(relative: str) -> Path:
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe corpus-relative path: {relative!r}")
    resolved = (PROTOCOL_ROOT / candidate).resolve()
    if not _is_relative_to(resolved, PROTOCOL_ROOT):
        raise ValueError(f"corpus path escapes protocol root: {relative!r}")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_acceptance(args.manifest, args.work_dir)
    print(render_acceptance_summary(result), end="")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
