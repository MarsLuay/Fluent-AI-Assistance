from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


APP_PATH = Path(__file__).with_name("tecan_prompt_builder_app.py")
SPEC = importlib.util.spec_from_file_location("tecan_prompt_builder_app_test", APP_PATH)
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = app
SPEC.loader.exec_module(app)


def test_detect_only_runs_without_site_packages() -> None:
    result = subprocess.run(
        [sys.executable, "-S", str(APP_PATH.with_name("tecan_prompt_builder.py")), "--detect-only"],
        cwd=APP_PATH.parents[3],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_choice_uses_back_after_the_first_question(monkeypatch: pytest.MonkeyPatch) -> None:
    app.set_back_allowed(True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "0")

    with pytest.raises(app.BackRequested):
        app.ask_choice("Question", ["Choice"])


def test_multi_zeia_summary_keeps_each_full_export_assessment() -> None:
    source = app.SourceChoice(
        kind="zeia_path",
        label="Multiple ZEIA files",
        manifest={
            "sources": [
                {"full_zeia_export": {"status": "pass", "summary": "first"}},
                {"full_zeia_export": {"status": "needs_user", "summary": "second"}},
            ]
        },
    )

    summary = app.source_summary_for_prompt(source)

    assert "Full ZEIA export checks: pass, needs_user" in summary
    assert "Full ZEIA summaries: first; second" in summary
