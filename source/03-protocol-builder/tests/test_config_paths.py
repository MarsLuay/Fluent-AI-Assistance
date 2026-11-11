from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from fluent_pipeline import config
from tecan_common import native_acceleration


def test_default_fluentcoder_python_uses_repo_level_venv() -> None:
    expected = config.TECAN_AI_DIR / ".venv"
    expected_python = expected / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    assert config.DEFAULT_FLUENTCODER_PYTHON == expected_python
    assert config.fluentcoder_python() == expected_python
    assert config.DEFAULT_FLUENTCODER_PYTHON.parent.parent == expected
    assert config.DEFAULT_FLUENTCODER_PYTHON.parent.parent != config.PROJECT_DIR / ".venv"


def test_portable_repo_path_uses_repo_relative_placeholder() -> None:
    inner = config.REPO_ROOT / "source" / "03-protocol-builder" / "AGENTS.md"
    assert config.portable_repo_path(inner) == "<repo>/source/03-protocol-builder/AGENTS.md"


def test_portable_repo_path_keeps_paths_outside_the_repo() -> None:
    outside = Path("/tmp/fluent-hygiene-outside")
    rendered = config.portable_repo_path(outside)
    assert not rendered.startswith("<repo>/")


def test_safe_log_label_uses_native_ascii_lower_with_unicode_fallback() -> None:
    values = ["My Label-2", "  Ångström Log  ", "A__B", "___"]
    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = [config._safe_log_label(value) for value in values]
    assert expected == ["my-label-2", "ångström-log", "a-b", "generation"]

    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            native_acceleration,
            "ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = [config._safe_log_label(value) for value in values]
            assert native_call.call_count == len(values)
        native_call.assert_any_call("My Label-2")
        native_call.assert_any_call("  Ångström Log  ")
    assert actual == expected

    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(native_acceleration, "ascii_lower", return_value=None):
            forced_fallback = [config._safe_log_label(value) for value in values]
    assert forced_fallback == expected
