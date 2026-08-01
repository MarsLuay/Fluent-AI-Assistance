import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from fluent_pipeline.api_v2_subroutine_validate import (
    extract_subroutine_path,
    load_runtime_script_inventory,
    resolve_subroutine_against_inventory,
    validate_subroutine_before_execute,
    validate_subroutine_fields,
    validate_subroutines_after_load,
)


@dataclass
class _FakeCommand:
    type_name: str
    index: int
    group: str
    payload_xml: str


_SUBROUTINE_XSCR = """<Object Type="Tecan.Core.Scripting.SubRoutineStatement">
  <SubRoutineStatement>
    <SubRoutine>"Demo\\SUB_Get_Fingers_v1.0"</SubRoutine>
    <Mode><ExecutionMode>JoinSubroutine</ExecutionMode></Mode>
  </SubRoutineStatement>
</Object>"""


class ApiV2SubroutineValidateTests(unittest.TestCase):
    def test_validate_subroutine_fields_requires_folder_prefix(self):
        result = validate_subroutine_fields("BareName")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "field_validate_failed")

    def test_extract_subroutine_path_from_payload(self):
        command = _FakeCommand(
            type_name="SubRoutineStatement",
            index=0,
            group="Checks",
            payload_xml=_SUBROUTINE_XSCR,
        )
        self.assertEqual(extract_subroutine_path(command), "Demo\\SUB_Get_Fingers_v1.0")

    def test_resolve_missing_on_worktable_inventory(self):
        result = resolve_subroutine_against_inventory(
            "Demo\\SUB_Missing",
            [{"object_name": "SUB_Other", "qualified_name": "Demo\\SUB_Other"}],
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "subroutine_missing_on_worktable")

    def test_resolve_unique_match_on_inventory(self):
        inventory = [
            {
                "object_name": "SUB_Get_Fingers_v1.0",
                "qualified_name": "Demo\\SUB_Get_Fingers_v1.0",
                "guid": "abc-123",
            }
        ]
        result = resolve_subroutine_against_inventory("Demo\\SUB_Get_Fingers_v1.0", inventory)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "inventory")

    def test_validate_before_execute_skips_non_subroutine(self):
        command = _FakeCommand(
            type_name="UserPromptStatement",
            index=0,
            group="G",
            payload_xml="",
        )
        result = validate_subroutine_before_execute(command)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "skipped_non_subroutine")

    def test_validate_before_execute_fails_when_missing_on_inventory(self):
        command = _FakeCommand(
            type_name="SubRoutineStatement",
            index=1,
            group="Arm",
            payload_xml=_SUBROUTINE_XSCR,
        )
        result = validate_subroutine_before_execute(
            command,
            runtime_inventory=[{"object_name": "SUB_Other"}],
        )
        self.assertFalse(result.ok)
        self.assertIn("worktable", result.message)

    def test_batch_after_load_reports_missing(self):
        command = _FakeCommand(
            type_name="SubRoutineStatement",
            index=0,
            group="Arm",
            payload_xml=_SUBROUTINE_XSCR,
        )
        report = validate_subroutines_after_load(
            commands=[command],
            runtime_inventory=[],
            provider="test",
        )
        self.assertTrue(report.needs_review)
        self.assertEqual(report.call_count, 1)

    def test_load_runtime_script_inventory_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(
                json.dumps(
                    {
                        "scripts": [
                            {"object_name": "SUB_Get_Fingers_v1.0", "guid": "g1"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.environ["TECAN_FLUENT_RUNTIME_SCRIPT_INVENTORY"] = str(path)
            try:
                inventory = load_runtime_script_inventory()
            finally:
                os.environ.pop("TECAN_FLUENT_RUNTIME_SCRIPT_INVENTORY", None)
            self.assertEqual(len(inventory), 1)
            self.assertEqual(inventory[0]["object_name"], "SUB_Get_Fingers_v1.0")


if __name__ == "__main__":
    unittest.main()
