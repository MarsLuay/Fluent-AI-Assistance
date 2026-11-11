"""Normalize and diff FC command XML for golden validation."""

from __future__ import annotations

from dataclasses import dataclass
import html
import re
from .. import xml_compat as ET
from tecan_common.native_acceleration import (
    ascii_casefold_trim as _native_ascii_casefold_trim,
    xml_compare_diff_hint_ascii as _native_xml_compare_diff_hint_ascii,
    xml_elements_by_name as _native_xml_elements_by_name,
    xml_local_name as _native_xml_local_name,
    xml_normalize_ascii_tail as _native_xml_normalize_ascii_tail,
    xml_normalize_line_numbers_ascii as _native_xml_normalize_line_numbers_ascii,
)


LINE_NUMBER_RE = re.compile(r"<LineNumber>\s*\d+\s*</LineNumber>", re.IGNORECASE)
WHITESPACE_RE = re.compile(r">\s+<")
NON_EXECUTABLE_OBJECT_TYPES = frozenset({"LabwareSectionInfo"})


@dataclass(frozen=True)
class CommandXmlCompareResult:
    equal: bool
    normalized_expected: str
    normalized_actual: str
    diff_hint: str = ""

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "equal": self.equal,
            "diff_hint": self.diff_hint,
        }


def normalize_command_xml(xml_text: str) -> str:
    """Normalize command XML for golden compare (ignore line numbers and whitespace)."""
    text = (xml_text or "").strip()
    if not text:
        return ""
    text = html.unescape(text)
    native = _native_xml_normalize_line_numbers_ascii(text)
    text = (
        native
        if native is not None
        else LINE_NUMBER_RE.sub("<LineNumber>0</LineNumber>", text)
    )
    native_tail = _native_xml_normalize_ascii_tail(text)
    if native_tail is not None:
        return native_tail
    text = WHITESPACE_RE.sub("><", text)
    text = re.sub(r"\s+", "", text)
    native_lower = _native_ascii_casefold_trim(text)
    lowered = native_lower if native_lower is not None else text.lower()
    return re.sub(r"<([a-z0-9_:.-]+)/>", r"<\1></\1>", lowered)


def compare_command_xml(expected: str, actual: str) -> CommandXmlCompareResult:
    norm_expected = normalize_command_xml(expected)
    norm_actual = normalize_command_xml(actual)
    equal = norm_expected == norm_actual
    diff_hint = ""
    if not equal:
        diff_hint = _native_xml_compare_diff_hint_ascii(norm_expected, norm_actual) or ""
        if not diff_hint:
            # Short hint for reports: first diverging tag pair.
            exp_tags = re.findall(r"<([A-Za-z0-9_:.-]+)>", norm_expected)
            act_tags = re.findall(r"<([A-Za-z0-9_:]+)>", norm_actual)
            for index, (exp_tag, act_tag) in enumerate(zip(exp_tags, act_tags)):
                if exp_tag != act_tag:
                    diff_hint = f"tag mismatch at index {index}: {exp_tag!r} vs {act_tag!r}"
                    break
            else:
                if len(exp_tags) != len(act_tags):
                    diff_hint = f"tag count mismatch: {len(exp_tags)} vs {len(act_tags)}"
                else:
                    diff_hint = "payload mismatch after normalization"
    return CommandXmlCompareResult(
        equal=equal,
        normalized_expected=norm_expected,
        normalized_actual=norm_actual,
        diff_hint=diff_hint,
    )


def _local_name(tag: str) -> str:
    native = _native_xml_local_name(tag)
    if native is not None:
        return native
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def extract_command_objects_from_xscr(xscr_text: str) -> list[dict[str, str]]:
    """Return compiled command ``Object`` nodes as ``{command_id, xml}`` records."""
    if not (xscr_text or "").strip():
        return []
    try:
        root = ET.fromstring(xscr_text)
    except ET.ParseError:
        return []
    records: list[dict[str, str]] = []
    elements = _native_xml_elements_by_name(root, ("Object",), ET.Element)
    if elements is None:
        elements = (
            element for element in root.iter() if _local_name(element.tag) == "Object"
        )
    for element in elements:
        object_type = str(element.attrib.get("Type") or "")
        if object_type.rsplit(".", 1)[-1] in NON_EXECUTABLE_OBJECT_TYPES:
            continue
        if ".Commands." not in object_type and "AddLabwareDataV1" not in object_type:
            if not any(
                marker in object_type
                for marker in (
                    "UserPromptStatement",
                    "RUPStandardStatement",
                    "RUPWorktableStatement",
                    "SubRoutineStatement",
                    "ApplicationDriverMacro",
                    "AddLabwareDataV1",
                    "CgaGetFingersScriptCommandDataV1",
                    "CgaDropFingersScriptCommandDataV1",
                    "SetVariableStatement",
                    "QueryVariableStatement",
                    "RUPVariableStatement",
                    "RemoveLabwareDataV1",
                    "SetLocationStatement",
                )
            ):
                continue
        command_id = object_type.rsplit(".", 1)[-1] if object_type else ""
        records.append(
            {
                "command_id": command_id,
                "object_type": object_type,
                "xml": ET.tostring(element, encoding="unicode"),
            }
        )
    return records
