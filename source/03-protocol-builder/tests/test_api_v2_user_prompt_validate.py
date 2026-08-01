import unittest

from fluent_pipeline.api_v2_stepped_inventory import (
    ICommand,
    RecordingExecutionChannel,
    SteppedRunner,
)
from fluent_pipeline.api_v2_user_prompt_validate import (
    extract_prompt_message,
    validate_user_prompt_before_execute,
    validate_user_prompt_offline,
)


class UserPromptValidateTests(unittest.TestCase):
    def test_extract_prompt_message_from_xscr_payload(self):
        command = ICommand(
            type_name="UserPromptStatement",
            index=0,
            group="Checks",
            payload_xml="<UserPromptStatement><Prompt>Confirm deck state.</Prompt></UserPromptStatement>",
        )
        self.assertEqual(extract_prompt_message(command), "Confirm deck state.")

    def test_offline_validate_passes_real_prompt(self):
        command = ICommand(
            type_name="UserPromptStatement",
            index=0,
            group="Checks",
            operation="prompt_user",
            payload_xml="<UserPromptStatement><Prompt>Ready?</Prompt></UserPromptStatement>",
        )
        result = validate_user_prompt_offline(command)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "prompt_text_quality")

    def test_offline_validate_blocks_empty_prompt(self):
        command = ICommand(
            type_name="UserPromptStatement",
            index=1,
            group="Checks",
            operation="prompt_user",
            payload_xml="<UserPromptStatement><Prompt>   </Prompt></UserPromptStatement>",
        )
        result = validate_user_prompt_offline(command)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "empty_prompt")
        self.assertEqual(result.source, "prompt_text_quality")

    def test_offline_validate_blocks_placeholder(self):
        command = ICommand(
            type_name="UserPromptStatement",
            index=1,
            group="Checks",
            operation="prompt_user",
            payload_xml="<UserPromptStatement><Prompt>TODO</Prompt></UserPromptStatement>",
        )
        result = validate_user_prompt_offline(command)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "placeholder_prompt")
        self.assertEqual(result.source, "prompt_text_quality")

    def test_native_validate_failure_surfaces_runtime_error(self):
        command = ICommand(
            type_name="UserPromptStatement",
            index=0,
            group="Checks",
            operation="prompt_user",
            payload_xml="<UserPromptStatement><Prompt>OK</Prompt></UserPromptStatement>",
        )

        def _fail() -> None:
            raise ValueError("UserPrompt.Validate(): prompt text is required.")

        result = validate_user_prompt_before_execute(command, native_validate=_fail)
        self.assertFalse(result.ok)
        self.assertEqual(result.source, "native")
        self.assertIn("UserPrompt.Validate", result.message)

    def test_stepped_runner_blocks_before_execute_on_empty_prompt(self):
        channel = RecordingExecutionChannel()
        command = ICommand(
            type_name="UserPromptStatement",
            index=0,
            group="Checks",
            operation="prompt_user",
            payload_xml="<UserPromptStatement><Prompt>   </Prompt></UserPromptStatement>",
        )
        result = SteppedRunner(channel).run(method="Demo", commands=[command])

        self.assertFalse(result.ok)
        self.assertEqual(result.commands_executed, 0)
        self.assertEqual(len(channel.execute_calls), 0)
        self.assertTrue(result.runtime_errors)
        self.assertEqual(result.command_log[0]["validate"]["reason"], "empty_prompt")

    def test_stepped_runner_blocks_placeholder_before_execute(self):
        channel = RecordingExecutionChannel()
        command = ICommand(
            type_name="UserPromptStatement",
            index=0,
            group="Checks",
            operation="prompt_user",
            payload_xml="<UserPromptStatement><Prompt>TODO</Prompt></UserPromptStatement>",
        )
        result = SteppedRunner(channel).run(method="Demo", commands=[command])

        self.assertFalse(result.ok)
        self.assertEqual(result.commands_executed, 0)
        self.assertIn("placeholder", result.runtime_errors[0].casefold())


if __name__ == "__main__":
    unittest.main()
