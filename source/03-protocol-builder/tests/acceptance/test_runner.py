import json
from pathlib import Path

from .runner import (
    DEFAULT_MANIFEST,
    RESULT_SCHEMA_VERSION,
    run_acceptance,
)


def test_runner_covers_workflow_and_writes_json_and_summary(tmp_path: Path) -> None:
    work_dir = tmp_path / "acceptance"
    result = run_acceptance(DEFAULT_MANIFEST, work_dir)

    assert result["schema_version"] == RESULT_SCHEMA_VERSION
    assert result["status"] == "passed"
    assert result["metrics"] == {
        "total_cases": 5,
        "passed_cases": 5,
        "failed_cases": 0,
        "total_checks": 30,
        "matched_checks": 30,
        "mismatched_checks": 0,
        "expected_failures_matched": 2,
    }

    cases = {case["id"]: case for case in result["cases"]}
    assert cases["xscr-synthetic-transfer"]["checks"]["validation"]["actual"] == "not_ready"
    assert cases["xscr-synthetic-transfer"]["checks"]["publication"]["actual"] == "blocked"
    assert cases["zeia-synthetic-partial"]["checks"]["inspect_provenance"]["details"][
        "full_zeia_export_status"
    ] == "needs_user"
    assert cases["invalid-spec-rejected"]["checks"]["spec_lint"]["actual"] == "fail"
    assert cases["malformed-xscr-rejected"]["checks"]["import"]["actual"] == "fail"
    assert all(case["checks"]["determinism"]["actual"] == "pass" for case in cases.values())

    json_path = work_dir / "acceptance-result.json"
    summary_path = work_dir / "acceptance-summary.md"
    assert json.loads(json_path.read_text(encoding="utf-8")) == result
    summary = summary_path.read_text(encoding="utf-8")
    assert "Status: **PASSED**" in summary
    assert "Expected publication boundaries" in summary
    assert "synthetic ZEIA is intentionally partial" in summary


def test_runner_keeps_materialized_zeia_inside_temp_work_dir(tmp_path: Path) -> None:
    work_dir = tmp_path / "acceptance"
    run_acceptance(DEFAULT_MANIFEST, work_dir)

    archives = sorted(work_dir.rglob("*.zeia"))
    assert len(archives) == 4
    assert all(path.is_relative_to(work_dir) for path in archives)
    assert not list(DEFAULT_MANIFEST.parent.rglob("*.zeia"))
