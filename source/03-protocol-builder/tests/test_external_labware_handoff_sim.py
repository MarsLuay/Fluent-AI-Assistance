import json
from pathlib import Path

from fluent_pipeline.external_labware_handoff_sim import (
    simulate_external_labware_handoffs,
    write_external_labware_handoff_report,
)


def _case(injected: str, **overrides):
    record = {
        "injected_outcome": injected,
        "external_position": "slot-a",
        "logical_labware": {"identity": "plate-a", "location": "Nest:1"},
        "logical_update": {"operation": "SetLocation", "identity": "plate-a", "barcode": "BC1", "location": "Nest:1"},
        "barcode": {"value": "BC1", "provenance": "user_intent"},
        "provenance": {"source": "user_intent", "evidence": ["injected"]},
    }
    record.update(overrides)
    return record


def test_injected_outcomes_keep_logical_reconciliation_separate_from_physical_readiness(tmp_path: Path):
    report = simulate_external_labware_handoffs([
        _case("success"),
        _case("failure"),
        _case("pending"),
        _case("confirmed"),
        _case("timeout"),
        _case("store"),
    ])
    assert report["physical_readiness"]["status"] == "unverified"
    assert report["physical_readiness"]["owner"] == "physical_verification"
    by_outcome = {row["injected_outcome"]: row for row in report["handoffs"]}
    assert by_outcome["success"]["logical_reconciliation"]["reconciled"] is True
    assert by_outcome["failure"]["logical_reconciliation"]["reconciled"] is False
    assert by_outcome["failure"]["diagnostics"][-1]["category"] == "device_failure"
    assert by_outcome["pending"]["logical_reconciliation"]["reconciled"] is False
    assert by_outcome["pending"]["diagnostics"][0]["code"] == "async_update_before_confirmation"
    assert by_outcome["confirmed"]["logical_reconciliation"]["reconciled"] is True
    assert by_outcome["timeout"]["logical_reconciliation"]["reconciled"] is False
    assert by_outcome["store"]["logical_reconciliation"]["reconciled"] is True
    assert all(row["physical_uncertainty"] is True for row in report["handoffs"])

    write_external_labware_handoff_report(report, tmp_path / "external_labware_handoffs.json")
    saved = json.loads((tmp_path / "external_labware_handoffs.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == "tecan.external_labware_handoffs.v1"
    assert "executable" in " ".join(saved["assumptions"])


def test_repeated_opaque_positions_keep_separate_identity_lineage():
    report = simulate_external_labware_handoffs([
        _case("store", external_position="hotel-2", logical_labware={"identity": "plate-a", "location": "Nest:1"}),
        _case("store", external_position="hotel-2", logical_labware={"identity": "plate-b", "location": "Nest:2"}),
    ])
    assert report["position_lineage"]["hotel-2"] == ["plate-a", "plate-b"]


def test_simulator_module_does_not_import_device_io():
    source = Path(__file__).parents[1].joinpath("fluent_pipeline", "external_labware_handoff_sim.py").read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert "import serial" not in source
