import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_reader import script
from tecan_reader.script import inspect_xscr, inspect_xscr_text


SAMPLE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="3">
  <Payload>
    <ObjectName>UnitTestScript</ObjectName>
    <Reference>
      <Guid>00000001-4321-eeee-ffff-000000000001</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>Base Worktable</ObjectName>
    </Reference>
    <PayloadData>
      <Script version="2.0">
        <Properties>
          <VariableDeclarations>
            <VariableDeclarations>
              <VariableDeclarations>
                <anyType xmlns:i="http://www.w3.org/2001/XMLSchema-instance" i:type="VariableDefinitionHelper">
                  <Name>Vol</Name>
                  <TypeName>Floating Point</TypeName>
                  <Scope>Script</Scope>
                </anyType>
              </VariableDeclarations>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
        <Commands>
          <Object Type="FluentControl.Fca.FcaPipettingCommand" family="LiHa/FCA">
            <LiquidClassName>Water Free Single</LiquidClassName>
            <SourceLabwareName>SourcePlate</SourceLabwareName>
          </Object>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum>PATTERN</Checksum>
</sd:VxData>
"""


class ScriptTests(unittest.TestCase):
    def test_inspect_xscr_reads_from_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test.xscr"
            tmp_path.write_text(SAMPLE_XSCR)

            report = inspect_xscr(tmp_path)

            self.assertEqual(report["object_name"], "UnitTestScript")
            self.assertEqual(report["kind"], "xscr")
            self.assertEqual(report["source"], str(tmp_path))
            self.assertEqual(report["command_count"], 1)

    def test_dependency_string_sorting_uses_rust_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "sorted_unique_ascii_strings"
        ):
            self.skipTest("optional Rust string sorter is not built")
        groups = (["zeta", "alpha", "zeta"], ["Alpha", "beta", "alpha"])
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_values = script._sorted_unique_strings(*groups)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "sorted_unique_ascii_strings",
                wraps=native_acceleration.sorted_unique_ascii_strings,
            ) as native_call:
                actual_values = script._sorted_unique_strings(*groups)
                native_call.assert_called_once_with(groups)
        self.assertEqual(actual_values, expected_values)
        self.assertEqual(actual_values, ["Alpha", "alpha", "beta", "zeta"])

        unicode_groups = (["éclair", "alpha"], ["café"])
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.sorted_unique_ascii_strings(unicode_groups)
            )
            self.assertEqual(
                script._sorted_unique_strings(*unicode_groups),
                sorted({value for group in unicode_groups for value in group}),
            )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_report = inspect_xscr_text(SAMPLE_XSCR, source_name="deps.xscr")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "sorted_unique_ascii_strings",
                wraps=native_acceleration.sorted_unique_ascii_strings,
            ) as native_call:
                actual_report = inspect_xscr_text(SAMPLE_XSCR, source_name="deps.xscr")
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(actual_report, expected_report)

    def test_variable_declaration_dedup_uses_rust_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "unique_ascii_tuple_indices"
        ):
            self.skipTest("optional Rust variable deduplicator is not built")
        source = (
            "<Root>"
            "<anyType Type=\"VariableDefinitionHelper\"><Name>Sample</Name>"
            "<TypeName>String</TypeName><Scope>Script</Scope>"
            "<QueryOnStartup>first</QueryOnStartup></anyType>"
            "<anyType Type=\"VariableDefinitionHelper\"><Name>Sample</Name>"
            "<TypeName>String</TypeName><Scope>Script</Scope>"
            "<QueryOnStartup>duplicate</QueryOnStartup></anyType>"
            "<anyType Type=\"VariableDefinitionHelper\"><Name>Sample</Name>"
            "<TypeName>Number</TypeName><Scope>Script</Scope></anyType>"
            "<anyType Type=\"VariableDefinitionHelper\"><Name>Sample</Name>"
            "<TypeName>String</TypeName><Scope>Project</Scope></anyType>"
            "</Root>"
        )
        root = script.parse_xml_text(source)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = script._variable_declarations(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "unique_ascii_tuple_indices",
                wraps=native_acceleration.unique_ascii_tuple_indices,
            ) as native_call:
                actual = script._variable_declarations(root)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 3)
        self.assertEqual(actual[0]["query_on_startup"], "first")
        self.assertEqual(
            [(value["name"], value["scope"], value["type"]) for value in actual],
            [
                ("Sample", "Script", "String"),
                ("Sample", "Script", "Number"),
                ("Sample", "Project", "String"),
            ],
        )

        unicode_root = script.parse_xml_text(
            "<Root><anyType Type=\"VariableDefinitionHelper\"><Name>é</Name>"
            "<TypeName>String</TypeName><Scope>Script</Scope></anyType></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.unique_ascii_tuple_indices(
                    [("é", "Script", "String")]
                )
            )
            self.assertEqual(
                script._variable_declarations(unicode_root),
                [
                    {
                        "name": "é",
                        "type": "String",
                        "scope": "Script",
                        "query_on_startup": "",
                        "read_only": "",
                    }
                ],
            )

    def test_startup_variable_values_use_native_ascii_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in ("xml_container_child_texts", "ascii_casefold_equals")
        ):
            self.skipTest("optional Rust XML child-text collector is not built")
        source = (
            "<Root><anyType Type=\"VariableDefinitionHelper\"><Name>Sample</Name>"
            "<TypeName>String</TypeName><Scope>Script</Scope><Values>"
            "<Value> first </Value><Value></Value><Value>second</Value>"
            "<Value>first</Value></Values><QueryOnStartup>TRUE</QueryOnStartup>"
            "<ReadOnly>false</ReadOnly></anyType></Root>"
        )
        root = script.parse_xml_text(source)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = script._startup_variables(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_container_child_texts",
                wraps=native_acceleration.xml_container_child_texts,
            ) as native_call, patch.object(
                native_acceleration,
                "ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_bool_call:
                actual = script._startup_variables(root)
                native_call.assert_called_once()
                self.assertEqual(native_bool_call.call_count, 2)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0]["default_values"], ["first", "second", "first"])
        self.assertTrue(actual[0]["query_on_startup"])

        unicode_root = script.parse_xml_text(
            "<Root><anyType Type=\"VariableDefinitionHelper\"><Name>Sample</Name>"
            "<TypeName>String</TypeName><Values><Value>café</Value></Values>"
            "<QueryOnStartup>trué</QueryOnStartup></anyType></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = script._startup_variables(unicode_root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_container_child_texts(
                    unicode_root[0], "Values", script.ET.Element
                )
            )
            self.assertIsNone(
                native_acceleration.ascii_casefold_equals("trué", "true")
            )
            unicode_actual = script._startup_variables(unicode_root)
        self.assertEqual(unicode_actual, unicode_expected)
        self.assertEqual(unicode_actual[0]["default_values"], ["café"])

    def test_warning_substring_scans_use_rust_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_case_insensitive_substring_match_groups"
        ):
            self.skipTest("optional Rust ASCII substring matcher is not built")
        commands = [
            {"raw_type": "Vendor.SubRoutineCall", "family": "Other", "fields": {}},
            {"raw_type": "ExecuteVBScript", "family": "Other", "fields": {}},
            {"raw_type": "LoadWorklist", "family": "Worklist", "fields": {}},
            {"raw_type": "TouchTools.RUPView", "family": "TouchTools/RUP", "fields": {}},
            {
                "raw_type": "FcaPipette",
                "family": "LiHa/FCA",
                "fields": {"Location": "PIN-2"},
            },
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = script._warnings(commands)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_case_insensitive_substring_match_groups",
                wraps=native_acceleration.ascii_case_insensitive_substring_match_groups,
            ) as native_matcher:
                actual = script._warnings(commands)
                self.assertEqual(native_matcher.call_count, 2)
                native_matcher.assert_any_call(
                    [command["raw_type"] for command in commands],
                    (
                        ("subroutine",),
                        ("executevbscript", "executeapplication"),
                        ("worklist",),
                        ("touchtools", ".rup."),
                    ),
                )
                native_matcher.assert_any_call(["PIN-2"], (("pin",),))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                "Contains subroutine calls; inspect referenced scripts before editing.",
                "Contains external application or VB script calls.",
                "Contains worklist import/load/execute commands.",
                "Contains TouchTools/RUP UI workflow commands.",
                "Contains pin-controlled or pin-located hardware references.",
                "Contains 2 commands outside the current family classifier.",
            ],
        )

        unicode_commands = [
            {"raw_type": "İSubroutineCall", "family": "Subroutine", "fields": {}}
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = script._warnings(unicode_commands)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.ascii_case_insensitive_substring_match_groups(
                    [unicode_commands[0]["raw_type"]], [("subroutine",)]
                )
            )
            self.assertEqual(script._warnings(unicode_commands), unicode_expected)

        unicode_field_commands = [
            {
                "raw_type": "FcaPipette",
                "family": "LiHa/FCA",
                "fields": {"Location": "İPIN-controlled"},
            }
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_field_expected = script._warnings(unicode_field_commands)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_case_insensitive_substring_match_groups",
                wraps=native_acceleration.ascii_case_insensitive_substring_match_groups,
            ) as native_matcher:
                self.assertEqual(
                    script._warnings(unicode_field_commands), unicode_field_expected
                )
                native_matcher.assert_called_once()
        self.assertIn(
            "Contains pin-controlled or pin-located hardware references.",
            unicode_field_expected,
        )

    def test_ordered_command_counts_use_rust_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ordered_ascii_string_counts"
        ):
            self.skipTest("optional Rust ordered counter is not built")
        values = ["Zeta", "Alpha", "Zeta", "Beta", "Alpha", "Gamma"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_counts = script._ordered_counts(values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ordered_ascii_string_counts",
                wraps=native_acceleration.ordered_ascii_string_counts,
            ) as native_call:
                actual_counts = script._ordered_counts(values)
                native_call.assert_called_once_with(values)
        self.assertEqual(list(actual_counts.items()), list(expected_counts.items()))
        self.assertEqual(
            list(actual_counts.items()),
            [("Zeta", 2), ("Alpha", 2), ("Beta", 1), ("Gamma", 1)],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.ordered_ascii_string_counts(["É", "A", "É"])
            )
            self.assertEqual(
                script._ordered_counts(["É", "A", "É"]), {"É": 2, "A": 1}
            )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_report = inspect_xscr_text(SAMPLE_XSCR, source_name="test.xscr")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ordered_ascii_string_counts",
                wraps=native_acceleration.ordered_ascii_string_counts,
            ) as native_call:
                actual_report = inspect_xscr_text(
                    SAMPLE_XSCR, source_name="test.xscr"
                )
                self.assertEqual(native_call.call_count, 2)
        self.assertEqual(actual_report, expected_report)

    def test_inspect_xscr_uses_explicit_source_name(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test.xscr"
            tmp_path.write_text(SAMPLE_XSCR)

            report = inspect_xscr(tmp_path, source_name="custom_script.xscr")

            self.assertEqual(report["object_name"], "UnitTestScript")
            self.assertEqual(report["kind"], "xscr")
            self.assertEqual(report["source"], "custom_script.xscr")
            self.assertEqual(report["command_count"], 1)
