from __future__ import annotations

import ast
import unittest
from unittest.mock import patch

import fluent_pipeline.protocol_ir as protocol_ir_module
import fluent_pipeline.protocol_ir_compat as protocol_ir_compat_module
import fluent_pipeline.protocol_ir_schema as protocol_ir_schema_module
from tecan_common import native_acceleration


class ProtocolIrNativeStringsTests(unittest.TestCase):
    def test_source_path_whitespace_uses_rust_with_python_fallback(self):
        sources = [
            "result = choose('alpha\u2003beta')",
            "result = invoke('" + "x" * 180 + "')",
        ]
        cases = [
            (source, ast.parse(source).body[0].value)
            for source in sources
        ]

        def reference(source: str, node: ast.AST) -> str:
            segment = " ".join((ast.get_source_segment(source, node) or "").split())
            if len(segment) > 140:
                segment = segment[:137].rstrip() + "..."
            return f"Draft -> build_worktable() -> Group -> {segment}"

        expected = [reference(source, node) for source, node in cases]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = [
                protocol_ir_module._source_path("Draft", "Group", source, node)
                for source, node in cases
            ]
        self.assertEqual(fallback, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "collapse_ascii_whitespace",
                wraps=native_acceleration.collapse_ascii_whitespace,
            ) as native_call:
                actual = [
                    protocol_ir_module._source_path("Draft", "Group", source, node)
                    for source, node in cases
                ]
                self.assertEqual(native_call.call_count, len(cases))
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "collapse_ascii_whitespace",
                return_value=None,
            ):
                unsupported_fallback = [
                    protocol_ir_module._source_path("Draft", "Group", source, node)
                    for source, node in cases
                ]
        self.assertEqual(unsupported_fallback, expected)

    def test_safe_variable_base_matches_python_with_collision_policy_retained(self):
        cases = [
            ("Tecan Pump", set()),
            ("__Alpha__", set()),
            ("alpha__beta", set()),
            ("123Flow", set()),
            ("Straße", set()),
            ("ſcript", set()),
            ("東京", set()),
            ("...", set()),
            ("Tecan Pump", {"tecan_pump", "tecan_pump_2"}),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_module._safe_var(value, used=used)
                for value, used in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_safe_variable_base",
                wraps=native_acceleration.ascii_safe_variable_base,
            ) as native_call:
                actual = [
                    protocol_ir_module._safe_var(value, used=used)
                    for value, used in cases
                ]
                self.assertEqual(native_call.call_count, len(cases))
            self.assertIsNone(native_acceleration.ascii_safe_variable_base("Straße"))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                "tecan_pump",
                "alpha",
                "alpha__beta",
                "item_123flow",
                "stra_e",
                "cript",
                "item",
                "item",
                "tecan_pump_3",
            ],
        )

    def test_bracket_variable_extraction_uses_shared_rust_scanner(self):
        values = [
            "[Alpha] [beta_2] [Alpha] [] [_x] [9bad] [with-dash] [[nested]] [é] [Aé]",
            "before [name9] after",
            "unicode [é] [Aé] [okay_1]",
            "",
            None,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_module._bracket_variable_names(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "bracket_variable_names",
                wraps=native_acceleration.bracket_variable_names,
            ) as native_call:
                actual = [
                    protocol_ir_module._bracket_variable_names(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], {"Alpha", "beta_2", "_x", "nested"})
        self.assertEqual(actual[1], {"name9"})
        self.assertEqual(actual[2], {"okay_1"})
        self.assertEqual(actual[3:], [set(), set()])

    def test_variable_name_validation_uses_native_ascii_with_python_fallback(self):
        values = ["Name_2", "_x", "\u001cAlpha9\u001f", "2name", "A-B", "", "é", "ſcript"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_module._valid_variable_name(value) for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_variable_name_is_valid",
                wraps=native_acceleration.ascii_variable_name_is_valid,
            ) as native_call:
                actual = [
                    protocol_ir_module._valid_variable_name(value) for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(
                native_acceleration.ascii_variable_name_is_valid("ſcript")
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [True, True, True, False, False, False, False, False])

    def test_schema_fc_variable_name_validation_uses_native_ascii(self):
        values = [
            "Name_2",
            "_x",
            "9bad",
            "A-B",
            "",
            "Name\n",
            "é",
            "ſcript",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_schema_module._is_fc_variable_name(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_variable_name_is_valid",
                wraps=native_acceleration.ascii_variable_name_is_valid,
            ) as native_call:
                actual = [
                    protocol_ir_schema_module._is_fc_variable_name(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(
                native_acceleration.ascii_variable_name_is_valid("ſcript")
            )
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [True, True, False, False, False, False, False, False],
        )

    def test_setup_group_name_whitespace_normalization_uses_native_ascii(self):
        values = [
            "  Operator   Setup  ",
            "Instrument\tInitialization Setup",
            "setup current",
            "Set up",
            "Straße Setup",
            "Opérator init",
            "\u001cOperator\t\nSetup\u001f",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_compat_module.is_setup_group_name(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module,
                "_native_ascii_collapse_casefold_whitespace",
                wraps=native_acceleration.ascii_collapse_casefold_whitespace,
            ) as native_call:
                actual = [
                    protocol_ir_compat_module.is_setup_group_name(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(
                native_acceleration.ascii_collapse_casefold_whitespace(
                    "Opérator init"
                )
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [True, True, True, False, True, False, True])

    def test_media_slot_filename_uses_native_ascii_lower_without_trimming(self):
        kinds = ["IMAGE", "VIDEO", " Image ", "VÍDEO", ""]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_compat_module.media_slot_filename("slot", kind)
                for kind in kinds
            ]
        self.assertEqual(
            expected,
            ["slot.png", "slot.gif", "slot.bin", "slot.bin", "slot.bin"],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = [
                    protocol_ir_compat_module.media_slot_filename("slot", kind)
                    for kind in kinds
                ]
                self.assertEqual(native_call.call_count, len(kinds))
            native_call.assert_any_call("IMAGE")
            native_call.assert_any_call("VÍDEO")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module, "_native_ascii_lower", return_value=None
            ):
                forced_fallback = [
                    protocol_ir_compat_module.media_slot_filename("slot", kind)
                    for kind in kinds
                ]
        self.assertEqual(forced_fallback, expected)

    def test_media_rup_kind_uses_native_ascii_lower_and_python_fallback(self):
        specs = [
            {"rup_kind": "  WORKTABLE "},
            {"rup_kind": "Standard"},
            {"rup_kind": "Wörktable", "command_id": "RUPStandardStatement"},
            {"rup_kind": "NÖNE"},
            {"rup_kind": None, "command_id": "RUPWorktableStatement"},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_compat_module._media_spec_rup_kind(spec)
                for spec in specs
            ]
        self.assertEqual(expected, ["worktable", "standard", "standard", "", "worktable"])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = [
                    protocol_ir_compat_module._media_spec_rup_kind(spec)
                    for spec in specs
                ]
                self.assertEqual(native_call.call_count, len(specs))
            native_call.assert_any_call("Wörktable")
            native_call.assert_any_call("NÖNE")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module, "_native_ascii_lower", return_value=None
            ):
                forced_fallback = [
                    protocol_ir_compat_module._media_spec_rup_kind(spec)
                    for spec in specs
                ]
        self.assertEqual(forced_fallback, expected)

    def test_media_slot_rows_use_native_lower_and_preserve_unicode_fallback(self):
        rows = [
            {
                "step_id": "step",
                "media": [
                    {"slot": "video-slot", "kind": "VIDEO"},
                    {"slot": "unicode-slot", "kind": "VÍDEO"},
                    {"slot": "default-slot"},
                ],
            }
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = protocol_ir_compat_module._media_slot_specs_from_rows(rows)
        summary = [
            (spec["kind"], spec["filename"], spec["path"]) for spec in expected
        ]
        self.assertEqual(
            summary,
            [
                ("video", "video-slot.gif", "media/video-slot.gif"),
                ("vídeo", "unicode-slot.bin", "media/unicode-slot.bin"),
                ("media", "default-slot.bin", "media/default-slot.bin"),
            ],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = protocol_ir_compat_module._media_slot_specs_from_rows(rows)
                self.assertGreaterEqual(native_call.call_count, len(rows[0]["media"]))
            native_call.assert_any_call("VIDEO")
            native_call.assert_any_call("VÍDEO")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module, "_native_ascii_lower", return_value=None
            ):
                forced_fallback = protocol_ir_compat_module._media_slot_specs_from_rows(rows)
        self.assertEqual(forced_fallback, expected)

    def test_prompt_media_kind_lookup_uses_native_lower_and_unicode_fallback(self):
        params = [
            {"media_placeholders": [{"kind": "IMAGE", "slot": "image-slot"}]},
            {"media_placeholders": [{"kind": "VIDEO", "slot": "video-slot"}]},
            {"media_placeholders": [{"kind": " Image ", "slot": "space-slot"}]},
            {"media_placeholders": [{"kind": "ÍMAGE", "slot": "unicode-slot"}]},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_compat_module.prompt_step_media_path(item)
                for item in params
            ]
        self.assertEqual(
            expected,
            ["media/image-slot.png", "media/video-slot.gif", None, None],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = [
                    protocol_ir_compat_module.prompt_step_media_path(item)
                    for item in params
                ]
                self.assertGreaterEqual(native_call.call_count, len(params))
            native_call.assert_any_call("IMAGE")
            native_call.assert_any_call(" Image ")
            native_call.assert_any_call("ÍMAGE")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module, "_native_ascii_lower", return_value=None
            ):
                forced_fallback = [
                    protocol_ir_compat_module.prompt_step_media_path(item)
                    for item in params
                ]
        self.assertEqual(forced_fallback, expected)

    def test_media_selection_normalizes_kind_aliases_with_native_fallback(self):
        specs = [
            {
                "step_id": "worktable",
                "slot": "wt-image",
                "kind": "IMAGE",
                "rup_kind": "WORKTABLE",
            },
            {
                "step_id": "worktable",
                "slot": "wt-video",
                "kind": "VIDEO",
                "rup_kind": "WORKTABLE",
            },
            {
                "step_id": "standard",
                "slot": "std-image",
                "kind": "IMAGE",
                "rup_kind": "STANDARD",
            },
            {
                "step_id": "standard",
                "slot": "std-video",
                "kind": "VIDEO",
                "rup_kind": "STANDARD",
            },
            {
                "step_id": "unicode",
                "slot": "unicode-video",
                "kind": "VÍDEO",
                "rup_kind": "STANDARD",
            },
        ]

        def select_slots():
            grouped = protocol_ir_compat_module._group_media_slot_specs_by_step(
                specs
            )
            return protocol_ir_compat_module._prompt_display_media_slots(grouped)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = select_slots()
        self.assertEqual(expected, ({"std-video"}, {"wt-image"}))

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = select_slots()
                self.assertGreaterEqual(native_call.call_count, len(specs))
            native_call.assert_any_call("IMAGE")
            native_call.assert_any_call("VIDEO")
            native_call.assert_any_call("VÍDEO")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module, "_native_ascii_lower", return_value=None
            ):
                forced_fallback = select_slots()
        self.assertEqual(forced_fallback, expected)

    def test_verification_rup_mode_uses_native_ascii_lower_with_unicode_fallback(self):
        modes = ["WORKTABLE", "STANDARD", "MIXED", "  WORKTABLE  ", "Wörktable"]

        def resolve_modes():
            return [
                protocol_ir_compat_module.resolve_verification_prompt_rup_kind(
                    {}, default_rup_kind=mode
                )
                for mode in modes
            ]

        def annotation_summary(mode: str) -> tuple[str, str, bool]:
            ir = {
                "steps": [
                    {
                        "id": "step",
                        "operation": "prompt_user",
                        "parameters": {"prompt": "Review the result."},
                    }
                ]
            }
            annotated = (
                protocol_ir_compat_module.annotate_verification_prompts_with_media(
                    ir, default_rup_kind=mode
                )
            )
            step = annotated["steps"][0]
            params = step["parameters"]
            image = params["media_placeholders"][0]
            return (
                params["rup_kind"],
                step["command_id"],
                bool(image.get("worktable_display")),
            )

        annotation_modes = ["WORKTABLE", "STANDARD", "Wörktable"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = resolve_modes()
            expected_annotations = [
                annotation_summary(mode) for mode in annotation_modes
            ]
        self.assertEqual(
            expected,
            ["worktable", "standard", "standard", "worktable", "standard"],
        )
        self.assertEqual(
            expected_annotations,
            [
                ("worktable", "RUPWorktableStatement", True),
                ("standard", "RUPStandardStatement", False),
                ("standard", "RUPStandardStatement", False),
            ],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = resolve_modes()
                actual_annotations = [
                    annotation_summary(mode) for mode in annotation_modes
                ]
                self.assertGreaterEqual(native_call.call_count, len(modes))
            native_call.assert_any_call("WORKTABLE")
            native_call.assert_any_call("Wörktable")
        self.assertEqual(actual, expected)
        self.assertEqual(actual_annotations, expected_annotations)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                protocol_ir_compat_module, "_native_ascii_lower", return_value=None
            ):
                forced_fallback = resolve_modes()
                forced_annotations = [
                    annotation_summary(mode) for mode in annotation_modes
                ]
        self.assertEqual(forced_fallback, expected)
        self.assertEqual(forced_annotations, expected_annotations)

    def test_rup_display_type_alias_normalization_uses_native_compaction(self):
        cases = [
            ("Combo Box", False),
            ("RADIO-BUTTON", False),
            ("Straße", True),
            ("東京", False),
            ("TextboxWithNumpad", False),
            ("not-a-kind", False),
            ("not-a-kind", True),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                protocol_ir_module._rup_variable_display_type(
                    value, has_allowed_values=has_allowed_values
                )
                for value, has_allowed_values in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_lower_alnum_compact",
                wraps=native_acceleration.ascii_lower_alnum_compact,
            ) as native_call:
                actual = [
                    protocol_ir_module._rup_variable_display_type(
                        value, has_allowed_values=has_allowed_values
                    )
                    for value, has_allowed_values in cases
                ]
                self.assertEqual(native_call.call_count, len(cases) - 1)
            self.assertIsNone(native_acceleration.ascii_lower_alnum_compact("東京"))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            ["Combobox", "RadioButton", "Combobox", "Empty", "TextboxWithNumpad", "Empty", "Combobox"],
        )

    def test_protocol_identifier_and_filename_components_match_python_fallback(self):
        values = ["__My-Protocol.Name__", "!!!", "Straße", "東京", " \u2003Mixed Name\u00a0 "]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_ids = [protocol_ir_module._safe_id(value) for value in values]
            expected_filenames = [protocol_ir_module._safe_filename(value) for value in values]
            expected_schema_ids = [protocol_ir_schema_module._safe_id(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_safe_component",
                wraps=native_acceleration.ascii_safe_component,
            ) as native_call:
                actual_ids = [protocol_ir_module._safe_id(value) for value in values]
                actual_filenames = [protocol_ir_module._safe_filename(value) for value in values]
                actual_schema_ids = [protocol_ir_schema_module._safe_id(value) for value in values]
                self.assertEqual(native_call.call_count, len(values) * 3)
            self.assertIsNone(native_acceleration.ascii_safe_component("東京", True))
        self.assertEqual(actual_ids, expected_ids)
        self.assertEqual(actual_filenames, expected_filenames)
        self.assertEqual(actual_schema_ids, expected_schema_ids)
        self.assertEqual(actual_ids, ["my-protocol.name", "protocol", "stra_e", "protocol", "mixed_name"])
        self.assertEqual(actual_filenames, ["My-Protocol.Name", "protocol", "Stra_e", "protocol", "Mixed_Name"])
        self.assertEqual(actual_schema_ids, expected_ids)


if __name__ == "__main__":
    unittest.main()
