"""Host-environment provenance contract."""

from __future__ import annotations

from pathlib import Path

from fluent_pipeline.compatibility import build_compatibility_report
from fluent_pipeline.host_environment import (
    HOST_ENVIRONMENT_SCHEMA_VERSION,
    HostEnvironmentOverrides,
    collect_host_environment,
    drift_diagnostics,
    fingerprint_host_environment,
    load_state_inventory,
    unverified_offline_host_environment,
)
from fluent_pipeline.instrument_config import DEFAULT_INSTRUMENT_CONFIG_DIR


def _exists_map(present: set[str]):
    def exists(path: Path) -> bool:
        return str(path) in present or path.as_posix() in present

    return exists


def test_non_windows_developer_host_is_unverified() -> None:
    payload = collect_host_environment(
        os_name="posix",
        platform_name="Linux-6.8",
        exists=lambda _path: False,
        listdir=lambda _path: [],
        offline_simulator_only=True,
    )
    assert payload["schema_version"] == HOST_ENVIRONMENT_SCHEMA_VERSION
    assert payload["verified"] is False
    assert payload["host_kind"] == "unknown"
    assert payload["windows_environment"] == "non-windows"
    assert payload["offline_simulator_only"] is True
    fluent = next(item for item in payload["products"] if item["family"] == "FluentControl")
    assert fluent["detected"] is False
    assert payload["secrets_copied"] is False


def test_windows_host_without_fluentcontrol() -> None:
    payload = collect_host_environment(
        os_name="nt",
        platform_name="Windows-10",
        exists=lambda _path: False,
        listdir=lambda _path: [],
    )
    fluent = next(item for item in payload["products"] if item["family"] == "FluentControl")
    assert fluent["detected"] is False
    assert payload["windows_environment"] == "Windows-10"


def test_fluentcontrol_install_and_version_override() -> None:
    install = Path(r"C:\Program Files\Tecan\FluentControl")
    payload = collect_host_environment(
        os_name="nt",
        platform_name="Windows-11",
        exists=_exists_map({str(install), str(install / "FluentControl.exe")}),
        listdir=lambda _path: [],
        executable_version=lambda _path: "3.8.1",
        overrides=HostEnvironmentOverrides(fluentcontrol_version="3.8 SP1", fluentcontrol_build="399935"),
    )
    fluent = next(item for item in payload["products"] if item["family"] == "FluentControl")
    assert fluent["detected"] is True
    assert fluent["version"] == "3.8 SP1"
    assert fluent["build"] == "399935"
    assert fluent["version_source"] == "override"


def test_vm_evidence_is_recorded_not_an_unsupported_verdict() -> None:
    payload = collect_host_environment(
        os_name="nt",
        platform_name="Windows-11",
        exists=lambda _path: False,
        listdir=lambda _path: [],
        hypervisor_evidence=("VMware Virtual Platform",),
    )
    assert payload["host_kind"] == "virtual_machine"
    report = build_compatibility_report()
    assert report["local_host"]["host_environment"]["schema_version"] == HOST_ENVIRONMENT_SCHEMA_VERSION
    assert all(row["status"] != "unsupported_vm" for row in report["rows"])


def test_fluentcontrol_and_evoware_are_distinct_products() -> None:
    fc = Path(r"C:\Program Files\Tecan\FluentControl")
    evo = Path(r"C:\Program Files\Tecan\EVOware")
    payload = collect_host_environment(
        os_name="nt",
        exists=_exists_map({str(fc), str(evo)}),
        listdir=lambda _path: [],
    )
    families = {item["family"]: item for item in payload["products"]}
    assert families["FluentControl"]["detected"] is True
    assert families["EVOware"]["detected"] is True
    assert families["EVOware"]["scope"] == "out-of-scope"


def test_sensitive_inventory_is_metadata_only() -> None:
    root = Path(r"C:\ProgramData\Tecan")
    user_mgmt = root / "VisionX" / "UserManagement"
    payload = collect_host_environment(
        os_name="nt",
        environ={"ProgramData": r"C:\ProgramData"},
        exists=_exists_map({str(user_mgmt)}),
        listdir=lambda path: [path / "users.db"] if path == user_mgmt else [],
    )
    secret = next(item for item in payload["state_inventory"] if item["id"] == "user_management")
    assert secret["present"] is True
    assert secret["copy_into_diagnostics"] is False
    assert secret["file_count"] is None
    assert secret["fingerprint"] is None
    assert payload["secrets_copied"] is False


def test_fingerprint_changes_when_version_changes() -> None:
    first = collect_host_environment(
        os_name="nt",
        exists=lambda _path: False,
        listdir=lambda _path: [],
        overrides=HostEnvironmentOverrides(fluentcontrol_version="3.4"),
    )
    second = collect_host_environment(
        os_name="nt",
        exists=lambda _path: False,
        listdir=lambda _path: [],
        overrides=HostEnvironmentOverrides(fluentcontrol_version="3.8"),
    )
    assert first["fingerprint"] != second["fingerprint"]
    assert first["fingerprint"] == fingerprint_host_environment(first)
    findings = drift_diagnostics(first, second)
    assert any(item["code"] == "fluentcontrol_version_changed" for item in findings)


def test_offline_generation_records_unverified_host() -> None:
    payload = unverified_offline_host_environment()
    assert payload["status"] == "unverified"
    assert payload["verified"] is False
    assert payload["fingerprint"] is None


def test_instrument_config_dir_comes_from_inventory() -> None:
    assert "InstrumentConfigurations" in str(DEFAULT_INSTRUMENT_CONFIG_DIR)


def test_bundle_setup_paths_are_in_inventory() -> None:
    bat = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "run_tecan_bundle_setup.bat"
    ).read_text(encoding="utf-8", errors="replace")
    inventory = load_state_inventory()
    required = (
        "VisionX\\Config",
        "VisionX\\InstrumentConfigurations",
        "VisionX\\InstrumentInformation",
        "VisionX\\MapDataBase",
        "VisionX\\DataBase\\UserSpecific",
    )
    relatives = [rel for item in inventory["categories"] for rel in item["relative_paths"]]
    for path in required:
        assert path in bat
        assert path in relatives
