"""Validate the repository compatibility inventory against live surfaces."""

from __future__ import annotations

import json
from pathlib import Path

import tecan_reader
from fluent_pipeline.project_context import PROJECT_MANIFEST_SCHEMA_VERSION
from tecan_reader.project_index import SCHEMA_VERSION as INDEX_SCHEMA_VERSION

from full_export_e2e.harness import (
    live_cli_commands,
    live_mcp_tools,
    live_reader_commands,
    live_worklist_commands,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = REPO_ROOT / "compatibility-inventory.json"
POLICY_PATH = REPO_ROOT / "COMPATIBILITY.md"
ALLOWED_CLASSES = {"supported", "deprecated", "internal", "removed"}
REQUIRED_FIELDS = {"id", "class", "kind", "path", "reason"}


def _inventory() -> dict:
    payload = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _surfaces_by_id(payload: dict) -> dict[str, dict]:
    surfaces = payload["surfaces"]
    by_id = {}
    for item in surfaces:
        assert set(REQUIRED_FIELDS) <= set(item)
        assert item["class"] in ALLOWED_CLASSES
        assert item["id"] not in by_id
        by_id[item["id"]] = item
    return by_id


def test_policy_files_exist() -> None:
    assert POLICY_PATH.is_file()
    payload = _inventory()
    assert payload["policyVersion"] == "fluent.compatibility.v1"
    assert payload["policyPath"] == "COMPATIBILITY.md"
    assert "backward compatible" in POLICY_PATH.read_text(encoding="utf-8").lower() or "Compatibility" in POLICY_PATH.read_text(encoding="utf-8")


def test_live_cli_mcp_and_reader_surfaces_are_classified() -> None:
    by_id = _surfaces_by_id(_inventory())
    expected = {f"cli.{name}" for name in live_cli_commands()}
    expected.update(f"mcp.{name}" for name in live_mcp_tools())
    expected.update(f"reader.{name}" for name in live_reader_commands())
    expected.update(f"worklist.{name}" for name in live_worklist_commands())
    missing = sorted(item for item in expected if item not in by_id)
    extra_live = sorted(
        item
        for item, row in by_id.items()
        if item.startswith(("cli.", "mcp.", "reader.", "worklist."))
        and item not in expected
        and row["class"] == "supported"
    )
    assert not missing, missing
    assert not extra_live, extra_live
    for item in expected:
        assert by_id[item]["class"] == "supported", item


def test_installed_and_python_exports_are_classified() -> None:
    by_id = _surfaces_by_id(_inventory())
    for key in (
        "installed.protocol-builder",
        "installed.tecan-ai-mcp",
        "installed.project-reader",
        "installed.tecan-reader",
        "python.application_services",
        "persist.project_manifest",
        "persist.project_index",
        "compact.project_query",
    ):
        assert by_id[key]["class"] == "supported", key
    for name in tecan_reader.__all__:
        assert by_id[f"python.tecan_reader.{name}"]["class"] == "supported"
    extra = sorted(
        item.removeprefix("python.tecan_reader.")
        for item, row in by_id.items()
        if item.startswith("python.tecan_reader.") and row["class"] == "supported"
        if item.removeprefix("python.tecan_reader.") not in tecan_reader.__all__
    )
    assert not extra


def test_removed_surfaces_stay_removed() -> None:
    by_id = _surfaces_by_id(_inventory())
    assert by_id["removed.obsidian_vault_auto_discovery"]["class"] == "removed"
    assert by_id["removed.agents_md_orig"]["class"] == "removed"
    assert by_id["removed.developer_home_paths"]["class"] == "removed"
    protocol_root = REPO_ROOT / "source" / "03-protocol-builder" / "fluent_pipeline"
    for path in protocol_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "obsidian_vault_root" not in source
        assert "discover_vault_root_zeia" not in source
    orig_hits = list(REPO_ROOT.rglob("AGENTS.md.orig"))
    assert orig_hits == []
    assert by_id["persist.project_manifest"]["supportedVersion"] == str(PROJECT_MANIFEST_SCHEMA_VERSION)
    assert by_id["persist.project_index"]["supportedVersion"] == str(INDEX_SCHEMA_VERSION)
