"""Focused driver error/recovery preservation and offline simulation tests."""

from __future__ import annotations

from pathlib import Path

from fluentcoder import xml_compat as ET
from fluentcoder.compiler.renderer import Renderer
from fluentcoder.decompiler import emit_python, parse_xscr
from fluentcoder.decompiler.xscr_parser import _parse_application_driver_macro
from fluentcoder.ir.driver_recovery import DriverRecoveryPolicy
from fluentcoder.ir.schema import ApplicationDriverMacroStep
from fluentcoder.simulator.options import SimulationOptions
from fluentcoder.worktable import Worktable


_OBJECT = """<Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
  <ApplicationDriverMacro Version="1" Name="Vendor_Run" ModuleName="VendorDevice">
    <ExecutionSettings>settings</ExecutionSettings>
    <ErrorHandling VendorMode="source">
      <ErrorCode>DEVICE_NOT_READY</ErrorCode>
      <Action>Retry</Action>
      <MaxRetries>2</MaxRetries>
    </ErrorHandling>
  </ApplicationDriverMacro>
</Object>"""


def _source_xscr(*objects: str) -> str:
    return f"""<?xml version="1.0"?><VxData><Payload><ScriptGroup><Objects>{''.join(objects)}</Objects><Name>Steps</Name></ScriptGroup></Payload></VxData>"""


def test_decompile_codegen_preserves_source_recovery_policy(tmp_path: Path) -> None:
    source = tmp_path / "driver.xscr"
    source.write_text(_source_xscr(_OBJECT), encoding="utf-8")

    protocol = parse_xscr(source)
    step = protocol.groups[0].steps[0]
    assert isinstance(step, ApplicationDriverMacroStep)
    assert step.recovery_policy is not None
    assert step.recovery_policy.error_code == "DEVICE_NOT_READY"
    assert step.recovery_policy.normalized_action == "retry"
    assert step.recovery_policy.max_retries == 2
    assert "VendorMode=\"source\"" in step.recovery_policy.raw_xml

    generated = emit_python(protocol, source_xscr=str(source))
    assert "recovery_policy=" in generated
    assert "DEVICE_NOT_READY" in generated

    step.raw_xml = None
    rendered = Renderer()._render_application_driver_macro_step(
        step,
        params={"LineNumber": "1", "IsBreakpoint": "false", "IsDisabledForExecution": "false"},
    )
    reparsed = _parse_application_driver_macro(ET.fromstring(rendered.replace("                        ", "")))
    assert reparsed.recovery_policy is not None
    assert reparsed.recovery_policy.error_code == "DEVICE_NOT_READY"
    assert reparsed.recovery_policy.normalized_action == "retry"
    assert reparsed.recovery_policy.max_retries == 2
    assert "VendorMode=\"source\"" in reparsed.recovery_policy.raw_xml


def test_authored_policy_requires_explicit_verified_element_name() -> None:
    policy = DriverRecoveryPolicy(action="retry", max_retries=1, element_name="ErrorHandling")
    worktable = Worktable(name="recovery")
    worktable.group("Steps")
    worktable.application_driver_macro("Vendor_Run", recovery_policy=policy)

    protocol = worktable.to_protocol()
    rendered = Renderer()._render_application_driver_macro_step(
        protocol.groups[0].steps[0],
        params={"LineNumber": "1", "IsBreakpoint": "false", "IsDisabledForExecution": "false"},
    )
    assert "<ErrorHandling>" in rendered
    assert "<Action>retry</Action>" in rendered


def test_offline_simulator_retries_injected_transient_failure() -> None:
    worktable = Worktable(name="recovery")
    worktable.group("Steps")
    worktable.application_driver_macro(
        "Vendor_Run",
        recovery_policy=DriverRecoveryPolicy(action="retry", max_retries=1, element_name="ErrorHandling"),
    )

    worktable.simulate(SimulationOptions(driver_outcomes={"Vendor_Run": ["transient_failure", "success"]}))
    details = worktable.simulation_report.steps[0].details
    assert details["driver_outcome"] == "transient_failure"
    assert details["selected_action"] == "retry_success"
    assert details["retry_attempts"] == 1
    assert details["execution_continues"] is True
    assert details["known_state"] is True


def test_offline_simulator_does_not_execute_unknown_recovery_action() -> None:
    worktable = Worktable(name="recovery")
    worktable.group("Steps")
    worktable.application_driver_macro(
        "Vendor_Run",
        recovery_policy=DriverRecoveryPolicy(
            action="VendorSpecificAction",
            raw_xml="<VendorRecovery><Action>VendorSpecificAction</Action></VendorRecovery>",
        ),
    )

    worktable.simulate(SimulationOptions(driver_outcomes={"Vendor_Run": "fatal_failure"}))
    details = worktable.simulation_report.steps[0].details
    assert details["selected_action"] == "unknown"
    assert details["execution_continues"] is False
    assert details["operator_action_required"] is False
