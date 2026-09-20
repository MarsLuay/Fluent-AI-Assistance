"""Reproducible FluentControl host-environment provenance."""

from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

HOST_ENVIRONMENT_SCHEMA_VERSION = "tecan.host_environment.v1"
INVENTORY_RESOURCE = Path(__file__).resolve().parent / "data" / "host_state_inventory.json"
FLUENTCONTROL_ROOT_ENV = "FLUENTCONTROL_ROOT"
EVOWARE_ROOT_ENV = "EVOWARE_ROOT"

ExistsFn = Callable[[Path], bool]
ListFn = Callable[[Path], list[Path]]


def load_state_inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_RESOURCE.read_text(encoding="utf-8"))


def programdata_tecan_root(*, environ: Mapping[str, str] | None = None) -> Path:
    env = environ or os.environ
    programdata = env.get("ProgramData") or env.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(programdata) / "Tecan"


def expand_inventory_path(relative: str, *, root: Path) -> Path:
    return (root / relative.replace("/", os.sep)).resolve()


@dataclass(frozen=True)
class HostEnvironmentOverrides:
    fluentcontrol_version: str | None = None
    fluentcontrol_build: str | None = None
    windows_environment: str | None = None
    host_kind: str | None = None
    offline_simulator_only: bool | None = None


def collect_host_environment(
    *,
    overrides: HostEnvironmentOverrides | None = None,
    environ: Mapping[str, str] | None = None,
    exists: ExistsFn | None = None,
    listdir: ListFn | None = None,
    platform_name: str | None = None,
    os_name: str | None = None,
    hypervisor_evidence: tuple[str, ...] | None = None,
    executable_version: Callable[[Path], str | None] | None = None,
    offline_simulator_only: bool = True,
) -> dict[str, Any]:
    """Return a sanitized host-environment document. Never copies secret files."""
    env = dict(environ or os.environ)
    path_exists = exists or (lambda path: path.exists())
    list_dir = listdir or _safe_listdir
    os_name = os_name if os_name is not None else os.name
    platform_name = platform_name if platform_name is not None else platform.platform()
    overrides = overrides or HostEnvironmentOverrides()

    tecan_root = programdata_tecan_root(environ=env)
    inventory = load_state_inventory()
    categories = []
    for item in inventory["categories"]:
        categories.append(_probe_category(item, tecan_root, path_exists, list_dir))

    fluent_root = _first_existing(
        [
            env.get(FLUENTCONTROL_ROOT_ENV),
            r"C:\Program Files\Tecan\FluentControl",
            r"C:\Program Files (x86)\Tecan\FluentControl",
        ],
        path_exists,
    )
    evo_root = _first_existing(
        [
            env.get(EVOWARE_ROOT_ENV),
            r"C:\Program Files\Tecan\EVOware",
            r"C:\Program Files (x86)\Tecan\EVOware",
        ],
        path_exists,
    )

    products = []
    if fluent_root is not None:
        exe = fluent_root / "FluentControl.exe"
        version = None
        if executable_version and path_exists(exe):
            version = executable_version(exe)
        products.append(
            {
                "family": "FluentControl",
                "detected": True,
                "install_root": str(fluent_root),
                "version": overrides.fluentcontrol_version or version,
                "build": overrides.fluentcontrol_build,
                "version_source": "override" if overrides.fluentcontrol_version else ("executable" if version else "install-root"),
            }
        )
    else:
        products.append(
            {
                "family": "FluentControl",
                "detected": False,
                "install_root": None,
                "version": overrides.fluentcontrol_version,
                "build": overrides.fluentcontrol_build,
                "version_source": "override" if overrides.fluentcontrol_version else "absent",
            }
        )
    if evo_root is not None:
        products.append(
            {
                "family": "EVOware",
                "detected": True,
                "install_root": str(evo_root),
                "version": None,
                "build": None,
                "version_source": "install-root",
                "scope": "out-of-scope",
            }
        )

    host_kind = overrides.host_kind or _host_kind(os_name, hypervisor_evidence)
    windows_environment = overrides.windows_environment
    if windows_environment is None and os_name == "nt":
        windows_environment = platform_name
    elif windows_environment is None:
        windows_environment = "non-windows"

    payload = {
        "schema_version": HOST_ENVIRONMENT_SCHEMA_VERSION,
        "verified": False,
        "platform": platform_name,
        "os_name": os_name,
        "windows_environment": windows_environment,
        "windows_environment_source": "override" if overrides.windows_environment else ("platform" if os_name == "nt" else "non-windows"),
        "host_kind": host_kind,
        "hypervisor_evidence": list(hypervisor_evidence or ()),
        "offline_simulator_only": (
            overrides.offline_simulator_only
            if overrides.offline_simulator_only is not None
            else offline_simulator_only
        ),
        "physical_instrument_evidence": False,
        "products": products,
        "tecan_programdata_root": str(tecan_root),
        "state_inventory": categories,
        "secrets_copied": False,
    }
    payload["fingerprint"] = fingerprint_host_environment(payload)
    return payload


def unverified_offline_host_environment() -> dict[str, Any]:
    """Record that offline generation did not inspect a live FluentControl host."""
    return {
        "schema_version": HOST_ENVIRONMENT_SCHEMA_VERSION,
        "verified": False,
        "status": "unverified",
        "reason": "offline generation; live FluentControl host was not inspected",
        "fingerprint": None,
    }


def fingerprint_host_environment(payload: Mapping[str, Any]) -> str:
    material = {
        "schema_version": payload.get("schema_version"),
        "host_kind": payload.get("host_kind"),
        "windows_environment": payload.get("windows_environment"),
        "products": payload.get("products") or [],
        "state_inventory": [
            {
                "id": item.get("id"),
                "present": item.get("present"),
                "file_count": item.get("file_count"),
                "fingerprint": item.get("fingerprint"),
            }
            for item in payload.get("state_inventory") or []
        ],
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def drift_diagnostics(previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    pairs = (
        ("windows_environment", "Windows edition/build changed"),
        ("host_kind", "Native/VM context changed"),
    )
    for key, message in pairs:
        if previous.get(key) and current.get(key) and previous.get(key) != current.get(key):
            findings.append({"code": f"host_{key}_changed", "message": message, "severity": "review"})
    prev_fc = _product(previous, "FluentControl")
    curr_fc = _product(current, "FluentControl")
    if prev_fc.get("version") and curr_fc.get("version") and prev_fc.get("version") != curr_fc.get("version"):
        findings.append({"code": "fluentcontrol_version_changed", "message": "FluentControl version/build changed", "severity": "review"})
    if (previous.get("fingerprint") or current.get("fingerprint")) and previous.get("fingerprint") != current.get("fingerprint"):
        if not findings:
            findings.append({"code": "host_fingerprint_changed", "message": "Host environment fingerprint changed", "severity": "review"})
    return findings


def _product(payload: Mapping[str, Any], family: str) -> dict[str, Any]:
    for item in payload.get("products") or []:
        if item.get("family") == family:
            return item
    return {}


def _probe_category(
    item: Mapping[str, Any],
    tecan_root: Path,
    exists: ExistsFn,
    listdir: ListFn,
) -> dict[str, Any]:
    candidates = [expand_inventory_path(rel, root=tecan_root) for rel in item.get("relative_paths") or []]
    present_paths = [path for path in candidates if exists(path)]
    file_count = 0
    total_size = 0
    names: list[str] = []
    for path in present_paths:
        if item.get("capture") == "metadata-only" or item.get("sensitivity") == "secret":
            continue
        for child in listdir(path):
            names.append(child.name)
            file_count += 1
    fingerprint = None
    if names:
        fingerprint = hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()
    return {
        "id": item["id"],
        "purpose": item["purpose"],
        "sensitivity": item["sensitivity"],
        "copy_into_diagnostics": bool(item.get("copy_into_diagnostics")) and item.get("sensitivity") != "secret",
        "capture": item.get("capture"),
        "present": bool(present_paths),
        "candidate_paths": [str(path) for path in candidates],
        "present_paths": [str(path) for path in present_paths],
        "file_count": file_count if item.get("sensitivity") != "secret" else None,
        "total_size": total_size if item.get("sensitivity") != "secret" else None,
        "fingerprint": fingerprint if item.get("sensitivity") != "secret" else None,
    }


def _first_existing(candidates: list[str | None], exists: ExistsFn) -> Path | None:
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        if exists(path):
            return path
    return None


def _host_kind(os_name: str, hypervisor_evidence: tuple[str, ...] | None) -> str:
    if hypervisor_evidence:
        joined = " ".join(hypervisor_evidence).lower()
        if "hyper-v" in joined or "vmware" in joined or "virtualbox" in joined or "kvm" in joined:
            return "virtual_machine"
        if "azure" in joined or "amazon" in joined or "google" in joined:
            return "cloud_vm"
        return "virtual_machine"
    if os_name != "nt":
        return "unknown"
    return "unknown"


def _safe_listdir(path: Path) -> list[Path]:
    try:
        return [child for child in path.iterdir() if child.is_file()]
    except OSError:
        return []
