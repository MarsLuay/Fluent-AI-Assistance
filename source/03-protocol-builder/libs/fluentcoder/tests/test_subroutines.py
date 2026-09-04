from __future__ import annotations
from fluentcoder.simulator.options import SimulationOptions



from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import Worktable  # noqa: E402
from fluentcoder.decompiler import parse_xscr  # noqa: E402
from fluentcoder.expressions import StringLiteral  # noqa: E402
from fluentcoder.ir.schema import SubRoutineStep, VariableMapping  # noqa: E402
from fluentcoder.simulator.report import EffectKind  # noqa: E402
from fluentcoder.subroutines import SubroutineRegistry  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SUBROUTINE_XSCR = FIXTURES / "subroutines" / "SUB_Minimal_v1.xscr"
FINGER_SUBROUTINE_XSCR = FIXTURES / "subroutines" / "SUB_FingerSelection_v1.xscr"
CALLER_XSCR = FIXTURES / "subroutine_call.xscr"
FINGER_CALLER_XSCR = FIXTURES / "subroutine_finger_call.xscr"


def test_subroutine_registry_register_and_resolve() -> None:
    registry = SubroutineRegistry()
    key = registry.register(SUBROUTINE_XSCR)
    assert key == r"TEST\SUB_Minimal_v1"
    assert registry.resolve(r"TEST\SUB_Minimal_v1") == SUBROUTINE_XSCR.resolve()
    assert registry.resolve("TEST/SUB_Minimal_v1") == SUBROUTINE_XSCR.resolve()
    assert registry.resolve(r'"TEST\SUB_Minimal_v1"') == SUBROUTINE_XSCR.resolve()
    assert registry.resolve("MISSING\\Sub") is None


def test_subroutine_registry_register_directory() -> None:
    registry = SubroutineRegistry()
    keys = registry.register_directory(FIXTURES / "subroutines")
    assert r"TEST\SUB_Minimal_v1" in keys


def test_parse_xscr_subroutine_finger_selection_mappings() -> None:
    proto = parse_xscr(FINGER_CALLER_XSCR)
    sub = proto.groups[0].steps[0]
    assert isinstance(sub, SubRoutineStep)
    assert sub.subroutine == r"TEST\SUB_FingerSelection_v1"
    assert sub.variable_mappings_start[0].target == "FingerSelection"
    assert sub.variable_mappings_start[0].source == "MyFinger"
    assert sub.variable_mappings_end[0].target == "OutFinger"
    assert sub.variable_mappings_end[0].source == "FingerSelection"


def test_parse_xscr_subroutine_statement() -> None:
    proto = parse_xscr(CALLER_XSCR)
    steps = proto.groups[0].steps
    assert len(steps) == 2
    sub = steps[0]
    assert isinstance(sub, SubRoutineStep)
    assert sub.subroutine == r"TEST\SUB_Minimal_v1"
    assert sub.execution_mode == "JoinSubroutine"
    assert len(sub.variable_mappings_start) == 1
    assert sub.variable_mappings_start[0].target == "cycles"
    assert sub.variable_mappings_start[0].source == StringLiteral(value="3")


def test_worktable_call_subroutine_emits_step() -> None:
    wt = Worktable(name="caller")
    wt.group("Steps")
    wt.call_subroutine(r"TEST\SUB_Minimal_v1", execution_mode="JoinSubroutine")
    step = wt.to_protocol().groups[0].steps[0]
    assert isinstance(step, SubRoutineStep)
    assert step.subroutine == r"TEST\SUB_Minimal_v1"
    assert step.execution_mode == "JoinSubroutine"


def test_simulator_subroutine_descent_inlines_body() -> None:
    registry = SubroutineRegistry()
    registry.register(SUBROUTINE_XSCR)

    wt = Worktable(name="caller")
    wt.group("Steps")
    wt.call_subroutine(r"TEST\SUB_Minimal_v1")
    wt.wait(1)

    wt.simulate(SimulationOptions(subroutine_registry=registry))

    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == 4
    assert report.opaque_noop_steps == 0
    assert report.validation_only_steps == 4
    assert report.modeled_coverage == pytest.approx(1.0)
    effects = [entry.effect for entry in report.steps]
    assert effects.count(EffectKind.VALIDATION_ONLY) == 4


def test_simulator_subroutine_opaque_without_registry() -> None:
    wt = Worktable(name="caller")
    wt.group("Steps")
    wt.call_subroutine(r"TEST\SUB_Minimal_v1")
    wt.wait(1)

    wt.simulate()

    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == 2
    assert report.opaque_noop_steps == 1
    assert report.validation_only_steps == 1


def test_simulator_subroutine_cycle_detection() -> None:
    registry = SubroutineRegistry()
    registry.register(FIXTURES / "subroutines" / "SUB_Recursive_v1.xscr")

    wt = Worktable(name="recursive")
    wt.group("Steps")
    wt.call_subroutine(r"TEST\SUB_Recursive_v1")

    wt.simulate(SimulationOptions(subroutine_registry=registry))

    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == 2
    assert report.opaque_noop_steps == 1
    assert report.validation_only_steps == 1
    messages = [entry.message or "" for entry in report.steps]
    assert any("cycle detected" in message.lower() for message in messages)


def test_simulator_subroutine_variable_mappings_literal_start() -> None:
    registry = SubroutineRegistry()
    registry.register(SUBROUTINE_XSCR)

    wt = Worktable(name="caller")
    wt.group("Steps")
    wt.call_subroutine(
        r"TEST\SUB_Minimal_v1",
        execution_mode="JoinSubroutine",
        variable_mappings_start=[VariableMapping(target="cycles", source="3")],
    )
    wt.wait(1)

    wt.simulate(SimulationOptions(subroutine_registry=registry))

    report = wt.simulation_report
    assert report is not None
    assert report.total_executed_steps == 4
    assert report.opaque_noop_steps == 0


def test_simulator_subroutine_variable_mappings_finger_selection() -> None:
    registry = SubroutineRegistry()
    registry.register(FINGER_SUBROUTINE_XSCR)

    wt = Worktable(name="finger_caller")
    wt.set_sim_value("MyFinger", 3)
    wt.group("Steps")
    wt.call_subroutine(
        r"TEST\SUB_FingerSelection_v1",
        execution_mode="JoinSubroutine",
        variable_mappings_start=[
            VariableMapping(target="FingerSelection", source="MyFinger"),
        ],
        variable_mappings_end=[
            VariableMapping(target="OutFinger", source="FingerSelection"),
        ],
    )
    wt.wait(1)

    wt.simulate(SimulationOptions(subroutine_registry=registry))

    report = wt.simulation_report
    assert report is not None
    # SubRoutineStep + LoopStep + 3 looped comments + caller wait
    assert report.total_executed_steps == 6
    assert report.opaque_noop_steps == 0
    assert wt.sim_values["OutFinger"] == 3

