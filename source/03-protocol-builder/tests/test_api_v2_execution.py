import unittest

from fluent_pipeline.api_v2_execution import (
    ABORT_REASON_BLOCKED_USER_PROMPT,
    ABORT_REASON_EXTERNAL_TIMEOUT,
    ExecutionAbortContext,
    SteppedExecutionTracker,
    abort_execution_channel,
    execution_abort_from_blocked_user_prompt,
    execution_abort_from_external_timeout,
    merge_execution_abort_into_report,
    perform_runtime_teardown,
    render_execution_abort_markdown,
)


class _FakeChannel:
    def __init__(self, *, abort_error: str | None = None) -> None:
        self.abort_error = abort_error
        self.calls: list[str] = []

    def AbortExecution(self) -> None:
        self.calls.append("AbortExecution")
        if self.abort_error:
            raise RuntimeError(self.abort_error)

    def FinishExecution(self) -> None:
        self.calls.append("FinishExecution")

    def Dispose(self) -> None:
        self.calls.append("Dispose")


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def StopMethod(self) -> None:
        self.calls.append("StopMethod")

    def CloseMethod(self) -> None:
        self.calls.append("CloseMethod")


class ApiV2ExecutionAbortTests(unittest.TestCase):
    def test_tracker_records_last_command_index(self) -> None:
        tracker = SteppedExecutionTracker()
        tracker.begin_command(3, "UserPrompt", step_id="step_009")
        abort = execution_abort_from_blocked_user_prompt(
            "Operator prompt blocked unattended Gate 27 run.",
            tracker=tracker,
        )
        self.assertEqual(abort.reason, ABORT_REASON_BLOCKED_USER_PROMPT)
        self.assertEqual(abort.last_command_index, 3)
        self.assertEqual(abort.last_command_type, "UserPrompt")
        self.assertEqual(abort.last_step_id, "step_009")

    def test_perform_runtime_teardown_orders_abort_before_stop_and_close(self) -> None:
        channel = _FakeChannel()
        runtime = _FakeRuntime()
        abort = ExecutionAbortContext(reason="timeout", message="Step timed out.")
        result = perform_runtime_teardown(
            channel=channel,
            runtime=runtime,
            abort_context=abort,
        )
        self.assertTrue(result.abort_execution_called)
        self.assertTrue(result.stop_method_called)
        self.assertTrue(result.close_method_called)
        self.assertTrue(result.channel_disposed)
        self.assertEqual(channel.calls, ["AbortExecution", "Dispose"])
        self.assertEqual(runtime.calls, ["StopMethod", "CloseMethod"])

    def test_abort_execution_channel_reports_error_without_raising(self) -> None:
        channel = _FakeChannel(abort_error="channel busy")
        called, error = abort_execution_channel(channel)
        self.assertFalse(called)
        self.assertIn("channel busy", error or "")

    def test_merge_execution_abort_into_report(self) -> None:
        abort = execution_abort_from_external_timeout("External provider timed out.")
        report = merge_execution_abort_into_report({"ok": False, "details": {}}, abort)
        nested = report["details"]["execution_abort"]
        self.assertEqual(nested["reason"], ABORT_REASON_EXTERNAL_TIMEOUT)
        self.assertEqual(nested["message"], "External provider timed out.")

    def test_render_execution_abort_markdown(self) -> None:
        abort = ExecutionAbortContext(
            reason="timeout",
            message="Timed out waiting for UserPrompt.",
            last_command_index=7,
            last_command_type="UserPrompt",
            abort_execution_called=True,
        )
        text = "\n".join(render_execution_abort_markdown(abort.as_dict()))
        self.assertIn("Execution abort", text)
        self.assertIn("Last command index", text)
        self.assertIn("UserPrompt", text)


if __name__ == "__main__":
    unittest.main()
