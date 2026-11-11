from __future__ import annotations

import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import tecan_reader.zeia_adapters as zeia_adapters_module
from tecan_common import native_acceleration
from tecan_common.xml_compat import MAX_XML_BYTES
from tecan_reader.zeia_adapters import (
    ZeiaFormatError,
    ingest_zeia,
    probe_zeia,
)


LEGACY_SCRIPT = """<?xml version="1.0"?>
<Root>
  <ObjectName>LegacyScript</ObjectName>
  <Script version="1.0" />
  <FutureField>retained</FutureField>
</Root>
"""

VISIONX_SCRIPT = """<?xml version="1.0"?>
<VxData xmlns="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="3">
  <Payload><ObjectName>VisionXScript</ObjectName><Script version="3.0" /></Payload>
</VxData>
"""


class TestObjectKindClassification(unittest.TestCase):
    def test_native_classification_matches_python_for_exact_strings(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "zeia_object_kind_from_suffix"
        ):
            self.skipTest("optional Rust ZEIA suffix classifier is not built")
        suffixes = [
            ".xcmp",
            ".xwsp",
            ".xlqc",
            ".xlcp",
            ".xsit",
            ".xcon",
            ".xml",
            "..custom",
            "",
            "...",
            ".XCMP",
            ".Custom",
            ".é",
            "\ud800",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [zeia_adapters_module._object_kind(suffix) for suffix in suffixes]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                zeia_adapters_module,
                "_native_zeia_object_kind_from_suffix",
                wraps=native_acceleration.zeia_object_kind_from_suffix,
            ) as native_call:
                actual = [zeia_adapters_module._object_kind(suffix) for suffix in suffixes]
                self.assertEqual(native_call.call_count, len(suffixes))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                "component",
                "workspace",
                "liquid_class",
                "liquid_class_map",
                "site",
                "connector",
                "xml",
                "custom",
                "xml",
                "xml",
                "XCMP",
                "Custom",
                "é",
                "\ud800",
            ],
        )

    def test_string_subclass_uses_python_fallback(self) -> None:
        class StringSubclass(str):
            pass

        suffix = StringSubclass(".xcmp")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.zeia_object_kind_from_suffix(suffix))
            self.assertEqual(zeia_adapters_module._object_kind(suffix), "component")


class TestEligibleMemberCounts(unittest.TestCase):
    def test_native_suffix_group_counts_match_python(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "count_string_groups"
        ):
            self.skipTest("optional Rust suffix counter is not built")
        entries = [
            "Scripts/main.XSCR",
            "scripts/other.xscr",
            "Objects/worktable.XWSP",
            "Assets/image.JpG",
            "lists/transfers.GWL",
            "inputs/task.TWL",
            "README.txt",
            ".hidden",
            "trailing.",
            "...",
            Path("folder/path-object.xscr"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = zeia_adapters_module._eligible_member_counts(entries)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                zeia_adapters_module,
                "_native_count_string_groups",
                wraps=native_acceleration.count_string_groups,
            ) as native_call:
                actual = zeia_adapters_module._eligible_member_counts(entries)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            {"scripts": 3, "objects": 2, "worklists": 1, "task_inputs": 1},
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.count_string_groups(
                    [".xscr", ".jpg", ".xscr"],
                    {"scripts": [".xscr"], "assets": [".jpg"]},
                ),
                {"scripts": 2, "assets": 1},
            )

    def test_subclass_inputs_keep_python_iteration_behavior(self) -> None:
        class EntryList(list):
            pass

        entries = EntryList(["file.xscr", "file.gwl"])
        expected = {"scripts": 1, "objects": 0, "worklists": 1, "task_inputs": 0}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                zeia_adapters_module,
                "_native_count_string_groups",
                wraps=native_acceleration.count_string_groups,
            ) as native_call:
                self.assertEqual(
                    zeia_adapters_module._eligible_member_counts(entries), expected
                )
                native_call.assert_not_called()


class ZeiaAdapterTests(unittest.TestCase):
    def _archive(self, root: Path, name: str, entries: dict[str, str]) -> Path:
        path = root / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, content in entries.items():
                archive.writestr(entry, content)
        return path

    def test_structurally_different_exports_share_canonical_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = self._archive(root, "legacy.zeia", {"Scripts/main.xscr": LEGACY_SCRIPT})
            visionx = self._archive(root, "visionx.zeia", {"DataStore/UserSpecific/main.xscr": VISIONX_SCRIPT})

            legacy_model = ingest_zeia(legacy)
            visionx_model = ingest_zeia(visionx)

        self.assertEqual(legacy_model.schema_version, visionx_model.schema_version)
        self.assertEqual(legacy_model.adapter_id, "structured-zeia")
        self.assertEqual(visionx_model.adapter_id, "visionx-datastore-v3")
        self.assertEqual(set(legacy_model.scripts[0]), set(visionx_model.scripts[0]))
        self.assertEqual(legacy_model.scripts[0]["kind"], "script")
        self.assertEqual(visionx_model.scripts[0]["kind"], "script")
        self.assertEqual(
            legacy_model.scripts[0]["source_metadata"]["unknown_fields"]["FutureField"],
            ["retained"],
        )
        self.assertEqual(
            legacy_model.scripts[0]["provenance"]["entry_path"],
            "Scripts/main.xscr",
        )

    def test_versioned_visionx_variants_are_selected_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v2 = self._archive(
                root,
                "visionx-v2.zeia",
                {"DataStore/v2.xscr": '<VxData dataStoreVersion="2"><ObjectName>Two</ObjectName><Script /></VxData>'},
            )
            v3 = self._archive(
                root,
                "visionx-v3.zeia",
                {"DataStore/v3.xscr": '<VxData dataStoreVersion="3"><ObjectName>Three</ObjectName><Script /></VxData>'},
            )

            v2_detection = probe_zeia(v2)
            v3_detection = probe_zeia(v3)

        self.assertEqual(v2_detection.status, "supported")
        self.assertEqual(v2_detection.selected.adapter_id, "visionx-datastore-v2")
        self.assertEqual(v3_detection.status, "supported")
        self.assertEqual(v3_detection.selected.adapter_id, "visionx-datastore-v3")

    def test_gwl_probe_uses_native_ascii_match_and_python_unicode_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ascii_archive = self._archive(
                root,
                "ascii-worklist.zeia",
                {"Worklists/records.gwl": "  d1;destination\r\n"},
            )
            unicode_archive = self._archive(
                root,
                "unicode-worklist.zeia",
                {"Worklists/records.gwl": "\u00a0A;source\n"},
            )
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(
                    zeia_adapters_module,
                    "_native_has_gwl_record_prefix",
                    wraps=native_acceleration.has_gwl_record_prefix,
                ) as native_call:
                    ascii_probe = probe_zeia(ascii_archive)
                    unicode_probe = probe_zeia(unicode_archive)
                    self.assertEqual(native_call.call_count, 2)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                ascii_fallback_probe = probe_zeia(ascii_archive)
                unicode_fallback_probe = probe_zeia(unicode_archive)

        self.assertEqual(ascii_probe.status, ascii_fallback_probe.status)
        self.assertEqual(unicode_probe.status, unicode_fallback_probe.status)
        self.assertEqual(ascii_probe.status, "supported")
        self.assertEqual(unicode_probe.status, "supported")
        if native_acceleration.available():
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                pattern = r"^\s*[ADWB](?:[1-4])?;"
                flags = re.IGNORECASE | re.MULTILINE
                for codepoint in range(128):
                    whitespace = chr(codepoint)
                    if whitespace.isspace():
                        candidate = f"{whitespace}A;"
                        self.assertEqual(
                            native_acceleration.has_gwl_record_prefix(candidate),
                            re.search(pattern, candidate, flags) is not None,
                            repr(candidate),
                        )
                for candidate in (
                    "A;source",
                    "d1;destination",
                    "foo\n \tW4;wash",
                    "foo\rD;not-a-line-start",
                    "A5;wrong-digit",
                    "A ;missing-semicolon-position",
                    "B2x;extra-character",
                ):
                    self.assertEqual(
                        native_acceleration.has_gwl_record_prefix(candidate),
                        re.search(pattern, candidate, flags) is not None,
                        repr(candidate),
                    )
                self.assertIsNone(native_acceleration.has_gwl_record_prefix("\u00a0A;source"))

    def test_version_probe_preserves_regex_ordering_and_unicode_fallback(self) -> None:
        text = (
            "xDATASTOREVERSION = '3' <VxData dataStoreVersion=\"2\"> "
            "dataStoreVersion='' dataStoreVersion=missing "
            "DATASTOREVERSION\t=\t'legacy value'"
        )
        pattern = r"dataStoreVersion\s*=\s*[\"']([^\"']+)"
        expected = tuple(sorted(set(re.findall(pattern, text, re.IGNORECASE))))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_values = native_acceleration.extract_data_store_versions(text)
        if native_acceleration.available():
            self.assertIsNotNone(native_values)
            self.assertEqual(tuple(sorted(set(native_values or []))), expected)
            self.assertIsNone(
                native_acceleration.extract_data_store_versions(
                    "dataStoreVersion\u00a0=\"3\""
                )
            )

        with tempfile.TemporaryDirectory() as tmp:
            unicode_archive = self._archive(
                Path(tmp),
                "unicode-version.zeia",
                {
                    "DataStore/v3.xscr": (
                        '<VxData dataStoreVersion\u00a0= "3">'
                        "<ObjectName>Three</ObjectName><Script /></VxData>"
                    )
                },
            )
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(
                    zeia_adapters_module,
                    "_native_extract_data_store_versions",
                    wraps=native_acceleration.extract_data_store_versions,
                ) as native_call:
                    detection = probe_zeia(unicode_archive)
                    self.assertEqual(native_call.call_count, 1)
        self.assertEqual(detection.status, "supported")
        self.assertEqual(detection.selected.adapter_id, "visionx-datastore-v3")

    def test_visionx_marker_matches_python_regex_and_native_probe(self) -> None:
        pattern = r"tecan\.com/TSCC/VisionX|<[^>]*:?VxData(?:\s|>)"
        samples = (
            "http://www.tecan.com/TSCC/VisionX/DataStore",
            "<VxData>",
            "<x:VxData\n attr>",
            "<Object Value='VxData'>",
            "<VxData/>tail",
            "<VxDatax>",
            "VxData>",
            "<Object Value='VxData\u00a0'>",
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            for sample in samples:
                native = native_acceleration.has_visionx_namespace(sample)
                if sample.isascii() and native_acceleration.available():
                    expected = bool(re.search(pattern, sample, re.IGNORECASE))
                    self.assertEqual(native, expected, repr(sample))
                else:
                    self.assertIsNone(native, repr(sample))

            with tempfile.TemporaryDirectory() as tmp:
                archive = self._archive(
                    Path(tmp),
                    "visionx-probe.zeia",
                    {"DataStore/UserSpecific/main.xscr": VISIONX_SCRIPT},
                )
                with patch.object(
                    zeia_adapters_module,
                    "_native_has_visionx_namespace",
                    wraps=native_acceleration.has_visionx_namespace,
                ) as native_call:
                    accelerated = probe_zeia(archive)
                    self.assertEqual(native_call.call_count, 1)
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                    fallback = probe_zeia(archive)
        self.assertEqual(accelerated.status, fallback.status)
        self.assertEqual(accelerated.selected.adapter_id, fallback.selected.adapter_id)

    def test_structural_probe_flags_match_python_regex_and_fallback(self) -> None:
        object_name = r"<[^>]*:?ObjectName\b[^>]*>"
        object_name_with_text = r"<[^>]*:?ObjectName\b[^>]*>[^<]+</[^>]*:?ObjectName>"
        script_tag = r"<[^>]*:?Script(?:\s|>)"
        object_type = r"<[^>]*:?Object\s+[^>]*Type="
        samples = (
            "<ObjectName>foo</ObjectName><Script />",
            "<ObjectName/>foo</FakeObjectName><x:Script\n>",
            "<ObjectNameX>foo</ObjectName><Script>",
            "<ObjectName>foo<Other></ObjectName><Script>",
            "<Thing><Object Type='A'/></Thing>",
            "<Object\tType=foo",
            "<Object Type = 'x'>",
            "<ObjectX Type=foo>",
            "<Object Value='Script'>",
            "<Script\tSomething>",
            "<ScriptX>",
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            for sample in samples:
                expected = (
                    bool(
                        re.search(object_name_with_text, sample, re.IGNORECASE)
                        and re.search(script_tag, sample, re.IGNORECASE)
                    ),
                    bool(
                        re.search(object_name, sample, re.IGNORECASE)
                        or re.search(object_type, sample, re.IGNORECASE)
                    ),
                )
                native = native_acceleration.zeia_xml_structural_flags(sample)
                if native_acceleration.available():
                    self.assertEqual(native, expected, repr(sample))
                else:
                    self.assertIsNone(native, repr(sample))

            self.assertIsNone(
                native_acceleration.zeia_xml_structural_flags(
                    "<ObjectName>café</ObjectName><Script>"
                )
            )
            with tempfile.TemporaryDirectory() as tmp:
                archive = self._archive(
                    Path(tmp),
                    "structure-probe.zeia",
                    {"Scripts/main.xscr": LEGACY_SCRIPT},
                )
                with patch.object(
                    zeia_adapters_module,
                    "_native_zeia_xml_structural_flags",
                    wraps=native_acceleration.zeia_xml_structural_flags,
                ) as native_call:
                    accelerated = probe_zeia(archive)
                    self.assertEqual(native_call.call_count, 1)
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                    fallback = probe_zeia(archive)
        self.assertEqual(accelerated.status, fallback.status)
        self.assertEqual(accelerated.selected.adapter_id, fallback.selected.adapter_id)

    def test_oversized_xscr_object_type_count_matches_python_regex(self) -> None:
        pattern = r"<Object\b[^>]*\bType=['\"]([^'\"]+)"
        samples = (
            "<Object Type='T1' /><object TYPE=\"T2\" />",
            "<ObjectType='bad'>",
            "<Object Type = 'bad'>",
            '<Object data="Type=\'inside\'">',
            "<Object Type='first' x='y' Type='last'>",
            "<Object Type='unterminated",
            "<Object Type=''>",
            "<Object\nTYPE='line'>",
            "<Object Type='one'><Object Type='two'>",
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            if native_acceleration.available():
                for sample in samples:
                    self.assertEqual(
                        native_acceleration.count_xscr_object_type_attributes(sample),
                        len(re.findall(pattern, sample, re.IGNORECASE)),
                        repr(sample),
                    )
                self.assertIsNone(
                    native_acceleration.count_xscr_object_type_attributes(
                        "<Object Type='café'>"
                    )
                )

            oversized_text = "<Root><Object Type='A'/><Object Type='B'/></Root>"
            with patch.object(
                zeia_adapters_module,
                "_native_count_xscr_object_type_attributes",
                wraps=native_acceleration.count_xscr_object_type_attributes,
            ) as native_call:
                accelerated = zeia_adapters_module._inspect_script_member(
                    oversized_text.encode(),
                    "Scripts/example.xscr",
                    size_bytes=MAX_XML_BYTES + 1,
                )
                self.assertEqual(native_call.call_count, 1)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                fallback = zeia_adapters_module._inspect_script_member(
                    oversized_text.encode(),
                    "Scripts/example.xscr",
                    size_bytes=MAX_XML_BYTES + 1,
                )

        self.assertEqual(accelerated["command_count"], 2)
        self.assertEqual(accelerated["command_count"], fallback["command_count"])

    def test_xml_scalar_probe_matches_python_regex_and_unicode_fallback(self) -> None:
        name = "Name"
        pattern = (
            rf"<(?:[A-Za-z_][\w.-]*:)?{re.escape(name)}"
            rf"(?:\s[^>]*)?>(.*?)</(?:[A-Za-z_][\w.-]*:)?{re.escape(name)}>"
        )
        samples = (
            "<Name>  alpha\tbeta\n</Name>",
            "<x:Name a='1'>first</bad:Name><Name>second</Name>",
            "prefix <NameX>wrong</NameX><Name>later</Name>",
            "<Name/>ignored</Name>",
            "<Name>unterminated",
            "<Name>outer <Name>inner</Name>",
            "<Name a='>'>value</Name>",
            "<1x:Name>bad prefix</Name>",
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            for sample in samples:
                match = re.search(pattern, sample, re.IGNORECASE | re.DOTALL)
                expected = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
                native = native_acceleration.xml_regex_first_value(sample, name)
                if native_acceleration.available():
                    self.assertEqual(native, expected, repr(sample))
                else:
                    self.assertIsNone(native, repr(sample))
                self.assertEqual(zeia_adapters_module._xml_value(sample, name), expected)

            unicode_text = "<Name> café </Name>"
            self.assertIsNone(native_acceleration.xml_regex_first_value(unicode_text, name))
            with patch.object(
                zeia_adapters_module,
                "_native_collapse_ascii_whitespace",
                wraps=native_acceleration.collapse_ascii_whitespace,
            ) as collapse_call:
                self.assertEqual(zeia_adapters_module._xml_value(unicode_text, name), "café")
                self.assertEqual(collapse_call.call_count, 1)

            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                with patch.object(
                    zeia_adapters_module,
                    "_native_collapse_ascii_whitespace",
                    wraps=native_acceleration.collapse_ascii_whitespace,
                ) as collapse_call:
                    self.assertEqual(zeia_adapters_module._xml_value(unicode_text, name), "café")
                    self.assertEqual(collapse_call.call_count, 1)
                for sample in samples:
                    match = re.search(pattern, sample, re.IGNORECASE | re.DOTALL)
                    expected = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
                    self.assertEqual(zeia_adapters_module._xml_value(sample, name), expected)

            with patch.object(
                zeia_adapters_module,
                "_native_xml_regex_first_value",
                wraps=native_acceleration.xml_regex_first_value,
            ) as native_call:
                self.assertEqual(
                    zeia_adapters_module._xml_value("<Name>native</Name>", name),
                    "native",
                )
                self.assertEqual(native_call.call_count, 1)

    def test_unknown_xml_field_scan_matches_regex_order_limits_and_fallback(self) -> None:
        repeated = "".join(f"<Repeated>value{index}</Repeated>" for index in range(22))
        text = (
            "<Root><ObjectName>skip</ObjectName>"
            "<Odd>outer <Inner>not independently matched</Inner></Odd>"
            "<Keep> alpha\t beta </keep><Keep>alpha beta</Keep>"
            "<keep>lowercase name</keep><Empty> \n </Empty>"
            "<After>retained</After><x:Prefixed attr='x'>value</Prefixed>"
            + repeated
        )
        known = ["ObjectName"]
        regex = re.compile(
            r"<(?:(?:[A-Za-z_][\w.-]*):)?([A-Za-z_][\w.-]*)(?:\s[^>]*)?>(.*?)</\1>",
            re.IGNORECASE | re.DOTALL,
        )
        reference: dict[str, list[str]] = {}
        for match in regex.finditer(text):
            name, raw_value = match.groups()
            value = re.sub(r"\s+", " ", raw_value).strip()
            if not value or "<" in value or name in known:
                continue
            bucket = reference.setdefault(name, [])
            if value not in bucket and len(bucket) < 20:
                bucket.append(value)
        expected = list(sorted(reference.items()))

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native = native_acceleration.xml_unknown_text_fields(text, known, 20)
            if native_acceleration.available():
                self.assertEqual(native, expected)
            else:
                self.assertIsNone(native)
            with patch.object(
                zeia_adapters_module,
                "_native_xml_unknown_text_fields",
                wraps=native_acceleration.xml_unknown_text_fields,
            ) as native_call:
                accelerated = zeia_adapters_module._unknown_fields_from_text(text)
                self.assertEqual(native_call.call_count, 1)
            unicode_text = "<Root><FutureField> café </FutureField>"
            self.assertIsNone(
                native_acceleration.xml_unknown_text_fields(unicode_text, known, 20)
            )
            with patch.object(
                zeia_adapters_module,
                "_native_collapse_ascii_whitespace",
                wraps=native_acceleration.collapse_ascii_whitespace,
            ) as collapse_call:
                self.assertEqual(
                    zeia_adapters_module._unknown_fields_from_text(unicode_text),
                    {"FutureField": ["café"]},
                )
                self.assertEqual(collapse_call.call_count, 1)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                fallback = zeia_adapters_module._unknown_fields_from_text(text)

        self.assertEqual(dict(expected), accelerated)
        self.assertEqual(accelerated, fallback)
        self.assertEqual(accelerated["Repeated"], [f"value{index}" for index in range(20)])

    def test_native_unknown_field_scanner_matches_tag_name_edge_matrix(self) -> None:
        regex = re.compile(
            r"<(?:(?:[A-Za-z_][\w.-]*):)?([A-Za-z_][\w.-]*)(?:\s[^>]*)?>(.*?)</\1>",
            re.IGNORECASE | re.DOTALL,
        )
        samples = [
            f"<{prefix}{name} attr='x'> value\t{index} </{name.lower()}>"
            for index, name in enumerate(("A", "_Leading", "Name2", "Dotted.Name", "Dash-Name"))
            for prefix in ("", "ns:", "ns_1:", "ns.part-2:")
        ]
        samples.extend(
            [
                "<Name data='>'>value</Name>",
                "<Name/>ignored</Name>",
                "<Outer>before <Thing>inner</Thing>",
                "<Outer>before <Thing>inner</Thing></Outer>",
                "<Repeated>one</Repeated><Repeated>two</Repeated><Repeated>three</Repeated>",
            ]
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            if not native_acceleration.available():
                self.skipTest("optional native extension is unavailable")
            for sample in samples:
                values: dict[str, list[str]] = {}
                for match in regex.finditer(sample):
                    name, raw = match.groups()
                    value = re.sub(r"\s+", " ", raw).strip()
                    if not value or "<" in value:
                        continue
                    bucket = values.setdefault(name, [])
                    if value not in bucket and len(bucket) < 2:
                        bucket.append(value)
                actual = native_acceleration.xml_unknown_text_fields(sample, [], 2)
                self.assertEqual(actual, list(sorted(values.items())), repr(sample))

            self.assertIsNone(
                native_acceleration.xml_unknown_text_fields(
                    "<Name>café</Name>", [], 2
                )
            )

    def test_worklists_are_canonical_entities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(
                Path(tmp),
                "worklist-only.zeia",
                {
                    "Worklists/transfers.gwl": (
                        "A;Source;;Plate Carrier 1;1;;10;Water Free Single\n"
                        "D;Destination;;Plate Carrier 1;1;;10;Water Free Single\n"
                    )
                },
            )
            model = ingest_zeia(archive)

        self.assertEqual(model.adapter_id, "structured-zeia")
        self.assertEqual(len(model.worklists), 1)
        self.assertEqual(model.worklists[0]["kind"], "worklist")
        self.assertEqual(
            model.worklists[0]["provenance"]["entry_path"],
            "Worklists/transfers.gwl",
        )

    def test_unsupported_and_ambiguous_exports_fail_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsupported = self._archive(root, "unknown.zeia", {"README.txt": "not a ZEIA"})
            ambiguous = self._archive(
                root,
                "ambiguous.zeia",
                {
                    "DataStore/v2.xscr": '<VxData dataStoreVersion="2"><ObjectName>Two</ObjectName><Script /></VxData>',
                    "DataStore/v3.xscr": '<VxData dataStoreVersion="3"><ObjectName>Three</ObjectName><Script /></VxData>',
                },
            )

            unsupported_result = probe_zeia(unsupported)
            ambiguous_result = probe_zeia(ambiguous)

            with self.assertRaises(ZeiaFormatError) as error:
                ingest_zeia(ambiguous)

        self.assertEqual(unsupported_result.status, "unsupported")
        self.assertIn("no supported ZEIA structural evidence", unsupported_result.diagnostics[0])
        self.assertEqual(ambiguous_result.status, "ambiguous")
        self.assertEqual(error.exception.result.status, "ambiguous")
        self.assertIn("visionx-datastore-v2", str(error.exception))
        self.assertIn("visionx-datastore-v3", str(error.exception))

    def test_known_malformed_export_is_reported_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(
                Path(tmp),
                "malformed.zeia",
                {"DataStore/UserSpecific/bad.xscr": "<broken"},
            )
            model = ingest_zeia(archive)

        self.assertEqual(model.detection["status"], "supported")
        self.assertEqual(len(model.scripts), 0)
        self.assertEqual(model.errors[0]["entry"], "DataStore/UserSpecific/bad.xscr")
        self.assertIn("ParseError", model.errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
