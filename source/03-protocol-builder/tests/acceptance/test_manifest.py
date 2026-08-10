import json
import re
from pathlib import Path

from .runner import CHECK_NAMES, PROTOCOL_ROOT, validate_manifest_paths


MANIFEST_PATH = PROTOCOL_ROOT / "corpus" / "local_corpus_manifest.json"
FIXTURE_ROOT = PROTOCOL_ROOT / "tests" / "fixtures" / "acceptance"


def test_manifest_paths_and_acceptance_contract_are_complete() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    resolved = validate_manifest_paths(MANIFEST_PATH)

    assert manifest["schema_version"] == 2
    assert manifest["privacy"]["classification"] == "synthetic_public"
    assert manifest["privacy"]["contains_private_goldens"] is False
    assert len(resolved) >= 8
    assert len(manifest["acceptance_cases"]) == 5
    assert all(set(case["expectations"]) == set(CHECK_NAMES) for case in manifest["acceptance_cases"])


def test_private_goldens_remain_opt_in_and_uncommitted() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["privacy"]["private_goldens_enabled_by_default"] is False
    assert manifest["privacy"]["private_golden_environment"] == [
        "TECAN_ENABLE_PRIVATE_GOLDENS",
        "TECAN_GOLDEN_SPEC",
        "TECAN_GOLDEN_CONTEXT",
        "TECAN_GOLDEN_CONTEXT_ROOT",
    ]
    assert list(FIXTURE_ROOT.glob("*.zeia")) == []


def test_synthetic_fixture_text_has_no_private_path_or_identity_markers() -> None:
    fixture_paths = [MANIFEST_PATH, *sorted(path for path in FIXTURE_ROOT.iterdir() if path.is_file())]
    text = "\n".join(path.read_text(encoding="utf-8") for path in fixture_paths)

    assert re.search(r"/(?:Users|home)/", text) is None
    assert re.search(r"[A-Za-z]:\\", text) is None
    assert re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text) is None
    assert "confidential" not in text.lower()
