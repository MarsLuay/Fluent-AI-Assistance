"""Deterministic source-backed motion/pathfinder compatibility tests."""

from __future__ import annotations

import json
from pathlib import Path

from fluent_pipeline.motion_compatibility import (
    MOTION_COMPATIBILITY_SCHEMA_VERSION,
    build_motion_compatibility_report,
    correlate_motion_logs,
    load_motion_defect_registry,
    render_motion_compatibility_markdown,
)


FIXTURES = Path(__file__).parent / "fixtures" / "motion_compatibility"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _statuses(report: dict) -> dict[str, str]:
    return {item["vendor_issue_id"]: item["status"] for item in report["findings"]}


def test_registry_retains_authoritative_issue_provenance() -> None:
    registry = load_motion_defect_registry()

    assert registry["schema_version"] == "tecan.fluentcontrol_motion_defect_registry.v1"
    assert registry["registry_source"]["document_version"] == "3.8.16.1118"
    assert {item["vendor_issue_id"] for item in registry["issues"]} == {"120382", "120985"}
    assert all(item["fixed_in"] == "3.5" for item in registry["issues"])
    assert "tecan.com" in registry["registry_source"]["url"]


def test_pre_fix_mca384_pathfinder_signal_is_affected() -> None:
    fixture = _fixture("mca384_pathfinder.json")
    report = build_motion_compatibility_report(**fixture)

    assert report["schema_version"] == MOTION_COMPATIBILITY_SCHEMA_VERSION
    assert _statuses(report) == {"120382": "affected", "120985": "trigger_not_present"}
    finding = next(item for item in report["findings"] if item["vendor_issue_id"] == "120382")
    assert finding["trigger_status"] == "present"
    assert finding["diagnostic"]["root_cause"] == "unknown"
    assert finding["diagnostic"]["pipetting_root_cause"] == "not_established"


def test_post_fix_rga_contouring_signal_is_fixed() -> None:
    fixture = _fixture("rga_contouring.json")
    report = build_motion_compatibility_report(**fixture)

    assert _statuses(report) == {"120382": "trigger_not_present", "120985": "fixed"}
    finding = next(item for item in report["findings"] if item["vendor_issue_id"] == "120985")
    assert finding["diagnostic"]["log_correlated"] is True


def test_unknown_version_stays_unknown_when_the_trigger_is_present() -> None:
    fixture = _fixture("mca384_pathfinder.json")
    fixture["host_environment"]["products"][0]["version"] = None

    report = build_motion_compatibility_report(**fixture)

    assert _statuses(report)["120382"] == "unknown"
    assert _statuses(report)["120985"] == "trigger_not_present"


def test_unrelated_workflow_has_no_motion_trigger_and_no_geometry_model() -> None:
    report = build_motion_compatibility_report(
        {"steps": [{"id": "aspirate", "operation": "aspirate"}]},
        host_environment={
            "schema_version": "tecan.host_environment.v1",
            "fingerprint": "fixture-host",
            "products": [{"family": "FluentControl", "version": "3.4", "build": "fixture", "version_source": "fixture"}],
        },
    )

    assert _statuses(report) == {"120382": "trigger_not_present", "120985": "trigger_not_present"}
    assert report["physical_readiness"] == {
        "status": "not_evaluated",
        "owner": "#160",
        "separate": True,
        "note": "Motion compatibility does not evaluate geometry, clearance, calibration, maintenance, accuracy, or hardware-run readiness.",
    }
    assert "geometry" not in report["protocol_motion"]


def test_log_correlation_is_stable_and_ignores_unrelated_records() -> None:
    first = correlate_motion_logs([
        {"timestamp": "2026-09-22 12:00:01", "message": "unrelated"},
        {"timestamp": "2026-09-22 12:00:00", "channel": "PathFinding", "message": "MCA384 arm move"},
    ])
    second = correlate_motion_logs([
        {"timestamp": "2026-09-22 12:00:01", "message": "unrelated"},
        {"timestamp": "2026-09-22 12:00:00", "channel": "PathFinding", "message": "MCA384 arm move"},
    ])

    assert first == second
    assert first["matched_record_count"] == 1
    assert first["unmatched_record_count"] == 1
    assert first["records"][0]["features"] == ["mca384_arm_motion", "pathfinder"]
    assert first["root_cause"] == "unknown"


def test_markdown_rendering_is_deterministic() -> None:
    fixture = _fixture("mca384_pathfinder.json")
    report = build_motion_compatibility_report(**fixture)

    assert render_motion_compatibility_markdown(report) == render_motion_compatibility_markdown(report)
