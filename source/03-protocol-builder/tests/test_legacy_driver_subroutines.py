"""Tests for legacy driver subroutine annotation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.legacy_driver_subroutines import (
    LEGACY_DRIVER_COMMENT_REASON,
    annotate_legacy_driver_subroutine_comments,
    validation_diff_check_for_legacy_driver_subroutines,
)


class LegacyDriverSubroutineAnnotationTests(unittest.TestCase):
    def _ir(self, subroutine_ref: str) -> dict:
        return {
            "protocol": {"name": "Test"},
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "group": "Tube scan",
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": subroutine_ref, "execution_mode": "JoinSubroutine"},
                }
            ],
        }

    def _manifest(self, scripts: list[dict]) -> dict:
        return {"scripts": scripts}

    def test_injects_comment_before_subroutine_with_legacy_driver_macro(self):
        with tempfile.TemporaryDirectory() as tmp:
            subroutine = Path(tmp) / "cap.xscr"
            subroutine.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <Object Type="Tecan.VisionX.LegacyDriver.LegacyDriverMacro">
    <LegacyDriverMacro Version="1" Name="BCRMicro_Read" ModuleName="BCRMicro" LineNumber="37" />
  </Object>
</Root>
""",
                encoding="utf-8",
            )
            ir = self._ir("Demo\\SUB_CapBCScanHandeling_50mL_v0.2")
            manifest = self._manifest(
                [
                    {
                        "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                        "extracted_path": str(subroutine),
                    }
                ]
            )
            annotated = annotate_legacy_driver_subroutine_comments(ir, manifest)

        steps = annotated.get("steps") or []
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["operation"], "comment")
        self.assertEqual(steps[1]["operation"], "call_subroutine")
        self.assertEqual(steps[0]["parameters"]["reason"], LEGACY_DRIVER_COMMENT_REASON)
        comment = steps[0]["parameters"]["comment"]
        self.assertLessEqual(len(comment.split()), 9)
        self.assertIn("instrument PC", comment)
        annotations = (annotated.get("source") or {}).get("legacy_driver_annotations") or {}
        self.assertEqual(len(annotations.get("subroutines") or []), 1)

    def test_annotation_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            subroutine = Path(tmp) / "cap.xscr"
            subroutine.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <Object Type="Tecan.VisionX.LegacyDriver.LegacyDriverMacro">
    <LegacyDriverMacro Version="1" Name="Device_Read" ModuleName="DeviceMod" LineNumber="1" />
  </Object>
</Root>
""",
                encoding="utf-8",
            )
            ir = self._ir("Demo\\SUB_Example")
            manifest = self._manifest(
                [{"object_name": "SUB_Example", "extracted_path": str(subroutine)}]
            )
            first = annotate_legacy_driver_subroutine_comments(ir, manifest)
            second = annotate_legacy_driver_subroutine_comments(first, manifest)

        self.assertEqual(len(second.get("steps") or []), 2)

    def test_validation_diff_surfaces_injected_comments(self):
        ir = {
            "source": {
                "legacy_driver_annotations": {
                    "subroutines": [
                        {
                            "subroutine": "Demo\\SUB_Example",
                            "macros": ["Device_Read (DeviceMod)"],
                        }
                    ]
                }
            }
        }
        validation_report = {
            "gates": [
                {
                    "gate": "subroutine_calls_resolve",
                    "details": {
                        "legacy_driver_macros": [
                            {
                                "subroutine": "Demo\\SUB_Example",
                                "command_name": "Device_Read",
                                "module_name": "DeviceMod",
                            }
                        ]
                    },
                }
            ]
        }
        check = validation_diff_check_for_legacy_driver_subroutines(ir, validation_report)
        self.assertEqual(check["status"], "passed")
        self.assertTrue(check["details"]["needs_review"])
        self.assertIn("kept", check["summary"])
        self.assertEqual(check["details"]["macro_names"], ["Device_Read"])


if __name__ == "__main__":
    unittest.main()
