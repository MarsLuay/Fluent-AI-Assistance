import unittest

from tecan_reader.command_registry import (
    command_registry_path,
    load_command_registry,
    lookup_command,
    registry_command_family,
    registry_command_approved_passthrough,
    registry_command_operation,
    registry_command_support_status,
    registry_command_supported,
    registry_field_value,
    registry_manual_step,
    registry_pattern_type,
    registry_requires,
)
from tecan_reader.pattern_library import classify_command_pattern
import tecan_common.command_registry as shared_command_registry


class ReaderCommandRegistryTests(unittest.TestCase):
    def test_reader_registry_helpers_are_shared(self):
        helpers = {
            "command_registry_path": command_registry_path,
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

    def test_reader_uses_shared_registry_for_command_family_and_patterns(self):
        command = {
            "command_type": "Mca384DispenseCommand",
            "raw_type": "Tecan.Core.Scripting.Commands.Mca384.Mca384DispenseScriptCommandDataV2",
            "name": "Mca384DispenseCommand",
            "fields": {
                "LabwareName": "DestPlate",
                "Volume": "20",
                "LiquidClassName": "Water Free Single",
            },
        }

        self.assertEqual(registry_command_family(command["raw_type"]), "MCA384")
        self.assertEqual(registry_pattern_type(command["command_type"]), "dispense")
        self.assertEqual(classify_command_pattern(command), ["dispense"])
        self.assertEqual(
            registry_manual_step(command["command_type"], command["fields"]),
            "Dispense 20 uL into DestPlate using Water Free Single",
        )

    def test_reader_sees_modeled_and_approved_support_statuses(self):
        self.assertEqual(registry_command_operation("ConditionalGroup"), "conditional_branch")
        self.assertEqual(registry_command_support_status("ConditionalGroup"), "mapped")
        self.assertEqual(registry_command_operation("SetVariableStatement"), "set_variable")
        self.assertEqual(registry_command_support_status("Int32"), "approved_non_command")

    def test_reader_maps_liha_wash_as_wash_pattern(self):
        self.assertEqual(registry_command_family("LihaWashScriptCommand"), "LiHa/FCA")
        self.assertEqual(registry_pattern_type("FcaWash"), "wash")

    def test_reader_approves_low_level_hardware_driver_commands(self):
        self.assertEqual(registry_command_family("TeGioSetPWMOutputStatement"), "Application driver")
        self.assertEqual(registry_command_support_status("MoveAxisCommandScriptStatement"), "approved_passthrough")


if __name__ == "__main__":
    unittest.main()
