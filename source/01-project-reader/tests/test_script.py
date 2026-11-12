from collections import Counter
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
    def test_worktable_pin_location_filter_uses_native_ascii_with_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native,
            "ascii_case_insensitive_substring_match_values",
        ):
            self.skipTest("optional Rust substring matcher is not built")
        source = (
            "<Root><Location>Pin rack</Location><Location>spindle</Location>"
            "<Location>pin shelf</Location><Location>pin shelf</Location></Root>"
        )
        root = script.parse_xml_text(source)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_case_insensitive_substring_match_values_required",
                return_value=None,
            ), patch.object(
                native_acceleration, "ascii_casefold_trim_required", return_value=None
            ):
                expected = script._dependencies(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ascii_case_insensitive_substring_match_values",
                wraps=(
                    native_acceleration._native.ascii_case_insensitive_substring_match_values
                ),
            ) as rust_call:
                actual = script._dependencies(root)
                rust_call.assert_called_once_with(
                    ["Pin rack", "spindle", "pin shelf"], "pin"
                )
        self.assertEqual(actual, expected)
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(script._dependencies(root), expected)
        self.assertEqual(
            actual["worktable_pin_locations"], ["Pin rack", "pin shelf", "spindle"]
        )

        unicode_root = script.parse_xml_text(
            "<Root><Location>Pin café</Location><Location>spindle</Location></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_case_insensitive_substring_match_values_required",
                return_value=None,
            ), patch.object(
                native_acceleration, "ascii_casefold_trim_required", return_value=None
            ):
                unicode_expected = script._dependencies(unicode_root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.ascii_case_insensitive_substring_match_values(
                    ["Pin café"], "pin"
                )
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_case_insensitive_substring_match_values_required",
                wraps=(
                    native_acceleration.ascii_case_insensitive_substring_match_values_required
                ),
            ) as required_match, patch.object(
                native_acceleration._native,
                "ascii_casefold_trim",
                wraps=native_acceleration._native.ascii_casefold_trim,
            ) as rust_fold:
                unicode_actual = script._dependencies(unicode_root)
                required_match.assert_called_once_with(["Pin café", "spindle"], "pin")
                rust_fold.assert_called_once_with("spindle")
        self.assertEqual(unicode_actual, unicode_expected)
        self.assertEqual(
            unicode_actual["worktable_pin_locations"], ["Pin café", "spindle"],
        )

    def test_ascii_casefold_text_preserves_python_unicode_semantics(self) -> None:
        values = ("PIN", "AſB", " café ")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "ascii_casefold_trim_required", return_value=None
            ):
                expected = [script._casefold_text(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ascii_casefold_trim",
                wraps=native_acceleration._native.ascii_casefold_trim,
            ) as rust_fold:
                actual = [script._casefold_text(value) for value in values]
                rust_fold.assert_called_once_with("PIN")
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["pin", "asb", " café "])
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(
                [script._casefold_text(value) for value in values], expected
            )

    def test_ascii_lower_and_casefold_equals_use_required_rust(self) -> None:
        class StringSubclass(str):
            pass

        lower_values = ("MiXeD", "İ", " padded ", StringSubclass("LaBeL"))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(native_acceleration, "ascii_lower_required", return_value=None):
                expected_lower = [script._lower_text(value) for value in lower_values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ascii_lower",
                wraps=native_acceleration._native.ascii_lower,
            ) as rust_lower:
                actual_lower = [script._lower_text(value) for value in lower_values]
                rust_lower.assert_called_once_with("MiXeD")
        self.assertEqual(actual_lower, expected_lower)
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(
                [script._lower_text(value) for value in lower_values], expected_lower
            )

        pairs = [
            ("TRUE", "true"),
            ("Straße", "strasse"),
            ("Straße", "STRASSE"),
            (StringSubclass("TRUE"), "true"),
            ("TRUE ", "true"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "ascii_casefold_equals_required", return_value=None
            ):
                expected_equal = [script._casefold_equals(*pair) for pair in pairs]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ascii_casefold_equals",
                wraps=native_acceleration._native.ascii_casefold_equals,
            ) as rust_equals:
                actual_equal = [script._casefold_equals(*pair) for pair in pairs]
                self.assertEqual(rust_equals.call_count, 2)
        self.assertEqual(actual_equal, expected_equal)
        self.assertEqual(actual_equal, [True, True, False, True, False])
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(
                [script._casefold_equals(*pair) for pair in pairs], expected_equal
            )

    def test_interesting_field_fallback_uses_native_xml_selection(self) -> None:
        from tecan_common import xml_helpers

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        root = script.parse_xml_text(
            "<Object><Name>Alpha</Name><LineNumber> 7 </LineNumber>"
            "<Name>Beta</Name><Name>Alpha</Name></Object>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = script._interesting_fields(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xml_helpers,
                "_native_xml_elements_by_name",
                wraps=xml_helpers._native_xml_elements_by_name,
            ) as native_call:
                actual = script._interesting_fields(root)
                native_call.assert_called_once_with(
                    root, script.INTERESTING_FIELDS, script.ET.Element
                )

        self.assertEqual(actual, expected)
        self.assertEqual(actual, {"Name": "Alpha | Beta", "LineNumber": "7"})

    def test_command_fallback_uses_native_object_selector(self) -> None:
        from tecan_common import xml_helpers

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        root = script.parse_xml_text(
            '<Root><Object Type="A.Command"><Name> first </Name>'
            "<LineNumber>1</LineNumber><Name>second</Name></Object>"
            '<Ignored Type="X.Command"><Name>skip</Name></Ignored>'
            '<Nested><Object Type="B.Command"><Name>third</Name></Object></Nested>'
            '<Object><Name>missing type</Name></Object></Root>'
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "xscr_command_records", return_value=None
            ):
                expected = script._commands(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration, "xscr_command_records", return_value=None
            ):
                with patch.object(
                    xml_helpers,
                    "_native_xml_elements_by_name",
                    wraps=xml_helpers._native_xml_elements_by_name,
                ) as native_call:
                    actual = script._commands(root)
                    object_scans = [
                        call
                        for call in native_call.call_args_list
                        if call.args[1] == {"Object"}
                    ]
                    self.assertEqual(len(object_scans), 1)
                    self.assertIs(object_scans[0].args[0], root)

        self.assertEqual(actual, expected)
        self.assertEqual(
            [command["raw_type"] for command in actual], ["A.Command", "B.Command"]
        )
        self.assertEqual(
            actual[0]["fields"], {"Name": "first | second", "LineNumber": "1"}
        )

    def test_inspect_xscr_reads_from_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test.xscr"
            tmp_path.write_text(SAMPLE_XSCR)

            report = inspect_xscr(tmp_path)

            self.assertEqual(report["object_name"], "UnitTestScript")
            self.assertEqual(report["kind"], "xscr")
            self.assertEqual(report["source"], str(tmp_path))
            self.assertEqual(report["command_count"], 1)

    def test_dependency_string_sorting_uses_rust_for_unicode(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "sorted_unique_strings"
        ):
            self.skipTest("optional Rust string sorter is not built")
        groups = (["zeta", "alpha", "zeta"], ["Alpha", "beta", "alpha"])
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "sorted_unique_strings_required", return_value=None
            ):
                expected_values = script._sorted_unique_strings(*groups)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "sorted_unique_strings",
                wraps=native_acceleration._native.sorted_unique_strings,
            ) as rust_call:
                actual_values = script._sorted_unique_strings(*groups)
                rust_call.assert_called_once_with([list(group) for group in groups])
        self.assertEqual(actual_values, expected_values)
        self.assertEqual(actual_values, ["Alpha", "alpha", "beta", "zeta"])

        unicode_groups = (
            ["éclair", "alpha", "😀", "Å", "e\u0301", "éclair"],
            ["café", "ß", "Ω", "a"],
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            expected_unicode = sorted(
                {value for group in unicode_groups for value in group}
            )
            self.assertEqual(
                native_acceleration.sorted_unique_strings(unicode_groups),
                expected_unicode,
            )
            self.assertEqual(
                script._sorted_unique_strings(*unicode_groups), expected_unicode
            )
            self.assertIsNone(
                native_acceleration.sorted_unique_strings(([chr(0xD800)],))
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "sorted_unique_strings",
                wraps=native_acceleration._native.sorted_unique_strings,
            ) as rust_call:
                required_unicode = script._sorted_unique_strings(*unicode_groups)
                rust_call.assert_called_once_with(
                    [list(group) for group in unicode_groups]
                )
        self.assertEqual(required_unicode, expected_unicode)
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(script._sorted_unique_strings(*groups), expected_values)
        surrogate_group = ["\ud800"]
        self.assertEqual(
            script._sorted_unique_strings(surrogate_group), sorted(set(surrogate_group))
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "sorted_unique_strings_required", return_value=None
            ):
                expected_report = inspect_xscr_text(SAMPLE_XSCR, source_name="deps.xscr")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "sorted_unique_strings",
                wraps=native_acceleration._native.sorted_unique_strings,
            ) as rust_call:
                actual_report = inspect_xscr_text(SAMPLE_XSCR, source_name="deps.xscr")
                self.assertGreater(rust_call.call_count, 0)
        self.assertEqual(actual_report, expected_report)

    def test_variable_declaration_dedup_uses_rust_for_unicode_with_surrogate_fallback(self) -> None:
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
            with patch.object(
                native_acceleration,
                "unique_ascii_tuple_indices_required",
                return_value=None,
            ):
                expected = script._variable_declarations(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "unique_ascii_tuple_indices",
                wraps=native_acceleration._native.unique_ascii_tuple_indices,
            ) as rust_call:
                actual = script._variable_declarations(root)
                rust_call.assert_called_once()
                self.assertEqual(
                    rust_call.call_args.args[0],
                    [
                        ("Sample", "Script", "String"),
                        ("Sample", "Script", "String"),
                        ("Sample", "Script", "Number"),
                        ("Sample", "Project", "String"),
                    ],
                )
        self.assertEqual(actual, expected)
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(script._variable_declarations(root), expected)
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
            "<Root>"
            "<anyType Type=\"VariableDefinitionHelper\"><Name>é</Name>"
            "<TypeName>String</TypeName><Scope>Script</Scope>"
            "<QueryOnStartup>first</QueryOnStartup></anyType>"
            "<anyType Type=\"VariableDefinitionHelper\"><Name>é</Name>"
            "<TypeName>String</TypeName><Scope>Script</Scope>"
            "<QueryOnStartup>duplicate</QueryOnStartup></anyType>"
            "<anyType Type=\"VariableDefinitionHelper\"><Name>e&#x301;</Name>"
            "<TypeName>String</TypeName><Scope>Script</Scope></anyType>"
            "</Root>"
        )
        unicode_rows = [
            ("é", "Script", "String"),
            ("e\u0301", "Script", "String"),
            ("é", "Script", "String"),
            ("東京", "Script", "String"),
            ("東京", "Script", "String"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.unique_ascii_tuple_indices(unicode_rows),
                [0, 1, 3],
            )
            self.assertIsNone(
                native_acceleration.unique_ascii_tuple_indices(
                    [(chr(0xD800), "Script", "String")]
                )
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "unique_ascii_tuple_indices",
                wraps=native_acceleration._native.unique_ascii_tuple_indices,
            ) as rust_call:
                unicode_actual = script._variable_declarations(unicode_root)
                rust_call.assert_called_once_with(
                    [
                        ("é", "Script", "String"),
                        ("é", "Script", "String"),
                        ("e\u0301", "Script", "String"),
                    ]
                )
        self.assertEqual(
            unicode_actual,
            [
                {
                    "name": "é",
                    "type": "String",
                    "scope": "Script",
                    "query_on_startup": "first",
                    "read_only": "",
                },
                {
                    "name": "e\u0301",
                    "type": "String",
                    "scope": "Script",
                    "query_on_startup": "",
                    "read_only": "",
                },
            ],
        )

        surrogate_root = script.ET.Element("Root")
        for value in ("\ud800", "\ud800"):
            element = script.ET.SubElement(
                surrogate_root, "anyType", {"Type": "VariableDefinitionHelper"}
            )
            script.ET.SubElement(element, "Name").text = value
            script.ET.SubElement(element, "TypeName").text = "String"
            script.ET.SubElement(element, "Scope").text = "Script"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "unique_ascii_tuple_indices_required", return_value=None
            ):
                expected_surrogate = script._variable_declarations(surrogate_root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "unique_ascii_tuple_indices",
                wraps=native_acceleration._native.unique_ascii_tuple_indices,
            ) as rust_call:
                actual_surrogate = script._variable_declarations(surrogate_root)
                rust_call.assert_called_once()
        self.assertEqual(actual_surrogate, expected_surrogate)
        self.assertEqual(len(actual_surrogate), 1)

    def test_startup_variable_values_use_native_with_unicode_text(self) -> None:
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
            with patch.object(
                native_acceleration, "ascii_casefold_equals_required", return_value=None
            ):
                expected = script._startup_variables(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_container_child_texts",
                wraps=native_acceleration.xml_container_child_texts,
            ) as native_call, patch.object(
                native_acceleration._native,
                "ascii_casefold_equals",
                wraps=native_acceleration._native.ascii_casefold_equals,
            ) as rust_bool_call:
                actual = script._startup_variables(root)
                native_call.assert_called_once()
                self.assertEqual(rust_bool_call.call_count, 2)
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
            self.assertEqual(
                native_acceleration.xml_container_child_texts(
                    unicode_root[0], "Values", script.ET.Element
                ),
                ["café"],
            )
            self.assertIsNone(
                native_acceleration.ascii_casefold_equals("trué", "true")
            )
            unicode_actual = script._startup_variables(unicode_root)
        self.assertEqual(unicode_actual, unicode_expected)
        self.assertEqual(unicode_actual[0]["default_values"], ["café"])

        values_root = script.ET.Element("Root")
        values_container = script.ET.SubElement(values_root, "{urn:test}Välues")
        for value in ("éclair", "東京"):
            script.ET.SubElement(values_container, "Valüe").text = (
                f"\u00a0{value}\u2003"
            )
        whitespace_chars = [
            chr(codepoint)
            for codepoint in range(0x110000)
            if chr(codepoint).isspace()
        ]
        for index, character in enumerate(whitespace_chars):
            script.ET.SubElement(values_container, "Value").text = (
                f"{character}item-{index}{character}"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_values = [
                (child.text or "").strip() for child in list(values_container)
                if (child.text or "").strip()
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_container_child_texts(
                    values_root, "Välues", script.ET.Element
                ),
                expected_values,
            )
            self.assertIsNone(
                native_acceleration.xml_container_child_texts(
                    values_root, chr(0xD800), script.ET.Element
                )
            )

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
            {"raw_type": "İSubroutineCall", "family": "Subroutine", "fields": {}},
            {
                "raw_type": "Vendor.ExecuteApplication",
                "family": "External application",
                "fields": {},
            },
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(native_acceleration, "ascii_lower_required", return_value=None):
                unicode_expected = script._warnings(unicode_commands)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            raw_types = [command["raw_type"] for command in unicode_commands]
            self.assertIsNone(
                native_acceleration.ascii_case_insensitive_substring_match_groups(
                    raw_types, [("subroutine",), ("executeapplication",)]
                )
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ascii_lower",
                wraps=native_acceleration._native.ascii_lower,
            ) as rust_lower:
                self.assertEqual(script._warnings(unicode_commands), unicode_expected)
                rust_lower.assert_called_once_with("Vendor.ExecuteApplication")

        unicode_field_commands = [
            {
                "raw_type": "FcaPipette",
                "family": "LiHa/FCA",
                "fields": {"Location": "İPIN-controlled"},
            }
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(native_acceleration, "ascii_lower_required", return_value=None):
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
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ascii_lower",
                wraps=native_acceleration._native.ascii_lower,
            ) as rust_lower:
                self.assertEqual(
                    script._warnings(unicode_field_commands), unicode_field_expected
                )
                rust_lower.assert_called_once_with("FcaPipette")
        self.assertIn(
            "Contains pin-controlled or pin-located hardware references.",
            unicode_field_expected,
        )

    def test_ordered_command_counts_use_rust_for_unicode(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ordered_string_counts"
        ):
            self.skipTest("optional Rust ordered counter is not built")
        values = ["Zeta", "Alpha", "Zeta", "Beta", "Alpha", "Gamma"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "ordered_string_counts_required", return_value=None
            ):
                expected_counts = script._ordered_counts(values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ordered_string_counts",
                wraps=native_acceleration._native.ordered_string_counts,
            ) as rust_call:
                actual_counts = script._ordered_counts(values)
                rust_call.assert_called_once_with(values)
        self.assertEqual(list(actual_counts.items()), list(expected_counts.items()))
        self.assertEqual(
            list(actual_counts.items()),
            [("Zeta", 2), ("Alpha", 2), ("Beta", 1), ("Gamma", 1)],
        )

        unicode_values = ["É", "A", "É", "A", "Ω"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "ordered_string_counts_required", return_value=None
            ):
                expected_unicode_counts = script._ordered_counts(unicode_values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_unicode_counts = native_acceleration.ordered_string_counts(
                unicode_values
            )
            self.assertIsNotNone(native_unicode_counts)
            self.assertEqual(
                list(native_unicode_counts.items()),
                list(expected_unicode_counts.items()),
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ordered_string_counts",
                wraps=native_acceleration._native.ordered_string_counts,
            ) as rust_call:
                required_unicode_counts = script._ordered_counts(unicode_values)
                rust_call.assert_called_once_with(unicode_values)
        self.assertEqual(required_unicode_counts, expected_unicode_counts)
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(script._ordered_counts(values), expected_counts)

        surrogate_values = ["\ud800", "a", "a"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ordered_string_counts",
                wraps=native_acceleration._native.ordered_string_counts,
            ) as rust_call:
                surrogate_counts = script._ordered_counts(surrogate_values)
                rust_call.assert_called_once_with(surrogate_values)
        self.assertEqual(surrogate_counts, dict(Counter(surrogate_values).most_common()))

        unsupported_values = ["a", 1, "a"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ordered_string_counts",
                wraps=native_acceleration._native.ordered_string_counts,
            ) as rust_call:
                unsupported_counts = script._ordered_counts(unsupported_values)
                rust_call.assert_not_called()
        self.assertEqual(
            unsupported_counts, dict(Counter(unsupported_values).most_common())
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "ordered_string_counts_required", return_value=None
            ):
                expected_report = inspect_xscr_text(
                    SAMPLE_XSCR, source_name="test.xscr"
                )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "ordered_string_counts",
                wraps=native_acceleration._native.ordered_string_counts,
            ) as rust_call:
                actual_report = inspect_xscr_text(
                    SAMPLE_XSCR, source_name="test.xscr"
                )
                self.assertEqual(rust_call.call_count, 2)
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
