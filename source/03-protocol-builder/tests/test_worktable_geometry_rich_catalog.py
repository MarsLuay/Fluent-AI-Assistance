"""Tests for pipettable / grip / site-template / compatible mining."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tecan_common import native_acceleration

from fluent_pipeline import worktable_geometry
from fluent_pipeline.labware_catalog_export import build_labware_catalog_from_geometry
from fluent_pipeline.worktable_geometry import parse_component


RICH_XCMP = """<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Payload>
    <ObjectName>Rich Plate</ObjectName>
    <Reference>
      <TypeId>WorktableMesh</TypeId>
      <Guid>11111111-1111-1111-1111-111111111111</Guid>
      <ObjectName>PlateMesh</ObjectName>
    </Reference>
    <Reference>
      <TypeId>WorktableComponent</TypeId>
      <Guid>22222222-2222-2222-2222-222222222222</Guid>
      <ObjectName>Lid Companion</ObjectName>
    </Reference>
    <Reference>
      <TypeId>WorktableSite</TypeId>
      <Guid>33333333-3333-3333-3333-333333333333</Guid>
      <ObjectName>33333333-3333-3333-3333-333333333333</ObjectName>
    </Reference>
    <PayloadData>
      <CarrierOrLabwareTemplate>
        <GUID>aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa</GUID>
        <Dimension><X>127.8</X><Y>85.5</Y><Z>14.4</Z></Dimension>
        <FunctionalGroup>Labware.Microplate</FunctionalGroup>
        <FootPrint>Microplate</FootPrint>
        <Arrangements>
          <ArrangementTemplate>
            <SitesInX>1</SitesInX>
            <SitesInY>1</SitesInY>
            <SitesInZ>1</SitesInZ>
            <SiteSpacingInX>0</SiteSpacingInX>
            <SiteSpacingInY>0</SiteSpacingInY>
            <SiteSpacingInZ>0</SiteSpacingInZ>
            <PositionInParent><X>0</X><Y>0</Y><Z>0</Z></PositionInParent>
            <SiteTemplateIdentifiers>
              <KeyValueOfintguid>
                <Key>0</Key>
                <Value>33333333-3333-3333-3333-333333333333</Value>
              </KeyValueOfintguid>
            </SiteTemplateIdentifiers>
            <AllowedGripModes>
              <KeyValueOfintArrayOfstring>
                <Key>0</Key>
                <Value>
                  <KeyValueOfstringstring>
                    <Key>Narrow</Key>
                    <Value>true</Value>
                  </KeyValueOfstringstring>
                  <KeyValueOfstringstring>
                    <Key>Wide</Key>
                    <Value>true</Value>
                  </KeyValueOfstringstring>
                </Value>
              </KeyValueOfintArrayOfstring>
            </AllowedGripModes>
          </ArrangementTemplate>
        </Arrangements>
        <Pipettable>
          <XNumberOfWells>12</XNumberOfWells>
          <YNumberOfWells>8</YNumberOfWells>
          <XSpacing>9</XSpacing>
          <YSpacing>-9</YSpacing>
          <PositionOfFirstWell><X>14.4</X><Y>11.2</Y><Z>0</Z></PositionOfFirstWell>
          <Cavity>
            <CavityShape xsi:type="TruncatedCone">
              <Height>10.9</Height>
              <DiameterBottom>6.58</DiameterBottom>
              <DiameterTop>6.96</DiameterTop>
            </CavityShape>
          </Cavity>
          <ZHeights>
            <KeyValueOfstringdouble>
              <Key>ZTravel</Key>
              <Value>2.0</Value>
            </KeyValueOfstringdouble>
          </ZHeights>
        </Pipettable>
        <CustomAttributes>
          <KeyValueOfstringCustomAttribute>
            <Key>Force</Key>
            <Value>
              <StringContent>&lt;int&gt;20&lt;/int&gt;</StringContent>
            </Value>
          </KeyValueOfstringCustomAttribute>
        </CustomAttributes>
        <CompatibleComponents>
          <KeyValueOfstringguid>
            <Key>Nested Insert</Key>
            <Value>44444444-4444-4444-4444-444444444444</Value>
          </KeyValueOfstringguid>
        </CompatibleComponents>
      </CarrierOrLabwareTemplate>
    </PayloadData>
  </Payload>
</VxData>
"""


class WorktableGeometryRichCatalogTests(unittest.TestCase):
    def test_int_text_uses_native_i64_and_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "parse_i64_text"
        ):
            self.skipTest("optional Rust integer parser is not built")
        cases = [
            (" +42 ", 42),
            ("-7", -7),
            ("001", 1),
            ("1_000", 1000),
            ("١٢", 12),
            (str(1 << 100), 1 << 100),
            ("not-an-int", None),
        ]

        def element(text: str):
            value = worktable_geometry.ET.Element("Value")
            value.text = text
            return value

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._int_text(element(text)) for text, _ in cases]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "parse_i64_text",
                wraps=native_acceleration.parse_i64_text,
            ) as native_call:
                actual = [worktable_geometry._int_text(element(text)) for text, _ in cases]
                self.assertEqual(native_call.call_count, len(cases))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [expected_value for _, expected_value in cases])

    def test_leaf_text_traversal_uses_native_with_tree_and_type_fallbacks(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_leaf_texts"
        ):
            self.skipTest("optional Rust XML leaf-text collector is not built")
        nested = worktable_geometry.ET.fromstring(
            "<Root>ignored<One> A </One><Branch>ignored<Two>B</Two><Three>\u00a0</Three></Branch><One>A</One></Root>"
        )
        comment_only = worktable_geometry.ET.Element("Root")
        comment_only.text = "\u00a0root\u00a0"
        comment_only.append(worktable_geometry.ET.Comment("ignored"))

        class CustomText(str):
            def strip(self):
                return "custom-text"

        custom_text = worktable_geometry.ET.Element("Leaf")
        custom_text.text = CustomText("ignored")

        class CustomTag(str):
            pass

        custom_tag_root = worktable_geometry.ET.Element("Root")
        custom_tag_child = worktable_geometry.ET.SubElement(custom_tag_root, "Leaf")
        custom_tag_child.tag = CustomTag("Leaf")
        custom_tag_child.text = " value "

        whitespace = [
            chr(codepoint)
            for codepoint in range(0x110000)
            if chr(codepoint).isspace()
        ]
        whitespace_elements = []
        for character in whitespace:
            leaf = worktable_geometry.ET.Element("Leaf")
            leaf.text = f"{character}value{character}"
            whitespace_elements.append(leaf)
        surrogate_text = worktable_geometry.ET.Element("Leaf")
        surrogate_text.text = "\ud800value\ud800"
        deep_root = worktable_geometry.ET.Element("Deep")
        deep_leaf = deep_root
        for _ in range(260):
            deep_leaf = worktable_geometry.ET.SubElement(deep_leaf, "Node")
        deep_leaf.text = "deep"
        elements = [nested, comment_only, custom_text, custom_tag_root]
        elements.extend(whitespace_elements)
        elements.extend([surrogate_text, deep_root])
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._leaf_texts(element) for element in elements]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_result = native_acceleration.xml_leaf_texts(
                nested, worktable_geometry.ET.Element
            )
            actual = [worktable_geometry._leaf_texts(element) for element in elements]
        self.assertEqual(native_result, ["A", "B", "A"])
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual[:4], [["A", "B", "A"], ["root"], ["custom-text"], ["value"]]
        )
        self.assertEqual(actual[4 : 4 + len(whitespace)], [["value"]] * len(whitespace))
        self.assertEqual(actual[-2:], [["\ud800value\ud800"], ["deep"]])
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_leaf_texts(custom_text, worktable_geometry.ET.Element)
            )
            self.assertIsNone(
                native_acceleration.xml_leaf_texts(custom_tag_root, worktable_geometry.ET.Element)
            )
            self.assertIsNone(
                native_acceleration.xml_leaf_texts(surrogate_text, worktable_geometry.ET.Element)
            )
            self.assertIsNone(
                native_acceleration.xml_leaf_texts(deep_root, worktable_geometry.ET.Element)
            )

    def test_site_index_sort_key_uses_native_integer_parse_with_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "parse_i64_text"
        ):
            self.skipTest("optional Rust integer parser is not built")
        values = [
            "0",
            "-1",
            "+2",
            " 3 ",
            "1_000",
            "١٢",
            str(1 << 100),
            "not-an-int",
            None,
            7,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._site_index_sort_key(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "parse_i64_text",
                wraps=native_acceleration.parse_i64_text,
            ) as native_call:
                actual = [worktable_geometry._site_index_sort_key(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], (0, "000000000000"))
        self.assertEqual(actual[-2:], [(1, "None"), (0, "000000000007")])

    def test_float_text_uses_native_decimal_subset_and_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "parse_f64_decimal_text"
        ):
            self.skipTest("optional Rust decimal parser is not built")
        cases = [
            (" +1.25 ", 1.25),
            (".5", 0.5),
            ("1.", 1.0),
            ("-2.5e+2", -250.0),
            ("-0.0", 0.0),
            ("1_000.5", 1000.5),
            ("١.٢", 1.2),
            ("inf", math.inf),
            ("not-a-number", None),
        ]

        def element(text: str):
            value = worktable_geometry.ET.Element("Value")
            value.text = text
            return value

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._float_text(element(text)) for text, _ in cases]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "parse_f64_decimal_text",
                wraps=native_acceleration.parse_f64_decimal_text,
            ) as native_call:
                actual = [worktable_geometry._float_text(element(text)) for text, _ in cases]
                self.assertEqual(native_call.call_count, len(cases))
            self.assertEqual(
                native_acceleration.parse_f64_decimal_text("+1.25"), 1.25
            )
            self.assertIsNone(native_acceleration.parse_f64_decimal_text("1_000.5"))
            self.assertIsNone(native_acceleration.parse_f64_decimal_text("١.٢"))
            self.assertIsNone(native_acceleration.parse_f64_decimal_text("inf"))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [expected_value for _, expected_value in cases])
        self.assertEqual(math.copysign(1.0, actual[4]), 1.0)

        generated = [
            f"{sign}{mantissa}{exponent}"
            for sign in ("", "+", "-")
            for mantissa in ("0", ".5", "1.", "1.234567890123456789", "9999999999999999.9")
            for exponent in ("", "e+0", "E-12", "e300", "e-320")
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            generated_expected = [
                worktable_geometry._float_text(element(text)) for text in generated
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            generated_actual = [
                worktable_geometry._float_text(element(text)) for text in generated
            ]
        self.assertEqual(generated_actual, generated_expected)

    def test_site_kind_ascii_compact_matching_uses_native_with_python_policy(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_case_insensitive_substring_match_groups"
        ):
            self.skipTest("optional Rust substring matcher is not built")
        cases = [
            (("pin carrier", "", ""), "pin"),
            (("Cap nest", "", ""), "cap_nest"),
            (("cap-holder", "", ""), "cap_nest"),
            (("Nest platform", "", ""), "nest"),
            (("nest-base", "", ""), "nest"),
            (("Regrip nest", "", ""), "nest"),
            (("7mm nest", "", ""), "nest"),
            (("東京 nest", "", ""), "nest"),
            (("nested", "", ""), ""),
            (("pin plus cap nest", "", ""), "pin"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                worktable_geometry.classify_site_kind(*parts) for parts, _ in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_case_insensitive_substring_match_groups",
                wraps=native_acceleration.ascii_case_insensitive_substring_match_groups,
            ) as native_call:
                actual = [
                    worktable_geometry.classify_site_kind(*parts) for parts, _ in cases
                ]
                self.assertEqual(
                    native_call.call_count,
                    sum(expected_value != "pin" for _, expected_value in cases),
                )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [expected_value for _, expected_value in cases])

    def test_guid_text_validation_uses_native_ascii_match_with_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "is_guid_text"
        ):
            self.skipTest("optional Rust GUID validator is not built")
        guid = "11111111-1111-1111-1111-111111111111"
        values = [
            guid,
            guid.upper(),
            "00000000-0000-0000-0000-000000000000",
            "11111111_1111-1111-1111-111111111111",
            f"{guid}\n",
            "東京-11111111-1111-1111-1111-1111111111",
            "\ud800",
            None,
            0,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._looks_like_guid(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "is_guid_text",
                wraps=native_acceleration.is_guid_text,
            ) as native_call:
                actual = [worktable_geometry._looks_like_guid(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
            self.assertTrue(native_acceleration.is_guid_text(guid))
            self.assertFalse(native_acceleration.is_guid_text(""))
            self.assertIsNone(native_acceleration.is_guid_text("東京"))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [True, True, True, False, False, False, False, False, False])

        class CustomGuid(str):
            pass

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.is_guid_text(CustomGuid(guid)))
            self.assertTrue(worktable_geometry._looks_like_guid(CustomGuid(guid)))

    def test_non_guid_uses_native_whitespace_normalizer_and_guid_match(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_clean_text"
        ):
            self.skipTest("optional Rust XML text trimmer is not built")

        class CountedText:
            def __init__(self):
                self.calls = 0

            def __bool__(self):
                return True

            def __str__(self):
                self.calls += 1
                return " Custom "

        counted = CountedText()
        values = [
            " 11111111-1111-1111-1111-111111111111 ",
            f" {worktable_geometry.ZERO_GUID} ",
            "\u00a0 place \u00a0",
            "\x1csite\x1c",
            " site ",
            "not-a-guid",
            "",
            None,
            0,
            "\ud800 raw \ud800",
            counted,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._non_guid(value) for value in values]
        self.assertEqual(counted.calls, 1)
        counted.calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_clean_text",
                wraps=native_acceleration.xml_clean_text,
            ) as native_call:
                actual = [worktable_geometry._non_guid(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(counted.calls, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            ["", "", "place", "site", "site", "not-a-guid", "", "", "", "\ud800 raw \ud800", "Custom"],
        )

    def test_orientation_text_uses_native_ascii_parser_and_python_regex_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "parse_orientation_ascii"
        ):
            self.skipTest("optional Rust orientation parser is not built")

        class OrientationText(str):
            pass

        overflow = f"Phi={'9' * 400},Theta=0,Psi=0"
        values = [
            "Phi=1, Theta=2, Psi=3",
            "prefix PHI \t=\n-1.25,\x1c tHeTa=0, PSI=3.5 trailing",
            "Phi=bad,Theta=0,Psi=0; phi=4,theta=5,psi=-6.25",
            "Phi=1.2.3,Theta=2,Psi=3",
            "Phi=1,Theta=2,Psi=3.4.5",
            "Phi=١,Theta=٢,Psi=٣",
            "Phi=1\u00a0,\u00a0Theta=2,\u00a0Psi=3",
            overflow,
            "no orientation",
            None,
            OrientationText("Phi=7,Theta=8,Psi=9"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                worktable_geometry._orientation_from_text(value) for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "parse_orientation_ascii",
                wraps=native_acceleration.parse_orientation_ascii,
            ) as native_call:
                actual = [
                    worktable_geometry._orientation_from_text(value) for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
            self.assertEqual(
                native_acceleration.parse_orientation_ascii(
                    "prefix Phi=-1.25,Theta=2,Psi=3.5 suffix"
                ),
                (-1.25, 2.0, 3.5),
            )
            self.assertIsNone(
                native_acceleration.parse_orientation_ascii("Phi=١,Theta=2,Psi=3")
            )
            self.assertIsNone(native_acceleration.parse_orientation_ascii(overflow))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], {"phi": 1.0, "theta": 2.0, "psi": 3.0})
        self.assertEqual(actual[1], {"phi": -1.25, "theta": 0.0, "psi": 3.5})
        self.assertEqual(actual[2], {"phi": 4.0, "theta": 5.0, "psi": -6.25})
        self.assertEqual(actual[3], {})
        self.assertEqual(actual[4], {"phi": 1.0, "theta": 2.0, "psi": 3.4})
        self.assertEqual(actual[5], {"phi": 1.0, "theta": 2.0, "psi": 3.0})
        self.assertTrue(math.isinf(actual[7]["phi"]))
        self.assertEqual(actual[-1], {"phi": 7.0, "theta": 8.0, "psi": 9.0})

    def test_label_base_uses_native_ascii_regex_parity_and_unicode_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "label_base_ascii_text"
        ):
            self.skipTest("optional Rust label suffix helper is not built")

        class CountedLabel:
            def __init__(self):
                self.calls = 0

            def __bool__(self):
                return True

            def __str__(self):
                self.calls += 1
                return "  Custom [suffix]"

        counted = CountedLabel()
        labels = [
            " Name [alias] ",
            "Name [alias]",
            "Name [alias]\n",
            "Name [alias]\n\n",
            "Name [alias]\r\n",
            "Name []",
            "Name [[alias]",
            "Name [first][last]",
            "Name [line\nbreak]",
            "[alias]",
            "東京 [alias]",
            "\ud800 [alias]",
            None,
            "",
            counted,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._label_base(label) for label in labels]
        self.assertEqual(counted.calls, 1)
        counted.calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "label_base_ascii_text",
                wraps=native_acceleration.label_base_ascii_text,
            ) as native_call:
                actual = [worktable_geometry._label_base(label) for label in labels]
                self.assertEqual(native_call.call_count, len(labels))
            self.assertEqual(native_acceleration.label_base_ascii_text("Name [alias]"), "Name")
            self.assertIsNone(native_acceleration.label_base_ascii_text("東京 [alias]"))
        self.assertEqual(counted.calls, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual[:10],
            [
                "Name [alias]",
                "Name",
                "Name",
                "Name [alias]",
                "Name [alias]",
                "Name []",
                "Name",
                "Name [first]",
                "Name",
                "",
            ],
        )
        self.assertEqual(actual[10:], ["東京", "\ud800", "", "", "Custom"])

    def test_clean_uses_native_for_safe_containers_and_falls_back_conservatively(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "clean_empty_containers"
        ):
            self.skipTest("optional Rust container cleaner is not built")

        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        nan_value = float("nan")
        source = {
            "empty-string": "",
            "none": None,
            "empty-list": [],
            "empty-dict": {},
            "zero": 0,
            "false": False,
            "empty-bytes": b"",
            "nan": nan_value,
            "nested": {"drop": [], "keep": {"value": 1}},
            "items": [None, "", [], {}, 0, False, {"empty": ""}, {"keep": 2}],
            "tuple-is-opaque": ("", []),
            "dict-subclass": DictSubclass({"empty": "", "kept": 3}),
            "list-subclass": ListSubclass(["", [], 4]),
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = worktable_geometry._clean(source)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "clean_empty_containers",
                wraps=native_acceleration.clean_empty_containers,
            ) as native_call:
                actual = worktable_geometry._clean(source)
                self.assertEqual(worktable_geometry._clean(None), None)
                self.assertEqual(worktable_geometry._clean({}), {})
                self.assertEqual(worktable_geometry._clean([]), [])
                self.assertGreater(native_call.call_count, 1)
        self.assertEqual(list(actual), list(expected))
        actual_without_nan = dict(actual)
        expected_without_nan = dict(expected)
        del actual_without_nan["nan"]
        del expected_without_nan["nan"]
        self.assertEqual(actual_without_nan, expected_without_nan)
        self.assertIs(actual["nan"], nan_value)
        self.assertEqual(actual["items"], [0, False, {"keep": 2}])
        self.assertEqual(actual["nested"], {"keep": {"value": 1}})
        self.assertEqual(actual["tuple-is-opaque"], ("", []))
        self.assertEqual(actual["dict-subclass"], {"kept": 3})
        self.assertEqual(actual["list-subclass"], [4])

        cyclic = []
        cyclic.append(cyclic)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with self.assertRaises(RecursionError):
                worktable_geometry._clean(cyclic)

    def test_matrix_double_rows_use_native_direct_selection_with_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_direct_elements_by_name"
        ):
            self.skipTest("optional Rust direct XML child selector is not built")
        root = worktable_geometry.ET.fromstring(
            "<Root><Mat>"
            "<ArrayOfdouble><double>1</double><double>2.5</double></ArrayOfdouble>"
            "<ArrayOfdouble><double>3</double><double>-4</double><!--skip--></ArrayOfdouble>"
            "<ArrayOfdouble><Group><double>99</double></Group></ArrayOfdouble>"
            "<ArrayOfdouble><double>invalid</double></ArrayOfdouble>"
            "</Mat></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = worktable_geometry._matrix(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_direct_elements_by_name",
                wraps=native_acceleration.xml_direct_elements_by_name,
            ) as native_call:
                actual = worktable_geometry._matrix(root)
                self.assertEqual(native_call.call_count, 5)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [[1.0, 2.5], [3.0, -4.0]])

    def test_position_label_uses_native_for_bounded_integer_paths(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "format_site_path"
        ):
            self.skipTest("optional Rust site-path formatter is not built")

        class CustomIndex(int):
            def __add__(self, other):
                return 41

        class CustomPath(tuple):
            pass

        paths = [
            (),
            (0,),
            (0, 2, -1),
            (2**63 - 1,),
            (2**63,),
            (True,),
            (CustomIndex(3),),
            CustomPath((1, 2)),
            [0, 2],
            tuple(range(257)),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._position_label({}, path) for path in paths]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "format_site_path",
                wraps=native_acceleration.format_site_path,
            ) as native_call:
                actual = [worktable_geometry._position_label({}, path) for path in paths]
                self.assertEqual(native_call.call_count, len(paths))
            self.assertEqual(native_acceleration.format_site_path(()), "")
            self.assertEqual(native_acceleration.format_site_path((0, 2, -1)), "1.3.0")
            self.assertIsNone(native_acceleration.format_site_path((2**63 - 1,)))
            self.assertIsNone(native_acceleration.format_site_path((True,)))
            self.assertIsNone(native_acceleration.format_site_path(tuple(range(257))))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[:3], ["", "1", "1.3.0"])
        self.assertEqual(actual[5:8], ["2", "41", "2.3"])

    def test_geometry_direct_children_use_native_selection_with_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_direct_elements_by_name"
        ):
            self.skipTest("optional Rust direct XML child selector is not built")
        root = worktable_geometry.ET.fromstring(
            '<Root xmlns="urn:test"><Item id="first"/><Group><Item id="nested"/></Group><Item id="last"/></Root>'
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_children = worktable_geometry._direct_children(root, "Item")
            expected_first = worktable_geometry._direct_child(root, "Item")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_direct_elements_by_name",
                wraps=native_acceleration.xml_direct_elements_by_name,
            ) as native_call:
                actual_children = worktable_geometry._direct_children(root, "Item")
                actual_first = worktable_geometry._direct_child(root, "Item")
                self.assertEqual(native_call.call_count, 2)
        self.assertEqual(actual_children, expected_children)
        self.assertIs(actual_first, expected_first)
        self.assertEqual(
            [element.attrib["id"] for element in actual_children], ["first", "last"]
        )

        root_with_comment = worktable_geometry.ET.Element("Root")
        root_with_comment.append(worktable_geometry.ET.Comment("ignored"))
        first = worktable_geometry.ET.SubElement(root_with_comment, "Item", id="first")
        worktable_geometry.ET.SubElement(root_with_comment, "Item", id="last")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_children = worktable_geometry._direct_children(root_with_comment, "Item")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_children = worktable_geometry._direct_children(root_with_comment, "Item")
        self.assertEqual(actual_children, expected_children)
        self.assertIs(actual_children[0], first)
        self.assertEqual([element.attrib["id"] for element in actual_children], ["first", "last"])

    def test_geometry_find_uses_native_element_selection_with_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML element selector is not built")
        root = worktable_geometry.ET.fromstring(
            '<Root xmlns="urn:test"><Item id="first"/><Group><Item id="second"/></Group></Root>'
        )
        names = ["Root", "Item", "Missing"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._find(root, name) for name in names]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual = [worktable_geometry._find(root, name) for name in names]
                self.assertEqual(native_call.call_count, len(names))
        for actual_element, expected_element in zip(actual, expected):
            self.assertIs(actual_element, expected_element)
        self.assertEqual(actual[1].attrib["id"], "first")
        self.assertIsNone(actual[2])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_matches = worktable_geometry._findall_local(root, "Item")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual_matches = worktable_geometry._findall_local(root, "Item")
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual_matches, expected_matches)
        self.assertEqual(
            [element.attrib["id"] for element in actual_matches], ["first", "second"]
        )

        root_with_comment = worktable_geometry.ET.Element("Root")
        root_with_comment.append(worktable_geometry.ET.Comment("ignored"))
        expected_comment_case = worktable_geometry.ET.SubElement(root_with_comment, "Item")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = worktable_geometry._find(root_with_comment, "Item")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual = worktable_geometry._find(root_with_comment, "Item")
        self.assertIs(actual, expected_comment_case)
        self.assertIs(actual, expected)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_matches = worktable_geometry._findall_local(root_with_comment, "Item")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_matches = worktable_geometry._findall_local(root_with_comment, "Item")
        self.assertEqual(actual_matches, expected_matches)
        self.assertEqual(actual_matches, [expected_comment_case])

    def test_is_nil_uses_native_true_match_without_changing_attribute_policy(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_casefold_equals"
        ):
            self.skipTest("optional Rust ASCII comparator is not built")
        attributes = [
            {"i:nil": "TRUE"},
            {"{xsi}nil": "false", "other": "true"},
            {"{xsi}nil": " true "},
            {"other": "true"},
            {"i:nil": "trüe"},
        ]
        elements = [worktable_geometry.ET.Element("Value", attrib) for attrib in attributes]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._is_nil(element) for element in elements]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_call:
                actual = [worktable_geometry._is_nil(element) for element in elements]
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [True, True, False, False, False])

    def test_geometry_bool_text_uses_native_ascii_comparisons(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_casefold_equals"
        ):
            self.skipTest("optional Rust ASCII comparator is not built")
        texts = ["true", "TRUE", "false", "FALSE", "TrUe", "FaLsE", "yes", " true ", "trüe", ""]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._bool_text(text) for text in texts]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_call:
                actual = [worktable_geometry._bool_text(text) for text in texts]
                self.assertGreaterEqual(native_call.call_count, len(texts) - 1)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual, [True, True, False, False, True, False, None, None, None, None]
        )

    def test_geometry_local_names_use_native_with_python_type_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_tag_local_name"
        ):
            self.skipTest("optional Rust XML local-name helper is not built")
        tags = ["Plain", "{urn:example}Tag", "{first}second}Tag", "東京"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._local_name(tag) for tag in tags]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_local_name",
                wraps=native_acceleration.xml_local_name,
            ) as native_call:
                actual = [worktable_geometry._local_name(tag) for tag in tags]
                self.assertEqual(native_call.call_count, len(tags))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["Plain", "Tag", "Tag", "東京"])

        class CustomTag(str):
            def rsplit(self, separator, maxsplit=-1):
                return ["custom-local-name"]

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                worktable_geometry._local_name(CustomTag("prefix}tag")),
                "custom-local-name",
            )

    def test_geometry_xml_text_trimming_uses_native_with_unsupported_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_clean_text"
        ):
            self.skipTest("optional Rust XML text trimmer is not built")
        whitespace = [
            chr(codepoint)
            for codepoint in range(0x110000)
            if chr(codepoint).isspace()
        ]
        texts = [f"{character}value{character}" for character in whitespace]
        texts.extend([" \t\r\n", "\u00a0β\u001c", "\u2003"])

        def element(text):
            value = worktable_geometry.ET.Element("Value")
            value.text = text
            return value

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [worktable_geometry._text(element(text)) for text in texts]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_clean_text",
                wraps=native_acceleration.xml_clean_text,
            ) as native_call:
                actual = [worktable_geometry._text(element(text)) for text in texts]
                self.assertEqual(native_call.call_count, len(texts))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["value"] * len(whitespace) + ["", "β", ""])

        class CustomText(str):
            def strip(self):
                return "custom-strip"

        unsupported = ["\ud800value\ud800", CustomText("value")]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unsupported_expected = [
                worktable_geometry._text(element(text)) for text in unsupported
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            unsupported_actual = [
                worktable_geometry._text(element(text)) for text in unsupported
            ]
        self.assertEqual(unsupported_actual, unsupported_expected)
        self.assertEqual(unsupported_actual, ["\ud800value\ud800", "custom-strip"])

    def test_parse_component_mines_pipettable_grip_compat_and_site_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rich.xcmp"
            path.write_text(RICH_XCMP, encoding="utf-8")
            component = parse_component(path)

        self.assertEqual(component["name"], "Rich Plate")
        pipettable = component["pipettable"]
        self.assertEqual(pipettable["cols"], 12)
        self.assertEqual(pipettable["rows"], 8)
        self.assertEqual(pipettable["pitch_x_mm"], 9.0)
        self.assertEqual(pipettable["pitch_y_mm"], -9.0)
        self.assertAlmostEqual(pipettable["well_diameter_mm"], 6.96)
        self.assertAlmostEqual(pipettable["well_depth_mm"], 10.9)
        self.assertEqual(pipettable["well_shape"], "round")
        self.assertGreater(pipettable["max_volume_ul"], 390.0)
        self.assertLess(pipettable["max_volume_ul"], 395.0)

        arrangement = component["arrangements"][0]
        self.assertEqual(
            arrangement["site_template_identifiers"]["0"],
            "33333333-3333-3333-3333-333333333333",
        )
        self.assertEqual(arrangement["allowed_grip_modes"]["0"], ["Narrow", "Wide"])
        self.assertEqual(component["custom_attributes"]["Force"], "20")
        self.assertIn("Lid Companion", component["compatible_component_names"])
        self.assertIn("Nested Insert", component["compatible_component_names"])
        self.assertIn("22222222-2222-2222-2222-222222222222", component["compatible_component_guids"])
        self.assertIn("44444444-4444-4444-4444-444444444444", component["compatible_component_guids"])

        catalog = build_labware_catalog_from_geometry(
            {
                "components": [component],
                "sites": [
                    {
                        "guid": "33333333-3333-3333-3333-333333333333",
                        "location_group_name": "Nest",
                        "type_name": "NestPlatform",
                        "site_kind": "nest",
                    }
                ],
                "workspaces": [],
            }
        )
        entry = catalog["entries"][0]
        self.assertEqual(entry["rows"], 8)
        self.assertEqual(entry["cols"], 12)
        self.assertEqual(entry["pitch_y_mm"], 9.0)
        self.assertEqual(entry["grip"]["force"], "20")
        self.assertEqual(entry["grip"]["allowed_modes"]["0"], ["Narrow", "Wide"])
        self.assertEqual(entry["site_templates"][0]["guid"], "33333333-3333-3333-3333-333333333333")
        self.assertIn("Nested Insert", entry["compatible_component_names"])


if __name__ == "__main__":
    unittest.main()
