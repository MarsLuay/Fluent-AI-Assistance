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
    def test_native_bootstrap_requires_rust_and_rejects_disable_setting(self) -> None:
        from fluent_pipeline import bootstrap

        with patch.dict("os.environ", {"TECAN_RUST_ACCELERATION": "auto"}, clear=False):
            packages = bootstrap.workspace_packages()
        self.assertEqual(packages[0].distribution, "tecan-native")

        with patch.dict("os.environ", {"TECAN_RUST_ACCELERATION": "0"}, clear=False):
            with self.assertRaisesRegex(bootstrap.PipelineError, "tecan-native is required"):
                bootstrap.workspace_packages()

    def test_ascii_lower_preserves_whitespace_and_uses_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_lower"
        ):
            self.skipTest("optional Rust ASCII lowercase helper is not built")
        cases = [
            (" Mixed Value ", " mixed value "),
            ("AZ_09", "az_09"),
            ("", ""),
            ("Straße", None),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            self.assertTrue(
                all(native_acceleration.ascii_lower(value) is None for value, _ in cases)
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual = [native_acceleration.ascii_lower(value) for value, _ in cases]
        self.assertEqual(actual, [expected for _, expected in cases])

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

    def test_selected_tip_integer_list_uses_native_i64_with_python_fallback(self) -> None:
        from fluentcoder.decompiler import xscr_parser

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "parse_i64_text"
        ):
            self.skipTest("optional Rust signed-integer parser is not built")
        root = ET.fromstring(
            "<Object><SelectedTipsIndexes>"
            "<int>1</int><int>-2</int><int> +3 </int><int>1_0</int>"
            "<int>١٢</int><int>999999999999999999999999999999999</int>"
            "<int>invalid</int><int> </int><int/>"
            "</SelectedTipsIndexes></Object>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xscr_parser._parse_int_list(root, "SelectedTipsIndexes")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xscr_parser,
                "_native_parse_i64_text",
                wraps=native_acceleration.parse_i64_text,
            ) as native_call:
                actual = xscr_parser._parse_int_list(root, "SelectedTipsIndexes")
                self.assertEqual(native_call.call_count, 8)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [1, -2, 3, 10, 12, 999999999999999999999999999999999],
        )

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

    def test_execution_settings_first_texts_use_native_map_with_python_parity(self) -> None:
        from html import escape

        from fluentcoder.decompiler import xscr_parser

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_first_nonempty_texts"
        ):
            self.skipTest("optional Rust XML text collector is not built")
        inner = (
            '<Root xmlns:ns="urn:test"><Empty> </Empty><ns:Labware> first </ns:Labware>'
            "<Labware>later</Labware><Site>one</Site><Site>two</Site>"
            "<Nested><Location> Deck A </Location></Nested></Root>"
        )
        execution_settings = escape(inner, quote=False)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xscr_parser._parse_execution_settings_parameters(execution_settings)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xscr_parser,
                "_native_xml_first_nonempty_texts",
                wraps=xscr_parser._native_xml_first_nonempty_texts,
            ) as native_call:
                actual = xscr_parser._parse_execution_settings_parameters(
                    execution_settings
                )
                native_call.assert_called_once()
                self.assertIs(native_call.call_args.args[1], ET.Element)

        self.assertEqual(actual, expected)
        self.assertEqual(
            list(actual.items()),
            [("Labware", "first"), ("Site", "one"), ("Location", "Deck A")],
        )

    def test_driver_macro_safety_scan_uses_native_tag_selector(self) -> None:
        from tecan_common import xml_helpers
        from fluentcoder.driver_macro_source import (
            application_driver_macro_has_unmodeled_source,
        )

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        sources = [
            '<Root xmlns:ns="urn:test"><ns:LegacyDriverMacro Version="1">'
            "<ExecutionSettings/></ns:LegacyDriverMacro>"
            "<ApplicationDriverMacro><Unknown/></ApplicationDriverMacro></Root>",
            "<Root><ApplicationDriverMacro><Unknown/></ApplicationDriverMacro></Root>",
            '<Root><LegacyDriverMacro unexpected="x"/></Root>',
            "<Root><Other/></Root>",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                application_driver_macro_has_unmodeled_source(source)
                for source in sources
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xml_helpers,
                "_native_xml_elements_by_name",
                wraps=xml_helpers._native_xml_elements_by_name,
            ) as native_call:
                with patch.object(
                    native_acceleration,
                    "xml_direct_elements_by_name",
                    wraps=native_acceleration.xml_direct_elements_by_name,
                ) as native_children:
                    actual = [
                        application_driver_macro_has_unmodeled_source(source)
                        for source in sources
                    ]
                    self.assertEqual(native_call.call_count, len(sources))
                    self.assertEqual(native_children.call_count, 2)
                    self.assertTrue(
                        any(
                            call.args[1]
                            == {"ExecutionSettings", "AvailableID", "ErrorHandling"}
                            and call.args[2] is ET.Element
                            for call in native_children.call_args_list
                        )
                    )

        self.assertEqual(actual, expected)
        self.assertEqual(actual, [False, True, True, True])

    def test_liquid_class_head_scan_uses_native_selector_with_fallback(self) -> None:
        from types import SimpleNamespace

        from fluentcoder.catalog import xlqc

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        root = ET.fromstring(
            '<Root xmlns:ns="urn:test"><Payload><ObjectName>Example</ObjectName>'
            "<PayloadData><ns:PipettingDeviceType>Fca</ns:PipettingDeviceType>"
            "<PipettingDeviceType> </PipettingDeviceType>"
            "<Nested><PipettingDeviceType>Mca96</PipettingDeviceType></Nested>"
            "<PipettingDeviceType>Fca</PipettingDeviceType></PayloadData>"
            "</Payload></Root>"
        )
        payload_data = root.find("./Payload/PayloadData")
        fake_tree = SimpleNamespace(getroot=lambda: root)
        source_path = "synthetic-liquid-class.xlqc"
        xlqc._load_xlqc_cached.cache_clear()
        self.addCleanup(xlqc._load_xlqc_cached.cache_clear)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(xlqc.ET, "parse", return_value=fake_tree):
                expected = xlqc._load_xlqc_cached(source_path)
        xlqc._load_xlqc_cached.cache_clear()
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(xlqc.ET, "parse", return_value=fake_tree):
                with patch.object(
                    native_acceleration,
                    "xml_elements_by_name",
                    wraps=native_acceleration.xml_elements_by_name,
                ) as native_call:
                    actual = xlqc._load_xlqc_cached(source_path)
                    native_call.assert_called_once_with(
                        payload_data, {"PipettingDeviceType"}, ET.Element
                    )

        self.assertEqual(actual, expected)
        self.assertEqual(actual.name, "Example")
        self.assertEqual(actual.head, "Fca")
        self.assertEqual(actual.supported_heads, ("Fca", "Mca96"))

    def test_xcmp_catalog_name_scans_use_native_selectors_with_parity(self) -> None:
        from fluentcoder.catalog import xcmp

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        root = ET.fromstring(
            '<Root xmlns:ns="urn:test">'
            "<ns:LocationGroupName> alpha </ns:LocationGroupName>"
            "<Nested><LocationGroupName>beta</LocationGroupName>"
            "<LocationGroupName> </LocationGroupName></Nested>"
            "<LabwareName><ns:Value> example plate </ns:Value>"
            "<Nested><Value>example rack</Value><Value> </Value></Nested></LabwareName>"
            "<ns:LabwareName><Value>example tray</Value></ns:LabwareName>"
            "</Root>"
        )
        labware_nodes = [
            node
            for node in root.iter()
            if isinstance(node.tag, str)
            and node.tag.rsplit("}", 1)[-1] == "LabwareName"
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_locations = xcmp._collect_location_names(root)
            expected_labware = xcmp._collect_labware_names(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual_locations = xcmp._collect_location_names(root)
                native_call.assert_called_once_with(
                    root, {"LocationGroupName"}, ET.Element
                )
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual_labware = xcmp._collect_labware_names(root)
                self.assertEqual(native_call.call_count, 1 + len(labware_nodes))
                native_call.assert_any_call(root, {"LabwareName"}, ET.Element)
                for node in labware_nodes:
                    native_call.assert_any_call(node, {"Value"}, ET.Element)

        self.assertEqual(actual_locations, expected_locations)
        self.assertEqual(actual_labware, expected_labware)
        self.assertEqual(actual_locations, {"alpha", "beta"})
        self.assertEqual(
            actual_labware, {"example plate", "example rack", "example tray"}
        )

    def test_workspace_occupancy_uses_native_structural_selectors(self) -> None:
        from fluentcoder.catalog import xcmp

        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in (
                "xml_direct_elements_by_name",
                "xml_direct_elements_by_ascii_prefix",
            )
        ):
            self.skipTest("optional Rust XML selectors are not built")
        root = ET.fromstring(
            '<Root xmlns:ns="urn:test"><Payload><PayloadData><Wrapper>'
            "<Worktables><KeyValueOfstringWorktable><Value><Frame>"
            "<Arrangements><ns:Arrangement><Sites><ns:KeyValueOfintSiteNode>"
            "<Key>1</Key><Value><ConnectedComponent><LabwareName>"
            "<ns:KeyValueOfstringstringField><Key>default</Key>"
            "<Value>example item</Value></ns:KeyValueOfstringstringField>"
            "<Nested><ns:KeyValueOfstringstringNested><Key>initial</Key>"
            "<Value>ignored nested item</Value></ns:KeyValueOfstringstringNested>"
            "</Nested></LabwareName><BaseLocationIdentifier>alpha</BaseLocationIdentifier>"
            "</ConnectedComponent></Value></ns:KeyValueOfintSiteNode>"
            "<Nested><ns:KeyValueOfintSiteNested><Key>2</Key><Value/>"
            "</ns:KeyValueOfintSiteNested></Nested></Sites></ns:Arrangement>"
            "<Nested><ns:Arrangement><Sites/></ns:Arrangement></Nested></Arrangements>"
            "</Frame></Value></KeyValueOfstringWorktable></Worktables>"
            "</Wrapper></PayloadData></Payload></Root>"
        )
        payload_data = root.find("./Payload/PayloadData")
        arrangements = payload_data.find(
            "./Wrapper/Worktables/KeyValueOfstringWorktable/Value/Frame/Arrangements"
        )
        sites = next(element for element in root.iter() if element.tag == "Sites")
        labware_name = next(
            element for element in root.iter() if element.tag == "LabwareName"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xcmp._walk_workspace_occupancy(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as first_native_call:
                with patch.object(
                    native_acceleration,
                    "xml_direct_elements_by_name",
                    wraps=native_acceleration.xml_direct_elements_by_name,
                ) as exact_native_call:
                    with patch.object(
                        native_acceleration,
                        "xml_direct_elements_by_ascii_prefix",
                        wraps=native_acceleration.xml_direct_elements_by_ascii_prefix,
                    ) as prefix_native_call:
                        actual = xcmp._walk_workspace_occupancy(root)
                        first_native_call.assert_any_call(
                            payload_data, "Worktables", ET.Element
                        )
                        exact_native_call.assert_called_once_with(
                            arrangements, {"Arrangement"}, ET.Element
                        )
                        self.assertEqual(prefix_native_call.call_count, 2)
                        prefix_native_call.assert_any_call(
                            sites, "KeyValueOfintSite", ET.Element
                        )
                        prefix_native_call.assert_any_call(
                            labware_name, "KeyValueOfstringstring", ET.Element
                        )

        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            (
                [xcmp.WorkspaceOccupant((1,), 1, "example item", "alpha", None)],
                [((1,), "alpha")],
            ),
        )

    def test_ascii_prefix_selector_falls_back_for_unicode_local_names(self) -> None:
        from fluentcoder.catalog import xcmp

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_direct_elements_by_ascii_prefix"
        ):
            self.skipTest("optional Rust XML prefix selector is not built")
        root = ET.fromstring(
            '<Root xmlns:ns="urn:test"><ns:KeyValueOfintSiteΩ/><Other/></Root>'
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xcmp._direct_children_with_prefix(root, "KeyValueOfintSite")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_direct_elements_by_ascii_prefix",
                wraps=native_acceleration.xml_direct_elements_by_ascii_prefix,
            ) as native_call:
                actual = xcmp._direct_children_with_prefix(root, "KeyValueOfintSite")
                native_call.assert_called_once_with(
                    root, "KeyValueOfintSite", ET.Element
                )

        self.assertEqual(actual, expected)
        self.assertEqual(
            [element.tag.rsplit("}", 1)[-1] for element in actual],
            ["KeyValueOfintSiteΩ"],
        )

    def test_driver_recovery_field_scan_uses_native_selector_with_parity(self) -> None:
        from fluentcoder.ir import driver_recovery

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_ascii_casefolded_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        macro = ET.fromstring(
            '<Macro xmlns:ns="urn:test"><ErrorHandling>'
            "<ns:ERRORCODE> CODE </ns:ERRORCODE><aCtIoN> Retry </aCtIoN>"
            "<MAXRETRIES>invalid</MAXRETRIES><Retries>2</Retries>"
            "<Nested><Reference>example-handler</Reference></Nested>"
            "</ErrorHandling></Macro>"
        )
        policy_element = macro.find("ErrorHandling")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = driver_recovery.parse_driver_recovery_policy(macro)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_ascii_casefolded_name",
                wraps=native_acceleration.xml_elements_by_ascii_casefolded_name,
            ) as native_call:
                actual = driver_recovery.parse_driver_recovery_policy(macro)
                native_call.assert_called_once_with(
                    policy_element,
                    driver_recovery._RECOVERY_FIELD_TAGS,
                    ET.Element,
                )

        self.assertEqual(actual, expected)
        self.assertEqual(actual.error_code, "CODE")
        self.assertEqual(actual.action, "Retry")
        self.assertEqual(actual.max_retries, 2)
        self.assertEqual(actual.source_metadata["invalid_max_retries"], "invalid")
        self.assertEqual(actual.handler_target, "example-handler")

    def test_driver_recovery_field_scan_falls_back_for_unicode_names(self) -> None:
        from fluentcoder.ir import driver_recovery

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_ascii_casefolded_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        macro = ET.fromstring(
            '<Macro xmlns:ns="urn:test"><ErrorHandling>'
            "<ns:Errorſource>example device</ns:Errorſource>"
            "</ErrorHandling></Macro>"
        )
        policy_element = macro.find("ErrorHandling")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = driver_recovery.parse_driver_recovery_policy(macro)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_ascii_casefolded_name",
                wraps=native_acceleration.xml_elements_by_ascii_casefolded_name,
            ) as native_call:
                actual = driver_recovery.parse_driver_recovery_policy(macro)
                native_call.assert_called_once_with(
                    policy_element,
                    driver_recovery._RECOVERY_FIELD_TAGS,
                    ET.Element,
                )

        self.assertEqual(actual, expected)
        self.assertEqual(actual.error_source, "example device")

    def test_liha_raw_preservation_check_uses_native_allowlist(self) -> None:
        from fluentcoder.decompiler import xscr_parser

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_has_unmodeled_local_name"
        ):
            self.skipTest("optional Rust XML allowlist check is not built")
        known = ET.fromstring(
            "<Object><SelectedTipsIndexes><int>1</int></SelectedTipsIndexes>"
            "<LihaDropTipsScriptCommandDataV2><Data><TipMask>2</TipMask>"
            "</Data></LihaDropTipsScriptCommandDataV2></Object>"
        )
        known.append(ET.Comment("ignored by the preservation check"))
        unknown = ET.fromstring(
            "<Object><SelectedTipsIndexes><int>1</int></SelectedTipsIndexes>"
            "<LihaDropTipsScriptCommandDataV2><Data><VendorExtension/>"
            "</Data></LihaDropTipsScriptCommandDataV2></Object>"
        )

        class StringSubclass(str):
            pass

        unsupported = ET.Element("Object")
        unsupported.tag = StringSubclass("Object")
        known_names = list(xscr_parser._LIHA_DROP_TIPS_KNOWN_XML_FIELDS)
        allowed_prefixes = ["LihaDropTipsScriptCommandData"]
        self.assertIs(
            native_acceleration._native.xml_has_unmodeled_local_name(
                known, known_names, allowed_prefixes, ET.Element
            ),
            False,
        )
        self.assertIs(
            native_acceleration._native.xml_has_unmodeled_local_name(
                unknown, known_names, allowed_prefixes, ET.Element
            ),
            True,
        )
        self.assertIsNone(
            native_acceleration.xml_has_unmodeled_local_name(
                unsupported,
                xscr_parser._LIHA_DROP_TIPS_KNOWN_XML_FIELDS,
                ("LihaDropTipsScriptCommandData",),
                ET.Element,
            )
        )
        roots = (known, unknown, unsupported)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                xscr_parser._liha_drop_tips_preserves_raw_xml(root)
                for root in roots
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                xscr_parser,
                "_native_xml_has_unmodeled_local_name",
                wraps=xscr_parser._native_xml_has_unmodeled_local_name,
            ) as native_call:
                actual = [
                    xscr_parser._liha_drop_tips_preserves_raw_xml(root)
                    for root in roots
                ]
                self.assertEqual(native_call.call_count, 3)
                for root in roots:
                    native_call.assert_any_call(
                        root,
                        xscr_parser._LIHA_DROP_TIPS_KNOWN_XML_FIELDS,
                        ("LihaDropTipsScriptCommandData",),
                        ET.Element,
                    )

        self.assertEqual(actual, expected)
        self.assertEqual(actual, [False, True, False])

    def test_simulator_xml_text_and_volume_use_native_selectors(self) -> None:
        from fluentcoder.simulator.walk import Simulator

        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in (
                "xml_elements_by_name",
                "xml_first_element_by_name",
                "xml_all_unique_texts",
            )
        ):
            self.skipTest("optional Rust XML selectors are not built")
        root = ET.fromstring(
            '<Root xmlns:ns="urn:test"><ns:Name/><Name> example </Name>'
            "<Volume/><Volumes><Value> 1.25 </Value></Volumes></Root>"
        )
        simulator = Simulator.__new__(Simulator)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_text = simulator._xml_text(root, "Name")
            expected_volume = simulator._xml_volume(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as elements_native_call:
                actual_text = simulator._xml_text(root, "Name")
                elements_native_call.assert_called_once_with(
                    root, {"Name"}, ET.Element
                )
            with patch.object(
                native_acceleration,
                "xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as first_native_call:
                with patch.object(
                    native_acceleration,
                    "xml_all_unique_texts",
                    wraps=native_acceleration.xml_all_unique_texts,
                ) as texts_native_call:
                    actual_volume = simulator._xml_volume(root)
                    self.assertEqual(first_native_call.call_count, 2)
                    first_native_call.assert_any_call(root, "Volume", ET.Element)
                    first_native_call.assert_any_call(root, "Volumes", ET.Element)
                    self.assertEqual(texts_native_call.call_count, 2)
                    texts_native_call.assert_any_call(root.find("Volume"), ET.Element)
                    texts_native_call.assert_any_call(root.find("Volumes"), ET.Element)

        self.assertEqual(actual_text, expected_text)
        self.assertEqual(actual_volume, expected_volume)
        self.assertEqual(actual_text, "example")
        self.assertEqual(actual_volume, 1.25)

    def test_expression_validation_uses_native_xml_selectors_with_parity(self) -> None:
        from fluentcoder.expressions import validation

        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in (
                "xml_elements_by_name",
                "xml_first_element_by_name",
            )
        ):
            self.skipTest("optional Rust XML selectors are not built")
        root = ET.fromstring(
            '<Root xmlns:ns="urn:test"><Section><ns:Value> first </ns:Value>'
            "<Nested><Value> second </Value></Nested></Section>"
            "<Section><Value>third</Value></Section>"
            "<VariableDefinitionHelper><Name>alpha</Name><TypeName>Number</TypeName>"
            "</VariableDefinitionHelper><anyType Type=\"VariableDefinitionHelper\">"
            "<Name>beta</Name><TypeName>Boolean</TypeName></anyType></Root>"
        )
        sections = [element for element in root.iter() if element.tag == "Section"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_values = validation._expression_field_values(
                root, "Section/Value"
            )
            expected_declarations = validation._variable_declarations(root)
            expected_first_name = validation._first_text(root, "Name")
            expected_elements = validation._elements_by_local_name(root, "Value")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as elements_native_call:
                actual_values = validation._expression_field_values(
                    root, "Section/Value"
                )
                self.assertEqual(elements_native_call.call_count, 3)
                elements_native_call.assert_any_call(root, {"Section"}, ET.Element)
                for section in sections:
                    elements_native_call.assert_any_call(
                        section, {"Value"}, ET.Element
                    )
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as declaration_native_call:
                with patch.object(
                    native_acceleration,
                    "xml_first_element_by_name",
                    wraps=native_acceleration.xml_first_element_by_name,
                ) as first_native_call:
                    actual_declarations = validation._variable_declarations(root)
                    declaration_native_call.assert_called_once_with(
                        root,
                        {"anyType", "VariableDefinitionHelper"},
                        ET.Element,
                    )
                    self.assertEqual(first_native_call.call_count, 4)
            with patch.object(
                native_acceleration,
                "xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as first_native_call:
                actual_first_name = validation._first_text(root, "Name")
                first_native_call.assert_called_once_with(
                    root, "Name", ET.Element
                )
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as elements_native_call:
                actual_elements = validation._elements_by_local_name(root, "Value")
                elements_native_call.assert_called_once_with(
                    root, {"Value"}, ET.Element
                )

        self.assertEqual(actual_values, expected_values)
        self.assertEqual(actual_declarations, expected_declarations)
        self.assertEqual(actual_first_name, expected_first_name)
        self.assertEqual(actual_elements, expected_elements)
        self.assertEqual(actual_values, ["first", "second", "third"])
        self.assertEqual(
            actual_declarations, {"alpha": "Number", "beta": "Boolean"}
        )
        self.assertEqual(actual_first_name, "alpha")
        self.assertEqual(len(actual_elements), 3)

    def test_xcon_orientation_rows_use_native_selector_with_python_parity(self) -> None:
        from fluentcoder.catalog import xcon

        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        orientation = ET.fromstring(
            '<Orientation xmlns:ns="urn:test"><Matrix><Wrapper>'
            "<ns:ArrayOfdouble><ns:double>1</ns:double><ns:double>0</ns:double>"
            "<ns:double>0</ns:double></ns:ArrayOfdouble></Wrapper>"
            "<ns:ArrayOfdouble><ns:double>0</ns:double><ns:double>1</ns:double>"
            "<ns:double>0</ns:double></ns:ArrayOfdouble>"
            "<Wrapper><ns:ArrayOfdouble><ns:double>0</ns:double><ns:double>0</ns:double>"
            "<ns:double>1</ns:double></ns:ArrayOfdouble></Wrapper></Matrix></Orientation>"
        )
        matrix = orientation.find("Matrix")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = xcon._parse_orientation(orientation)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual = xcon._parse_orientation(orientation)
                native_call.assert_called_once_with(
                    matrix, {"ArrayOfdouble"}, ET.Element
                )

        self.assertEqual(actual, expected)
        self.assertEqual(
            actual, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        )

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

        expected = json.dumps(value, ensure_ascii=False, sort_keys=True)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration._native,
                "canonical_json",
                wraps=native_acceleration._native.canonical_json,
            ) as rust_call:
                self.assertEqual(fast_json_dumps(value), expected)
                rust_call.assert_called_once()
        with patch.object(native_acceleration, "_native", None):
            self.assertEqual(fast_json_dumps(value), expected)

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
