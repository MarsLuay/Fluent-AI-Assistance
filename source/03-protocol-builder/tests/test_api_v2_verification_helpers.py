import os
import unittest
from unittest import mock

from fluent_pipeline.api_v2.verification_helpers import (
    InteriorLightOptions,
    call_interior_light,
    interior_light_options_from_env,
    interior_light_policy,
    interior_light_verification_action,
    toggle_interior_light_before_prompt,
)
from fluent_pipeline.command_registry import (
    registry_command_family,
    registry_command_support_status,
    registry_command_supported,
)


class InteriorLightVerificationHelperTests(unittest.TestCase):
    def test_action_metadata_on(self):
        payload = interior_light_verification_action(on=True)
        self.assertEqual(payload["registry_command"], "InteriorLightOnStatement")
        self.assertEqual(payload["runtime_method"], "RuntimeController.InteriorLight")
        self.assertTrue(payload["observe_only"])

    def test_action_metadata_off(self):
        payload = interior_light_verification_action(on=False)
        self.assertEqual(payload["registry_command"], "InteriorLightOffStatement")

    def test_toggle_disabled_by_default(self):
        result = toggle_interior_light_before_prompt(None, at_prompt_boundary=True)
        self.assertFalse(result["invoked"])
        self.assertEqual(result["reason"], "disabled")

    def test_toggle_requires_prompt_boundary(self):
        runtime = mock.Mock()
        result = toggle_interior_light_before_prompt(
            runtime,
            options=InteriorLightOptions(interior_light_before_prompts=True),
            at_prompt_boundary=False,
        )
        self.assertFalse(result["invoked"])
        runtime.InteriorLight.assert_not_called()

    def test_toggle_calls_runtime_when_enabled(self):
        calls: list[bool] = []

        class Runtime:
            def InteriorLight(self, on_off: bool) -> None:
                calls.append(on_off)

        result = toggle_interior_light_before_prompt(
            Runtime(),
            options=InteriorLightOptions(interior_light_before_prompts=True),
            at_prompt_boundary=True,
            on=True,
        )
        self.assertTrue(result["invoked"])
        self.assertTrue(result["success"])
        self.assertEqual(calls, [True])

    def test_call_interior_light_snake_case_fallback(self):
        calls: list[bool] = []

        class Runtime:
            def interior_light(self, on_off: bool) -> None:
                calls.append(on_off)

        success, error = call_interior_light(Runtime(), on=False)
        self.assertTrue(success)
        self.assertIsNone(error)
        self.assertEqual(calls, [False])

    def test_policy_defaults_off(self):
        policy = interior_light_policy(enabled=False)
        self.assertFalse(policy["interior_light_before_prompts"])
        self.assertTrue(policy["observe_only_compile"])
        self.assertEqual(policy["enable_env"], "TECAN_INTERIOR_LIGHT_BEFORE_PROMPTS")

    def test_options_from_env(self):
        with mock.patch.dict(os.environ, {"TECAN_INTERIOR_LIGHT_BEFORE_PROMPTS": "yes"}, clear=False):
            self.assertTrue(interior_light_options_from_env().interior_light_before_prompts)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(interior_light_options_from_env().interior_light_before_prompts)


class InteriorLightRegistryTests(unittest.TestCase):
    def test_interior_light_statements_are_approved_passthroughs(self):
        for command in ("InteriorLightOnStatement", "InteriorLightOffStatement"):
            self.assertTrue(registry_command_supported(command))
            self.assertEqual(registry_command_support_status(command), "approved_passthrough")
            self.assertEqual(registry_command_family(command), "Device")


if __name__ == "__main__":
    unittest.main()
