import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.api_v2_stepped_runner import (
    RecordingExecutionChannel,
    RegistryValidationExecutionChannel,
    SteppedRunner,
    extract_commands_from_xscr,
    map_ir_steps_to_commands,
    resolve_commands,
)


_SAMPLE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Stepped Runner Demo</ObjectName>
    <ScriptGroup>
      <Objects>
        <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
          <ScriptGroupDataV1>
            <Name>Setup</Name>
            <Statements>
              <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
                <AddLabwareDataV1>
                  <LabwareType>96 Well Flat</LabwareType>
                  <LabwareLable>Plate1</LabwareLable>
                  <Location>Site</Location>
                  <Position>1</Position>
                </AddLabwareDataV1>
              </Object>
              <Object Type="Tecan.Core.Scripting.UserPromptStatement">
                <UserPromptStatement>
                  <Prompt>Confirm deck state.</Prompt>
                </UserPromptStatement>
              </Object>
            </Statements>
          </ScriptGroupDataV1>
        </Object>
      </Objects>
    </ScriptGroup>
  </Payload>
</VxData>
"""


class ApiV2SteppedRunnerTests(unittest.TestCase):
    def test_extract_commands_from_xscr_maps_statement_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "demo.xscr"
            xscr.write_text(_SAMPLE_XSCR, encoding="utf-8")
            commands = extract_commands_from_xscr(xscr)

        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0].type_name, "AddLabwareDataV1")
        self.assertEqual(commands[0].operation, "add_labware")
        self.assertEqual(commands[0].api_v2_type, "AddLabware")
        self.assertGreater(len(commands[0].execute_xml), 0)
        self.assertEqual(commands[1].type_name, "UserPromptStatement")
        self.assertEqual(commands[1].operation, "prompt_user")
        self.assertEqual(commands[1].api_v2_type, "UserPrompt")

    def test_map_ir_steps_to_commands(self):
        ir = {
            "steps": [
                {
                    "group": "Setup",
                    "operation": "comment",
                    "command_id": "CommentStatement",
                    "parameters": {"comment": "hello"},
                },
                {
                    "group": "Checks",
                    "operation": "prompt_user",
                    "command_id": "UserPromptStatement",
                    "parameters": {"prompt": "Ready?"},
                },
            ]
        }
        commands = map_ir_steps_to_commands(ir)
        self.assertEqual([item.type_name for item in commands], ["CommentStatement", "UserPromptStatement"])

    def test_stepped_runner_calls_finish_execution_after_each_command(self):
        channel = RecordingExecutionChannel()
        commands = extract_commands_from_xscr(self._write_xscr())
        result = SteppedRunner(channel).run(method="Demo Method", commands=commands)

        self.assertTrue(result.ok)
        self.assertEqual(len(channel.execute_calls), 2)
        self.assertEqual(len(channel.finish_calls), 2)
        self.assertEqual(len(result.execution_steps), 2)
        self.assertTrue(all(step.get("ok") for step in result.execution_steps))

    def test_stepped_runner_executes_commands_one_at_a_time(self):
        channel = RecordingExecutionChannel()
        commands = extract_commands_from_xscr(self._write_xscr())
        result = SteppedRunner(channel).run(method="Demo Method", commands=commands)

        self.assertTrue(result.ok)
        self.assertEqual(result.commands_executed, 2)
        self.assertEqual(len(channel.execute_calls), 2)
        self.assertTrue(channel.stopped)
        self.assertTrue(channel.closed)
        self.assertEqual(channel.prepare_calls, [("Demo Method", True)])

    def test_stepped_runner_stops_on_first_failure(self):
        channel = RecordingExecutionChannel(fail_at=1, fail_error="invalid liquid class")
        commands = extract_commands_from_xscr(self._write_xscr())
        result = SteppedRunner(channel).run(method="Demo Method", commands=commands)

        self.assertFalse(result.ok)
        self.assertEqual(result.failed_index, 1)
        self.assertEqual(result.commands_executed, 1)
        self.assertIn("invalid liquid class", result.summary)

    def test_registry_validation_channel_passes_known_commands(self):
        channel = RegistryValidationExecutionChannel()
        commands = extract_commands_from_xscr(self._write_xscr())
        result = SteppedRunner(channel).run(method="Demo Method", commands=commands)

        self.assertTrue(result.ok, result.runtime_errors)
        self.assertEqual(result.commands_executed, 2)
        self.assertEqual(len(result.execution_steps), 2)

    def test_registry_validation_channel_fails_on_unknown_command_type(self):
        xscr_text = _SAMPLE_XSCR.replace(
            "UserPromptStatement",
            "TotallyUnknownRuntimeCommand",
        )
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "demo.xscr"
            xscr.write_text(xscr_text, encoding="utf-8")
            commands = extract_commands_from_xscr(xscr)

        result = SteppedRunner(RegistryValidationExecutionChannel()).run(
            method="Demo Method",
            commands=commands,
        )
        self.assertFalse(result.ok)

    def test_resolve_commands_requires_artifact(self):
        commands, error = resolve_commands(xscr_path=None)
        self.assertEqual(commands, [])
        self.assertIn("No compiled XSCR", error)

    def _write_xscr(self) -> Path:
        tmp = tempfile.mkdtemp()
        xscr = Path(tmp) / "demo.xscr"
        xscr.write_text(_SAMPLE_XSCR, encoding="utf-8")
        return xscr


if __name__ == "__main__":
    unittest.main()
