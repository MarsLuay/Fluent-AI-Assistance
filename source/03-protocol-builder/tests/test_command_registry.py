import unittest

from fluent_pipeline.command_registry import (
    command_registry_path,
    command_registry_resource,
    command_registry_sha256,
    load_command_registry,
    load_command_registry_with_provenance,
    lookup_command,
    lookup_command_by_operation,
    registry_command_approved_passthrough,
    registry_command_family,
    registry_command_operation,
    registry_command_support_status,
    registry_command_supported,
    registry_field_value,
    registry_fluentcontrol_name_confidence,
    registry_fluentcontrol_name_metadata,
    registry_fluentcontrol_name_source,
    registry_manual_step,
    registry_requires,
    registry_pattern_type,
    source_command_registry_path,
    validate_command_registry_name_provenance,
)
import tecan_common.command_registry as shared_command_registry


class CommandRegistryTests(unittest.TestCase):
    def test_common_registry_helpers_are_shared(self):
        helpers = {
            "command_registry_path": command_registry_path,
            "command_registry_resource": command_registry_resource,
            "command_registry_sha256": command_registry_sha256,
            "source_command_registry_path": source_command_registry_path,
            "load_command_registry": load_command_registry,
            "lookup_command": lookup_command,
            "registry_command_family": registry_command_family,
            "registry_command_operation": registry_command_operation,
            "registry_command_support_status": registry_command_support_status,
            "registry_command_supported": registry_command_supported,
            "registry_command_approved_passthrough": registry_command_approved_passthrough,
            "registry_pattern_type": registry_pattern_type,
            "registry_requires": registry_requires,
            "registry_field_value": registry_field_value,
            "registry_manual_step": registry_manual_step,
        }
        for name, helper in helpers.items():
            self.assertIs(helper, getattr(shared_command_registry, name))
            self.assertEqual(helper.__module__, "tecan_common.command_registry")

        self.assertEqual(lookup_command_by_operation.__module__, "fluent_pipeline.command_registry")
        self.assertEqual(registry_fluentcontrol_name_metadata.__module__, "fluent_pipeline.command_registry")
        self.assertEqual(load_command_registry_with_provenance.__module__, "fluent_pipeline.command_registry")

        with self.assertRaises(ValueError):
            validate_command_registry_name_provenance(
                {
                    "commands": {
                        "Example": {
                            "fluentcontrol_name": "Example",
                        }
                    }
                }
            )

    def test_command_registry_source_path_points_to_editable_registry_file(self):
        source_path = source_command_registry_path()
        resource_path = command_registry_resource()

        self.assertTrue(source_path.is_file())
        self.assertTrue(resource_path.is_file())
        self.assertTrue(
            source_path.as_posix().endswith("tecan_common/data/command_registry.json"),
            msg=f"unexpected source registry path: {source_path}",
        )
        self.assertIn("tecan_common/data/command_registry.json", resource_path.as_posix())

    def test_exact_command_id_maps_to_operation_contract(self):
        entry = lookup_command("Mca384AspirateScriptCommandDataV2")

        self.assertIsNotNone(entry)
        self.assertEqual(entry["operation"], "aspirate")
        self.assertEqual(entry["family"], "MCA384")
        self.assertEqual(registry_requires("Mca384AspirateScriptCommandDataV2"), ["labware", "volume_ul", "liquid_class"])

    def test_alias_and_field_map_work_for_short_names(self):
        fields = {
            "LabwareName": "SourcePlate",
            "Volume": "20",
            "LiquidClassNameBySelection": "Water Free Single",
        }

        self.assertEqual(registry_command_operation("Mca384AspirateCommand"), "aspirate")
        self.assertEqual(registry_command_family("Mca384AspirateCommand"), "MCA384")
        self.assertEqual(registry_field_value("Mca384AspirateCommand", "volume_ul", fields), "20")
        self.assertEqual(
            registry_manual_step("Mca384AspirateCommand", fields),
            "Aspirate 20 uL from SourcePlate using Water Free Single",
        )

    def test_liha_aliases_keep_specific_ir_operation(self):
        self.assertEqual(registry_command_operation("LihaDispenseScriptCommandDataV5"), "liha_dispense")
        self.assertEqual(registry_command_family("FcaDispense"), "LiHa/FCA")
        self.assertEqual(registry_command_operation("LihaWashScriptCommand"), "wash")
        self.assertEqual(registry_command_family("FcaWash"), "LiHa/FCA")

    def test_variable_and_branch_commands_map_to_ir_operations(self):
        self.assertEqual(registry_command_operation("ConditionalGroup"), "conditional_branch")
        self.assertEqual(registry_command_support_status("ConditionalGroup"), "mapped")
        self.assertTrue(registry_command_supported("ConditionalGroup"))
        self.assertFalse(registry_command_approved_passthrough("ConditionalGroup"))
        self.assertEqual(registry_command_operation("AlternateGroup"), "default_branch")
        self.assertEqual(registry_command_operation("QueryVariableStatement"), "query_variable")
        self.assertEqual(registry_command_operation("SetVariableStatement"), "set_variable")
        self.assertEqual(registry_command_operation("RUPVariableStatement"), "runtime_variable_prompt")
        self.assertEqual(
            registry_manual_step("SetVariableStatement", {"Name": "TubeCount", "Value": "8"}),
            "Set FluentControl variable TubeCount to 8",
        )

    def test_approved_non_command_typed_literals_are_supported_but_not_mapped_to_ir(self):
        self.assertIsNone(registry_command_operation("String"))
        self.assertEqual(registry_command_support_status("String"), "approved_non_command")
        self.assertTrue(registry_command_supported("String"))

    def test_user_prompt_statement_maps_to_existing_prompt_operation(self):
        self.assertEqual(registry_command_operation("UserPromptStatement"), "prompt_user")
        self.assertEqual(registry_command_support_status("UserPromptStatement"), "mapped")
        self.assertFalse(registry_command_approved_passthrough("UserPromptStatement"))

    def test_subroutine_statement_maps_to_first_class_operation(self):
        fields = {"SubRoutine": '"Demo\\SUB_Get_Fingers_v1.0"', "ExecutionMode": "JoinSubroutine"}

        self.assertEqual(registry_command_operation("SubRoutineStatement"), "call_subroutine")
        self.assertEqual(registry_command_support_status("SubRoutineStatement"), "mapped")
        self.assertFalse(registry_command_approved_passthrough("SubRoutineStatement"))
        self.assertEqual(registry_field_value("SubRoutineStatement", "subroutine", fields), '"Demo\\SUB_Get_Fingers_v1.0"')
        self.assertEqual(registry_field_value("SubRoutineStatement", "execution_mode", fields), "JoinSubroutine")

    def test_low_level_hardware_driver_commands_are_approved_passthroughs(self):
        for command in (
            "MoveAxisCommandScriptStatement",
            "StartMoveCommandScriptStatement",
            "WaitForAsyncResponseScriptStatement",
            "TeGioSetPWMOutputStatement",
        ):
            self.assertIsNone(registry_command_operation(command))
            self.assertEqual(registry_command_support_status(command), "approved_passthrough")
            self.assertEqual(registry_command_family(command), "Application driver")

    def test_fluentcontrol_name_provenance_is_traceable(self):
        metadata = registry_fluentcontrol_name_metadata("Mca384AspirateScriptCommandDataV2")
        self.assertEqual(metadata["fluentcontrol_name"], "Aspirate")
        self.assertEqual(metadata["fluentcontrol_name_source"], "fluentcontrol_control_bar")
        self.assertEqual(metadata["confidence"], "verified")

        self.assertEqual(registry_fluentcontrol_name_source("LegacyDriverMacro"), "manual_curated")
        self.assertEqual(registry_fluentcontrol_name_confidence("LegacyDriverMacro"), "medium")
        self.assertEqual(registry_fluentcontrol_name_source("GenerateReportStatement"), "source_script_observed")
        self.assertEqual(registry_fluentcontrol_name_confidence("GenerateReportStatement"), "high")


if __name__ == "__main__":
    unittest.main()
