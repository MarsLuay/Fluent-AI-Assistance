"""Deterministic tests for the bounded external-device health contract."""

from __future__ import annotations

import json

import pytest

from fluent_pipeline.external_device_health import (
    EXTERNAL_DEVICE_DIAGNOSTIC_IDS,
    CommandRoleEvidence,
    ExternalDeviceIntegrationProfile,
    analyze_external_device_session,
    correlate_external_device_logs,
)


def _contract(
    contract_id: str,
    macro_name: str,
    *,
    module_name: str = "DeviceModule",
    companion: str | None = None,
) -> dict:
    return {
        "contract_id": contract_id,
        "macro_name": macro_name,
        "module_name": module_name,
        "command_kind": "ApplicationDriverMacro",
        "source_script": "SyntheticSource",
        "source_path": "scripts/source.xscr",
        "execution_time": "PT2S",
        "following_companion": (
            {
                "name": companion,
                "module_name": module_name,
                "execution_settings": "source-backed",
            }
            if companion
            else None
        ),
    }


def _profile(*, device_identity: str = "device-a", **evidence: str) -> ExternalDeviceIntegrationProfile:
    return ExternalDeviceIntegrationProfile.from_sources(
        module_name="DeviceModule",
        source_contracts=[_contract("init-1", "Device_Initialize")],
        host_environment={
            "fingerprint": "host-fingerprint",
            "products": [{"family": "FluentControl", "version": "3.8", "build": "399935"}],
        },
        explicit_evidence={
            "driver_identity": "source-backed-driver",
            "device_identity": device_identity,
            "transport": "USB",
            "endpoint": "USB:explicit-source-endpoint",
            "driverframework_version": "3.1.20",
            "third_party_driver_version": "2.4",
            "device_firmware": "7.0",
            "license_status": "valid",
            "license_provenance": "sanitized test evidence",
            **evidence,
        },
    )


def test_one_device_initialize_read_read_uses_only_explicit_roles_and_states() -> None:
    contracts = [
        _contract("init-1", "Device_Initialize"),
        _contract("read-1", "Device_Read"),
        _contract("read-2", "Device_Read"),
    ]
    report = analyze_external_device_session(
        _profile(),
        contracts,
        [
            CommandRoleEvidence(
                contract_id="init-1",
                macro_name="Device_Initialize",
                module_name="DeviceModule",
                role="initialize",
                provenance="imported source role map",
                result_state="Enabled & Idle",
            ),
            CommandRoleEvidence(
                contract_id="read-1",
                macro_name="Device_Read",
                module_name="DeviceModule",
                role="read",
                provenance="imported source role map",
                requires_prior_roles=("initialize",),
            ),
            CommandRoleEvidence(
                contract_id="read-2",
                macro_name="Device_Read",
                module_name="DeviceModule",
                role="read",
                provenance="imported source role map",
                requires_prior_roles=("initialize",),
            ),
        ],
    )

    assert report["state"] == "Enabled & Idle"
    assert [item["role"] for item in report["commands"]] == ["initialize", "read", "read"]
    assert report["findings"] == []


def test_independent_device_profiles_do_not_share_sessions() -> None:
    contracts = [_contract("read-1", "Device_Read")]
    role = CommandRoleEvidence(
        contract_id="read-1",
        macro_name="Device_Read",
        module_name="DeviceModule",
        role="read",
        provenance="explicit imported source mapping",
    )
    first = analyze_external_device_session(_profile(device_identity="device-a"), contracts, [role])
    second = analyze_external_device_session(_profile(device_identity="device-b"), contracts, [role])

    assert first["profile"]["device_identity"] == "device-a"
    assert second["profile"]["device_identity"] == "device-b"
    assert first["profile"]["integration_fingerprint"] != second["profile"]["integration_fingerprint"]


def test_unknown_role_does_not_invent_a_state_transition() -> None:
    report = analyze_external_device_session(
        _profile(),
        [_contract("unknown-1", "Device_Read")],
        (),
    )

    assert report["commands"][0]["role"] == "unknown"
    assert report["commands"][0]["state_after"] == "unknown"
    assert EXTERNAL_DEVICE_DIAGNOSTIC_IDS["unknown_role"] in {item["id"] for item in report["findings"]}


def test_source_backed_companion_is_retained_and_checked_in_order() -> None:
    contracts = [
        _contract("read-1", "Device_Read", companion="Device_Status"),
        _contract("status-1", "Device_Status"),
    ]
    report = analyze_external_device_session(
        _profile(),
        contracts,
        [
            CommandRoleEvidence(
                contract_id="read-1",
                macro_name="Device_Read",
                module_name="DeviceModule",
                role="read",
                provenance="source-backed sequence",
                required_companion="Device_Status",
            ),
            CommandRoleEvidence(
                contract_id="status-1",
                macro_name="Device_Status",
                module_name="DeviceModule",
                role="status",
                provenance="source-backed sequence",
            ),
        ],
    )

    assert report["commands"][0]["following_companion"]["name"] == "Device_Status"
    assert report["findings"] == []


def test_missing_initialization_is_reported_only_when_contract_requires_it() -> None:
    contract = _contract("read-1", "Device_Read")
    optional = CommandRoleEvidence(
        contract_id="read-1",
        macro_name="Device_Read",
        module_name="DeviceModule",
        role="read",
        provenance="explicit source evidence",
    )
    required = CommandRoleEvidence(
        contract_id="read-1",
        macro_name="Device_Read",
        module_name="DeviceModule",
        role="read",
        provenance="explicit source evidence",
        requires_prior_roles=("initialize",),
    )

    optional_report = analyze_external_device_session(_profile(), [contract], [optional])
    required_report = analyze_external_device_session(_profile(), [contract], [required])

    assert EXTERNAL_DEVICE_DIAGNOSTIC_IDS["initialization_missing"] not in {
        item["id"] for item in optional_report["findings"]
    }
    assert EXTERNAL_DEVICE_DIAGNOSTIC_IDS["initialization_missing"] in {
        item["id"] for item in required_report["findings"]
    }


def test_conflicting_role_evidence_is_ambiguous_without_contract_identity() -> None:
    contract = _contract("action-1", "Device_Action")
    report = analyze_external_device_session(
        _profile(),
        [contract],
        [
            CommandRoleEvidence(
                macro_name="Device_Action",
                module_name="DeviceModule",
                role="read",
                provenance="source A",
            ),
            CommandRoleEvidence(
                macro_name="Device_Action",
                module_name="DeviceModule",
                role="write",
                provenance="source B",
            ),
        ],
    )

    assert report["commands"][0]["role"] == "unknown"
    assert EXTERNAL_DEVICE_DIAGNOSTIC_IDS["role_ambiguity"] in {item["id"] for item in report["findings"]}


def test_integration_fingerprint_changes_with_external_version_evidence() -> None:
    first = _profile(driverframework_version="3.1.20", third_party_driver_version="2.4", device_firmware="7.0")
    second = _profile(driverframework_version="3.1.21", third_party_driver_version="2.4", device_firmware="7.0")
    same = _profile(driverframework_version="3.1.20", third_party_driver_version="2.4", device_firmware="7.0")

    assert first.integration_fingerprint == same.integration_fingerprint
    assert first.integration_fingerprint != second.integration_fingerprint


def test_license_blocked_initialization_does_not_claim_real_ready_state() -> None:
    profile = _profile(license_status="missing", license_provenance="sanitized License Manager evidence")
    report = analyze_external_device_session(
        profile,
        [_contract("init-1", "Device_Initialize")],
        [
            CommandRoleEvidence(
                contract_id="init-1",
                macro_name="Device_Initialize",
                module_name="DeviceModule",
                role="initialize",
                provenance="source-backed role",
                result_state="Enabled & Idle",
            )
        ],
    )

    assert report["state"] == "unknown"
    assert EXTERNAL_DEVICE_DIAGNOSTIC_IDS["license_blocked"] in {item["id"] for item in report["findings"]}


def test_log_correlation_is_explicit_and_does_not_claim_root_cause() -> None:
    report = correlate_external_device_logs(
        _profile(),
        [
            {
                "timestamp": "2026-09-22 12:00:02",
                "application": "Pegasus",
                "module": "DeviceModule",
                "source": r"C:\ProgramData\Tecan\Pegasus\Log\driver.log",
                "message": "state changed to Error",
            },
            {
                "timestamp": "2026-09-22 12:00:01",
                "application": "FluentControl",
                "module": "DeviceModule",
                "source": r"C:\ProgramData\Tecan\LoggingServer\LogFiles\run.ulf",
                "message": "Enabled & Idle",
            },
            {"timestamp": "2026-09-22 12:00:03", "module": "", "message": "timeout"},
            {
                "timestamp": "2026-09-22 12:00:04",
                "module": "OtherModule",
                "message": "unmatched device record",
            },
        ],
    )

    assert report["matched_record_count"] == 2
    assert report["ambiguous_record_count"] == 1
    assert report["unmatched_record_count"] == 1
    assert [item["application"] for item in report["events"]] == ["FluentControl", "Pegasus"]
    assert report["root_cause"] == "unknown"
    assert EXTERNAL_DEVICE_DIAGNOSTIC_IDS["error_observed"] in {item["id"] for item in report["findings"]}


def test_analyzer_never_creates_timeout_or_keepalive_semantics() -> None:
    report = analyze_external_device_session(
        _profile(),
        [_contract("read-1", "Device_Read")],
        [
            CommandRoleEvidence(
                contract_id="read-1",
                macro_name="Device_Read",
                module_name="DeviceModule",
                role="read",
                provenance="explicit source evidence",
            )
        ],
    )

    encoded = json.dumps(report, sort_keys=True).lower()
    assert "idle_timeout" not in encoded
    assert "keepalive" not in encoded
    assert report["commands"][0]["source_execution_time"] == "PT2S"


def test_non_unknown_license_status_requires_provenance() -> None:
    with pytest.raises(ValueError, match="license status requires provenance"):
        _profile(license_status="expired", license_provenance="")
