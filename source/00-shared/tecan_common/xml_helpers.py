"""Small XML tree convenience helpers shared across Tecan readers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import xml_compat as ET
from .native_acceleration import (
    xml_clean_text as _native_xml_clean_text,
    xml_find_text as _native_xml_find_text,
    xml_local_name as _native_xml_local_name,
    xml_texts_by_name as _native_xml_texts_by_name,
    xml_unique_texts as _native_xml_unique_texts,
)

__all__ = [
    "all_text",
    "child_text",
    "direct_child_text",
    "first_text",
    "local_name",
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
