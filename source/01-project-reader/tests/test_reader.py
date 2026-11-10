from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
import json
import random
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tecan_common import native_acceleration
from tecan_common import xml_compat as ET
import tecan_common.xml_helpers as xml_helpers
import tecan_reader.common as reader_common
from tecan_reader.archive import inspect_archive
from tecan_reader.common import (
    command_short_name,
    compact_list,
    extension_counts,
    parse_xml_text,
    read_text,
    to_jsonable,
)
import tecan_reader.gwl as reader_gwl
import tecan_reader.xmlobj as reader_xmlobj
import tecan_reader.script as reader_script
from tecan_reader.gwl import inspect_gwl, inspect_gwl_lines
from tecan_reader.pattern_library import mine_script_patterns, search_script_patterns, summarize_script_patterns
import sqlite3
from unittest.mock import patch, MagicMock

from tecan_reader.project_index import build_project_index, search_project_index, summarize_project_index, _initialize_database
from tecan_reader.script import inspect_xscr_text


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
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.CommentStatement">
                <CommentStatement><Comment>hello</Comment><LineNumber>1</LineNumber></CommentStatement>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
                <LihaAspirateScriptCommandDataV5>
                  <LiquidClassName>Water Free Single</LiquidClassName>
                  <Volume>Vol</Volume>
                  <LabwareName>SourcePlate</LabwareName>
                  <LineNumber>2</LineNumber>
                </LihaAspirateScriptCommandDataV5>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum>ABC</Checksum>
</sd:VxData>
"""


WORKTABLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <ObjectName>Deck A Worktable</ObjectName>
  <TypeId>WorktableWorkspace</TypeId>
  <Name>Deck A Worktable</Name>
  <Guid>00000001-0000-0000-0000-000000000002</Guid>
</Root>
"""


CARRIER_XML = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <ObjectName>Plate Carrier 1</ObjectName>
  <TypeId>CarrierDefinition</TypeId>
  <FunctionalGroup>Carrier</FunctionalGroup>
  <Name>Plate Carrier 1</Name>
  <Guid>00000001-0000-0000-0000-000000000003</Guid>
</Root>
"""


LIQUID_CLASS_XML = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <ObjectName>Water Free Single</ObjectName>
  <TypeId>LiquidClass</TypeId>
  <Name>Water Free Single</Name>
  <Guid>00000001-0000-0000-0000-000000000004</Guid>
</Root>
"""


PATTERN_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="3">
  <Payload>
    <ObjectName>MCA384PatternScript</ObjectName>
    <Reference>
      <Guid>00000001-4321-eeee-ffff-000000000001</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>Base Worktable</ObjectName>
    </Reference>
    <PayloadData>
      <Script version="2.0">
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.UserPromptStatement">
                <UserPromptStatement>
                  <Name>ConfirmTips</Name>
                  <QueryPrompt>Confirm MCA384 tips are loaded</QueryPrompt>
                  <LineNumber>10</LineNumber>
                </UserPromptStatement>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.MCA384.Scripting.Mca384PickUpTipsScriptCommandDataV5">
                <Mca384PickUpTipsScriptCommandDataV5>
                  <LabwareName>MCA384TipBox</LabwareName>
                  <DeviceAlias>MCA384</DeviceAlias>
                  <LineNumber>11</LineNumber>
                </Mca384PickUpTipsScriptCommandDataV5>
              </Object>
              <Object Type="Tecan.Core.Scripting.CommentStatement">
                <CommentStatement>
                  <Comment>Confirm tip count</Comment>
                  <LineNumber>12</LineNumber>
                </CommentStatement>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.MCA384.Scripting.Mca384AspirateScriptCommandDataV5">
                <Mca384AspirateScriptCommandDataV5>
                  <LiquidClassName>Water Free Single</LiquidClassName>
                  <Volume>25</Volume>
                  <LabwareName>SourcePlate</LabwareName>
                  <DeviceAlias>MCA384</DeviceAlias>
                  <LineNumber>13</LineNumber>
                </Mca384AspirateScriptCommandDataV5>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.MCA384.Scripting.Mca384DispenseScriptCommandDataV5">
                <Mca384DispenseScriptCommandDataV5>
                  <LiquidClassName>Water Free Single</LiquidClassName>
                  <Volume>25</Volume>
                  <LabwareName>DestinationPlate</LabwareName>
                  <DeviceAlias>MCA384</DeviceAlias>
                  <LineNumber>14</LineNumber>
                </Mca384DispenseScriptCommandDataV5>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.MCA384.Scripting.Mca384DropTipsScriptCommandDataV5">
                <Mca384DropTipsScriptCommandDataV5>
                  <LabwareName>Waste</LabwareName>
                  <DeviceAlias>MCA384</DeviceAlias>
                  <LineNumber>15</LineNumber>
                </Mca384DropTipsScriptCommandDataV5>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.CGA.Scripting.CgaDropFingersScriptCommandDataV1">
                <CgaDropFingersScriptCommandDataV1>
                  <ScriptCommandCommonDataV2>
                    <LabwareName>AdapterA200</LabwareName>
                    <DeviceAlias>Instrument=1/Device=RGA:1</DeviceAlias>
                    <LineNumber>16</LineNumber>
                  </ScriptCommandCommonDataV2>
                </CgaDropFingersScriptCommandDataV1>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum>PATTERN</Checksum>
</sd:VxData>
"""


class ReaderTests(unittest.TestCase):
    def test_native_command_short_name_matches_regex_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "command_short_name"
        ):
            self.skipTest("optional Rust command-name helper is not built")
        cases = [
            ("Tecan.Core.LihaAspirateCommandDataV5", "LihaAspirateCommand"),
            ("ScriptCommandDataV3", "ScriptCommand"),
            ("Namespace.TypeDataV12", "Type"),
            ("Namespace.DataV5", ""),
            ("Namespace.DataV2DataV3", "DataV2"),
            ("Namespace.TypeDataV", "TypeDataV"),
            ("Namespace.TypeDataV2tail", "TypeDataV2tail"),
            ("Namespace.TypeDataV2.Inner", "Inner"),
            ("Namespace.CaféDataV2", "Café"),
        ]
        for type_name, expected in cases:
            with self.subTest(type_name=type_name):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    python_result = command_short_name(type_name)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    if type_name.isascii():
                        with patch.object(
                            reader_common,
                            "_native_command_short_name",
                            wraps=native_acceleration.command_short_name,
                        ) as native_call:
                            actual = command_short_name(type_name)
                        native_call.assert_called_once()
                    else:
                        self.assertIsNone(native_acceleration.command_short_name(type_name))
                        actual = command_short_name(type_name)
                self.assertEqual(python_result, expected)
                self.assertEqual(actual, expected)

    def test_native_compact_list_matches_python_order_and_limit_behavior(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "compact_list"
        ):
            self.skipTest("optional Rust compact-list helper is not built")
        cases = [
            (["alpha", "", "beta", "alpha", "gamma"], 2, ["alpha", "beta"]),
            (("alpha", "beta", "alpha"), 20, ["alpha", "beta"]),
            (["alpha", "beta"], 0, ["alpha"]),
            (["", "alpha"], 0, []),
            (["alpha", "alpha", "beta"], -1, ["alpha"]),
            ([], 0, []),
        ]
        for values, limit, expected in cases:
            with self.subTest(limit=limit, count=len(values)):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    python_result = compact_list(values, limit=limit)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    with patch.object(
                        reader_common,
                        "_native_compact_list",
                        wraps=native_acceleration.compact_list,
                    ) as native_call:
                        actual = compact_list(values, limit=limit)
                    native_call.assert_called_once()
                self.assertEqual(python_result, expected)
                self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                reader_common,
                "_native_compact_list",
                wraps=native_acceleration.compact_list,
            ) as native_call:
                self.assertEqual(compact_list(iter(["alpha", "alpha", "beta"])), ["alpha", "beta"])
                self.assertEqual(compact_list(["alpha", "beta"], limit=True), ["alpha"])
            self.assertEqual(native_call.call_count, 2)

    def test_native_xml_kind_from_suffix_matches_python_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_kind_from_suffix"
        ):
            self.skipTest("optional Rust XML suffix classifier is not built")
        cases = [
            (".xcmp", "component"),
            (".XWSP", "workspace"),
            (".XLQC", "liquid_class"),
            (".xlcp", "liquid_class_map"),
            (".XSIT", "site"),
            (".XCON", "connector"),
            (".XML", "xml"),
            ("", "xml"),
            (".", "xml"),
            ("...", "xml"),
            ("...custom", "custom"),
            ("catalog", "catalog"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [reader_xmlobj._kind_from_suffix(suffix) for suffix, _ in cases]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                reader_xmlobj,
                "_native_xml_kind_from_suffix",
                wraps=reader_xmlobj._native_xml_kind_from_suffix,
            ) as native_call:
                actual = [reader_xmlobj._kind_from_suffix(suffix) for suffix, _ in cases]
                self.assertEqual(native_call.call_count, len(cases))
        self.assertEqual(expected, [value for _, value in cases])
        self.assertEqual(actual, expected)

        class StringSubclass(str):
            pass

        unicode_suffix = "\u0130"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.xml_kind_from_suffix(unicode_suffix))
            self.assertEqual(reader_xmlobj._kind_from_suffix(unicode_suffix), "i\u0307")
            self.assertEqual(reader_xmlobj._kind_from_suffix(StringSubclass(".xcmp")), "component")

    def test_native_xml_local_name_matches_python_and_preserves_string_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_tag_local_name"
        ):
            self.skipTest("optional Rust XML local-name helper is not built")
        tags = ["tag", "{namespace}tag", "{one}{two}last", "}", "{}", "", "µ标签"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [xml_helpers.local_name(tag) for tag in tags]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xml_helpers,
                "_native_xml_local_name",
                wraps=xml_helpers._native_xml_local_name,
            ) as native_call:
                actual = [xml_helpers.local_name(tag) for tag in tags]
                self.assertEqual(native_call.call_count, len(tags))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["tag", "tag", "last", "", "", "", "µ标签"])

        class CustomTag:
            str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "{namespace}custom"

        custom = CustomTag()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.xml_local_name(custom))
            self.assertEqual(custom.str_calls, 0)
            self.assertEqual(xml_helpers.local_name(custom), "custom")
            self.assertEqual(custom.str_calls, 1)

    def test_native_xml_clean_text_matches_python_and_falls_back(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_clean_text"
        ):
            self.skipTest("optional Rust XML text helper is not built")
        values = [None, "", " ", "\t value \r\n", "plain", "\x1cvalue\x1c", "\u2003µ\u2003"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [xml_helpers._clean_text(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xml_helpers,
                "_native_xml_clean_text",
                wraps=xml_helpers._native_xml_clean_text,
            ) as native_call:
                actual = [xml_helpers._clean_text(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["", "", "", "value", "plain", "value", "µ"])

        class TextValue:
            def __bool__(self) -> bool:
                return True

            def __str__(self) -> str:
                return "  custom text  "

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.xml_clean_text(TextValue()))
            self.assertEqual(xml_helpers._clean_text(TextValue()), "custom text")

    def test_native_xml_texts_by_name_matches_python_grouping(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_texts_by_name"
        ):
            self.skipTest("optional Rust grouped XML text helper is not built")
        root = ET.Element("Root")
        ET.SubElement(root, "A").text = " first "
        ET.SubElement(root, "{urn:test}A").text = "first"
        ET.SubElement(root, "B").text = "second"
        ET.SubElement(root, "A").text = "third"
        name_list = ["A", "B", "A", "Missing"]
        names_in_order = list(set(name_list))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xml_helpers.texts_by_name(root, name_list)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.xml_texts_by_name(
                root, names_in_order, ET.Element
            )
            self.assertIsNotNone(direct)
            with patch.object(
                xml_helpers,
                "_native_xml_texts_by_name",
                wraps=xml_helpers._native_xml_texts_by_name,
            ) as native_call:
                actual = xml_helpers.texts_by_name(root, name_list)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual), list(expected))
        self.assertEqual(
            [actual[name] for name in names_in_order],
            direct,
        )
        self.assertEqual(actual["A"], ["first", "third"])
        self.assertEqual(actual["B"], ["second"])
        self.assertEqual(actual["Missing"], [])

        unicode_root = ET.Element("Root")
        ET.SubElement(unicode_root, "A").text = "café"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_texts_by_name(
                    unicode_root, ["A"], ET.Element
                )
            )
            self.assertEqual(xml_helpers.texts_by_name(unicode_root, ["A"]), {"A": ["café"]})

    def test_native_all_xml_texts_matches_python_without_default_limit(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_unique_texts"
        ):
            self.skipTest("optional Rust XML unique-text helper is not built")
        root = ET.Element("Root")
        for index in range(205):
            ET.SubElement(root, "Value").text = f"item-{index}"
        ET.SubElement(root, "Value").text = "item-0"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xml_helpers.all_text(root, {"Value"})
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_unique_texts(
                    root, {"Value"}, (1 << 63) - 1, ET.Element
                ),
                expected,
            )
            with patch.object(
                xml_helpers,
                "_native_xml_unique_texts",
                wraps=xml_helpers._native_xml_unique_texts,
            ) as native_call:
                actual = xml_helpers.all_text(root, {"Value"})
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 205)
        self.assertEqual((actual[0], actual[-1]), ("item-0", "item-204"))

        unicode_root = ET.Element("Root")
        ET.SubElement(unicode_root, "Value").text = "café"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = xml_helpers.all_text(unicode_root, {"Value"})
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_unique_texts(
                    unicode_root, {"Value"}, (1 << 63) - 1, ET.Element
                )
            )
            self.assertEqual(xml_helpers.all_text(unicode_root, {"Value"}), unicode_expected)

    def test_native_unique_xml_texts_matches_python_limits_and_fallbacks(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_unique_texts"
        ):
            self.skipTest("optional Rust XML unique-text helper is not built")
        root = ET.Element("Root")
        ET.SubElement(root, "A").text = " first "
        ET.SubElement(root, "{urn:test}A").text = "first"
        ET.SubElement(root, "B").text = "second"
        ET.SubElement(root, "A").text = "third"
        cases = [
            (root, {"A", "B"}, 20),
            (root, {"A", "B"}, 2),
            (root, {"A", "B"}, 0),
            (root, {"A", "B"}, -1),
            (root, {"Missing"}, 20),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                xml_helpers.unique_texts(tree, names, limit=limit)
                for tree, names, limit in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_unique_texts(root, {"A", "B"}, 20, ET.Element),
                expected[0],
            )
            with patch.object(
                xml_helpers,
                "_native_xml_unique_texts",
                wraps=xml_helpers._native_xml_unique_texts,
            ) as native_call:
                actual = [
                    xml_helpers.unique_texts(tree, names, limit=limit)
                    for tree, names, limit in cases
                ]
                self.assertEqual(native_call.call_count, len(cases))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [["first", "second", "third"], ["first", "second"], ["first"], ["first"], []],
        )

        unicode_root = ET.Element("Root")
        ET.SubElement(unicode_root, "A").text = "café"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = xml_helpers.unique_texts(unicode_root, {"A"})
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_unique_texts(
                    unicode_root, {"A"}, 200, ET.Element
                )
            )
            self.assertEqual(xml_helpers.unique_texts(unicode_root, {"A"}), unicode_expected)

        comment_root = ET.Element("Root")
        comment_root.append(ET.Comment("comment"))
        ET.SubElement(comment_root, "A").text = "after comment"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_unique_texts(
                    comment_root, {"A"}, 200, ET.Element
                )
            )
            self.assertEqual(xml_helpers.unique_texts(comment_root, {"A"}), ["after comment"])
            self.assertIsNone(
                native_acceleration.xml_unique_texts(root, {"A"}, True, ET.Element)
            )
            self.assertEqual(xml_helpers.unique_texts(root, {"A"}, limit=True), ["first"])

    def test_native_xml_find_text_matches_python_and_preserves_defaults(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_find_text"
        ):
            self.skipTest("optional Rust XML text-search helper is not built")
        root = ET.Element("{urn:test}Root")
        ET.SubElement(root, "A").text = "   "
        ET.SubElement(root, "{urn:test}A").text = " alpha "
        nested = ET.SubElement(root, "Wrap")
        ET.SubElement(nested, "A").text = "later"
        ET.SubElement(nested, "NestedValue").text = "nested result"
        absent = object()

        def results() -> tuple[object, ...]:
            return (
                xml_helpers.first_text(root, "A"),
                xml_helpers.child_text(root, "A"),
                xml_helpers.first_text(root, "NestedValue"),
                xml_helpers.child_text(root, "Wrap", default=absent),
                xml_helpers.first_text(root, "Missing", default=absent),
            )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = results()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_find_text(root, "A", False, ET.Element),
                (True, "alpha"),
            )
            self.assertEqual(
                native_acceleration.xml_find_text(root, "A", True, ET.Element),
                (True, "alpha"),
            )
            self.assertEqual(
                native_acceleration.xml_find_text(root, "Missing", False, ET.Element),
                (False, None),
            )
            with patch.object(
                xml_helpers,
                "_native_xml_find_text",
                wraps=xml_helpers._native_xml_find_text,
            ) as native_call:
                actual = results()
                self.assertEqual(native_call.call_count, 5)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[:3], ("alpha", "alpha", "nested result"))
        self.assertIs(actual[3], absent)
        self.assertIs(actual[4], absent)

        unicode_root = ET.Element("Root")
        ET.SubElement(unicode_root, "A").text = "\u2003café\u2003"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = xml_helpers.first_text(unicode_root, "A")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_find_text(unicode_root, "A", False, ET.Element)
            )
            self.assertEqual(xml_helpers.first_text(unicode_root, "A"), unicode_expected)

        comment_root = ET.Element("Root")
        comment_root.append(ET.Comment("comment"))
        ET.SubElement(comment_root, "A").text = "after comment"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_find_text(
                    comment_root, "A", False, ET.Element
                )
            )
            self.assertEqual(xml_helpers.first_text(comment_root, "A"), "after comment")

    def test_native_xml_first_attribute_preserves_first_match_and_default(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_find_attribute"
        ):
            self.skipTest("optional Rust XML attribute-search helper is not built")
        root = ET.Element("Root")
        ET.SubElement(root, "{urn:test}Script")
        ET.SubElement(root, "Script", {"version": "later"})
        valued = ET.Element("Root")
        ET.SubElement(valued, "{urn:test}Script", {"version": "  v1  "})
        absent = ET.Element("Root")
        ET.SubElement(absent, "Other", {"version": "not-a-script"})
        comment_root = ET.Element("Root")
        comment_root.append(ET.Comment("comment"))
        ET.SubElement(comment_root, "Script", {"version": "comment-safe"})

        def results() -> tuple[str, str, str, str]:
            return (
                xml_helpers.first_attribute(root, "Script", "version"),
                xml_helpers.first_attribute(valued, "Script", "version"),
                xml_helpers.first_attribute(absent, "Script", "version", default="missing"),
                reader_script._script_version(comment_root),
            )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = results()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_find_attribute(
                    valued, "Script", "version", "", ET.Element
                ),
                "  v1  ",
            )
            self.assertIsNone(
                native_acceleration.xml_find_attribute(
                    comment_root, "Script", "version", "", ET.Element
                )
            )
            with patch.object(
                xml_helpers,
                "_native_xml_find_attribute",
                wraps=xml_helpers._native_xml_find_attribute,
            ) as native_call:
                actual = results()
                self.assertEqual(native_call.call_count, 4)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ("", "  v1  ", "missing", "comment-safe"))

    def test_native_xml_records_by_name_matches_python_first_text_traversal(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_records_by_name"
        ):
            self.skipTest("optional Rust XML record extractor is not built")
        root = ET.Element("Root")
        prompt = ET.SubElement(root, "{urn:test}QueryVariableStatement")
        ET.SubElement(prompt, "Name").text = "   "
        nested = ET.SubElement(prompt, "Nested")
        ET.SubElement(nested, "Name").text = " prompt-x "
        ET.SubElement(prompt, "QueryPrompt").text = " question-x "
        ET.SubElement(prompt, "MinimumText").text = "1"
        ET.SubElement(prompt, "MaximumText").text = "2"
        ET.SubElement(prompt, "LineNumber").text = "7"
        second_prompt = ET.SubElement(root, "QueryVariableStatement")
        ET.SubElement(second_prompt, "Name").text = "second-x"
        ET.SubElement(second_prompt, "QueryPrompt").text = "question-y"
        assignment = ET.SubElement(root, "SetVariableStatement")
        ET.SubElement(assignment, "Name").text = "variable-x"
        ET.SubElement(assignment, "Value").text = "value-x"
        ET.SubElement(assignment, "LineNumber").text = "9"

        def results() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
            return reader_script._query_prompts(root), reader_script._set_variables(root)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = results()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_records_by_name(
                    root,
                    "QueryVariableStatement",
                    ("Name", "QueryPrompt", "MinimumText", "MaximumText", "LineNumber"),
                    ET.Element,
                ),
                [
                    ["prompt-x", "question-x", "1", "2", "7"],
                    ["second-x", "question-y", "", "", ""],
                ],
            )
            with patch.object(
                xml_helpers,
                "_native_xml_records_by_name",
                wraps=xml_helpers._native_xml_records_by_name,
            ) as native_call:
                actual = results()
                self.assertEqual(native_call.call_count, 2)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0][0]["name"], "prompt-x")
        self.assertEqual(actual[0][1]["prompt"], "question-y")
        self.assertEqual(actual[1], [{"name": "variable-x", "value": "value-x", "line": "9"}])

        fallback_root = ET.Element("Root")
        fallback_root.append(ET.Comment("comment"))
        fallback_prompt = ET.SubElement(fallback_root, "QueryVariableStatement")
        ET.SubElement(fallback_prompt, "Name").text = "fallback-x"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_records_by_name(
                    fallback_root, "QueryVariableStatement", ("Name",), ET.Element
                )
            )
            self.assertEqual(
                reader_script._query_prompts(fallback_root)[0]["name"], "fallback-x"
            )
        unicode_root = ET.Element("Root")
        unicode_prompt = ET.SubElement(unicode_root, "QueryVariableStatement")
        ET.SubElement(unicode_prompt, "QueryPrompt").text = "café"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_records_by_name(
                    unicode_root, "QueryVariableStatement", ("QueryPrompt",), ET.Element
                )
            )
            self.assertEqual(
                reader_script._query_prompts(unicode_root)[0]["prompt"], "café"
            )

    def test_native_xml_direct_records_match_reference_extraction(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_records_by_name"
        ):
            self.skipTest("optional Rust XML record extractor is not built")
        root = ET.Element("Root")
        first = ET.SubElement(root, "{urn:test}Reference")
        ET.SubElement(first, "Guid").text = "   "
        ET.SubElement(first, "Guid").text = " guid-x "
        ET.SubElement(first, "TypeId").text = "type-x"
        nested = ET.SubElement(first, "Nested")
        ET.SubElement(nested, "ObjectName").text = "nested-name-x"
        ET.SubElement(first, "ObjectSubfolderPath").text = " path-x "
        empty = ET.SubElement(root, "Reference")
        ET.SubElement(empty, "Nested")
        third = ET.SubElement(root, "Reference")
        ET.SubElement(third, "TypeId").text = "type-y"

        def references() -> list[dict[str, str]]:
            return reader_script._references(root)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = references()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_records_by_name(
                    root,
                    "Reference",
                    ("Guid", "TypeId", "ObjectName", "ObjectSubfolderPath"),
                    ET.Element,
                    direct=True,
                ),
                [
                    ["guid-x", "type-x", "", "path-x"],
                    ["", "", "", ""],
                    ["", "type-y", "", ""],
                ],
            )
            with patch.object(
                xml_helpers,
                "_native_xml_records_by_name",
                wraps=xml_helpers._native_xml_records_by_name,
            ) as native_call:
                actual = references()
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 2)
        self.assertEqual(actual[0]["guid"], "guid-x")
        self.assertEqual(actual[0]["object_name"], "")
        self.assertEqual(actual[0]["object_subfolder_path"], actual[0]["object_path"])
        self.assertEqual(actual[1]["type_id"], "type-y")

    def test_native_asset_path_names_match_pathlib_and_deduplicate(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "asset_reference_names"
        ):
            self.skipTest("optional Rust asset path basename helper is not built")
        paths = [
            "folder\\image.png",
            "folder/image.png",
            "image.png",
            "folder\\",
            "/",
            ".",
            "..",
            "folder/.",
            "folder/..",
            "C:\\",
            "C:",
            "C:relative.png",
            "\\\\server\\share\\file.bmp",
            "a\\b//c",
            ".hidden",
            "..hidden",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_script._asset_refs(paths)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native = native_acceleration.asset_reference_names(paths)
            self.assertEqual(native, expected)
            with patch.object(
                reader_script.native_acceleration,
                "asset_reference_names",
                wraps=reader_script.native_acceleration.asset_reference_names,
            ) as native_call:
                actual = reader_script._asset_refs(paths)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)

        unicode_paths = ["folder\\café.png"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.asset_reference_names(unicode_paths))
            self.assertEqual(
                reader_script._asset_refs(unicode_paths), ["café.png"]
            )

    def test_native_pin_reference_filter_matches_python_regex(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "pin_reference_candidates"
        ):
            self.skipTest("optional Rust pin-reference filter is not built")
        values = [
            "prefixGIO1_Pin2",
            "(GIO1_Pin2) GIO12_Pin3",
            "xWorktable_Pin",
            "Worktable_Pin Worktable_namePin_suffix",
            "WorktablePin_extra WorktablePin_",
            "GIO3_Pin4x",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_script._pin_refs(values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.pin_reference_candidates(values), expected
            )
            with patch.object(
                reader_script.native_acceleration,
                "pin_reference_candidates",
                wraps=reader_script.native_acceleration.pin_reference_candidates,
            ) as native_call:
                actual = reader_script._pin_refs(values)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                "GIO12_Pin3",
                "GIO1_Pin2",
                "WorktablePin_extra",
                "Worktable_Pin",
                "Worktable_namePin_suffix",
            ],
        )

        generated = [
            prefix + token + suffix
            for prefix in ("", "x", "_", " ", "(", "/")
            for token in (
                "GIO1_Pin2",
                "GIO12_Pin345",
                "Worktable_Pin",
                "Worktable_xPinY",
                "WorktablePin_x",
                "GIO_Pin",
                "Worktable_xPin",
                "WorktablePin_",
            )
            for suffix in ("", "x", "_", " ", ".", "-", "9")
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            generated_expected = reader_script._pin_refs(generated)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.pin_reference_candidates(generated),
                generated_expected,
            )
        unicode_values = ["éGIO1_Pin2", "GIO2_Pin3 café"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = reader_script._pin_refs(unicode_values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.pin_reference_candidates(unicode_values)
            )
            self.assertEqual(reader_script._pin_refs(unicode_values), unicode_expected)

    def test_xml_object_unknown_fields_use_native_grouping_with_python_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_grouped_texts_excluding"
        ):
            self.skipTest("optional Rust XML grouping helper is not built")
        root = ET.Element("Root")
        ET.SubElement(root, "ObjectName").text = "known"
        ET.SubElement(root, "Unknown").text = " first "
        ET.SubElement(root, "Unknown").text = "first"
        ET.SubElement(root, "Unknown").text = "second"
        for index in range(22):
            ET.SubElement(root, "Wide").text = f"value-{index:02d}"
        expected = {
            "Unknown": ["first", "second"],
            "Wide": [f"value-{index:02d}" for index in range(20)],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            self.assertEqual(reader_xmlobj._unknown_fields(root), expected)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xml_helpers,
                "_native_xml_grouped_texts_excluding",
                wraps=xml_helpers._native_xml_grouped_texts_excluding,
            ) as native_call:
                actual = reader_xmlobj._unknown_fields(root)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)

    def test_xml_object_asset_reference_filter_matches_legacy_regex(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_asset_reference_names"
        ):
            self.skipTest("optional Rust XML asset-reference filter is not built")
        text = (
            "<Root><A>first.png and second.jpg</A>"
            "<B>folder\\image.TIFF</B><C>only.pngx</C>"
            "<D>quotes \" hidden.gif</D><E>.starts.png</E>"
            "<F>.bmp</F><G>split.bmp</G><H>dup.bmp</H><I>dup.bmp</I></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_xmlobj._asset_refs(text)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_asset_reference_names(text), expected
            )
            with patch.object(
                reader_xmlobj,
                "_native_xml_asset_reference_names",
                wraps=native_acceleration.xml_asset_reference_names,
            ) as native_call:
                actual = reader_xmlobj._asset_refs(text)
                native_call.assert_called_once_with(text)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                ".starts.png",
                "dup.bmp",
                "first.png and second.jpg",
                "hidden.gif",
                "image.TIFF",
                "only.png",
                "split.bmp",
            ],
        )

        unicode_text = "<Root><Asset>café.png</Asset></Root>"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = reader_xmlobj._asset_refs(unicode_text)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_asset_reference_names(unicode_text)
            )
            self.assertEqual(reader_xmlobj._asset_refs(unicode_text), unicode_expected)

    def test_xml_object_pin_reference_filter_uses_rust_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "pin_reference_candidates"
        ):
            self.skipTest("optional Rust pin-reference filter is not built")
        text = "GIO1_Pin2 Worktable_Pin Worktable_xPin_3 GIO1_Pin2"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_xmlobj._pin_refs(text)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                reader_xmlobj,
                "_native_pin_reference_candidates",
                wraps=native_acceleration.pin_reference_candidates,
            ) as native_call:
                actual = reader_xmlobj._pin_refs(text)
                native_call.assert_called_once_with([text])
        self.assertEqual(actual, expected)

        unicode_text = "éGIO1_Pin2 GIO3_Pin4"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = reader_xmlobj._pin_refs(unicode_text)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.pin_reference_candidates([unicode_text]))
            self.assertEqual(reader_xmlobj._pin_refs(unicode_text), unicode_expected)

    def test_native_asset_reference_suffix_filter_matches_python_regex(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "asset_reference_candidates"
        ):
            self.skipTest("optional Rust image-reference filter is not built")
        values = [
            "C:\\Assets\\MAP.JPEG",
            "picture.pngsometext",
            "picture.png\\path",
            "note.gif' annotation",
            'chart.tiff"',
            "picture.png ",
            "diagram.tif\x1cannotation",
            "false-positive.bmpx",
            "not-a-suffix.png/path",
            "plain text",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_script._asset_refs_from_text(values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            candidates = native_acceleration.asset_reference_candidates(values)
            self.assertEqual(
                candidates,
                [values[0], values[1], values[2], values[3], values[4]],
            )
            with patch.object(
                reader_script.native_acceleration,
                "asset_reference_candidates",
                wraps=reader_script.native_acceleration.asset_reference_candidates,
            ) as native_call:
                actual = reader_script._asset_refs_from_text(values)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)

        unicode_values = ["picture.png\N{NO-BREAK SPACE}", "picture.g\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}f"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = reader_script._asset_refs_from_text(unicode_values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.asset_reference_candidates(unicode_values)
            )
            self.assertEqual(
                reader_script._asset_refs_from_text(unicode_values), unicode_expected
            )

    def test_native_entry_object_guid_matches_pure_posixpath_rules(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "entry_object_guid"
        ):
            self.skipTest("optional Rust filename GUID helper is not built")
        guid = "0035411b-0f50-47d8-90e4-69a2e577ce36"
        zero_guid = "00000000-0000-0000-0000-000000000000"
        cases = [
            f"C:\\Data\\{guid.upper()}.XSCR",
            f"/archive/{guid}",
            f"{guid}/",
            f"{guid}/./",
            f"{guid}.backup.xscr",
            f".{guid}",
            f"{zero_guid}.xscr",
            f"{guid}/..",
            "not-a-guid.xscr",
            "",
            None,
            42,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [reader_script._entry_object_guid(value) for value in cases]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual = [reader_script._entry_object_guid(value) for value in cases]
            self.assertEqual(
                native_acceleration.entry_object_guid(f"C:\\Data\\{guid.upper()}.XSCR"),
                guid,
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [guid, guid, guid, guid, "", "", "", "", "", "", "", ""])

    def test_native_xscr_command_records_preserve_fields_and_python_classification(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xscr_command_records"
        ):
            self.skipTest("optional Rust XSCR command extractor is not built")
        root = ET.Element("Root")
        ET.SubElement(root, "Object").text = "not-a-command-x"
        first = ET.SubElement(
            root, "{urn:test}Object", {"Type": "Namespace.SampleCommandDataV2"}
        )
        ET.SubElement(first, "Name").text = "  "
        ET.SubElement(first, "Name").text = " command-x "
        ET.SubElement(first, "LineNumber").text = " 9 "
        ET.SubElement(first, "Comment").text = " alpha | beta "
        ET.SubElement(first, "Comment").text = "alpha"
        ET.SubElement(first, "Comment").text = "gamma"
        ET.SubElement(first, "Value").text = " 1 "
        nested = ET.SubElement(
            first, "Object", {"Type": "Namespace.NestedCommandDataV2"}
        )
        ET.SubElement(nested, "Name").text = "nested-x"
        second = ET.SubElement(root, "Object", {"Type": "Namespace.OtherCommand"})
        ET.SubElement(second, "Value").text = "2"

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_script._commands(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_records = native_acceleration.xscr_command_records(
                root, reader_script.INTERESTING_FIELDS, ET.Element
            )
            self.assertEqual(
                native_records,
                [
                    (
                        "Namespace.SampleCommandDataV2",
                        {
                            "Name": "command-x | nested-x",
                            "LineNumber": "9",
                            "Comment": "alpha | beta | gamma",
                            "Value": "1",
                        },
                    ),
                    (
                        "Namespace.NestedCommandDataV2",
                        {"Name": "nested-x"},
                    ),
                    ("Namespace.OtherCommand", {"Value": "2"}),
                ],
            )
            with patch.object(
                reader_script.native_acceleration,
                "xscr_command_records",
                wraps=reader_script.native_acceleration.xscr_command_records,
            ) as native_call:
                actual = reader_script._commands(root)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual([command["index"] for command in actual], [1, 2, 3])
        self.assertEqual(actual[0]["type"], "SampleCommand")
        self.assertEqual(actual[0]["family"], "Other")
        self.assertEqual(actual[0]["line"], "9")
        self.assertEqual(actual[0]["fields"]["Comment"], "alpha | beta | gamma")
        self.assertEqual(actual[1]["type"], "NestedCommand")
        self.assertEqual(actual[1]["name"], "nested-x")
        self.assertEqual(
            list(actual[0]["fields"]), ["Name", "LineNumber", "Comment", "Value"]
        )

        unicode_root = ET.Element("Root")
        unicode_command = ET.SubElement(
            unicode_root, "Object", {"Type": "Namespace.SampleCommand"}
        )
        ET.SubElement(unicode_command, "Name").text = "café"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xscr_command_records(
                    unicode_root, reader_script.INTERESTING_FIELDS, ET.Element
                )
            )
            self.assertEqual(
                reader_script._commands(unicode_root)[0]["name"], "café"
            )

    def test_native_operator_prompt_element_selection_preserves_document_order(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML element selector is not built")
        root = ET.Element("Root")
        first = ET.SubElement(root, "{urn:test}RUPVariableStatement")
        ET.SubElement(first, "RUPScreenTitle").text = " title-1 "
        ET.SubElement(first, "LineNumber").text = "11"
        variable = ET.SubElement(first, "RupVariableItem")
        ET.SubElement(variable, "VariableName").text = "variable-x"
        ET.SubElement(variable, "DisplayText").text = "display-x"
        second = ET.SubElement(root, "RUPStandardStatement")
        ET.SubElement(second, "Instructions").text = " instructions-2 "
        ET.SubElement(root, "RUPWorktableStatement")
        ET.SubElement(root, "RUPVariableStatement").text = ""
        fifth = ET.SubElement(root, "RUPStandardStatement")
        ET.SubElement(fifth, "RUPScreenTitle").text = "title-5"
        wanted = {
            "RUPVariableStatement",
            "RUPWorktableStatement",
            "RUPStandardStatement",
        }

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_script._operator_prompts(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            elements = native_acceleration.xml_elements_by_name(root, wanted, ET.Element)
            self.assertIsNotNone(elements)
            self.assertEqual(
                [xml_helpers.local_name(element.tag) for element in elements or []],
                [
                    "RUPVariableStatement",
                    "RUPStandardStatement",
                    "RUPWorktableStatement",
                    "RUPVariableStatement",
                    "RUPStandardStatement",
                ],
            )
            with patch.object(
                xml_helpers,
                "_native_xml_elements_by_name",
                wraps=xml_helpers._native_xml_elements_by_name,
            ) as native_call:
                actual = reader_script._operator_prompts(root)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual([prompt["kind"] for prompt in actual], ["RUPVariableStatement", "RUPStandardStatement", "RUPStandardStatement"])
        self.assertEqual(actual[0]["variables"][0]["name"], "variable-x")
        self.assertEqual(actual[1]["instructions"], "instructions-2")
        self.assertEqual(actual[2]["title"], "title-5")

        comment_root = ET.Element("Root")
        comment_root.append(ET.Comment("comment"))
        comment_prompt = ET.SubElement(comment_root, "RUPStandardStatement")
        ET.SubElement(comment_prompt, "Instructions").text = "after-comment"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_elements_by_name(
                    comment_root, {"RUPStandardStatement"}, ET.Element
                )
            )
            self.assertEqual(
                reader_script._operator_prompts(comment_root)[0]["instructions"],
                "after-comment",
            )

    def test_native_unknown_xscr_fields_match_python_grouping_and_limits(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_grouped_texts_excluding"
        ):
            self.skipTest("optional Rust XML grouped-text helper is not built")
        root = ET.Element("Root")
        ET.SubElement(root, "Name").text = "known-field-x"
        for index in range(22):
            ET.SubElement(root, "UnknownZ").text = f" value-{index} "
        ET.SubElement(root, "UnknownA").text = " first-a "
        ET.SubElement(root, "UnknownA").text = " first-a "
        ET.SubElement(root, "UnknownA").text = " second-a "

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_script._unknown_fields(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xml_helpers,
                "_native_xml_grouped_texts_excluding",
                wraps=xml_helpers._native_xml_grouped_texts_excluding,
            ) as native_call:
                actual = reader_script._unknown_fields(root)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual), ["UnknownA", "UnknownZ"])
        self.assertEqual(actual["UnknownA"], ["first-a", "second-a"])
        self.assertEqual(actual["UnknownZ"], [f"value-{index}" for index in range(20)])
        self.assertNotIn("Name", actual)

        unicode_root = ET.Element("Root")
        ET.SubElement(unicode_root, "Unknown").text = " café "
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_grouped_texts_excluding(
                    unicode_root, {"Name"}, 20, ET.Element
                )
            )
            self.assertEqual(reader_script._unknown_fields(unicode_root), {"Unknown": ["café"]})

    def test_native_rup_variable_record_extraction_matches_python(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_records_by_name"
        ):
            self.skipTest("optional Rust XML record extractor is not built")
        root = ET.Element("Statement")
        first = ET.SubElement(root, "{urn:test}RupVariableItem")
        ET.SubElement(first, "VariableName").text = "  "
        ET.SubElement(first, "VariableName").text = " variable-x "
        ET.SubElement(first, "DisplayText").text = " value-x "
        second = ET.SubElement(root, "RupVariableItem")
        nested = ET.SubElement(second, "Nested")
        ET.SubElement(nested, "VariableName").text = "not-direct-x"
        third = ET.SubElement(root, "RupVariableItem")
        ET.SubElement(third, "IsEnabled").text = "false"

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = reader_script._rup_variable_items(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xml_helpers,
                "_native_xml_records_by_name",
                wraps=xml_helpers._native_xml_records_by_name,
            ) as native_call:
                actual = reader_script._rup_variable_items(root)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                {
                    "name": "variable-x",
                    "display_text": "value-x",
                    "display_type": "",
                    "allowed_values": "",
                    "enabled": "",
                },
                {
                    "name": "",
                    "display_text": "",
                    "display_type": "",
                    "allowed_values": "",
                    "enabled": "false",
                },
            ],
        )

    def test_native_xml_all_text_values_preserves_order_comments_and_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_all_unique_texts"
        ):
            self.skipTest("optional Rust XML text collector is not built")
        root = ET.Element("Root")
        root.text = " root-x "
        ET.SubElement(root, "First").text = " repeat-x "
        root.append(ET.Comment("comment-x"))
        ET.SubElement(root, "Last").text = "repeat-x"
        ET.SubElement(root, "Empty").text = "  "
        expected_values = ["root-x", "repeat-x", "comment-x"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xml_helpers.all_text_values(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.xml_all_unique_texts(root, ET.Element),
                expected_values,
            )
            with patch.object(
                xml_helpers,
                "_native_xml_all_unique_texts",
                wraps=xml_helpers._native_xml_all_unique_texts,
            ) as native_call:
                actual = xml_helpers.all_text_values(root)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)

        unicode_root = ET.Element("Root")
        ET.SubElement(unicode_root, "Text").text = " café "
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_all_unique_texts(unicode_root, ET.Element)
            )
            self.assertEqual(xml_helpers.all_text_values(unicode_root), ["café"])

    def test_native_extension_counts_match_path_suffix_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "extension_counts"
        ):
            self.skipTest("optional Rust extension counter is not built")
        names = [
            Path("script.XSCR"),
            "archive.tar.GZ",
            ".profile",
            "trailing.",
            "folder/name.XML",
            "..",
            "...",
            "folder/..",
            "foo..bar",
            "....jpg",
            "..foo.bar",
            ".foo...",
            "C:relative.png",
            Path("C:\\"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = extension_counts(names)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            expected_suffixes = [Path(name).suffix.lower() or "<none>" for name in names]
            self.assertEqual(
                native_acceleration.path_suffixes(names), expected_suffixes
            )
            with patch.object(
                reader_common,
                "_native_path_suffixes",
                wraps=native_acceleration.path_suffixes,
            ) as suffix_call:
                extension_counts(names)
                suffix_call.assert_called_once()
            with patch.object(
                reader_common,
                "_native_extension_counts",
                wraps=native_acceleration.extension_counts,
            ) as native_call:
                actual = extension_counts(names)
            native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(
            list(actual.items()),
            [
                (".bar", 2),
                (".gz", 1),
                (".jpg", 1),
                (".png", 1),
                (".xml", 1),
                (".xscr", 1),
                ("<none>", 7),
            ],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.path_suffixes(["café.txt"]))
            self.assertEqual(extension_counts(["café.txt"]), {".txt": 1})
            with patch.object(
                reader_common,
                "_native_extension_counts",
                side_effect=AssertionError("generic iterables must retain streaming fallback"),
            ):
                self.assertEqual(
                    extension_counts(iter(["a.X", "b.X", "c.Y"])),
                    {".x": 2, ".y": 1},
                )

    def test_native_reader_text_decode_matches_python_encoding_order(self) -> None:
        if (
            not native_acceleration.available()
            or not hasattr(native_acceleration._native, "decode_text_bytes")
            or not hasattr(native_acceleration._native, "read_text_file")
        ):
            self.skipTest("optional Rust text decoder is not built")

        def python_decode(data: bytes) -> str:
            for encoding in ("utf-8-sig", "utf-8", "utf-16"):
                try:
                    return data.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return data.decode("latin-1", errors="replace")

        native_endian = "utf-16-le" if sys.byteorder == "little" else "utf-16-be"
        samples = [
            b"",
            b"plain ASCII",
            b"\xef\xbb\xbfUTF-8 BOM",
            "naïve café".encode("utf-8"),
            "αβ".encode("utf-16"),
            "αβ".encode(native_endian),
            b"\xfe\xff\x00a",
            b"\xff\xfe\x00",
            b"\xff\xfe\x00\xd8",
            b"\xff\xfe",
            b"\xef\xbb\xbf\xff",
        ]
        generator = random.Random(0x54454341)
        samples.extend(
            generator.randbytes(generator.randrange(0, 33)) for _ in range(300)
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            for data in samples:
                with self.subTest(length=len(data), prefix=data[:4]):
                    expected = python_decode(data)
                    self.assertEqual(native_acceleration.decode_text_bytes(data), expected)

            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "sample.txt"
                for data in samples:
                    target.write_bytes(data)
                    self.assertEqual(read_text(target), python_decode(data))

    def test_native_file_reader_avoids_python_byte_read_and_keeps_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "read_text_file"
        ):
            self.skipTest("optional Rust file reader is not built")
        with tempfile.TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "utf16.txt"
            path.write_bytes("native reader ✓".encode("utf-16"))
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("native file reader should avoid Python byte reads"),
                ):
                    self.assertEqual(read_text(path), "native reader ✓")
            with patch.object(native_acceleration, "_native", None):
                self.assertEqual(read_text(path), "native reader ✓")

    def test_native_jsonable_containers_match_python_recursive_conversion(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "to_jsonable_containers"
        ):
            self.skipTest("optional Rust JSON-container converter is not built")
        default_factory_calls = 0

        def default_factory() -> list[object]:
            nonlocal default_factory_calls
            default_factory_calls += 1
            return []

        default_mapping = defaultdict(
            default_factory, {"path": Path("nested.xml"), "values": (1, 2)}
        )
        ordered_mapping = OrderedDict(
            [("second", (2,)), ("first", Path("first.xml"))]
        )
        cases = [
            (
                {"items": (1, ["two", {"three": (3,)}]), "empty": []},
                {"items": [1, ["two", {"three": (3,)}]], "empty": []},
            ),
            (
                (None, True, 4, 2.5, "text", b"raw"),
                [None, True, 4, 2.5, "text", b"raw"],
            ),
            ([], []),
            ({}, {}),
            (set(), []),
            ({False, True}, [False, True]),
            ({True, 2.5, -3, 2**80}, [-3, True, 2.5, 2**80]),
            (
                {float("-inf"), -1.5, 2.5, float("inf")},
                [float("-inf"), -1.5, 2.5, float("inf")],
            ),
            ({b"z", b"\x00", b"a"}, [b"\x00", b"a", b"z"]),
            (
                {-10, 0, 2**80, -(2**100)},
                [-(2**100), -10, 0, 2**80],
            ),
            (Path("sample.xscr"), "sample.xscr"),
            (
                {"path": Path("nested.xml"), "values": (1, 2)},
                {"path": "nested.xml", "values": [1, 2]},
            ),
            (
                default_mapping,
                {"path": "nested.xml", "values": [1, 2]},
            ),
            (
                ordered_mapping,
                {"second": [2], "first": "first.xml"},
            ),
            (
                Counter({"warning": 2, "opaque": (1,)}),
                {"warning": 2, "opaque": (1,)},
            ),
            (
                [Counter({"warning": 2, "opaque": (1,)})],
                [{"warning": 2, "opaque": (1,)}],
            ),
            ({"é", "z", "A", "a", "Å"}, ["A", "a", "z", "Å", "é"]),
            (
                {
                    1: "integer",
                    "1": "string",
                    2.5: "float",
                    False: "boolean",
                    None: "none",
                    b"raw": "bytes",
                    (1, ("x",)): "tuple",
                },
                {
                    "1": "string",
                    "2.5": "float",
                    "False": "boolean",
                    "None": "none",
                    "b'raw'": "bytes",
                    "(1, ('x',))": "tuple",
                },
            ),
        ]
        for value, expected in cases:
            with self.subTest(value_type=type(value).__name__):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    python_result = to_jsonable(value)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    native = native_acceleration.to_jsonable_containers(value)
                    self.assertIsNotNone(native)
                    actual = to_jsonable(value)
                self.assertEqual(python_result, expected)
                self.assertEqual(actual, expected)
        self.assertEqual(default_factory_calls, 0)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            ordered_result = to_jsonable(ordered_mapping)
        self.assertEqual(list(ordered_result), ["second", "first"])

        class CustomKey:
            def __init__(self) -> None:
                self.string_calls = 0

            def __str__(self) -> str:
                self.string_calls += 1
                return "custom"

        custom_key = CustomKey()
        custom_key_mapping = {custom_key: "value"}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.to_jsonable_containers(custom_key_mapping)
            )
        self.assertEqual(custom_key.string_calls, 0)

        class DerivedCounter(Counter):
            pass

        class DerivedDefaultDict(defaultdict):
            pass

        class DerivedOrderedDict(OrderedDict):
            pass

        fallback_values = [
            DerivedCounter({"warning": 2}),
            DerivedDefaultDict(list, {"value": (1,)}),
            DerivedOrderedDict([("value", (1,))]),
            custom_key_mapping,
        ]
        for value in fallback_values:
            with self.subTest(fallback_type=type(value).__name__):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    self.assertIsNone(
                        native_acceleration.to_jsonable_containers(value)
                    )
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = to_jsonable(value)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    actual = to_jsonable(value)
                self.assertEqual(actual, expected)
        self.assertEqual(custom_key.string_calls, 2)

        nan_set = {float("nan"), 1.0}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            nan_fallback = to_jsonable(nan_set)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.to_jsonable_containers(nan_set))
            nan_actual = to_jsonable(nan_set)
        self.assertEqual(len(nan_actual), len(nan_fallback))
        self.assertTrue(
            all(actual is fallback for actual, fallback in zip(nan_actual, nan_fallback))
        )

        nested_list: list[object] = []
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            converted_tuple = to_jsonable((nested_list,))
        self.assertIs(converted_tuple[0], nested_list)

        cyclic: list[object] = []
        cyclic.append(cyclic)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.to_jsonable_containers(cyclic))
            with self.assertRaises(RecursionError):
                to_jsonable(cyclic)

    def test_native_jsonable_sorted_sets_match_python_on_generated_inputs(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust set converter is not built")
        generator = random.Random(20260311)
        pools = [
            [
                False,
                True,
                -11,
                0,
                3,
                2**80,
                -(2**100),
                -2.75,
                0.5,
                19.25,
                float("inf"),
                float("-inf"),
            ],
            ["", "ascii", "é", "Å", "a", "\x00", "𝄞", "\ud800"],
            [b"", b"\x00", b"a", b"\xff", b"\x00a", b"a\x00"],
        ]
        for case in range(60):
            value = set(generator.choices(generator.choice(pools), k=generator.randrange(10)))
            with self.subTest(case=case, size=len(value)):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = to_jsonable(value)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    self.assertIsNotNone(
                        native_acceleration.to_jsonable_containers(value)
                    )
                    actual = to_jsonable(value)
                self.assertEqual(actual, expected)

    def test_inspect_xscr_text(self) -> None:
        report = inspect_xscr_text(SAMPLE_XSCR, source_name="sample.xscr")
        self.assertEqual(report["object_name"], "UnitTestScript")
        self.assertEqual(report["command_count"], 2)
        self.assertEqual(report["family_counts"]["LiHa/FCA"], 1)
        self.assertEqual(report["variables"][0]["name"], "Vol")
        self.assertIn("Water Free Single", report["dependencies"]["liquid_classes"])
        self.assertIn("SourcePlate", report["dependencies"]["labware_names"])

    def test_parse_xml_text_rejects_malformed_xml(self) -> None:
        with self.assertRaises(ET.ParseError):
            parse_xml_text("<root>")

    def test_parse_xml_text_rejects_entity_xml(self) -> None:
        xml = '<!DOCTYPE root [<!ENTITY x "boom">]><root>&x;</root>'
        with self.assertRaises(ET.ParseError):
            parse_xml_text(xml)

    def test_parse_xml_text_rejects_excessive_depth(self) -> None:
        xml = "<root><a><b><c /></b></a></root>"
        with self.assertRaises(ET.ParseError):
            parse_xml_text(xml, max_depth=3)

    def test_parse_xml_text_rejects_oversized_document(self) -> None:
        xml = "<root>" + ("x" * 64) + "</root>"
        with self.assertRaises(ET.ParseError):
            parse_xml_text(xml, max_bytes=32)

    def test_inspect_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "sample.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", SAMPLE_XSCR)
                zf.writestr("DataStore/nodedescription.xml", "<root />")
            report = inspect_archive(archive)
        self.assertEqual(report["script_count_total"], 1)
        self.assertEqual(report["script_names"], ["UnitTestScript"])

    def test_inspect_gwl_lines_uses_shared_permissive_parser(self) -> None:
        report = inspect_gwl_lines(
            [
                "A;Source;;96 Well Flat;1;;20;Water Free Single;;;",
                "W5;",
                "X;unsupported;field",
            ],
            source_name="sample.gwl",
        )

        self.assertEqual(report["record_counts"]["A"], 1)
        self.assertEqual(report["record_counts"]["W5"], 1)
        self.assertEqual(report["record_counts"]["X"], 1)
        self.assertEqual(report["pipette_examples"][0]["rack_label"], "Source")
        self.assertEqual(report["line_count"], 3)

    def test_inspect_gwl_file_uses_native_utf8_sig_read_with_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "read_utf8_sig_file"
        ):
            self.skipTest("optional Rust UTF-8-sig file reader is not built")
        with tempfile.TemporaryDirectory() as raw_directory:
            path = Path(raw_directory) / "fixture.gwl"
            payload = (
                "\ufeffA;Source;;Plate;1;;20;Water;;;\r\n"
                "W;\rC;row\u2028separator"
            ).encode("utf-8")
            path.write_bytes(payload)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                expected = inspect_gwl(path, source_name="fixture.gwl")
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(
                    Path,
                    "read_text",
                    side_effect=AssertionError("native file reader should be used"),
                ):
                    actual = inspect_gwl(path, source_name="fixture.gwl")
            self.assertEqual(actual, expected)
            self.assertEqual(actual["line_count"], 4)

    def test_inspect_gwl_lines_uses_native_summary_with_ordered_examples(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "worklist_inspect_gwl_lines"
        ):
            self.skipTest("optional Rust GWL summary is not built")
        lines = [
            "",
            "W5;",
            "X;unsupported;field",
            "W;",
            "W1;",
            "B;",
            "C;comment;with;semicolons",
            "F;opaque;parameters",
            "S;opaque;parameters",
        ]
        lines.extend(
            f"A;Rack{index};;Plate;{index + 1};;20;Water;;;"
            for index in range(13)
        )
        lines.append("D;Destination;;Plate;1;;20;Water;;;")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = inspect_gwl_lines(lines, source_name="sample.gwl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                reader_gwl,
                "_native_worklist_inspect_gwl_lines",
                wraps=reader_gwl._native_worklist_inspect_gwl_lines,
            ) as native_parser:
                actual = inspect_gwl_lines(lines, source_name="sample.gwl")
                native_parser.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual["record_counts"],
            {"W5": 1, "X": 1, "W": 1, "W1": 1, "B": 1, "C": 1, "F": 1, "S": 1, "A": 13, "D": 1},
        )
        self.assertEqual(
            list(actual["record_counts"]),
            ["W5", "X", "W", "W1", "B", "C", "F", "S", "A", "D"],
        )
        self.assertEqual(actual["line_count"], 23)
        self.assertEqual(actual["transfer_pairs_estimate"], 1)
        self.assertEqual(actual["pipette_examples"][0]["line"], "10")
        self.assertEqual(len(actual["pipette_examples"]), 12)
        self.assertEqual(actual["pipette_examples"][-1]["rack_label"], "Rack11")

    def test_inspect_gwl_lines_falls_back_for_unsupported_unicode(self) -> None:
        lines = ["\x1c", "C;µ", "A;Räck;;Plate;1;;20;Water;;;"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = inspect_gwl_lines(lines, source_name="unicode.gwl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.worklist_inspect_gwl_lines(lines))
            with patch.object(
                reader_gwl,
                "_native_worklist_inspect_gwl_lines",
                wraps=reader_gwl._native_worklist_inspect_gwl_lines,
            ) as native_summary:
                actual = inspect_gwl_lines(lines, source_name="unicode.gwl")
                native_summary.assert_called_once()
        self.assertEqual(actual, expected)

    def test_inspect_archive_collects_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "errors.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                # Malformed XML to cause inspect_xscr_text to fail
                zf.writestr("DataStore/UserSpecific/bad.xscr", "<malformed")
                # Invalid UTF-8 to cause .decode("utf-8-sig") to fail
                zf.writestr("Worklists/bad.gwl", b"\xff\xfe")

            report = inspect_archive(archive)

        self.assertEqual(len(report["errors"]), 2)

        errors = {e["entry"]: e["error"] for e in report["errors"]}

        self.assertIn("DataStore/UserSpecific/bad.xscr", errors)
        self.assertIn("Worklists/bad.gwl", errors)

        self.assertTrue("ParseError" in errors["DataStore/UserSpecific/bad.xscr"] or "unclosed token" in errors["DataStore/UserSpecific/bad.xscr"])
        self.assertTrue("codec can't decode" in errors["Worklists/bad.gwl"])

    def test_inspect_archive_rejects_entry_count_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "too_many_entries.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", SAMPLE_XSCR)
                zf.writestr("DataStore/nodedescription.xml", "<root />")

            with self.assertRaises(zipfile.BadZipFile):
                inspect_archive(archive, max_entry_count=1)

    def test_inspect_archive_rejects_total_uncompressed_size_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "too_large.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", SAMPLE_XSCR)

            with self.assertRaises(zipfile.BadZipFile):
                inspect_archive(archive, max_total_uncompressed_bytes=32)

    def test_inspect_archive_indexes_custom_parts_pins_and_subroutines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "custom.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "DataStore/UserSpecific/custom.xscr",
                    SAMPLE_XSCR.replace(
                        "</Objects>",
                        """
              <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
                <SubRoutine>"Subroutines\\GetFingers"</SubRoutine>
                <PinNumber>GIO1_Pin20</PinNumber>
                <Location>WorktablePin_MiddleFront</Location>
                <CustomDetailImageFilePath>C:\\ProgramData\\Tecan\\VisionX\\TouchToolsData\\Images\\sourcecapholder.jpg</CustomDetailImageFilePath>
                <Barcode>FakeBarcode</Barcode>
              </Object>
            </Objects>
""",
                    ),
                )
                zf.writestr(
                    "DataStore/SystemSpecific/Worktable/Connectors/custom.xcon",
                    """<?xml version="1.0"?>
<VxData>
  <Payload>
    <ObjectName>Generated connector for Worktable_Segment_WorktablePin_MiddleFront and custom cap holder</ObjectName>
    <PayloadData>
      <WorktableConnector>
        <ConnectorTemplate>
          <ComponentGuid>component-guid</ComponentGuid>
          <SiteGuid>site-guid</SiteGuid>
          <Description>Worktable_Segment_WorktablePin_MiddleFront connector</Description>
        </ConnectorTemplate>
      </WorktableConnector>
    </PayloadData>
  </Payload>
</VxData>
""",
                )
                zf.writestr("fs/1/sourcecapholder.jpg", b"not really a jpeg")

            report = inspect_archive(archive)
            deps = report["scripts"][0]["dependencies"]
            db_path = Path(tmp) / "build" / "custom.sqlite"
            build_project_index([archive], db_path, force=True)
            pin_hits = search_project_index(db_path, "GIO1_Pin20", kind="hardware_pin")
            asset_hits = search_project_index(db_path, "sourcecapholder", kind="custom_asset")
            subroutine_hits = search_project_index(db_path, "GetFingers", kind="subroutine")

        self.assertIn('"Subroutines\\GetFingers"', deps["subroutine_refs"])
        self.assertIn("GIO1_Pin20", deps["pin_refs"])
        self.assertIn("WorktablePin_MiddleFront", deps["worktable_pin_locations"])
        self.assertIn("sourcecapholder.jpg", deps["custom_asset_refs"])
        self.assertTrue(any(obj["kind"] == "connector" for obj in report["objects"]))
        self.assertTrue(any(obj["kind"] == "asset" for obj in report["objects"]))
        self.assertTrue(pin_hits["results"])
        self.assertTrue(asset_hits["results"])
        self.assertTrue(subroutine_hits["results"])

    def test_project_index_searches_multiple_zeia_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _write_sample_archive(root / "first.zeia", SAMPLE_XSCR)
            second_script = (
                SAMPLE_XSCR
                .replace("UnitTestScript", "SecondScript")
                .replace("SourcePlate", "DestinationPlate")
                .replace("Water Free Single", "DMSO Single")
                .replace("Base Worktable", "Deck B Worktable")
            )
            _write_sample_archive(root / "nested" / "second.zeia", second_script)

            db_path = root / "build" / "project.sqlite"
            build_report = build_project_index([root], db_path, force=True)
            summary = summarize_project_index(db_path)
            liquid_hits = search_project_index(db_path, "Water Free Single", kind="liquid_class")
            sequence_hits = search_project_index(
                db_path,
                "CommentStatement > LihaAspirateScriptCommand",
                kind="command_sequence",
            )
            worktable_hits = search_project_index(db_path, "Base Worktable", kind="worktable")
            worklist_hits = search_project_index(db_path, "sample.gwl", kind="worklist")

        self.assertEqual(build_report["zeia_file_count"], 2)
        self.assertEqual(summary["script_count"], 2)
        self.assertGreaterEqual(summary["entity_counts"]["labware"], 2)
        self.assertGreaterEqual(summary["entity_counts"]["carrier"], 1)
        self.assertTrue(any(hit["name"] == "Water Free Single" for hit in liquid_hits["results"]))
        self.assertTrue(sequence_hits["results"])
        self.assertTrue(any(hit["name"] == "Base Worktable" for hit in worktable_hits["results"]))
        self.assertTrue(any(hit["name"] == "sample.gwl" for hit in worklist_hits["results"]))
        self.assertEqual(first.name, "first.zeia")

    def test_summarize_project_index(self) -> None:
        conn = sqlite3.connect(":memory:")
        _initialize_database(conn)

        # Insert test data
        conn.execute("INSERT INTO zeia_files(id, path, file_name, sha256, indexed_at) VALUES (1, '/path/to/archive.zeia', 'archive.zeia', 'hash', '2023-01-01')")
        conn.execute("INSERT INTO scripts(id, zeia_file_id, entry_path, object_name) VALUES (1, 1, 'script1.xscr', 'Script 1')")
        conn.execute("INSERT INTO scripts(id, zeia_file_id, entry_path, object_name) VALUES (2, 1, 'script2.xscr', 'Script 2')")

        conn.execute("INSERT INTO commands(id, zeia_file_id, script_id, command_index, command_type, family) VALUES (1, 1, 1, 0, 'LiquidHandling', 'pipette')")
        conn.execute("INSERT INTO commands(id, zeia_file_id, script_id, command_index, command_type, family) VALUES (2, 1, 1, 1, 'Comment', 'misc')")
        conn.execute("INSERT INTO commands(id, zeia_file_id, script_id, command_index, command_type, family) VALUES (3, 1, 2, 0, 'LiquidHandling', 'pipette')")

        conn.execute("INSERT INTO entities(id, zeia_file_id, script_id, kind, name) VALUES (1, 1, 1, 'labware', 'Plate1')")
        conn.execute("INSERT INTO entities(id, zeia_file_id, script_id, kind, name) VALUES (2, 1, 1, 'labware', 'Plate2')")
        conn.execute("INSERT INTO entities(id, zeia_file_id, script_id, kind, name) VALUES (3, 1, 2, 'liquid_class', 'Water')")

        conn.execute("INSERT INTO catalog_objects(id, zeia_file_id, entry_path, kind) VALUES (1, 1, 'plate.xml', 'plate')")
        conn.execute("INSERT INTO catalog_objects(id, zeia_file_id, entry_path, kind) VALUES (2, 1, 'tip.xml', 'tip')")

        summary = summarize_project_index(conn)

        self.assertEqual(summary["kind"], "project_index_summary")
        self.assertEqual(summary["zeia_file_count"], 1)
        self.assertEqual(summary["script_count"], 2)
        self.assertEqual(summary["command_count"], 3)
        self.assertEqual(summary["catalog_object_count"], 2)
        self.assertEqual(summary["entity_counts"]["labware"], 2)
        self.assertEqual(summary["entity_counts"]["liquid_class"], 1)
        self.assertEqual(summary["command_family_counts"]["pipette"], 2)
        self.assertEqual(summary["command_family_counts"]["misc"], 1)
        self.assertEqual(len(summary["files"]), 1)
        self.assertEqual(summary["files"][0]["file_name"], "archive.zeia")

    def test_script_pattern_library_mines_reusable_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_sample_archive(root / "patterns.zeia", PATTERN_XSCR)
            db_path = root / "build" / "project.sqlite"
            build_project_index([root], db_path, force=True)

            fallback_db = root / "build" / "project-python.sqlite"
            shutil.copy2(db_path, fallback_db)
            mine_report = mine_script_patterns(db_path)
            with patch.object(native_acceleration, "_native", None):
                fallback_report = mine_script_patterns(fallback_db)
            summary = summarize_script_patterns(db_path)

            def rows(database: Path, query: str) -> list[tuple]:
                connection = sqlite3.connect(database)
                try:
                    return connection.execute(query).fetchall()
                finally:
                    connection.close()

            native_patterns = rows(
                db_path,
                "SELECT pattern_type, name, source_script, source_path, "
                "start_command_index, end_command_index, step_count, "
                "command_signature, confidence, specifications_json, "
                "safety_notes_json, metadata_json FROM script_patterns ORDER BY id",
            )
            fallback_patterns = rows(
                fallback_db,
                "SELECT pattern_type, name, source_script, source_path, "
                "start_command_index, end_command_index, step_count, "
                "command_signature, confidence, specifications_json, "
                "safety_notes_json, metadata_json FROM script_patterns ORDER BY id",
            )
            native_steps = rows(
                db_path,
                "SELECT pattern_id, step_number, command_index, command_name, "
                "command_family, line, summary, fields_json "
                "FROM script_pattern_steps ORDER BY id",
            )
            fallback_steps = rows(
                fallback_db,
                "SELECT pattern_id, step_number, command_index, command_name, "
                "command_family, line, summary, fields_json "
                "FROM script_pattern_steps ORDER BY id",
            )
            tip_hits = search_script_patterns(db_path, "MCA384TipBox", pattern_type="pick_up_tips")
            aspirate_hits = search_script_patterns(db_path, "Water Free Single", pattern_type="aspirate")
            drop_hits = search_script_patterns(db_path, "Waste", pattern_type="drop_tips")
            gripper_hits = search_script_patterns(db_path, "AdapterA200", pattern_type="gripper")

        self.assertEqual(mine_report["mined_pattern_count"], fallback_report["mined_pattern_count"])
        self.assertEqual(mine_report["pattern_type_counts"], fallback_report["pattern_type_counts"])
        self.assertEqual(native_patterns, fallback_patterns)
        self.assertEqual(native_steps, fallback_steps)
        self.assertGreaterEqual(mine_report["pattern_type_counts"]["pick_up_tips"], 1)
        self.assertGreaterEqual(mine_report["pattern_type_counts"]["gripper"], 1)
        self.assertGreaterEqual(summary["pattern_type_counts"]["aspirate"], 1)
        self.assertGreaterEqual(summary["pattern_type_counts"]["dispense"], 1)
        self.assertTrue(tip_hits["results"])
        self.assertEqual(tip_hits["results"][0]["source_script"], "MCA384PatternScript")
        self.assertTrue(any(step["step_number"] == 1 for step in tip_hits["results"][0]["steps"]))
        self.assertTrue(any("Pick up tips" in step["summary"] for step in tip_hits["results"][0]["steps"]))
        self.assertTrue(aspirate_hits["results"])
        self.assertIn("liquid_classes", aspirate_hits["results"][0]["specifications"])
        self.assertTrue(drop_hits["results"])
        self.assertTrue(gripper_hits["results"])
        self.assertIn("Physical RGA/CGA motion pattern", gripper_hits["results"][0]["safety_notes"][-1])

    def test_build_project_index_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = tmp_path / "test.zeia"
            with zipfile.ZipFile(zeia_path, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

            db_path = tmp_path / "new_dir" / "nested" / "test.db"
            self.assertFalse(db_path.parent.exists())

            with patch("tecan_reader.project_index.ingest_zeia") as mock_ingest:
                mock_ingest.return_value = {"scripts": []}
                build_project_index([zeia_path], db_path)

            self.assertTrue(db_path.parent.exists())
            self.assertTrue(db_path.exists())

    def test_build_project_index_force_deletes_existing_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = tmp_path / "test.zeia"
            with zipfile.ZipFile(zeia_path, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

            db_path = tmp_path / "test.db"
            db_path.write_text("dummy data")

            with patch("tecan_reader.project_index.ingest_zeia") as mock_ingest:
                mock_ingest.return_value = {"scripts": []}
                build_project_index([zeia_path], db_path, force=True)

            # The dummy data should have been overwritten by a new sqlite database
            with sqlite3.connect(db_path) as conn:
                res = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
                self.assertIsNotNone(res)

            # Windows CI issue with file locks: connection context manager closes it,
            # but sometimes it takes a bit. Let's explicitly close any dangling refs just in case
            # and unlink it so TemporaryDirectory cleanup does not fail with PermissionError.
            try:
                conn.close()
                import time
                time.sleep(0.1)
                db_path.unlink()
            except OSError:
                pass

    def test_build_project_index_closes_connection_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = tmp_path / "test.zeia"
            with zipfile.ZipFile(zeia_path, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

            db_path = tmp_path / "test.db"

            with patch("tecan_reader.project_index.ingest_zeia") as mock_ingest:
                mock_ingest.side_effect = ValueError("Simulated error")
                with patch("tecan_reader.project_index._connect") as mock_connect:
                    mock_conn = MagicMock()
                    mock_connect.return_value = mock_conn
                    with self.assertRaises(ValueError):
                        build_project_index([zeia_path], db_path)
                    mock_conn.close.assert_called_once()

    def test_build_project_index_passes_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = tmp_path / "test.zeia"
            with zipfile.ZipFile(zeia_path, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

            db_path = tmp_path / "test.db"

            with patch("tecan_reader.project_index.ingest_zeia") as mock_ingest:
                mock_ingest.return_value = {"scripts": []}
                build_project_index([zeia_path], db_path, script_limit=42, object_limit=99)
                mock_ingest.assert_called_once_with(zeia_path.resolve(), script_limit=42, object_limit=99)

    def test_build_project_index_persists_canonical_entity_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = _write_sample_archive(tmp_path / "canonical.zeia", SAMPLE_XSCR)
            db_path = tmp_path / "canonical.db"

            summary = build_project_index([zeia_path], db_path)

            conn = sqlite3.connect(db_path)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT metadata_json FROM entities WHERE kind = 'script'"
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(summary["script_count"], 1)
        self.assertIsNotNone(row)
        metadata = json.loads(row["metadata_json"])
        self.assertEqual(
            metadata["provenance"]["entry_path"],
            "DataStore/UserSpecific/sample.xscr",
        )
        self.assertIn("unknown_fields", metadata)


def _write_sample_archive(path: Path, script: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("DataStore/UserSpecific/sample.xscr", script)
        zf.writestr("DataStore/Worktables/deck.xwsp", WORKTABLE_XML)
        zf.writestr("DataStore/Carriers/carrier.xsit", CARRIER_XML)
        zf.writestr("DataStore/LiquidClasses/water.xlqc", LIQUID_CLASS_XML)
        zf.writestr(
            "Worklists/sample.gwl",
            "\n".join(
                [
                    "A;SourcePlate;;Plate Carrier 1;1;;10;Water Free Single",
                    "D;DestinationPlate;;Plate Carrier 1;1;;10;Water Free Single",
                ]
            ),
        )
    return path


if __name__ == "__main__":
    unittest.main()
