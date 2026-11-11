from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_reader import pattern_library


class PatternSummaryHelperTests(unittest.TestCase):
    def test_join_summary_uses_rust_for_supported_ascii_and_falls_back(self) -> None:
        parts = (" \tAspirate\x1c", "", "Water\x1f ")
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = pattern_library._join_summary(*parts)
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                pattern_library,
                "_native_pattern_join_summary",
                wraps=native_acceleration.pattern_join_summary,
            ) as native_call:
                actual = pattern_library._join_summary(*parts)
                native_call.assert_called_once_with(parts)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "Aspirate Water")
        if native_acceleration.available() and hasattr(
            native_acceleration._native, "pattern_join_summary"
        ):
            with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertEqual(
                    native_acceleration.pattern_join_summary(parts), expected
                )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(pattern_library._join_summary(), "Command")
            self.assertIsNone(native_acceleration.pattern_join_summary((" café ",)))
            self.assertEqual(pattern_library._join_summary(" café "), "café")
            self.assertIsNone(
                native_acceleration.pattern_join_summary((chr(0xD800),))
            )

    def test_append_unique_uses_rust_for_supported_ascii_and_falls_back(self) -> None:
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = ["Plate A", "Tube"]
            pattern_library._append_unique(expected, " Plate A ")
            pattern_library._append_unique(expected, " Water\x1c ")
            pattern_library._append_unique(expected, " Water ")
            pattern_library._append_unique(expected, None)
            pattern_library._append_unique(expected, "\x1c \x1f")

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual = ["Plate A", "Tube"]
            with patch.object(
                pattern_library,
                "_native_append_unique_ascii_string",
                wraps=native_acceleration.append_unique_ascii_string,
            ) as native_call:
                pattern_library._append_unique(actual, " Plate A ")
                pattern_library._append_unique(actual, " Water\x1c ")
                pattern_library._append_unique(actual, " Water ")
                pattern_library._append_unique(actual, None)
                pattern_library._append_unique(actual, "\x1c \x1f")
                self.assertEqual(native_call.call_count, 5)
            self.assertEqual(actual, expected)
            self.assertEqual(actual, ["Plate A", "Tube", "Water"])

            if native_acceleration.available() and hasattr(
                native_acceleration._native, "append_unique_ascii_string"
            ):
                native_values: list[str] = []
                self.assertTrue(
                    native_acceleration.append_unique_ascii_string(
                        native_values, " Plate A "
                    )
                )
                self.assertFalse(
                    native_acceleration.append_unique_ascii_string(
                        native_values, "Plate A"
                    )
                )
                self.assertFalse(
                    native_acceleration.append_unique_ascii_string(native_values, None)
                )
                self.assertEqual(native_values, ["Plate A"])

            unicode_values = ["Plate"]
            self.assertIsNone(
                native_acceleration.append_unique_ascii_string(unicode_values, "Café")
            )
            pattern_library._append_unique(unicode_values, " Café ")
            self.assertEqual(unicode_values, ["Plate", "Café"])
            numeric_values: list[str] = []
            self.assertIsNone(
                native_acceleration.append_unique_ascii_string(numeric_values, 3)
            )
            pattern_library._append_unique(numeric_values, 3)
            pattern_library._append_unique(numeric_values, 0)
            self.assertEqual(numeric_values, ["3"])

    def test_specification_groups_batch_ascii_values_and_preserve_python_fallback(self) -> None:
        raw_groups = {
            "labware": [" Plate A ", None, "Plate A", "Plate B"],
            "rack_labels": ["\x1cRack 1\x1f", "Rack 1"],
            "empty": [None, " \t "],
        }
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_groups = {}
            for key, values in raw_groups.items():
                expected_groups[key] = []
                for value in values:
                    pattern_library._append_unique(expected_groups[key], value)
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_groups = native_acceleration.pattern_specification_groups(
                raw_groups
            )
            if native_acceleration.available() and hasattr(
                native_acceleration._native, "pattern_specification_groups"
            ):
                self.assertEqual(actual_groups, expected_groups)
                self.assertEqual(tuple(actual_groups), tuple(raw_groups))

            steps = [
                {
                    "fields": {
                        "LabwareName": " Plate A ",
                        "LabwareLable": "Plate A",
                        "LabwareLabel": "Plate B",
                        "RackLabel": "Rack 1",
                        "RackType": "Rack X",
                        "LiquidClassName": "Water",
                        "LiquidClassNameBySelection": " Water ",
                        "Volume": "10",
                        "DeviceAlias": "Pipette",
                        "WorklistName": "orders.gwl",
                        "FileName": "Orders.GWL",
                        "Name": "V",
                        "LoopVariable": "V",
                        "Condition": "ready",
                    }
                },
                {"fields": {"LabwareName": "Plate B", "Name": "W"}},
            ]
            with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                expected = pattern_library._collect_specifications(steps)
            with patch.object(
                pattern_library,
                "_native_pattern_specification_groups",
                wraps=native_acceleration.pattern_specification_groups,
            ) as native_call:
                actual = pattern_library._collect_specifications(steps)
                native_call.assert_called_once()
            self.assertEqual(actual, expected)
            self.assertEqual(
                list(actual),
                [
                    "labware",
                    "rack_labels",
                    "rack_types",
                    "liquid_classes",
                    "volumes",
                    "device_aliases",
                    "worklists",
                    "variables",
                    "conditions",
                ],
            )
            self.assertEqual(actual["labware"], ["Plate A", "Plate B", "Rack 1"])
            self.assertEqual(
                actual["worklists"], ["orders.gwl", "Orders.GWL"]
            )

            events: list[str] = []

            class StringRecorder:
                def __init__(self, name: str) -> None:
                    self.name = name

                def __str__(self) -> str:
                    events.append(self.name)
                    return self.name

            unsupported_steps = [
                {
                    "fields": {
                        "LabwareName": StringRecorder("first"),
                        "RackType": StringRecorder("second"),
                    }
                }
            ]
            with patch.object(
                pattern_library, "_native_pattern_specification_groups"
            ) as batch_native:
                fallback = pattern_library._collect_specifications(unsupported_steps)
                batch_native.assert_not_called()
            self.assertEqual(events, ["first", "second"])
            self.assertEqual(
                fallback, {"labware": ["first"], "rack_types": ["second"]}
            )

    def test_ascii_lower_uses_rust_for_pattern_summary(self) -> None:
        class LowerSubclass(str):
            def lower(self) -> str:
                return "custom-lower"

        values = (
            "SyntheticAspirateCommandType",
            " Aspirate ",
            "Café",
            "\u217c",
            LowerSubclass(".GWL"),
        )
        command = {
            "command_type": "SyntheticAspirateCommandType",
            "fields": {"Volume": "10"},
        }
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [pattern_library._lower_text(value) for value in values]
            with patch.object(
                pattern_library, "registry_manual_step", return_value=None
            ):
                expected_summary = pattern_library.summarize_pattern_step(command)
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                pattern_library,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [pattern_library._lower_text(value) for value in values]
                self.assertEqual(native_call.call_count, 3)
            with patch.object(
                pattern_library, "registry_manual_step", return_value=None
            ):
                with patch.object(
                    pattern_library,
                    "_native_ascii_casefold_trim",
                    wraps=native_acceleration.ascii_casefold_trim,
                ) as summary_native_call:
                    actual_summary = pattern_library.summarize_pattern_step(command)
                    summary_native_call.assert_called_once_with(
                        "SyntheticAspirateCommandType"
                    )
        self.assertEqual(actual, expected)
        expected_values = [value.lower() for value in values[:-1]] + ["custom-lower"]
        self.assertEqual(actual, expected_values)
        self.assertEqual(actual_summary, expected_summary)
        self.assertEqual(actual_summary, "Aspirate 10")

    def test_command_haystack_uses_rust_and_unicode_fallback(self) -> None:
        commands = [
            {"command_type": "SyntheticAspirate", "fields": {}},
            {"command_type": "Aspirateſ", "fields": {}},
        ]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(pattern_library, "registry_pattern_type", return_value=None):
                expected = [
                    pattern_library.classify_command_pattern(command)
                    for command in commands
                ]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(pattern_library, "registry_pattern_type", return_value=None):
                with patch.object(
                    pattern_library,
                    "_native_ascii_casefold_trim",
                    wraps=native_acceleration.ascii_casefold_trim,
                ) as native_call:
                    actual = [
                        pattern_library.classify_command_pattern(command)
                        for command in commands
                    ]
                    self.assertEqual(native_call.call_count, 2)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [["aspirate"], ["aspirate"]])

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(pattern_library, "registry_pattern_type", return_value=None):
                with patch.object(
                    pattern_library, "_native_ascii_casefold_trim", return_value=None
                ):
                    fallback = [
                        pattern_library.classify_command_pattern(command)
                        for command in commands
                    ]
        self.assertEqual(fallback, expected)

    def test_pattern_type_normalization_uses_rust_with_python_fallback(self) -> None:
        values = (" Aspirate-Drop Tips ", "Café-Name", "Aſpirate")
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [pattern_library.normalize_pattern_type(value) for value in values]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                pattern_library,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [pattern_library.normalize_pattern_type(value) for value in values]
                self.assertEqual(native_call.call_count, 3)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["aspirate_drop_tips", "café_name", "aſpirate"])

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                pattern_library, "_native_ascii_casefold_trim", return_value=None
            ):
                fallback = [pattern_library.normalize_pattern_type(value) for value in values]
        self.assertEqual(fallback, expected)

    def test_gwl_suffix_check_uses_rust_and_preserves_python_lower(self) -> None:
        steps = [
            {"fields": {"Condition": "café", "FileName": "Orders.GWL"}},
            {"fields": {"Condition": "ready", "Path": "café.GWL"}},
            {"fields": {"Condition": "ready", "Path": "orders.gw\u217c"}},
            {"fields": {"Condition": "ready", "Path": " Orders.GWL "}},
        ]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = pattern_library._collect_specifications(steps)
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                pattern_library,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = pattern_library._collect_specifications(steps)
                self.assertEqual(native_call.call_count, 3)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["worklists"], ["Orders.GWL", "café.GWL"])
        self.assertFalse(pattern_library._has_gwl_suffix("orders.gw\u217c"))

    def test_liquid_step_uses_rust_for_supported_ascii_and_falls_back(self) -> None:
        values = ("Aspirate", "Plate A", "10", "Water")
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = pattern_library._liquid_step(*values)
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                pattern_library,
                "_native_pattern_liquid_step",
                wraps=native_acceleration.pattern_liquid_step,
            ) as native_call:
                actual = pattern_library._liquid_step(*values)
                native_call.assert_called_once_with(*values)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "Aspirate 10 at Plate A using Water")
        if native_acceleration.available() and hasattr(
            native_acceleration._native, "pattern_liquid_step"
        ):
            with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertEqual(
                    native_acceleration.pattern_liquid_step(*values), expected
                )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                pattern_library._liquid_step("Aspirate", "Café", "10", "Water"),
                "Aspirate 10 at Café using Water",
            )
            self.assertIsNone(
                native_acceleration.pattern_liquid_step(
                    "Aspirate", "Café", "10", "Water"
                )
            )
            self.assertEqual(
                pattern_library._liquid_step("Aspirate", "Plate", 0, "Water"),
                "Aspirate at Plate using Water",
            )


if __name__ == "__main__":
    unittest.main()
