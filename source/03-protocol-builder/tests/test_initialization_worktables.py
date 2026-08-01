"""Tests for initialization worktable detection and IR annotation."""

from __future__ import annotations

import unittest

from fluent_pipeline.initialization_worktables import (
    INITIALIZATION_COMMENT_REASON,
    MAX_OPERATOR_FALLBACK_WORKTABLES,
    annotate_initialization_worktable_comment,
    build_initialization_worktable_plan,
    detect_initialization_worktable_candidates,
    operator_fallback_worktables,
)
from fluent_pipeline.api_v2_workflow_helpers import FluentContextCheckConfig, build_initialize_steps

def _sample_manifest() -> dict:
    return {
        "worktable_geometry": {
            "workspaces": [
                {
                    "name": "Demo_Worktable_A",
                    "guid": "aaaaaaaa-bbbb-4ccc-8ddd-111111111111",
                    "location_names": ["NestPlatform", "Demo_Device_Pos", "RGA_Pos"],
                    "pin_sites": ["NestPlatform"],
                },
                {
                    "name": "Demo_Wt_v1.2",
                    "guid": "d449c47e-e9ee-4d9c-86b3-54649f349dc3",
                    "location_names": ["RGA_Pos", "Demo_Nest_Pos"],
                    "pin_sites": ["RGA_Pos"],
                },
                {
                    "name": "780_Empty",
                    "guid": "e7cd672d-4fe2-4f3f-a8b5-a4d94df498a3",
                    "location_names": ["LiHa_Pos"],
                    "pin_sites": [],
                },
            ]
        },
        "scripts": [
            {
                "object_name": "Demo\\SUB_Get_Fingers_v1.0",
                "references": [
                    {
                        "type_id": "WorktableWorkspace",
                        "object_name": "Demo_Wt_v1.2",
                        "guid": "d449c47e-e9ee-4d9c-86b3-54649f349dc3",
                    }
                ],
            }
        ],
    }


class InitializationWorktableTests(unittest.TestCase):
    def test_detect_candidates_prefers_spec_init_worktable(self):
        manifest = _sample_manifest()
        ir = {
            "worktable": {"name": "Demo_Worktable_A", "guid": "aaaaaaaa-bbbb-4ccc-8ddd-111111111111"},
            "steps": [
                {
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_Get_Fingers_v1.0"},
                },
                {
                    "operation": "move_plate",
                    "parameters": {"to_location": "Demo_Device_Pos"},
                },
            ],
            "labware": [{"location": "NestPlatform"}],
        }
        spec = {
            "verification_recipe": {
                "initialization_worktable": "Demo_Wt_v1.2",
                "initialization_worktable_guid": "d449c47e-e9ee-4d9c-86b3-54649f349dc3",
            }
        }
        candidates = detect_initialization_worktable_candidates(manifest, ir=ir, spec=spec)
        self.assertTrue(candidates)
        self.assertEqual(candidates[0].name, "Demo_Wt_v1.2")
        self.assertTrue(
            any(reason.startswith("requested initialization_worktable") for reason in candidates[0].reasons)
        )
        # Name-token decks like 780_Empty are not invented without recipe/script binding.
        self.assertNotIn("780_Empty", [item.name for item in candidates])
        self.assertIn("Demo_Wt_v1.2", [item.name for item in candidates])

    def test_annotate_inserts_opening_comment_step(self):
        manifest = _sample_manifest()
        ir = {
            "worktable": {"name": "Demo_Worktable_A"},
            "steps": [
                {
                    "group": "Arm verification",
                    "operation": "prompt_user",
                    "parameters": {"prompt": "Confirm fingers"},
                }
            ],
        }
        spec = {
            "verification_recipe": {
                "worktable": "Demo_WT",
                "initialization_worktable": "Demo_Wt_v1.2",
            }
        }
        updated = annotate_initialization_worktable_comment(ir, manifest, spec)
        steps = updated.get("steps") or []
        self.assertGreaterEqual(len(steps), 2)
        first = steps[0]
        self.assertEqual(first.get("operation"), "comment")
        self.assertEqual((first.get("parameters") or {}).get("reason"), INITIALIZATION_COMMENT_REASON)
        self.assertEqual(
            (first.get("parameters") or {}).get("comment", ""),
            "Initialization won't work on this worktable so initialize on Demo_Wt_v1.2 worktable in Demo\\SUB_Get_Fingers_v1.0 script first.",
        )
        self.assertIn("initialization_worktable_plan", updated.get("source") or {})

    def test_annotate_refreshes_existing_opening_comment_step(self):
        manifest = _sample_manifest()
        ir = {
            "worktable": {"name": "Demo_Worktable_A"},
            "steps": [
                {
                    "group": "Operator setup",
                    "operation": "comment",
                    "parameters": {
                        "reason": INITIALIZATION_COMMENT_REASON,
                        "comment": "Instrument initialization: this older comment is too long.",
                    },
                }
            ],
        }
        spec = {
            "verification_recipe": {
                "worktable": "Demo_WT",
                "initialization_worktable": "Demo_Wt_v1.2",
            }
        }

        updated = annotate_initialization_worktable_comment(ir, manifest, spec)

        self.assertEqual(len(updated["steps"]), 1)
        self.assertEqual(
            updated["steps"][0]["parameters"]["comment"],
            "Initialization won't work on this worktable so initialize on Demo_Wt_v1.2 worktable in Demo\\SUB_Get_Fingers_v1.0 script first.",
        )

    def test_build_initialize_steps_attaches_fallback_names(self):
        manifest = _sample_manifest()
        ir = {"worktable": {"name": "Demo_Worktable_A"}}
        spec = {
            "verification_recipe": {
                "worktable": "Demo_WT",
                "initialization_worktable": "Demo_Wt_v1.2",
            }
        }
        config = FluentContextCheckConfig(
            method="Demo",
            initialize_workspace="Demo_Wt_v1.2",
            script_workspace="Demo_Worktable_A",
        )
        steps = build_initialize_steps(config, manifest=manifest, ir=ir, spec=spec)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].name, "Demo_Wt_v1.2")
        # Fallbacks come from other script-bound / overlap-mined decks, not empty-name tokens.
        self.assertIsNotNone(steps[0].fallback_names)
        self.assertNotIn("780_Empty", list(steps[0].fallback_names or ()))

    def test_plan_comment_mentions_script_and_fallbacks(self):
        plan = build_initialization_worktable_plan(
            _sample_manifest(),
            ir={"worktable": {"name": "Demo_Worktable_A"}},
            spec={
                "verification_recipe": {
                    "initialization_worktable": "Demo_Wt_v1.2",
                }
            },
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        text = plan.comment_text()
        self.assertEqual(
            text,
            "Initialization won't work on this worktable so initialize on Demo_Wt_v1.2 worktable in Demo\\SUB_Get_Fingers_v1.0 script first.",
        )

    def test_comment_lists_one_operator_fallback_worktable(self):
        workspaces = [
            {
                "name": "Demo_Worktable_A",
                "guid": "aaaaaaaa-bbbb-4ccc-8ddd-111111111111",
                "location_names": ["NestPlatform", "RGA_Pos"],
                "pin_sites": ["NestPlatform"],
            },
            {
                "name": "Demo_Wt_v1.2",
                "guid": "d449c47e-e9ee-4d9c-86b3-54649f349dc3",
                "location_names": ["RGA_Pos", "NestPlatform"],
                "pin_sites": ["RGA_Pos"],
            },
        ]
        for index in range(10):
            workspaces.append(
                {
                    "name": f"Alt_Init_Deck_{index}",
                    "guid": f"00000000-0000-0000-0000-{index:012d}",
                    "location_names": ["RGA_Pos", "NestPlatform"],
                    "pin_sites": [],
                }
            )
        manifest = {
            "worktable_geometry": {"workspaces": workspaces},
            "scripts": [
                {
                    "object_name": "Demo\\SUB_Get_Fingers_v1.0",
                    "references": [
                        {
                            "type_id": "WorktableWorkspace",
                            "object_name": "Demo_Wt_v1.2",
                            "guid": "d449c47e-e9ee-4d9c-86b3-54649f349dc3",
                        }
                    ],
                },
                {
                    "object_name": "Demo\\SUB_Init_Helper_v1.0",
                    "references": [
                        {
                            "type_id": "WorktableWorkspace",
                            "object_name": "Alt_Init_Deck_0",
                            "guid": "00000000-0000-0000-0000-000000000000",
                        }
                    ],
                },
            ],
        }
        ir = {
            "worktable": {"name": "Demo_Worktable_A"},
            "steps": [
                {
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_Get_Fingers_v1.0"},
                },
                {
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_Init_Helper_v1.0"},
                },
            ],
        }
        plan = build_initialization_worktable_plan(
            manifest,
            ir=ir,
            spec={"verification_recipe": {"initialization_worktable": "Demo_Wt_v1.2"}},
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(MAX_OPERATOR_FALLBACK_WORKTABLES, 1)
        self.assertGreaterEqual(len(plan.fallback_init_worktables), MAX_OPERATOR_FALLBACK_WORKTABLES)
        shown = operator_fallback_worktables(plan.fallback_init_worktables)
        self.assertEqual(len(shown), MAX_OPERATOR_FALLBACK_WORKTABLES)
        text = plan.comment_text()
        self.assertNotIn("Alt_Init_Deck_9", text)
        self.assertEqual(
            text,
            "Initialization won't work on this worktable so initialize on Demo_Wt_v1.2 worktable in Demo\\SUB_Get_Fingers_v1.0 script first.",
        )

        updated = annotate_initialization_worktable_comment(
            {
                "worktable": {"name": "Demo_Worktable_A"},
                "steps": [
                    {
                        "operation": "call_subroutine",
                        "parameters": {"subroutine": "Demo\\SUB_Get_Fingers_v1.0"},
                    },
                    {
                        "operation": "call_subroutine",
                        "parameters": {"subroutine": "Demo\\SUB_Init_Helper_v1.0"},
                    },
                ],
            },
            manifest,
            spec={"verification_recipe": {"initialization_worktable": "Demo_Wt_v1.2"}},
        )
        params = (updated["steps"][0].get("parameters") or {})
        self.assertEqual(len(params.get("fallback_init_worktables") or []), 1)
        self.assertEqual(params.get("fallback_init_worktables_total"), len(plan.fallback_init_worktables))
        self.assertGreaterEqual(
            len(updated["source"]["initialization_worktable_plan"]["fallback_init_worktables"]),
            MAX_OPERATOR_FALLBACK_WORKTABLES,
        )

    def test_plan_prefers_fca_waste_candidate_over_repeated_subroutine_affinity(self):
        manifest = {
            "worktable_geometry": {
                "workspaces": [
                    {
                        "name": "Demo_Worktable_A",
                        "guid": "aaaaaaaa-bbbb-4ccc-8ddd-111111111111",
                        "location_names": ["RGA_Pos", "Demo_Tube_Pos_1"],
                    },
                    {
                        "name": "Demo_Worktable_Init_A",
                        "guid": "99ac7782-f027-4fa6-9771-378320491e47",
                        "location_names": ["RGA_Pos", "Demo_Tube_Pos_1"],
                        "placements": [
                            {"label": "Wash Station Waste_1"},
                            {"label": "Waste Thru Trough 8x100mL Corrected[001]"},
                        ],
                    },
                    {
                        "name": "Demo_Wt_v1.2",
                        "guid": "d449c47e-e9ee-4d9c-86b3-54649f349dc3",
                        "location_names": ["RGA_Pos", "Demo_Tube_Pos_1"],
                        "placements": [
                            {"label": "FCA Thru Deck Waste Chute Custom_1"},
                            {"label": "Waste Thru Trough 8x100mL Corrected[001]"},
                        ],
                    },
                ]
            },
            "scripts": [
                {
                    "object_name": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                    "references": [
                        {
                            "type_id": "WorktableWorkspace",
                            "object_name": "Demo_Worktable_Init_A",
                        }
                    ],
                },
                {
                    "object_name": "Demo\\SUB_Get_Fingers_v1.0",
                    "references": [
                        {
                            "type_id": "WorktableWorkspace",
                            "object_name": "Demo_Wt_v1.2",
                        }
                    ],
                },
            ],
        }
        ir = {
            "worktable": {"name": "Demo_Worktable_A"},
            "steps": [
                {
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                },
                {
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                },
                {
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2"},
                },
                {
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_Get_Fingers_v1.0"},
                },
            ],
        }

        plan = build_initialization_worktable_plan(manifest, ir=ir, spec=None)

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.primary_init_worktable, "Demo_Wt_v1.2")
        self.assertEqual(plan.primary_init_script, "Demo\\SUB_Get_Fingers_v1.0")
        self.assertTrue(plan.candidates[0].has_fca_waste)
        self.assertIn("FCA liquid/plastics waste present", plan.candidates[0].reasons)


if __name__ == "__main__":
    unittest.main()
