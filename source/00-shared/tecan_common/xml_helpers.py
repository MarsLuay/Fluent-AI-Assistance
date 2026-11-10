"""Small XML tree convenience helpers shared across Tecan readers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import xml_compat as ET
from .native_acceleration import (
    xml_all_unique_texts as _native_xml_all_unique_texts,
    xml_clean_text as _native_xml_clean_text,
    xml_find_attribute as _native_xml_find_attribute,
    xml_find_text as _native_xml_find_text,
    xml_local_name as _native_xml_local_name,
    xml_records_by_name as _native_xml_records_by_name,
    xml_texts_by_name as _native_xml_texts_by_name,
    xml_unique_texts as _native_xml_unique_texts,
)

__all__ = [
    "all_text",
    "all_text_values",
    "child_text",
    "direct_child_text",
    "first_attribute",
    "first_text",
    "local_name",
    "records_by_name",
    "texts_by_name",
    "unique_texts",
]


def local_name(tag: Any) -> str:
    """Return the namespace-free XML tag name."""
    native = _native_xml_local_name(tag)
    if native is not None:
        return native
    text = str(tag)
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _clean_text(value: Any) -> str:
    native = _native_xml_clean_text(value)
    if native is not None:
        return native
    return str(value or "").strip()


def first_attribute(
    root: ET.Element, name: str, attribute: str, *, default: str = ""
) -> str:
    """Return an attribute from the first descendant with the matching local tag."""
    native = _native_xml_find_attribute(root, name, attribute, default, ET.Element)
    if native is not None:
        return native
    for element in root.iter():
        if local_name(element.tag) == name:
            return element.attrib.get(attribute, default)
    return default


def first_text(root: ET.Element, name: str, *, default: str = "") -> str:
    """Return the first non-empty descendant text for a local tag name."""
    native = _native_xml_find_text(root, name, False, ET.Element)
    if native is not None:
        found, value = native
        return value if found else default
    for element in root.iter():
        if local_name(element.tag) == name:
            value = _clean_text(element.text)
            if value:
                return value
    return default


def child_text(parent: ET.Element, name: str, *, default: str = "") -> str:
    """Return the first non-empty direct-child text for a local tag name."""
    native = _native_xml_find_text(parent, name, True, ET.Element)
    if native is not None:
        found, value = native
        return value if found else default
    for child in list(parent):
        if local_name(child.tag) == name:
            value = _clean_text(child.text)
            if value:
                return value
    return default


def direct_child_text(parent: ET.Element, name: str, *, default: str = "") -> str:
    """Alias for callers that prefer explicit direct-child naming."""
    return child_text(parent, name, default=default)


def all_text(root: ET.Element, names: Iterable[str]) -> list[str]:
    """Return unique non-empty descendant text values for local tag names."""
    wanted = set(names)
    native = _native_xml_unique_texts(root, wanted, (1 << 63) - 1, ET.Element)
    if native is not None:
        return native
    values: list[str] = []
    for element in root.iter():
        if local_name(element.tag) not in wanted:
            continue
        value = _clean_text(element.text)
        if value and value not in values:
            values.append(value)
    return values


def all_text_values(root: ET.Element) -> list[str]:
    """Return first-seen unique non-empty text from every XML element."""
    native = _native_xml_all_unique_texts(root, ET.Element)
    if native is not None:
        return native
    values: list[str] = []
    for element in root.iter():
        value = (element.text or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def texts_by_name(root: ET.Element, names: Iterable[str]) -> dict[str, list[str]]:
    """Return unique non-empty descendant text grouped by local tag name."""
    wanted = set(names)
    names_in_order = list(wanted)
    native = _native_xml_texts_by_name(root, names_in_order, ET.Element)
    if native is not None:
        return dict(zip(names_in_order, native))
    out: dict[str, list[str]] = {name: [] for name in wanted}
    for element in root.iter():
        name = local_name(element.tag)
        if name not in wanted:
            continue
        value = _clean_text(element.text)
        if value and value not in out[name]:
            out[name].append(value)
    return out


def records_by_name(
    root: ET.Element,
    record_name: str,
    field_names: Iterable[str],
    *,
    direct: bool = False,
) -> list[list[str]]:
    """Extract descendant text fields from each element with the local tag."""
    names = tuple(field_names)
    native = _native_xml_records_by_name(
        root, record_name, names, ET.Element, direct=direct
    )
    if native is not None:
        return native
    records: list[list[str]] = []
    for element in root.iter():
        if local_name(element.tag) == record_name:
            if direct:
                records.append([child_text(element, name) for name in names])
            else:
                records.append([first_text(element, name) for name in names])
    return records


def unique_texts(root: ET.Element, names: Iterable[str], *, limit: int = 200) -> list[str]:
    """Return unique non-empty descendant text values up to a limit."""
    wanted = set(names)
    native = _native_xml_unique_texts(root, wanted, limit, ET.Element)
    if native is not None:
        return native
    values: list[str] = []
    for element in root.iter():
        if local_name(element.tag) not in wanted:
            continue
        value = _clean_text(element.text)
        if not value or value in values:
            continue
        values.append(value)
        if len(values) >= limit:
            break
    return values
