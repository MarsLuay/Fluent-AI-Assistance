from __future__ import annotations

import unittest

from fluent_pipeline import xml_compat as ET


class XmlCompatTests(unittest.TestCase):
    def test_wrapper_comes_from_shared_module(self) -> None:
        self.assertEqual(ET.fromstring.__module__, "tecan_common.xml_compat")
        self.assertEqual(ET.parse.__module__, "tecan_common.xml_compat")

    def test_fromstring_rejects_malformed_xml(self) -> None:
        with self.assertRaises(ET.ParseError):
            ET.fromstring("<root>")

    def test_fromstring_rejects_entity_xml(self) -> None:
        xml = '<!DOCTYPE root [<!ENTITY x "boom">]><root>&x;</root>'
        with self.assertRaises(ET.ParseError):
            ET.fromstring(xml)

    def test_fromstring_rejects_excessive_depth(self) -> None:
        xml = "<root><a><b><c /></b></a></root>"
        with self.assertRaises(ET.ParseError):
            ET.fromstring(xml, max_depth=3)

    def test_fromstring_rejects_oversized_document(self) -> None:
        xml = "<root>" + ("x" * 64) + "</root>"
        with self.assertRaises(ET.ParseError):
            ET.fromstring(xml, max_bytes=32)


if __name__ == "__main__":
    unittest.main()

