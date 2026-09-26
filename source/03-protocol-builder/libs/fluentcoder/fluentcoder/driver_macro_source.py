"""Detect driver-macro XML that the typed fields cannot represent.

Unknown siblings stay on ``raw_xml`` so a decompile, codegen, compile cycle
does not drop them. This is not a recovery-policy model and does not assign
vendor error codes.
"""

from __future__ import annotations

from . import xml_compat as ET

_KNOWN_MACRO_TAGS = {"ApplicationDriverMacro", "LegacyDriverMacro"}
_KNOWN_ATTRIBUTES = {
    "Version",
    "Name",
    "ModuleName",
    "ExecutionTime",
    "IsBreakpoint",
    "IsDisabledForExecution",
    "LineNumber",
}
_KNOWN_CHILDREN = {"ExecutionSettings", "AvailableID"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def application_driver_macro_has_unmodeled_source(raw_xml: str | None) -> bool:
    """True when source XML has structure the typed macro fields do not cover."""
    if raw_xml is None or not raw_xml.strip():
        return False
    try:
        obj = ET.fromstring(raw_xml)
    except ET.ParseError:
        return True
    macros = [
        element
        for element in obj.iter()
        if isinstance(element.tag, str) and _local(element.tag) in _KNOWN_MACRO_TAGS
    ]
    if not macros:
        return True
    macro = macros[0]
    if any(name not in _KNOWN_ATTRIBUTES for name in macro.attrib):
        return True
    for child in list(macro):
        if isinstance(child.tag, str) and _local(child.tag) not in _KNOWN_CHILDREN:
            return True
    return False
