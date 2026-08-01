"""Safe XML helpers with stdlib ElementTree compatibility."""

from __future__ import annotations

from io import BytesIO
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator
import xml.etree.ElementTree as _StdET

from defusedxml import ElementTree as _SafeET
from defusedxml.common import DefusedXmlException


MAX_XML_BYTES = 4 * 1024 * 1024
MAX_XML_DEPTH = 256
ET = sys.modules[__name__]

Comment = _StdET.Comment
Element = _StdET.Element
ElementTree = _StdET.ElementTree
ParseError = _StdET.ParseError
ProcessingInstruction = _StdET.ProcessingInstruction
QName = _StdET.QName
SubElement = _StdET.SubElement
TreeBuilder = _StdET.TreeBuilder
XMLParser = _StdET.XMLParser
dump = _StdET.dump
iselement = _StdET.iselement
register_namespace = _StdET.register_namespace
tostring = _StdET.tostring

__all__ = [
    "MAX_XML_BYTES",
    "MAX_XML_DEPTH",
    "Comment",
    "Element",
    "ElementTree",
    "ParseError",
    "ProcessingInstruction",
    "QName",
    "SubElement",
    "TreeBuilder",
    "XMLParser",
    "ET",
    "XML",
    "dump",
    "fromstring",
    "fromstringlist",
    "iselement",
    "iterparse",
    "parse",
    "register_namespace",
    "tostring",
]


def _raise_parse_error(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        raise ParseError(message)
    raise ParseError(message) from exc


def _coerce_xml_bytes(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, bytearray):
        return bytes(payload)
    if isinstance(payload, memoryview):
        return payload.tobytes()
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise TypeError(f"XML payload must be str or bytes, got {type(payload).__name__}")


def _read_source_bytes(source: Any) -> bytes:
    reader = getattr(source, "read", None)
    if callable(reader):
        return _coerce_xml_bytes(reader())
    return Path(os.fsdecode(source)).read_bytes()


def _check_size_limit(data: bytes, *, max_bytes: int) -> None:
    if max_bytes >= 0 and len(data) > max_bytes:
        _raise_parse_error(f"XML document exceeds the safe size limit of {max_bytes} bytes")


def _parse_bytes(data: bytes, *, max_bytes: int, max_depth: int) -> Element:
    _check_size_limit(data, max_bytes=max_bytes)
    if max_depth <= 0:
        _raise_parse_error("XML nesting depth limit must be positive")

    depth = 0
    root: Element | None = None
    try:
        for event, elem in _SafeET.iterparse(BytesIO(data), events=("start", "end")):
            if event == "start":
                depth += 1
                if depth > max_depth:
                    _raise_parse_error(f"XML nesting depth exceeds the safe limit of {max_depth} levels")
                if root is None:
                    root = elem
            elif event == "end":
                depth = max(depth - 1, 0)
        if root is None:
            _raise_parse_error("XML document is empty")
        return root
    except ParseError:
        raise
    except DefusedXmlException as exc:
        _raise_parse_error(str(exc), exc)
    except Exception as exc:  # pragma: no cover - safety net
        _raise_parse_error(str(exc), exc)


def fromstring(
    text: str | bytes,
    parser: Any = None,
    *,
    max_bytes: int = MAX_XML_BYTES,
    max_depth: int = MAX_XML_DEPTH,
) -> Element:
    """Parse XML from a string or bytes payload."""
    del parser
    return _parse_bytes(_coerce_xml_bytes(text), max_bytes=max_bytes, max_depth=max_depth)


def fromstringlist(
    sequence: Iterable[str | bytes],
    parser: Any = None,
    *,
    max_bytes: int = MAX_XML_BYTES,
    max_depth: int = MAX_XML_DEPTH,
) -> Element:
    """Parse XML from a sequence of string fragments."""
    del parser
    data = b"".join(_coerce_xml_bytes(part) for part in sequence)
    return _parse_bytes(data, max_bytes=max_bytes, max_depth=max_depth)


def parse(
    source: Any,
    parser: Any = None,
    *,
    max_bytes: int = MAX_XML_BYTES,
    max_depth: int = MAX_XML_DEPTH,
) -> ElementTree:
    """Parse XML from a path or file-like object."""
    del parser
    root = _parse_bytes(_read_source_bytes(source), max_bytes=max_bytes, max_depth=max_depth)
    return ElementTree(root)


def iterparse(
    source: Any,
    events: Iterable[str] | None = None,
    parser: Any = None,
    *,
    max_bytes: int = MAX_XML_BYTES,
    max_depth: int = MAX_XML_DEPTH,
) -> Iterator[tuple[str, Element]]:
    """Stream XML parse events with size and nesting-depth limits."""
    del parser
    requested = tuple(events) if events is not None else ("end",)
    data = _read_source_bytes(source)
    _check_size_limit(data, max_bytes=max_bytes)
    if max_depth <= 0:
        _raise_parse_error("XML nesting depth limit must be positive")

    depth = 0
    try:
        for event, elem in _SafeET.iterparse(BytesIO(data), events=("start", "end")):
            if event == "start":
                depth += 1
                if depth > max_depth:
                    _raise_parse_error(f"XML nesting depth exceeds the safe limit of {max_depth} levels")
            elif event == "end":
                depth = max(depth - 1, 0)
            if event in requested:
                yield event, elem
    except ParseError:
        raise
    except DefusedXmlException as exc:
        _raise_parse_error(str(exc), exc)
    except Exception as exc:  # pragma: no cover - safety net
        _raise_parse_error(str(exc), exc)


XML = fromstring
