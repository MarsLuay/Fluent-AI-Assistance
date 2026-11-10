from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_common import xml_compat as ET


class NativeAccelerationTests(unittest.TestCase):
    def test_native_bootstrap_is_optional_without_cargo(self) -> None:
        from fluent_pipeline import bootstrap

        with patch.dict("os.environ", {"TECAN_RUST_ACCELERATION": "auto"}, clear=False):
            with patch("fluent_pipeline.bootstrap.shutil.which", return_value=None):
                packages = bootstrap.workspace_packages()
        self.assertNotIn("tecan-native", {package.distribution for package in packages})

        with patch.dict("os.environ", {"TECAN_RUST_ACCELERATION": "1"}, clear=False):
            packages = bootstrap.workspace_packages()
        self.assertIn("tecan-native", {package.distribution for package in packages})

    def test_disabled_extension_uses_python_xml_parser(self) -> None:
        with patch.object(native_acceleration, "_native", None):
            root = ET.fromstring(b"<root><child>ok</child></root>")
        self.assertEqual(root.findtext("child"), "ok")

    def test_python_and_native_xml_trees_match(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        xml = (
            b'<root xmlns="urn:example" xmlns:x="urn:attr" x:flag="a&amp;b">'
            b"before<child>inside</child>between<![CDATA[+cdata]]>after</root>"
        )
        with patch.object(native_acceleration, "_native", None):
            expected = ET.fromstring(xml)
        actual = native_acceleration._native.parse_xml(
            xml, ET.MAX_XML_BYTES, ET.MAX_XML_DEPTH
        )
        self.assertEqual(ET.tostring(actual), ET.tostring(expected))

    def test_native_xscr_field_index_and_protocol_parity(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        from fluentcoder.decompiler.xscr_parser import parse_xscr
        from fluentcoder.ir.schema import STEP_TO_COMMAND_ID, StepType

        root = ET.fromstring(
            b"<Root><Object><Name>outer</Name><Object><Name>inner</Name></Object></Object></Root>"
        )
        parent = root.find("Object")
        nested = parent.find("Object")
        indexed = native_acceleration._native.xscr_object_fields(root)
        self.assertEqual(indexed[parent]["texts"].get("Name"), "outer")
        self.assertEqual(indexed[nested]["texts"].get("Name"), "inner")

        command_id = STEP_TO_COMMAND_ID[StepType.ASPIRATE]
        padding = "<Padding><Value>ignored</Value></Padding>" * 24
        objects = "".join(
            f'<Object Type="{command_id}"><LineNumber>{index + 1}</LineNumber>{padding}</Object>'
            for index in range(12)
        )
        xml = (
            "<Root><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>"
            f"{objects}"
            "</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></Root>"
        )
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "synthetic.xscr"
            path.write_text(xml, encoding="utf-8")
            with patch.object(native_acceleration, "_native", None):
                expected = parse_xscr(path).model_dump(mode="json")
            actual = parse_xscr(path).model_dump(mode="json")
        self.assertEqual(actual, expected)

    def test_decompiler_scalar_field_scans_use_native_selectors(self) -> None:
        from fluentcoder.decompiler import xscr_parser

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        move = ET.fromstring(
            "<Object><ChargeCondition/><Nested><ChargeCondition> ready </ChargeCondition>"
            "</Nested><ChargeCondition>later</ChargeCondition></Object>"
        )
        end = ET.fromstring(
            "<Object><ScriptExecutionResult/><Nested><ScriptExecutionResult> exit </ScriptExecutionResult>"
            "</Nested><ScriptExecutionResult>later</ScriptExecutionResult></Object>"
        )
        available_id = ET.fromstring(
            "<Object><AvailableID/><Nested><AvailableID> identifier </AvailableID>"
            "</Nested><AvailableID>later</AvailableID></Object>"
        )
        diti = ET.fromstring(
            "<Object><DitiType><AvailableID/><Nested><AvailableID> diti-id </AvailableID>"
            "</Nested><AvailableID>later</AvailableID></DitiType>"
            "<Object><DitiType><AvailableID>skip</AvailableID></DitiType></Object></Object>"
        )
        diti_type = diti.find("DitiType")
        diti_type.insert(0, ET.Comment("preserve fallback"))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = (
                xscr_parser._parse_move_axis_command(move),
                xscr_parser._parse_end_script(end),
                xscr_parser._extract_nested_available_id(available_id),
                xscr_parser._extract_diti_type_available_id(diti),
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual = (
                    xscr_parser._parse_move_axis_command(move),
                    xscr_parser._parse_end_script(end),
                    xscr_parser._extract_nested_available_id(available_id),
                    xscr_parser._extract_diti_type_available_id(diti),
                )
                for root, names in (
                    (move, {"ChargeCondition"}),
                    (end, {"ScriptExecutionResult"}),
                    (available_id, {"AvailableID"}),
                    (diti_type, {"AvailableID"}),
                ):
                    self.assertTrue(
                        any(
                            call.args[0] is root and call.args[1] == names
                            for call in native_call.call_args_list
                        )
                    )

        self.assertEqual(actual, expected)
        self.assertEqual(actual[0].charge_condition, "ready")
        self.assertEqual(actual[1].return_code, "exit")
        self.assertEqual(actual[2], "identifier")
        self.assertEqual(actual[3], "diti-id")

    def test_rga_transfer_inner_fields_use_native_xml_helpers(self) -> None:
        from html import escape

        from tecan_common import xml_helpers
        from fluentcoder.decompiler import xscr_parser

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        inner = (
            "<Root><Labware>first</Labware><Location>Deck A</Location><Site>2</Site>"
            "<Nested><Labware>second</Labware><Location>Deck B</Location><Site>4</Site>"
            "</Nested></Root>"
        )
        obj = ET.Element("Object")
        ET.SubElement(obj, "ExecutionSettings").text = escape(inner, quote=False)
        first_macro = ET.SubElement(obj, "ApplicationDriverMacro")
        first_macro.set("ModuleName", "first-module")
        nested = ET.SubElement(obj, "Nested")
        second_macro = ET.SubElement(nested, "ApplicationDriverMacro")
        second_macro.set("ModuleName", "second-module")

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xscr_parser._parse_rga_transfer(obj)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xml_helpers,
                "_native_xml_elements_by_name",
                wraps=xml_helpers._native_xml_elements_by_name,
            ) as native_fields:
                with patch.object(
                    native_acceleration,
                    "xml_first_element_by_name",
                    wraps=native_acceleration.xml_first_element_by_name,
                ) as native_macro:
                    actual = xscr_parser._parse_rga_transfer(obj)
                    self.assertEqual(
                        sum(
                            call.args[1] == {"Labware", "Location", "Site"}
                            for call in native_fields.call_args_list
                        ),
                        1,
                    )
                    native_macro.assert_any_call(
                        obj, "ApplicationDriverMacro", ET.Element
                    )

        self.assertEqual(actual, expected)
        self.assertEqual(actual.labware_name, "second")
        self.assertEqual(actual.destination_location, "Deck B")
        self.assertEqual(actual.destination_site, 4)
        self.assertEqual(actual.module_name, "first-module")

    def test_native_pattern_window_order_and_confidence(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        scripts = [
            {
                "script_id": 7,
                "commands": [
                    {"command_index": 0, "command_type": "A"},
                    {"command_index": 1, "command_type": "B"},
                    {"command_index": 2, "command_type": "C"},
                ],
            }
        ]
        candidates = native_acceleration._native.pattern_windows(
            scripts, [[ ["aspirate"], [], ["aspirate"] ]], 1, 1
        )
        self.assertEqual(
            [candidate["signature"] for candidate in candidates],
            ["A > B", "B > C"],
        )
        self.assertEqual([candidate["confidence"] for candidate in candidates], [1.0, 1.0])

    def test_native_deepcopy_preserves_nested_data_and_aliases(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        shared = [{"value": 4}]
        original = {"left": shared, "right": shared}
        copied = native_acceleration._native.deepcopy_json(original)
        self.assertEqual(copied, original)
        self.assertIsNot(copied, original)
        self.assertIs(copied["left"], copied["right"])
        self.assertIsNot(copied["left"], shared)

        shared_tuple = ([1],)
        copied_tuples = native_acceleration._native.deepcopy_json(
            [shared_tuple, shared_tuple]
        )
        self.assertIs(copied_tuples[0], copied_tuples[1])
        self.assertIsNot(copied_tuples[0], shared_tuple)
        self.assertIsNot(copied_tuples[0][0], shared_tuple[0])

        tuple_key = (1, "key")
        copied_tuple_key = native_acceleration._native.deepcopy_json(
            {tuple_key: tuple_key}
        )
        self.assertIs(copied_tuple_key[tuple_key], tuple_key)

    def test_native_deepcopy_cycles_fall_back_to_python(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        from tecan_reader.project_model import deepcopy as project_deepcopy

        cyclic_list: list[object] = []
        cyclic_list.append(cyclic_list)
        indirect_list: list[object] = []
        indirect_tuple = (indirect_list,)
        indirect_list.append(indirect_tuple)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.deepcopy_json(cyclic_list))
            copied_list = project_deepcopy(cyclic_list)
            self.assertIsNone(native_acceleration.deepcopy_json(indirect_list))
            copied_indirect = project_deepcopy(indirect_list)

        self.assertIs(copied_list[0], copied_list)
        self.assertIs(copied_indirect[0][0], copied_indirect)

    def test_native_deepcopy_falls_back_for_overridden_python_types(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        from tecan_reader.project_model import deepcopy as project_deepcopy

        class ShiftedInt(int):
            def __deepcopy__(self, memo: dict[int, object]) -> ShiftedInt:
                return ShiftedInt(int(self) + 1)

        class CustomList(list):
            def __deepcopy__(self, memo: dict[int, object]) -> dict[str, list[object]]:
                return {"custom": list(self)}

        class CustomDict(dict):
            def __deepcopy__(self, memo: dict[int, object]) -> dict[str, object]:
                return {"custom": dict(self)}

        class CustomTuple(tuple):
            def __deepcopy__(self, memo: dict[int, object]) -> tuple[object, ...]:
                return ("copied", *self)

        values = [
            ShiftedInt(4),
            CustomList([1]),
            CustomDict({"value": 1}),
            CustomTuple((1, 2)),
            {ShiftedInt(4): "value"},
        ]
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}
                ):
                    expected = project_deepcopy(value)
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    self.assertIsNone(native_acceleration.deepcopy_json(value))
                    actual = project_deepcopy(value)
                self.assertEqual(actual, expected)

    def test_native_deepcopy_randomized_builtin_parity(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        generator = random.Random(20260313)
        scalars = [
            None,
            True,
            False,
            -5,
            0,
            2**80,
            -2.5,
            0.125,
            "",
            "plain",
            "μ",
            b"raw",
        ]
        keys = ["", "key", "μ", 0, 1, True, 2.5, b"key", (1, "nested")]

        def make_value(depth: int):
            if depth == 0 or generator.randrange(4) == 0:
                return generator.choice(scalars)
            kind = generator.randrange(3)
            if kind == 0:
                return [make_value(depth - 1) for _ in range(generator.randrange(4))]
            if kind == 1:
                return tuple(
                    make_value(depth - 1) for _ in range(generator.randrange(4))
                )
            return {
                generator.choice(keys): make_value(depth - 1)
                for _ in range(generator.randrange(4))
            }

        for case in range(80):
            value = make_value(4)
            with self.subTest(case=case):
                expected = copy.deepcopy(value)
                actual = native_acceleration._native.deepcopy_json(value)
                self.assertEqual(actual, expected)

    def test_native_canonical_json_matches_python_format(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        value = {
            "z": ["μ", True, None, {"quote": 'a"b', "newline": "a\nb"}],
            "a": {"integer": 12, "float": 0.125},
        }
        self.assertEqual(
            native_acceleration._native.canonical_json(value),
            json.dumps(value, ensure_ascii=False, sort_keys=True),
        )
        from tecan_reader.common import fast_json_dumps

        edge_values = {"small": 1e-7, "large": 1e16}
        self.assertEqual(
            fast_json_dumps(edge_values),
            json.dumps(edge_values, ensure_ascii=False, sort_keys=True),
        )

        class CustomList(list):
            def __iter__(self):
                return iter(["custom-list-value"])

        class CustomTuple(tuple):
            def __iter__(self):
                return iter(["custom-tuple-value"])

        class CustomDict(dict):
            def items(self):
                return [("custom-dict-key", "custom-dict-value")]

        custom_values = [
            CustomList(["stored-list-value"]),
            CustomTuple(("stored-tuple-value",)),
            CustomDict({"stored-dict-key": "stored-dict-value"}),
        ]
        for custom_value in custom_values:
            with self.subTest(value_type=type(custom_value).__name__):
                expected = json.dumps(
                    custom_value, ensure_ascii=False, sort_keys=True
                )
                with patch.dict(
                    "os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}
                ):
                    self.assertIsNone(
                        native_acceleration.canonical_json(custom_value)
                    )
                    self.assertEqual(fast_json_dumps(custom_value), expected)

    def test_native_canonical_json_randomized_builtin_parity(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "canonical_json_compact"
        ):
            self.skipTest("optional Rust compact JSON serializer is not built")
        generator = random.Random(20260312)
        scalars = [
            None,
            True,
            False,
            -5,
            0,
            2**40,
            -3.5,
            -0.5,
            0.0,
            0.125,
            12.5,
            "",
            "plain",
            "μ",
            "𝄞",
            "quote\"slash\\newline\n",
            "control\x01",
        ]
        keys = ["", "a", "z", "é", "𝄞", "line\nbreak", "quote\""]

        def make_value(depth: int):
            if depth == 0 or generator.randrange(4) == 0:
                return generator.choice(scalars)
            kind = generator.randrange(3)
            if kind == 0:
                return [make_value(depth - 1) for _ in range(generator.randrange(4))]
            if kind == 1:
                return tuple(
                    make_value(depth - 1) for _ in range(generator.randrange(4))
                )
            return {
                generator.choice(keys): make_value(depth - 1)
                for _ in range(generator.randrange(4))
            }

        for case in range(80):
            value = make_value(4)
            with self.subTest(case=case):
                expected = json.dumps(value, ensure_ascii=False, sort_keys=True)
                actual = native_acceleration.canonical_json(value)
                self.assertIsNotNone(actual)
                self.assertEqual(actual, expected)
                expected_compact = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                actual_compact = native_acceleration.canonical_json_compact(value)
                self.assertIsNotNone(actual_compact)
                self.assertEqual(actual_compact, expected_compact)

    def test_catalog_path_and_fingerprint_parity(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust extension is not built")
        from fluentcoder.catalog import indexer

        with tempfile.TemporaryDirectory() as raw_install:
            install = Path(raw_install)
            component_dir = install / "SystemSpecific" / "Worktable" / "Components"
            liquid_dir = install / "SystemSpecific" / "LiquidClasses"
            component_dir.mkdir(parents=True)
            liquid_dir.mkdir(parents=True)
            (component_dir / "b.xcmp").write_bytes(b"<component/>")
            (component_dir / "a.xcmp").write_bytes(b"<component id='a'/>")
            (liquid_dir / "class.xlqc").write_bytes(b"<liquid-class/>")

            with patch.object(native_acceleration, "_native", None):
                expected_paths = indexer._catalog_source_paths(install)
                expected_stat = indexer._install_stat_fingerprint(install)
            actual_paths = native_acceleration._native.catalog_source_paths(
                str(install), False
            )
            self.assertEqual(actual_paths, expected_paths)

            actual_stat = native_acceleration._native.catalog_stat_fingerprint(
                str(install), False
            )
            self.assertEqual(actual_stat, expected_stat)

            expected_content = {}
            for relative in expected_paths:
                path = install / relative
                digest = hashlib.sha256()
                digest.update(b"fluentcoder.catalog.content.v1")
                digest.update(relative.encode("utf-8"))
                digest.update(str(path.stat().st_size).encode("utf-8"))
                digest.update(path.read_bytes())
                expected_content[relative] = digest.hexdigest()
            actual_content = dict(
                native_acceleration._native.catalog_content_fingerprints(
                    str(install), False
                )
            )
            self.assertEqual(actual_content, expected_content)


if __name__ == "__main__":
    unittest.main()
