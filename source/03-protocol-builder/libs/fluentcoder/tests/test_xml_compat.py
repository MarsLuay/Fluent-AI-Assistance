from __future__ import annotations

import pytest

from fluentcoder import xml_compat as ET


def test_wrapper_comes_from_shared_module() -> None:
    assert ET.fromstring.__module__ == "tecan_common.xml_compat"
    assert ET.parse.__module__ == "tecan_common.xml_compat"


@pytest.mark.parametrize(
    "xml",
    [
        "<root>",
        '<!DOCTYPE root [<!ENTITY x "boom">]><root>&x;</root>',
    ],
)
def test_fromstring_rejects_bad_xml(xml: str) -> None:
    with pytest.raises(ET.ParseError):
        ET.fromstring(xml)


def test_fromstring_rejects_excessive_depth() -> None:
    xml = "<root><a><b><c /></b></a></root>"
    with pytest.raises(ET.ParseError):
        ET.fromstring(xml, max_depth=3)


def test_fromstring_rejects_oversized_document() -> None:
    xml = "<root>" + ("x" * 64) + "</root>"
    with pytest.raises(ET.ParseError):
        ET.fromstring(xml, max_bytes=32)

