from __future__ import annotations

import inspect
import json
import sys

from full_export_e2e.harness import (
    MANIFEST_PATH,
    RECIPE_PATH,
    load_feature_manifest,
    resolved_features,
)


def test_feature_manifest_covers_every_live_surface() -> None:
    features = {item["id"]: item for item in resolved_features()}
    missing_tests = [
        item["id"]
        for item in features.values()
        if not item.get("tests")
    ]
    missing_gap_reason = [
        item["id"]
        for item in features.values()
        if item.get("coverage") == "declared_gap" and not item.get("gap_reason")
    ]
    assert missing_tests == [], f"features missing mapped tests: {missing_tests}"
    assert missing_gap_reason == [], f"declared gaps missing reasons: {missing_gap_reason}"

    import test_full_export_e2e as e2e

    available = {
        name
        for module in (e2e, sys.modules[__name__])
        for name, value in vars(module).items()
        if name.startswith("test_") and inspect.isfunction(value)
    }
    unknown = sorted(
        {
            test_name
            for item in features.values()
            for test_name in item.get("tests") or []
            if test_name not in available
        }
    )
    assert unknown == [], f"manifest maps missing tests: {unknown}"


def test_feature_manifest_is_synthetic_and_fail_closed() -> None:
    manifest = load_feature_manifest()
    assert manifest["schema_version"] == 1
    assert manifest["hardware_readiness_claim"] is False
    assert manifest["privacy"]["classification"] == "synthetic_public"
    assert manifest["privacy"]["contains_private_goldens"] is False
    assert RECIPE_PATH.is_file()
    assert MANIFEST_PATH.is_file()
    fixture = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    assert fixture["classification"] == "synthetic_public"
    assert not list(RECIPE_PATH.parent.glob("*.zeia"))
