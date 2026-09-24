from __future__ import annotations

from fluentcoder.ir.schema import SubRoutineStep
from fluentcoder.simulator import analyze_subroutine_lifecycle


def _step(target: str, mode: str) -> SubRoutineStep:
    return SubRoutineStep(subroutine=target, execution_mode=mode)


def test_matching_join_consumes_async_invocation_without_launching_again() -> None:
    report = analyze_subroutine_lifecycle(
        [_step(r"Scripts\Worker", "Asynchronous"), _step(r"Scripts\Worker", "JoinSubroutine")]
    )

    assert report.status == "passed"
    assert [event["action"] for event in report.events] == ["launch", "join"]
    assert report.events[1]["happens_before"] is True
    assert report.outstanding == []


def test_wrong_join_is_stable_and_actionable() -> None:
    report = analyze_subroutine_lifecycle(
        [_step(r"Scripts\Worker", "Asynchronous"), _step(r"Scripts\Other", "JoinSubroutine")]
    )

    assert report.status == "review"
    assert report.findings[0].code == "unmatched_join"
    assert report.findings[0].candidates == (r"Scripts\Worker",)
    assert report.outstanding == [r"Scripts\Worker#1"]


def test_detached_and_unknown_modes_remain_logical_only() -> None:
    detached = analyze_subroutine_lifecycle([_step(r"Scripts\Worker", "FireAndForget")])
    unknown = analyze_subroutine_lifecycle([_step(r"Scripts\Worker", "FutureMode")])

    assert detached.status == "passed"
    assert detached.events[0]["action"] == "detach"
    assert unknown.status == "review"
    assert unknown.findings[0].code == "unknown_execution_mode"
