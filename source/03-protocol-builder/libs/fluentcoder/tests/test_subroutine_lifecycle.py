from __future__ import annotations

from fluentcoder.ir.schema import SubRoutineStep, VariableMapping
from fluentcoder.ir.subroutine_semantics import subroutine_generation_mode
from fluentcoder.simulator.subroutine_lifecycle import (
    analyze_subroutine_lifecycle,
    handoff_reconcile_allowed,
    preserve_source_async_joins,
)
from fluentcoder.decompiler.codegen import _emit_call_subroutine


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


def test_wrong_wait_target_blocks_handoff_until_the_named_join() -> None:
    wrong = analyze_subroutine_lifecycle(
        [_step(r"Scripts\Worker", "Asynchronous"), _step(r"Scripts\Other", "JoinSubroutine")]
    )
    corrected = analyze_subroutine_lifecycle(
        [_step(r"Scripts\Worker", "Asynchronous"), _step(r"Scripts\Worker", "JoinSubroutine")]
    )

    assert handoff_reconcile_allowed(wrong, r"Scripts\Worker") is False
    assert handoff_reconcile_allowed(corrected, r"Scripts\Worker") is True


def test_generation_keeps_source_async_join_and_rejects_delay() -> None:
    launch = _step(r"Scripts\Worker", "Asynchronous")
    join = _step(r"Scripts\Worker", "JoinSubroutine")
    launch.variable_mappings_start = [VariableMapping(target="Plate", source="Plate")]
    assert subroutine_generation_mode({}) == "Synchronous"
    assert subroutine_generation_mode({"execution_mode": "Asynchronous"}) == "Asynchronous"
    assert "execution_mode='Asynchronous'" in _emit_call_subroutine(launch, set())
    assert "execution_mode='JoinSubroutine'" in _emit_call_subroutine(join, set())
    assert "VariableMapping" in _emit_call_subroutine(launch, set())

    assert preserve_source_async_joins([launch, join], [launch, join]) == []
    delayed = preserve_source_async_joins(
        [launch, join],
        [launch, {"kind": "delay"}, _step(r"Scripts\Other", "JoinSubroutine")],
    )
    assert {finding.code for finding in delayed} >= {"replaced_with_delay", "wrong_join_target"}
