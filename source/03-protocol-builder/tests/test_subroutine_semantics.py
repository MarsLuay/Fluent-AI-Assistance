from __future__ import annotations

from pathlib import Path

import pytest

from fluent_pipeline.api_v2 import (
    KNOWN_SUBROUTINE_EXECUTION_MODES,
    Subroutine,
    SubroutineExecutionMode,
    classify_subroutine_target,
    normalize_subroutine_execution_mode,
)
from fluent_pipeline.api_v2.types import ApiV2ValidationError
from fluentcoder.decompiler.xscr_parser import parse_xscr


FIXTURE = Path(__file__).parents[1] / "libs" / "fluentcoder" / "tests" / "fixtures" / "subroutine_call.xscr"


def test_verified_modes_are_one_shared_contract() -> None:
    assert KNOWN_SUBROUTINE_EXECUTION_MODES == {
        "Synchronous",
        "Asynchronous",
        "JoinSubroutine",
        "FireAndForget",
    }
    assert normalize_subroutine_execution_mode(SubroutineExecutionMode.JOIN_SUBROUTINE) == "JoinSubroutine"


def test_authored_unknown_mode_is_rejected_but_source_mode_is_preserved() -> None:
    with pytest.raises(ValueError):
        normalize_subroutine_execution_mode("FutureMode")
    assert normalize_subroutine_execution_mode("FutureMode", source_preserved=True) == "FutureMode"
    with pytest.raises(ApiV2ValidationError):
        Subroutine(r"Scripts\Child", execution_mode="FutureMode").validate()


def test_target_identity_does_not_guess_dynamic_names() -> None:
    assert classify_subroutine_target(r"Scripts\Child")["kind"] == "static"
    assert classify_subroutine_target({"expression": "GetSubroutinePath()"})["kind"] == "expression"
    assert classify_subroutine_target("Child")["kind"] == "unresolved"
    assert Subroutine("Child").target_identity["kind"] == "unresolved"


def test_decompiler_preserves_future_source_mode(tmp_path: Path) -> None:
    source = FIXTURE.read_text(encoding="utf-8").replace("JoinSubroutine", "FutureMode")
    path = tmp_path / "future-mode.xscr"
    path.write_text(source, encoding="utf-8")

    step = parse_xscr(path).groups[0].steps[0]

    assert step.execution_mode == "FutureMode"
    assert step.target_identity == {
        "kind": "static",
        "value": r"TEST\SUB_Minimal_v1",
        "source": r"TEST\SUB_Minimal_v1",
    }
