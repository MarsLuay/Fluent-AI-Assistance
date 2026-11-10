"""Optional, fail-safe access to the compiled Rust acceleration kernels."""

from __future__ import annotations

from collections import (
    Counter as _Counter,
    OrderedDict as _OrderedDict,
    defaultdict as _DefaultDict,
)
import json
import os
from pathlib import Path
from typing import Any

_PathType = type(Path())

try:
    from tecan_native import _native
except (ImportError, OSError):  # pragma: no cover - depends on optional wheel
    _native = None


def enabled() -> bool:
    """Return whether the extension is present and not explicitly disabled."""
    disabled = os.environ.get("TECAN_DISABLE_RUST_ACCELERATION", "").strip().lower()
    return _native is not None and disabled not in {"1", "true", "yes", "on"}


def available() -> bool:
    """Report availability without changing the active feature flag."""
    return _native is not None


def parse_xml(data: bytes, max_bytes: int, max_depth: int) -> Any | None:
    """Try native XML parsing; callers retain the Python parser as authority."""
    if not enabled():
        return None
    try:
        return _native.parse_xml(data, max_bytes, max_depth)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        # Unsupported encodings and parser edge cases deliberately fall back.
        return None


def xml_local_name(tag: Any) -> str | None:
    """Return the namespace-free exact-string tag name natively when enabled."""
    if not enabled() or type(tag) is not str:
        return None
    try:
        result = _native.xml_tag_local_name(tag)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def xml_clean_text(value: Any) -> str | None:
    """Trim supported ASCII XML text natively, preserving Python fallback cases."""
    if not enabled():
        return None
    if value is None:
        text = ""
    elif (
        type(value) is str
        and value.isascii()
        and not any(0x1C <= ord(character) <= 0x1F for character in value)
    ):
        text = value
    else:
        return None
    try:
        result = _native.xml_clean_text(text)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def xml_container_child_texts(
    root: Any, container_name: Any, element_type: Any
) -> list[str] | None:
    """Collect direct child texts beneath matching XML containers when ASCII."""
    if (
        not enabled()
        or type(container_name) is not str
        or not container_name.isascii()
    ):
        return None
    try:
        result = _native.xml_container_child_texts(
            root, container_name, element_type
        )
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def collapse_ascii_whitespace(value: Any) -> str | None:
    """Collapse Python ASCII whitespace runs, falling back for Unicode text."""
    if not enabled():
        return None
    if value is None:
        text = ""
    elif type(value) is str and value.isascii():
        text = value
    else:
        return None
    try:
        result = _native.collapse_ascii_whitespace(text)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def xml_unique_texts(
    root: Any, names: Any, limit: Any, element_type: Any
) -> list[str] | None:
    """Collect supported standard-Element text natively; otherwise use Python."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(names) not in (set, frozenset, list, tuple)
        or not all(type(name) is str for name in names)
        or type(limit) is not int
        or not -(1 << 63) <= limit < (1 << 63)
    ):
        return None
    try:
        result = _native.xml_unique_texts(root, list(names), limit, element_type)
        if result is None:
            return None
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_texts_by_name(root: Any, names: Any, element_type: Any) -> list[list[str]] | None:
    """Collect grouped supported XML text natively; otherwise use Python."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(names) not in (list, tuple)
        or not all(type(name) is str for name in names)
        or len(set(names)) != len(names)
    ):
        return None
    try:
        result = _native.xml_texts_by_name(root, list(names), element_type)
        if (
            type(result) is list
            and len(result) == len(names)
            and all(
                type(group) is list and all(type(value) is str for value in group)
                for group in result
            )
        ):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_find_attribute(
    root: Any, name: Any, attribute: Any, default: Any, element_type: Any
) -> str | None:
    """Find an XML attribute on the first matching element, or fall back."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(name) is not str
        or type(attribute) is not str
        or type(default) is not str
    ):
        return None
    try:
        result = _native.xml_find_attribute(
            root, name, attribute, default, element_type
        )
        return result if type(result) is str else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_all_unique_texts(root: Any, element_type: Any) -> list[str] | None:
    """Collect first-seen unique supported XML text values natively."""
    if not enabled() or type(root) is not element_type:
        return None
    try:
        result = _native.xml_all_unique_texts(root, element_type)
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_elements_by_name(
    root: Any, names: Any, element_type: Any
) -> list[Any] | None:
    """Select standard XML elements with matching namespace-free names."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(names) not in (set, frozenset, list, tuple)
        or not all(type(name) is str for name in names)
    ):
        return None
    try:
        result = _native.xml_elements_by_name(root, list(names), element_type)
        if type(result) is list and all(type(element) is element_type for element in result):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_elements_by_folded_name_with_children(
    root: Any, names: Any, element_type: Any
) -> list[Any] | None:
    """Select ASCII local names case-insensitively when they have children."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(names) not in (set, frozenset, list, tuple)
        or not all(type(name) is str and name.isascii() for name in names)
    ):
        return None
    try:
        result = _native.xml_elements_by_folded_name_with_children(
            root, list(names), element_type
        )
        if type(result) is list and all(type(element) is element_type for element in result):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_variable_definition_elements(root: Any, element_type: Any) -> list[Any] | None:
    """Select supported startup-variable XML definitions in document order."""
    if not enabled() or type(root) is not element_type:
        return None
    try:
        result = _native.xml_variable_definition_elements(root, element_type)
        if type(result) is list and all(type(element) is element_type for element in result):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_grouped_texts_excluding(
    root: Any, excluded_names: Any, limit: Any, element_type: Any
) -> list[tuple[str, list[str]]] | None:
    """Group unique supported XML text by tag, excluding known local names."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(excluded_names) not in (set, frozenset, list, tuple)
        or not all(type(name) is str for name in excluded_names)
        or type(limit) is not int
        or not 0 <= limit < (1 << 63)
    ):
        return None
    try:
        result = _native.xml_grouped_texts_excluding(
            root, list(excluded_names), limit, element_type
        )
        if type(result) is not list:
            return None
        rows: list[tuple[str, list[str]]] = []
        for row in result:
            if (
                type(row) is not list
                or len(row) != 2
                or type(row[0]) is not str
                or type(row[1]) is not list
                or not all(type(value) is str for value in row[1])
            ):
                return None
            rows.append((row[0], row[1]))
        return rows
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_records_by_name(
    root: Any, record_name: Any, field_names: Any, element_type: Any, *, direct: Any = False
) -> list[list[str]] | None:
    """Extract bounded descendant text rows for matching XML records."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(record_name) is not str
        or type(field_names) not in (list, tuple)
        or not all(type(name) is str for name in field_names)
        or type(direct) is not bool
    ):
        return None
    try:
        result = _native.xml_records_by_name(
            root, record_name, list(field_names), direct, element_type
        )
        if (
            type(result) is list
            and all(
                type(row) is list
                and len(row) == len(field_names)
                and all(type(value) is str for value in row)
                for row in result
            )
        ):
            return result
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xml_find_text(
    root: Any, name: Any, direct: Any, element_type: Any
) -> tuple[bool, str | None] | None:
    """Find first supported XML text value natively, retaining Python fallback."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(name) is not str
        or type(direct) is not bool
    ):
        return None
    try:
        result = _native.xml_find_text(root, name, direct, element_type)
        if (
            type(result) is list
            and len(result) == 2
            and type(result[0]) is bool
            and ((result[0] and type(result[1]) is str) or (not result[0] and result[1] is None))
        ):
            return result[0], result[1]
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def render_append_list(title: Any, values: Any, limit: Any) -> list[str] | None:
    """Format supported list-report sections natively, otherwise request fallback."""
    if (
        not enabled()
        or type(title) is not str
        or type(values) not in (list, tuple)
        or not all(type(value) is str for value in values)
        or type(limit) is not int
        or not -(1 << 63) <= limit < (1 << 63)
    ):
        return None
    try:
        result = _native.render_append_list(title, list(values), limit)
        if type(result) is list and all(type(line) is str for line in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_append_dict(title: Any, values: Any, limit: Any) -> list[str] | None:
    """Format supported ordered report mappings natively with Python value strings."""
    if (
        not enabled()
        or type(title) is not str
        or type(values) is not dict
        or type(limit) is not int
        or not -(1 << 63) <= limit < (1 << 63)
    ):
        return None
    entries: list[tuple[str, str]] = []
    try:
        for key, value in values.items():
            if type(key) is not str or type(value) not in (str, bool, int, float, type(None)):
                return None
            entries.append((key, str(value)))
        result = _native.render_append_dict(title, entries, limit)
        if type(result) is list and all(type(line) is str for line in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_object_report(
    title: Any, fields: Any, names: Any, guids: Any
) -> str | None:
    """Render supported XML-object report sections natively; otherwise fall back."""
    if (
        not enabled()
        or type(title) is not str
        or type(fields) not in (list, tuple)
        or len(fields) != 5
        or not all(type(value) is str for value in fields)
        or type(names) not in (list, tuple)
        or not all(type(value) is str for value in names)
        or type(guids) not in (list, tuple)
        or not all(type(value) is str for value in guids)
    ):
        return None
    try:
        result = _native.render_object_report(
            title, list(fields), list(names), list(guids)
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_gwl_report(
    source: Any,
    line_count: Any,
    transfer_pairs_estimate: Any,
    record_counts: Any,
    examples: Any,
) -> str | None:
    """Render supported GWL summary data natively, or request Python fallback."""
    safe_values = (str, bool, int, float, type(None))
    if (
        not enabled()
        or type(source) not in safe_values
        or type(line_count) not in safe_values
        or type(transfer_pairs_estimate) not in safe_values
        or type(record_counts) is not dict
        or type(examples) not in (list, tuple)
    ):
        return None
    record_entries: list[tuple[str, str]] = []
    rendered_examples: list[list[str]] = []
    try:
        for key, value in record_counts.items():
            if type(key) is not str or type(value) not in safe_values:
                return None
            record_entries.append((key, str(value)))
        for example in examples:
            if type(example) is not dict:
                return None
            values = [
                example.get("line"),
                example.get("operation"),
                example.get("rack_label"),
                example.get("position"),
                example.get("volume"),
                example.get("liquid_class"),
            ]
            if not all(type(value) in safe_values for value in values):
                return None
            rendered_examples.append([str(value) for value in values])
        result = _native.render_gwl_report(
            str(source),
            str(line_count),
            str(transfer_pairs_estimate),
            record_entries,
            rendered_examples,
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_compare_report(
    left: Any,
    right: Any,
    left_entry_count: Any,
    right_entry_count: Any,
    common_script_count: Any,
    scripts_added: Any,
    scripts_removed: Any,
    left_families: Any,
    right_families: Any,
) -> str | None:
    """Render supported compare-report data natively, or request Python fallback."""
    safe_values = (str, bool, int, float, type(None))
    if (
        not enabled()
        or any(
            type(value) not in safe_values
            for value in (
                left,
                right,
                left_entry_count,
                right_entry_count,
                common_script_count,
            )
        )
        or type(scripts_added) not in (list, tuple)
        or not all(type(value) is str for value in scripts_added)
        or type(scripts_removed) not in (list, tuple)
        or not all(type(value) is str for value in scripts_removed)
        or type(left_families) is not dict
        or type(right_families) is not dict
    ):
        return None
    family_entries: list[list[tuple[str, str]]] = []
    try:
        for mapping in (left_families, right_families):
            entries: list[tuple[str, str]] = []
            for key, value in mapping.items():
                if type(key) is not str or type(value) not in safe_values:
                    return None
                entries.append((key, str(value)))
            family_entries.append(entries)
        result = _native.render_compare_report(
            str(left),
            str(right),
            str(left_entry_count),
            str(right_entry_count),
            str(common_script_count),
            list(scripts_added),
            list(scripts_removed),
            family_entries[0],
            family_entries[1],
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_xscr_report(report: Any) -> str | None:
    """Render supported XSCR summaries natively, leaving edge cases to Python."""
    if not enabled() or type(report) is not dict:
        return None
    safe_values = (str, bool, int, float, type(None))
    object_name = report.get("object_name")
    source = report.get("source")
    script_version = report.get("script_version")
    command_count = report.get("command_count")
    if not all(
        type(value) in safe_values
        for value in (object_name, source, script_version, command_count)
    ):
        return None
    mappings = [
        report.get("family_counts", {}),
        report.get("command_counts", {}),
    ]
    references = report.get("references", [])
    variables = report.get("variables", [])
    query_prompts = report.get("query_prompts", [])
    dependencies = report.get("dependencies", {})
    comments = report.get("comments", [])
    warnings = report.get("warnings", [])
    commands = report.get("commands", [])
    if (
        any(type(mapping) is not dict for mapping in mappings)
        or type(references) not in (list, tuple)
        or type(variables) not in (list, tuple)
        or type(query_prompts) not in (list, tuple)
        or type(dependencies) is not dict
        or type(comments) not in (list, tuple)
        or not all(type(value) in safe_values for value in comments)
        or type(warnings) not in (list, tuple)
        or not all(type(value) in safe_values for value in warnings)
        or type(commands) not in (list, tuple)
    ):
        return None
    try:
        count_sections: list[list[tuple[str, str]]] = []
        for mapping in mappings:
            entries: list[tuple[str, str]] = []
            for key, value in mapping.items():
                if type(key) is not str or type(value) not in safe_values:
                    return None
                entries.append((key, str(value)))
            count_sections.append(entries)

        reference_rows: list[list[str]] = []
        for reference in references[:20]:
            if type(reference) is not dict:
                return None
            values = [
                reference.get("type_id"),
                reference.get("object_name"),
                reference.get("guid"),
            ]
            if not all(type(value) in safe_values for value in values):
                return None
            reference_rows.append(
                [str(values[0] or "Reference"), str(values[1]), str(values[2])]
            )

        rendered_variables: list[str] = []
        for variable in variables:
            if type(variable) is not dict:
                return None
            name = variable.get("name")
            variable_type = variable.get("type")
            if type(name) not in safe_values or type(variable_type) not in safe_values:
                return None
            rendered_variables.append(
                f"{name} ({variable_type or 'unknown'})"
            )

        rendered_prompts: list[str] = []
        for prompt in query_prompts:
            if type(prompt) is not dict:
                return None
            values = [
                prompt.get("name"),
                prompt.get("prompt"),
                prompt.get("minimum"),
                prompt.get("maximum"),
            ]
            if not all(type(value) in safe_values for value in values):
                return None
            rendered_prompts.append(
                f"{values[0]}: {values[1]} [{values[2]}..{values[3]}]"
            )

        dependency_rows: list[list[str]] = []
        for key, values in dependencies.items():
            if type(key) is not str or type(values) not in (list, tuple):
                return None
            if not values:
                continue
            if not all(type(value) is str for value in values):
                return None
            unique_values = compact_list(values, limit=20)
            if unique_values is None:
                return None
            rendered_values = ", ".join(f"`{value}`" for value in unique_values)
            dependency_rows.append([key, rendered_values])

        command_rows: list[list[str]] = []
        for command in commands[:40]:
            if type(command) is not dict:
                return None
            name = command.get("name")
            line = command.get("line")
            index = command.get("index")
            command_type = command.get("type")
            family = command.get("family")
            if not all(
                type(value) in safe_values
                for value in (name, line, index, command_type, family)
            ):
                return None
            label = name
            if not label:
                fields = command.get("fields", {})
                if type(fields) is not dict:
                    return None
                comment = fields.get("Comment")
                if type(comment) not in safe_values:
                    return None
                label = comment or ""
            command_rows.append(
                [
                    str(index),
                    str(line or "?"),
                    str(command_type),
                    str(family),
                    str(label),
                ]
            )

        result = _native.render_xscr_report(
            [
                str(object_name or source),
                str(source),
                str(script_version or "unknown"),
                str(command_count),
            ],
            reference_rows,
            count_sections[0],
            count_sections[1],
            [str(value) for value in rendered_variables],
            [str(value) for value in rendered_prompts],
            bool(dependencies),
            dependency_rows,
            [str(value) for value in comments],
            [str(value) for value in warnings],
            command_rows,
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_zeia_report(report: Any) -> str | None:
    """Render supported ZEIA summary data natively, or request fallback."""
    if not enabled() or type(report) is not dict:
        return None
    safe_values = (str, bool, int, float, type(None))
    header = [
        report.get("source"),
        report.get("entry_count"),
        report.get("script_count_total"),
        report.get("script_count_summarized"),
        report.get("object_count_summarized"),
        report.get("gwl_count_summarized"),
    ]
    mappings = [
        report.get("extension_counts", {}),
        report.get("family_counts", {}),
        report.get("command_counts", {}),
        report.get("warning_counts", {}),
    ]
    script_names = report.get("script_names", [])
    scripts = report.get("scripts", [])
    errors = report.get("errors", [])
    if (
        not all(type(value) in safe_values for value in header)
        or any(type(mapping) is not dict for mapping in mappings)
        or type(script_names) not in (list, tuple)
        or not all(type(value) in safe_values for value in script_names)
        or type(scripts) not in (list, tuple)
        or type(errors) not in (list, tuple)
    ):
        return None
    try:
        count_sections: list[list[tuple[str, str]]] = []
        for mapping in mappings:
            entries: list[tuple[str, str]] = []
            for key, value in mapping.items():
                if type(key) is not str or type(value) not in safe_values:
                    return None
                entries.append((key, str(value)))
            count_sections.append(entries)
        script_rows: list[list[str]] = []
        for script in scripts[:40]:
            if type(script) is not dict:
                return None
            object_name = script.get("object_name")
            command_count = script.get("command_count")
            family_counts = script.get("family_counts", {})
            if (
                type(object_name) not in safe_values
                or type(command_count) not in safe_values
                or type(family_counts) is not dict
            ):
                return None
            row = [str(object_name), str(command_count)]
            for key, value in list(family_counts.items())[:5]:
                if type(key) not in safe_values or type(value) not in safe_values:
                    return None
                row.extend((str(key), str(value)))
            script_rows.append(row)
        error_rows: list[list[str]] = []
        for error in errors[:20]:
            if type(error) is not dict:
                return None
            values = [error.get("entry"), error.get("error")]
            if not all(type(value) in safe_values for value in values):
                return None
            error_rows.append([str(value) for value in values])
        result = _native.render_zeia_report(
            [str(value) for value in header],
            count_sections,
            [str(value) for value in script_names],
            script_rows,
            error_rows,
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_directory_report(report: Any) -> str | None:
    """Render supported directory summary data natively, or request fallback."""
    if not enabled() or type(report) is not dict:
        return None
    safe_values = (str, bool, int, float, type(None))
    header = [report.get("source"), report.get("file_count")]
    mappings = [
        report.get("extension_counts", {}),
        report.get("family_counts", {}),
        report.get("command_counts", {}),
        report.get("warning_counts", {}),
    ]
    script_names = report.get("script_names", [])
    items = report.get("items", [])
    if (
        not all(type(value) in safe_values for value in header)
        or any(type(mapping) is not dict for mapping in mappings)
        or type(script_names) not in (list, tuple)
        or not all(type(value) in safe_values for value in script_names)
        or type(items) not in (list, tuple)
    ):
        return None
    try:
        count_entries: list[list[tuple[str, str]]] = []
        for mapping in mappings:
            entries: list[tuple[str, str]] = []
            for key, value in mapping.items():
                if type(key) is not str or type(value) not in safe_values:
                    return None
                entries.append((key, str(value)))
            count_entries.append(entries)

        script_rows: list[list[str]] = []
        file_sources: list[str] = []
        for item in items:
            if type(item) is not dict:
                return None
            kind = item.get("kind")
            source = item.get("source", "")
            if type(kind) not in safe_values or type(source) not in safe_values:
                return None
            file_sources.append(str(source))
            if kind != "xscr" or len(script_rows) >= 80:
                continue
            object_name = item.get("object_name")
            command_count = item.get("command_count")
            script_families = item.get("family_counts", {})
            if (
                type(object_name) not in safe_values
                or type(command_count) not in safe_values
                or type(script_families) is not dict
            ):
                return None
            row = [str(object_name), str(command_count)]
            for key, value in list(script_families.items())[:5]:
                if type(key) not in safe_values or type(value) not in safe_values:
                    return None
                row.extend((str(key), str(value)))
            script_rows.append(row)

        result = _native.render_directory_report(
            [str(header[0]), str(header[1])],
            count_entries[0],
            count_entries[1],
            count_entries[2],
            count_entries[3],
            [str(value) for value in script_names],
            script_rows,
            file_sources,
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_project_index_summary(report: Any) -> str | None:
    """Render supported project-index summary data natively, or request fallback."""
    if not enabled() or type(report) is not dict:
        return None
    kind = report.get("kind")
    if type(kind) is not str:
        return None
    safe_values = (str, bool, int, float, type(None))
    fields = [
        report.get("database"),
        report.get("schema_version"),
        report.get("zeia_file_count"),
        report.get("script_count"),
        report.get("command_count"),
        report.get("command_sequence_count"),
        report.get("catalog_object_count"),
        report.get("worklist_count"),
    ]
    indexed_files = report.get("indexed_files", [])
    entity_counts = report.get("entity_counts", {})
    family_counts = report.get("command_family_counts", {})
    files = report.get("files", [])
    if (
        not all(type(value) in safe_values for value in fields)
        or type(indexed_files) not in (list, tuple)
        or not all(type(value) in safe_values for value in indexed_files)
        or type(entity_counts) is not dict
        or type(family_counts) is not dict
        or type(files) not in (list, tuple)
    ):
        return None
    file_rows: list[list[str]] = []
    try:
        count_entries: list[list[tuple[str, str]]] = []
        for mapping in (entity_counts, family_counts):
            entries: list[tuple[str, str]] = []
            for key, value in mapping.items():
                if type(key) is not str or type(value) not in safe_values:
                    return None
                entries.append((key, str(value)))
            count_entries.append(entries)
        for file_item in files[:100]:
            if type(file_item) is not dict:
                return None
            values = [
                file_item.get("file_name"),
                file_item.get("script_count_summarized"),
                file_item.get("object_count_summarized"),
                file_item.get("gwl_count_summarized"),
                file_item.get("path"),
            ]
            if not all(type(value) in safe_values for value in values):
                return None
            file_rows.append([str(value) for value in values])
        header = [
            "Tecan Project Index Build"
            if kind == "project_index_build"
            else "Tecan Project Index",
            *(str(value) for value in fields),
        ]
        result = _native.render_project_index_summary(
            header,
            [str(value) for value in indexed_files],
            count_entries[0],
            count_entries[1],
            file_rows,
            len(files),
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_project_index_search(report: Any) -> str | None:
    """Render supported project-index search data natively, or request fallback."""
    if not enabled() or type(report) is not dict:
        return None
    safe_values = (str, bool, int, float, type(None))
    header = [
        report.get("database"),
        report.get("query"),
        report.get("kind_filter"),
        report.get("result_count"),
    ]
    results = report.get("results", [])
    if (
        not all(type(value) in safe_values for value in header)
        or type(results) not in (list, tuple)
    ):
        return None
    rendered_results: list[list[str]] = []
    try:
        for result in results:
            if type(result) is not dict:
                return None
            values = [
                result.get("kind"),
                result.get("name"),
                result.get("value"),
                result.get("source_path"),
                result.get("script"),
                result.get("command_index"),
                result.get("zeia_file"),
            ]
            if not all(type(value) in safe_values for value in values):
                return None
            kind, name, value, source_path, script, command_index, zeia_file = values
            title = name or value or source_path
            rendered_results.append(
                [
                    str(kind),
                    str(title),
                    str(value) if value else "",
                    str(script) if script else "",
                    str(command_index) if command_index is not None else "",
                    str(zeia_file),
                    str(source_path),
                    "1" if command_index is not None else "0",
                ]
            )
        result = _native.render_project_index_search(
            str(header[0]),
            str(header[1]),
            str(header[2] or "any"),
            str(header[3]),
            rendered_results,
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_script_pattern_summary(report: Any) -> str | None:
    """Render supported script-pattern summary reports natively."""
    if type(report) is not dict:
        return None
    kind = report.get("kind")
    if type(kind) is not str:
        return None
    is_mining = kind == "script_pattern_mining"
    safe_values = (str, bool, int, float, type(None))
    fields = [
        report.get("database"),
        report.get("pattern_schema_version"),
        report.get("pattern_count"),
        report.get("step_count"),
    ]
    mining_fields = (
        [report.get("mined_pattern_count"), report.get("replaced_existing")]
        if is_mining
        else ["", ""]
    )
    pattern_counts = report.get("pattern_type_counts", {})
    sources = report.get("top_sources", [])
    if (
        not enabled()
        or not all(type(value) in safe_values for value in fields + mining_fields)
        or type(pattern_counts) is not dict
        or type(sources) not in (list, tuple)
    ):
        return None
    count_entries: list[tuple[str, str]] = []
    source_rows: list[list[str]] = []
    try:
        for key, value in pattern_counts.items():
            if type(key) is not str or type(value) not in safe_values:
                return None
            count_entries.append((key, str(value)))
        for source in sources:
            if type(source) is not dict:
                return None
            values = [
                source.get("source_script"),
                source.get("pattern_count"),
                source.get("zeia_file"),
            ]
            if not all(type(value) in safe_values for value in values):
                return None
            source_rows.append([str(value) for value in values])
        title = "Tecan Script Pattern Mining" if is_mining else "Tecan Script Patterns"
        result = _native.render_script_pattern_summary(
            title,
            *(str(value) for value in fields),
            is_mining,
            *(str(value) for value in mining_fields),
            count_entries,
            source_rows,
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_script_pattern_search(report: Any) -> str | None:
    """Render supported script-pattern search reports natively."""
    if not enabled() or type(report) is not dict:
        return None
    safe_values = (str, bool, int, float, type(None))
    header = [
        report.get("database"),
        report.get("query"),
        report.get("pattern_type_filter"),
        report.get("source_script_filter"),
        report.get("result_count"),
    ]
    results = report.get("results", [])
    if (
        not all(type(value) in safe_values for value in header)
        or type(results) not in (list, tuple)
    ):
        return None
    patterns: list[
        tuple[list[str], list[tuple[str, list[str]]], list[str], list[list[str]]]
    ] = []
    try:
        for pattern in results:
            if type(pattern) is not dict:
                return None
            fields = [
                pattern.get("name"),
                pattern.get("pattern_type"),
                pattern.get("label"),
                pattern.get("source_script"),
                pattern.get("start_command_index"),
                pattern.get("end_command_index"),
                pattern.get("zeia_file"),
                pattern.get("source_path"),
            ]
            if not all(type(value) in safe_values for value in fields):
                return None
            specifications = pattern.get("specifications", {})
            safety_notes = pattern.get("safety_notes", [])
            steps = pattern.get("steps", [])
            if (
                type(specifications) is not dict
                or type(safety_notes) not in (list, tuple)
                or not all(type(value) in safe_values for value in safety_notes)
                or type(steps) not in (list, tuple)
            ):
                return None
            specification_entries: list[tuple[str, list[str]]] = []
            for key, values in specifications.items():
                if (
                    type(key) not in safe_values
                    or type(values) not in (list, tuple)
                    or not all(type(value) is str for value in values)
                ):
                    return None
                specification_entries.append(
                    (str(key), list(values))
                )
            rendered_steps: list[list[str]] = []
            for step in steps:
                if type(step) is not dict:
                    return None
                step_fields = [
                    step.get("step_number"),
                    step.get("summary"),
                    step.get("command_name"),
                    step.get("command_index"),
                ]
                if not all(type(value) in safe_values for value in step_fields):
                    return None
                rendered_steps.append([str(value) for value in step_fields])
            patterns.append(
                (
                    [str(value) for value in fields],
                    specification_entries,
                    [str(value) for value in safety_notes],
                    rendered_steps,
                )
            )
        result = _native.render_script_pattern_search(
            [
                str(header[0]),
                str(header[1]),
                str(header[2] or "any"),
                str(header[3] or "any"),
                str(header[4]),
            ],
            patterns,
        )
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_pattern_types_report(pattern_types: Any) -> str | None:
    """Render supported script pattern type rows natively, or request fallback."""
    if not enabled() or type(pattern_types) not in (list, tuple):
        return None
    safe_values = (str, bool, int, float, type(None))
    rows: list[list[str]] = []
    try:
        for pattern_type in pattern_types:
            if type(pattern_type) is not dict:
                return None
            values = [
                pattern_type.get("pattern_type"),
                pattern_type.get("label"),
                pattern_type.get("description"),
            ]
            if not all(type(value) in safe_values for value in values):
                return None
            rows.append([str(value) for value in values])
        result = _native.render_pattern_types_report(rows)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_pattern_specs(specs: Any) -> list[str] | None:
    """Render supported pattern specification mappings natively."""
    if not enabled() or type(specs) is not dict:
        return None
    entries: list[tuple[str, list[str]]] = []
    try:
        for key, values in specs.items():
            if (
                type(key) is not str
                or type(values) not in (list, tuple)
                or not all(type(value) is str for value in values)
            ):
                return None
            entries.append((key, list(values)))
        result = _native.render_pattern_specs(entries)
        if type(result) is list and all(type(line) is str for line in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def render_join_lines(lines: Any) -> str | None:
    """Join supported Markdown lines with one final newline natively."""
    if (
        not enabled()
        or type(lines) not in (list, tuple)
        or not all(type(line) is str for line in lines)
    ):
        return None
    try:
        result = _native.render_join_lines(list(lines))
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def decode_text_bytes(data: Any) -> str | None:
    """Decode the reader's supported text encodings natively when possible."""
    if not enabled() or type(data) is not bytes:
        return None
    try:
        result = _native.decode_text_bytes(data)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def read_text_file(path: str | Path) -> str | None:
    """Read and decode a text file natively, or request the Python fallback."""
    if not enabled() or not isinstance(path, (str, Path)):
        return None
    try:
        result = _native.read_text_file(str(path))
        return result if type(result) is str else None
    except (AttributeError, OSError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def read_utf8_sig_file(path: str | Path) -> str | None:
    """Read strict UTF-8 with BOM removal and universal newlines, or fall back."""
    if not enabled() or not isinstance(path, (str, Path)):
        return None
    try:
        result = _native.read_utf8_sig_file(str(path))
        return result if type(result) is str else None
    except (AttributeError, OSError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def to_jsonable_containers(value: Any) -> tuple[bool, Any] | None:
    """Convert supported exact JSON containers natively, or request Python fallback."""
    if not enabled():
        return None
    try:
        supported, result = _native.to_jsonable_containers(
            value, _Counter, _PathType, _DefaultDict, _OrderedDict
        )
        if type(supported) is bool and supported:
            return True, result
        return None
    except (AttributeError, TypeError, ValueError, OverflowError, UnicodeError):
        return None


def deepcopy_json(value: Any) -> Any | None:
    """Clone JSON-like containers natively, or request ``copy.deepcopy`` fallback."""
    if not enabled():
        return None
    try:
        return _native.deepcopy_json(value)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def canonical_json(value: Any) -> str | None:
    """Serialize JSON-compatible values with sorted keys, or request fallback."""
    if not enabled():
        return None
    try:
        return _native.canonical_json(value)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def canonical_json_compact(value: Any) -> str | None:
    """Serialize JSON-compatible values with compact separators, or fall back."""
    if not enabled():
        return None
    try:
        result = _native.canonical_json_compact(value)
        return result if type(result) is str else None
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def compact_list(values: Any, limit: Any = 20) -> list[str] | None:
    """Deduplicate exact string sequences natively, preserving order and limit rules."""
    if (
        not enabled()
        or type(values) not in (list, tuple)
        or not all(type(value) is str for value in values)
        or type(limit) is not int
    ):
        return None
    try:
        result = _native.compact_list(list(values), limit)
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, OverflowError, UnicodeError):
        return None


def path_suffixes(paths: Any) -> list[str] | None:
    """Extract ASCII Path suffixes natively for exact strings and Paths."""
    if (
        not enabled()
        or type(paths) not in (list, tuple)
        or not all(type(path) in (str, _PathType) for path in paths)
    ):
        return None
    path_strings = [str(path) for path in paths]
    if any(not path.isascii() for path in path_strings):
        return None
    try:
        result = _native.path_suffixes(path_strings)
        if type(result) is list and all(type(suffix) is str for suffix in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def extension_counts(suffixes: Any) -> dict[str, int] | None:
    """Count exact string suffixes natively and return them in sorted order."""
    if (
        not enabled()
        or type(suffixes) not in (list, tuple)
        or not all(type(suffix) is str for suffix in suffixes)
    ):
        return None
    try:
        pairs = _native.extension_counts(list(suffixes))
        if type(pairs) is not list:
            return None
        counts: dict[str, int] = {}
        for pair in pairs:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not int
                or pair[1] < 0
                or pair[0] in counts
            ):
                return None
            counts[pair[0]] = pair[1]
        return counts
    except (AttributeError, TypeError, ValueError, OverflowError, UnicodeError):
        return None


def diagnostic_sort_indices(keys: Any) -> list[int] | None:
    """Return stable native ordering for pre-normalized five-string keys."""
    if (
        not enabled()
        or type(keys) not in (list, tuple)
        or not all(
            type(key) in (list, tuple)
            and len(key) == 5
            and all(type(value) is str for value in key)
            for key in keys
        )
    ):
        return None
    try:
        encoded = _native.diagnostic_sort_indices(_encode_json(keys))
        if not isinstance(encoded, str):
            return None
        order = json.loads(encoded)
        if (
            type(order) is list
            and all(type(index) is int for index in order)
            and sorted(order) == list(range(len(keys)))
        ):
            return order
        return None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def diagnostic_message(message: Any, code: Any) -> str | None:
    """Select a diagnostic's exact-string message or code natively."""
    if (
        not enabled()
        or (message is not None and type(message) is not str)
        or (code is not None and type(code) is not str)
    ):
        return None
    try:
        result = _native.diagnostic_message(message, code)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def xml_kind_from_suffix(suffix: Any) -> str | None:
    """Classify supported ASCII XML suffixes natively, or request Python fallback."""
    if not enabled() or type(suffix) is not str or not suffix.isascii():
        return None
    try:
        result = _native.xml_kind_from_suffix(suffix)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def pin_reference_candidates(values: Any) -> list[str] | None:
    """Filter exact ASCII pin-reference tokens with Python fallback for Unicode."""
    if (
        not enabled()
        or type(values) not in (list, tuple)
        or not all(type(value) is str and value.isascii() for value in values)
    ):
        return None
    try:
        result = _native.pin_reference_candidates(list(values))
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def casefold_alias_values(mapping: Any, alias_groups: Any) -> list[Any] | None:
    """Resolve ordered ASCII aliases in exact built-in string/dictionary fields."""
    if (
        not enabled()
        or type(mapping) is not dict
        or type(alias_groups) not in (list, tuple)
        or not all(
            type(group) in (list, tuple)
            and all(type(alias) is str and alias.isascii() for alias in group)
            for group in alias_groups
        )
        or not all(
            type(key) is str
            and key.isascii()
            and type(value) in (str, dict)
            for key, value in mapping.items()
        )
    ):
        return None
    try:
        result = _native.casefold_alias_values(
            mapping, [list(group) for group in alias_groups]
        )
        return result if type(result) is list else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def merge_unique_string_groups(groups: Any, limit: Any) -> dict[str, list[str]] | None:
    """Merge exact ASCII string groups with first-seen deduplication and a cap."""
    if (
        not enabled()
        or type(groups) not in (list, tuple)
        or type(limit) is not int
        or not 0 <= limit < (1 << 63)
        or not all(
            type(group) is dict
            and all(
                type(key) is str
                and key.isascii()
                and type(values) is list
                and all(type(value) is str and value.isascii() for value in values)
                for key, values in group.items()
            )
            for group in groups
        )
    ):
        return None
    try:
        result = _native.merge_unique_string_groups(list(groups), limit)
        if type(result) is dict and all(
            type(key) is str
            and type(values) is list
            and all(type(value) is str for value in values)
            for key, values in result.items()
        ):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def delimited_row_fields(
    row: Any, header: Any, has_header: Any
) -> dict[str, str] | None:
    """Normalize supported ASCII CSV row fields with Python fallback otherwise."""
    if (
        not enabled()
        or type(row) is not list
        or type(header) is not list
        or type(has_header) is not bool
        or not all(type(value) is str and value.isascii() for value in row)
        or not all(type(value) is str and value.isascii() for value in header)
    ):
        return None
    try:
        result = _native.delimited_row_fields(row, header, has_header)
        if type(result) is dict and all(
            type(key) is str and type(value) is str
            for key, value in result.items()
        ):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def ascii_casefold_equals(value: Any, expected: Any) -> bool | None:
    """Compare supported ASCII strings with Python-compatible case-folding."""
    if (
        not enabled()
        or type(value) is not str
        or type(expected) is not str
        or not value.isascii()
        or not expected.isascii()
    ):
        return None
    try:
        result = _native.ascii_casefold_equals(value, expected)
        return result if type(result) is bool else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def ascii_case_insensitive_substring_match_groups(
    values: Any, groups: Any
) -> list[bool] | None:
    """Match ASCII substrings without case sensitivity for Python-owned terms."""
    if (
        not enabled()
        or type(values) not in (list, tuple)
        or type(groups) not in (list, tuple)
        or not all(type(value) is str and value.isascii() for value in values)
        or not all(
            type(group) in (list, tuple)
            and all(type(needle) is str and needle.isascii() for needle in group)
            for group in groups
        )
    ):
        return None
    try:
        result = _native.ascii_case_insensitive_substring_match_groups(
            list(values), [list(group) for group in groups]
        )
        if (
            type(result) is list
            and len(result) == len(groups)
            and all(type(matched) is bool for matched in result)
        ):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def any_ascii_casefold_in(values: Any, candidates: Any) -> bool | None:
    """Check ASCII strings against a Python-owned case-folded candidate set."""
    if (
        not enabled()
        or type(values) not in (list, tuple)
        or type(candidates) not in (list, tuple, set, frozenset)
        or not all(type(value) is str and value.isascii() for value in values)
        or not all(
            type(candidate) is str and candidate.isascii()
            for candidate in candidates
        )
    ):
        return None
    try:
        result = _native.any_ascii_casefold_in(list(values), list(candidates))
        return result if type(result) is bool else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def unique_ascii_tuple_indices(rows: Any) -> list[int] | None:
    """Keep the first row for each exact three-string ASCII tuple."""
    if (
        not enabled()
        or type(rows) not in (list, tuple)
        or not all(
            type(row) is tuple
            and len(row) == 3
            and all(type(value) is str and value.isascii() for value in row)
            for row in rows
        )
    ):
        return None
    try:
        result = _native.unique_ascii_tuple_indices(list(rows))
        if (
            type(result) is list
            and all(type(index) is int and 0 <= index < len(rows) for index in result)
            and result == sorted(set(result))
        ):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def sorted_unique_strings(groups: Any) -> list[str] | None:
    """Sort and deduplicate exact built-in Unicode string groups."""
    if (
        not enabled()
        or type(groups) not in (list, tuple)
        or not all(
            type(group) in (list, tuple)
            and all(type(value) is str for value in group)
            for group in groups
        )
    ):
        return None
    try:
        result = _native.sorted_unique_strings(
            [list(group) for group in groups]
        )
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def most_common_string_counts(
    mapping: Any, limit: Any = None
) -> dict[str, int] | None:
    """Order exact string-keyed integer counts like ``Counter.most_common``."""
    if (
        not enabled()
        or type(mapping) is not dict
        or (limit is not None and (type(limit) is not int or not 0 <= limit < (1 << 63)))
        or not all(
            type(key) is str
            and type(count) is int
            and -(1 << 63) <= count < (1 << 63)
            for key, count in mapping.items()
        )
    ):
        return None
    try:
        result = _native.most_common_string_counts(mapping, limit)
        if (
            type(result) is dict
            and all(type(key) is str and type(count) is int for key, count in result.items())
            and (limit is None or len(result) <= limit)
        ):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def ordered_string_counts(values: Any) -> dict[str, int] | None:
    """Count exact string values in stable ``Counter.most_common`` order."""
    if (
        not enabled()
        or type(values) not in (list, tuple)
        or not all(type(value) is str for value in values)
    ):
        return None
    try:
        result = _native.ordered_string_counts(list(values))
        if type(result) is dict and all(
            type(key) is str and type(count) is int
            for key, count in result.items()
        ):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def append_unique_ascii_string(values: Any, value: Any) -> bool | None:
    """Append one supported ASCII string with Python-compatible whitespace handling."""
    if (
        not enabled()
        or type(values) is not list
        or not all(type(item) is str and item.isascii() for item in values)
        or (value is not None and (type(value) is not str or not value.isascii()))
    ):
        return None
    try:
        result = _native.append_unique_ascii_string(values, value)
        return result if type(result) is bool else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def pattern_join_summary(parts: Any) -> str | None:
    """Join exact ASCII summary fields with Python-compatible stripping."""
    if (
        not enabled()
        or type(parts) not in (list, tuple)
        or not all(type(part) is str and part.isascii() for part in parts)
    ):
        return None
    try:
        result = _native.pattern_join_summary(list(parts))
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def pattern_liquid_step(
    action: Any, labware: Any, volume: Any, liquid_class: Any
) -> str | None:
    """Format exact ASCII liquid-step fields without changing policy selection."""
    parts = (action, labware, volume, liquid_class)
    if not enabled() or not all(type(part) is str and part.isascii() for part in parts):
        return None
    try:
        result = _native.pattern_liquid_step(list(parts))
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def sort_string_keys(mapping: Any) -> dict[str, Any] | None:
    """Sort exact built-in dictionaries with string keys, preserving value objects."""
    if (
        not enabled()
        or type(mapping) is not dict
        or not all(type(key) is str for key in mapping)
    ):
        return None
    try:
        result = _native.sort_string_keys(mapping)
        return result if type(result) is dict else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def asset_reference_names(values: Any) -> list[str] | None:
    """Extract and deduplicate ASCII path basenames natively."""
    if (
        not enabled()
        or type(values) not in (list, tuple)
        or not all(type(value) is str and value.isascii() for value in values)
    ):
        return None
    try:
        result = _native.asset_reference_names(list(values))
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def xml_asset_reference_names(text: Any) -> list[str] | None:
    """Extract ASCII image basenames from XML text using the legacy regex shape."""
    if not enabled() or type(text) is not str or not text.isascii():
        return None
    try:
        result = _native.xml_asset_reference_names(text)
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def asset_reference_candidates(values: Any) -> list[str] | None:
    """Filter exact ASCII image-reference text, retaining Python regex fallback."""
    if (
        not enabled()
        or type(values) not in (list, tuple)
        or not all(type(value) is str and value.isascii() for value in values)
    ):
        return None
    try:
        result = _native.asset_reference_candidates(list(values))
        if type(result) is list and all(type(value) is str for value in result):
            return result
        return None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def entry_object_guid(source_name: Any) -> str | None:
    """Extract a canonical nonzero filename GUID natively for exact strings."""
    if not enabled() or type(source_name) is not str:
        return None
    try:
        result = _native.entry_object_guid(source_name)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def command_short_name(type_name: Any) -> str | None:
    """Shorten supported ASCII command type names natively, or request fallback."""
    if not enabled() or type(type_name) is not str or not type_name.isascii():
        return None
    try:
        result = _native.command_short_name(type_name)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def catalog_source_paths(install_path: str | Path, include_all_connectors: bool) -> list[str] | None:
    if not enabled():
        return None
    try:
        return _native.catalog_source_paths(str(install_path), include_all_connectors)
    except (OSError, ValueError, TypeError):
        return None


def catalog_stat_fingerprint(install_path: str | Path, include_all_connectors: bool) -> str | None:
    if not enabled():
        return None
    try:
        return _native.catalog_stat_fingerprint(str(install_path), include_all_connectors)
    except (OSError, ValueError, TypeError):
        return None


def source_content_fingerprint(path: str | Path, install_path: str | Path) -> str | None:
    if not enabled():
        return None
    try:
        return _native.source_content_fingerprint(str(path), str(install_path))
    except (OSError, ValueError, TypeError):
        return None


def catalog_content_fingerprints(
    install_path: str | Path, include_all_connectors: bool
) -> dict[str, str] | None:
    if not enabled():
        return None
    try:
        return dict(_native.catalog_content_fingerprints(str(install_path), include_all_connectors))
    except (OSError, ValueError, TypeError):
        return None


def xscr_command_records(
    root: Any, field_names: Any, element_type: Any
) -> list[tuple[str, dict[str, str]]] | None:
    """Extract supported XSCR command type and ordered interesting fields."""
    if (
        not enabled()
        or type(root) is not element_type
        or type(field_names) not in (set, frozenset, list, tuple)
        or not all(type(name) is str for name in field_names)
    ):
        return None
    try:
        result = _native.xscr_command_records(root, list(field_names), element_type)
        if type(result) is not list:
            return None
        records: list[tuple[str, dict[str, str]]] = []
        for row in result:
            if type(row) is not list or len(row) != 2 or type(row[0]) is not str:
                return None
            if type(row[1]) is not list:
                return None
            fields: dict[str, str] = {}
            for pair in row[1]:
                if (
                    type(pair) is not tuple
                    or len(pair) != 2
                    or type(pair[0]) is not str
                    or type(pair[1]) is not str
                ):
                    return None
                fields[pair[0]] = pair[1]
            records.append((row[0], fields))
        return records
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def xscr_object_fields(root: Any) -> dict[Any, dict[str, dict[str, Any]]] | None:
    """Index bounded XSCR Object fields without changing Python IR conversion."""
    if not enabled():
        return None
    try:
        return dict(_native.xscr_object_fields(root))
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def pattern_windows(
    scripts: list[dict[str, Any]],
    classifications: list[list[list[str]]],
    context_before: int,
    context_after: int,
) -> list[dict[str, Any]] | None:
    if not enabled():
        return None
    try:
        return _native.pattern_windows(scripts, classifications, context_before, context_after)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def _encode_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def worklist_execution_contract(commands: Any) -> list[dict[str, Any]] | None:
    """Build JSON-compatible Load/Execute contracts natively when available."""
    if not enabled():
        return None
    try:
        encoded = _encode_json(commands)
        result = json.loads(_native.worklist_execution_contract(encoded))
        return result if isinstance(result, list) else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_load_contract(
    fields: Any,
    command_id: str,
    command_version: str,
    provenance: Any,
) -> dict[str, Any] | None:
    """Normalize one JSON-compatible Load Worklist contract natively."""
    if not enabled():
        return None
    try:
        result = json.loads(
            _native.worklist_load_contract(
                _encode_json(fields),
                command_id,
                command_version,
                _encode_json(provenance),
            )
        )
        return result if isinstance(result, dict) else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_validate_transfers(
    transfers: Any, strict: bool
) -> dict[str, Any] | None:
    """Validate JSON-compatible transfer records with the native worklist core."""
    if not enabled():
        return None
    try:
        result = json.loads(
            _native.worklist_validate_transfers(_encode_json(transfers), strict)
        )
        if not isinstance(result, dict):
            return None
        if not all(
            isinstance(result.get(key), list)
            and all(isinstance(item, str) for item in result[key])
            for key in ("errors", "warnings")
        ):
            return None
        return result
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_parse_bool(value: Any) -> bool | None:
    """Parse supported exact ASCII worklist booleans; invalid forms fall back."""
    if not enabled() or type(value) is not str or not value.isascii():
        return None
    try:
        result = _native.worklist_parse_bool(value)
        return result if type(result) is bool else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_well_to_position(value: str, rows: int) -> int | None:
    """Convert a supported well coordinate natively, or request Python fallback."""
    if not enabled():
        return None
    try:
        result = _native.worklist_well_to_position(value, rows)
        return result if type(result) is int and result >= 0 else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_parse_tip_selection(
    raw: str, supported_channels: int | None
) -> dict[str, Any] | None:
    """Parse common ASCII tip masks natively, or request Python fallback."""
    if (
        not enabled()
        or type(raw) is not str
        or (
            supported_channels is not None
            and (type(supported_channels) is not int or supported_channels < 0)
        )
    ):
        return None
    try:
        encoded = _native.worklist_parse_tip_selection(raw, supported_channels)
        if encoded is None:
            return None
        result = json.loads(encoded)
        if (
            not isinstance(result, dict)
            or not {"raw", "mode", "value", "channel", "status", "diagnostic_code"}.issubset(result)
            or result.get("raw") != raw
            or result.get("mode") not in {"automatic", "explicit"}
            or result.get("status")
            not in {"valid", "malformed", "invalid", "non_one_hot", "out_of_range"}
            or (result.get("value") is not None and type(result["value"]) is not int)
            or (result.get("channel") is not None and type(result["channel"]) is not int)
            or (
                result.get("diagnostic_code") is not None
                and not isinstance(result["diagnostic_code"], str)
            )
        ):
            return None
        return result
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_parse_enabled_tip_mask(value: str, supported_channels: int) -> int | None:
    """Parse an in-range command mask natively; invalid cases retain Python errors."""
    if (
        not enabled()
        or type(value) is not str
        or not value.isascii()
        or any(0x1C <= ord(character) <= 0x1F for character in value)
        or type(supported_channels) is not int
        or not 1 <= supported_channels <= 127
    ):
        return None
    try:
        result = _native.worklist_parse_enabled_tip_mask(value, supported_channels)
        if not isinstance(result, str):
            return None
        mask = int(result, 10)
        maximum = (1 << supported_channels) - 1
        return mask if 0 < mask <= maximum else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_load_transfers(
    path: str | Path, well_rows: int
) -> list[dict[str, Any]] | None:
    """Load transfer CSV records natively, or request the Python parser fallback."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_load_transfers(str(path), well_rows)
        if encoded is None:
            return None
        result = json.loads(encoded)
        if isinstance(result, list) and all(isinstance(item, dict) for item in result):
            return result
        return None
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_build_transfers(
    transfers: Any,
    name: str,
    wash_policy: str,
    batch_size: int | None,
) -> dict[str, Any] | None:
    """Assemble GWL text and ordered counts with the native worklist core."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_build_transfers(
            _encode_json(transfers), name, wash_policy, batch_size
        )
        if encoded is None:
            return None
        result = json.loads(encoded)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            return None
        counts = result.get("counts")
        order = result.get("count_order")
        if (
            not isinstance(counts, dict)
            or not all(isinstance(key, str) and type(value) is int for key, value in counts.items())
            or not isinstance(order, list)
            or not all(isinstance(key, str) for key in order)
            or len(order) != len(set(order))
            or set(order) != set(counts)
        ):
            return None
        return {
            "text": result["text"],
            "counts": {key: counts[key] for key in order},
        }
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_build_schedule(
    transfers: Any, wash_policy: str, batch_size: int | None
) -> list[dict[str, Any]] | None:
    """Return native worklist operations while preserving Python record objects."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_build_schedule(
            _encode_json(transfers), wash_policy, batch_size
        )
        if encoded is None:
            return None
        result = json.loads(encoded)
        kinds = {"comment", "aspirate", "dispense", "wash", "break"}
        if not isinstance(result, list) or not all(
            isinstance(operation, dict)
            and operation.get("kind") in kinds
            and type(operation.get("transfer_index")) is int
            and operation["transfer_index"] >= 0
            for operation in result
        ):
            return None
        return result
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_parse_gwl(path: str | Path) -> dict[str, Any] | None:
    """Parse a strict GWL file natively, or request the Python fallback."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_parse_gwl(str(path))
        if encoded is None:
            return None
        result = json.loads(encoded)
        if not isinstance(result, dict):
            return None
        counts = result.get("counts")
        order = result.get("count_order")
        if (
            not isinstance(counts, dict)
            or not all(isinstance(key, str) and type(value) is int for key, value in counts.items())
            or not isinstance(order, list)
            or not all(isinstance(key, str) for key in order)
            or len(order) != len(set(order))
            or set(order) != set(counts)
        ):
            return None
        ordered_counts = {key: counts[key] for key in order}
        return {
            "counts": ordered_counts,
            "record_count": sum(ordered_counts.values()),
        }
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def _decode_worklist_record_payload(encoded: str | None) -> list[dict[str, Any]] | None:
    if not isinstance(encoded, str):
        return None
    try:
        records = json.loads(encoded)
        if not isinstance(records, list):
            return None
        for record in records:
            if not isinstance(record, dict):
                return None
            kind = record.get("kind")
            if kind == "pipette":
                fields = (
                    "operation",
                    "rack_label",
                    "rack_id",
                    "rack_type",
                    "position",
                    "tube_id",
                    "volume",
                    "liquid_class",
                    "tip_mask",
                    "forced_rack_type",
                )
                if (
                    record.get("operation") not in {"A", "D"}
                    or not all(isinstance(record.get(field), str) for field in fields)
                ):
                    return None
            elif kind == "wash":
                scheme = record.get("scheme")
                if "scheme" not in record or (
                    scheme is not None
                    and (type(scheme) is not int or scheme not in {1, 2, 3, 4})
                ):
                    return None
            elif kind == "break":
                continue
            elif kind == "comment":
                if not isinstance(record.get("text"), str):
                    return None
            elif kind in {"flush", "set_diti_type"}:
                expected_type = "F" if kind == "flush" else "S"
                if (
                    record.get("type_character") != expected_type
                    or not isinstance(record.get("raw_line"), str)
                    or not isinstance(record.get("parameters"), list)
                    or not all(isinstance(item, str) for item in record["parameters"])
                ):
                    return None
            elif kind == "raw":
                if not isinstance(record.get("type_character"), str) or not isinstance(
                    record.get("raw_line"), str
                ):
                    return None
            else:
                return None
        return records
    except (
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_parse_gwl_records(
    lines: Any, permissive: bool
) -> list[dict[str, Any]] | None:
    """Parse supported GWL lines natively, preserving Python record adapters."""
    if not enabled() or type(permissive) is not bool:
        return None
    try:
        encoded = _native.worklist_parse_gwl_records(_encode_json(lines), permissive)
        return _decode_worklist_record_payload(encoded)
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return None


def worklist_inspect_gwl_lines(lines: Any) -> dict[str, Any] | None:
    """Summarize supported ASCII GWL lines natively, or request Python fallback."""
    if (
        not enabled()
        or type(lines) is not list
        or not all(
            type(line) is str
            and line.isascii()
            and not any(0x1C <= ord(character) <= 0x1F for character in line)
            for line in lines
        )
    ):
        return None
    try:
        encoded = _native.worklist_inspect_gwl_lines(_encode_json(lines))
        if not isinstance(encoded, str):
            return None
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            return None
        pairs = payload.get("record_counts")
        line_count = payload.get("line_count")
        transfer_pairs = payload.get("transfer_pairs_estimate")
        raw_examples = payload.get("pipette_examples")
        if (
            not isinstance(pairs, list)
            or type(line_count) is not int
            or line_count != len(lines)
            or type(transfer_pairs) is not int
            or transfer_pairs < 0
            or not isinstance(raw_examples, list)
            or len(raw_examples) > 12
        ):
            return None
        counts: dict[str, int] = {}
        for pair in pairs:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or type(pair[1]) is not int
                or pair[1] < 0
                or pair[0] in counts
            ):
                return None
            counts[pair[0]] = pair[1]
        examples = []
        for example in raw_examples:
            if (
                not isinstance(example, list)
                or len(example) != 7
                or not all(isinstance(value, str) for value in example)
            ):
                return None
            line, operation, rack_label, rack_type, position, volume, liquid_class = example
            examples.append(
                {
                    "line": line,
                    "operation": operation,
                    "rack_label": rack_label,
                    "rack_type": rack_type,
                    "position": position,
                    "volume": volume,
                    "liquid_class": liquid_class,
                }
            )
        return {
            "record_counts": counts,
            "line_count": line_count,
            "transfer_pairs_estimate": transfer_pairs,
            "pipette_examples": examples,
        }
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_parse_gwl_text_records(
    text: str, permissive: bool
) -> list[dict[str, Any]] | None:
    """Parse common GWL text natively while retaining line-splitting fallback."""
    if not enabled() or type(text) is not str or type(permissive) is not bool:
        return None
    try:
        encoded = _native.worklist_parse_gwl_text_records(text, permissive)
        return _decode_worklist_record_payload(encoded)
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return None


def worklist_read_gwl_records(
    path: str | Path, permissive: bool
) -> list[dict[str, Any]] | None:
    """Read and parse supported GWL files natively, preserving Python fallback."""
    if not enabled() or type(permissive) is not bool:
        return None
    try:
        encoded = _native.worklist_read_gwl_records(str(path), permissive)
        return _decode_worklist_record_payload(encoded)
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_format_volume(value: Any) -> str | None:
    """Format supported exact numeric GWL volumes natively, or request fallback."""
    if not enabled() or type(value) not in (str, int, float):
        return None
    if type(value) is str and (
        not value.isascii()
        or any(0x1C <= ord(character) <= 0x1F for character in value)
    ):
        return None
    try:
        result = _native.worklist_format_volume(value)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, OverflowError, UnicodeError):
        return None


def worklist_comment_line(text: Any) -> str | None:
    """Serialize exact-string GWL comments natively, or request Python fallback."""
    if not enabled() or type(text) is not str:
        return None
    try:
        result = _native.worklist_comment_line(text)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_clean_field(value: Any) -> str | None:
    """Trim supported ASCII GWL fields natively, preserving Python fallback cases."""
    if not enabled() or (
        value is not None
        and (
            type(value) is not str
            or not value.isascii()
            or any(0x1C <= ord(character) <= 0x1F for character in value)
        )
    ):
        return None
    try:
        result = _native.worklist_clean_field(value)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_ascii_uppercase(value: Any) -> str | None:
    """Uppercase exact ASCII strings natively, preserving Unicode fallback."""
    if not enabled() or type(value) is not str or not value.isascii():
        return None
    try:
        result = _native.worklist_ascii_uppercase(value)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_wash_type(scheme: Any) -> str | None:
    """Format exact integer or ``None`` wash schemes natively, or request fallback."""
    if not enabled() or (scheme is not None and type(scheme) is not int):
        return None
    try:
        result = _native.worklist_wash_type(scheme)
        return result if type(result) is str else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def worklist_record_type_line(type_character: Any) -> str | None:
    """Format exact-string GWL record types natively, or request fallback."""
    if not enabled() or type(type_character) is not str:
        return None
    try:
        result = _native.worklist_record_type_line(type_character)
        return result if isinstance(result, str) else None
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def worklist_join_gwl_fields(fields: Any) -> str | None:
    """Join exact-text GWL fields natively, or request Python fallback."""
    if (
        not enabled()
        or type(fields) not in (list, tuple)
        or not all(type(field) is str for field in fields)
    ):
        return None
    try:
        result = _native.worklist_join_gwl_fields(_encode_json(fields))
        return result if isinstance(result, str) else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
    ):
        return None


def worklist_serialize_gwl_lines(lines: Any) -> str | None:
    """Join exact text records natively, or request Python string-join fallback."""
    if (
        not enabled()
        or type(lines) not in (list, tuple)
        or not all(type(line) is str for line in lines)
    ):
        return None
    try:
        result = _native.worklist_serialize_gwl_lines(_encode_json(lines))
        return result if isinstance(result, str) else None
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_count_record_types(types: Any) -> dict[str, int] | None:
    """Count exact string record types natively, preserving first-seen order."""
    if (
        not enabled()
        or type(types) not in (list, tuple)
        or not all(type(record_type) is str for record_type in types)
    ):
        return None
    try:
        encoded = _native.worklist_count_record_types(_encode_json(types))
        if encoded is None:
            return None
        pairs = json.loads(encoded)
        if not isinstance(pairs, list):
            return None
        counts: dict[str, int] = {}
        for pair in pairs:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or type(pair[1]) is not int
                or pair[1] < 0
                or pair[0] in counts
            ):
                return None
            counts[pair[0]] = pair[1]
        return counts
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_analyze_lines(
    lines: Any,
    enabled_tip_mask: str | None,
    supported_channels: int,
) -> dict[str, Any] | None:
    """Analyze normalized GWL lines with the native semantic engine."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_analyze_lines(
            _encode_json(lines), enabled_tip_mask, supported_channels
        )
        if encoded is None:
            return None
        result = json.loads(encoded)
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return None
        if not all(
            isinstance(result.get(key), str)
            for key in ("schema_version", "runtime_parallelism")
        ):
            return None
        if not all(
            type(result.get(key)) is int
            for key in ("record_count", "unit_count", "automatic_tip_units")
        ):
            return None
        explicit = result.get("explicit_tip_assignments")
        if not isinstance(explicit, dict) or not all(
            isinstance(key, str) and type(value) is int
            for key, value in explicit.items()
        ):
            return None
        if not all(
            isinstance(result.get(key), list)
            for key in ("barriers", "parallel_windows", "diagnostics")
        ):
            return None
        if result.get("enabled_tip_mask") is not None and type(
            result["enabled_tip_mask"]
        ) is not int:
            return None
        return result
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None


def worklist_optimize_lines(
    lines: Any,
    name: str,
    enabled_tip_mask: str | None,
    supported_channels: int,
) -> dict[str, Any] | None:
    """Optimize normalized GWL lines with the native semantic engine."""
    if not enabled():
        return None
    try:
        encoded = _native.worklist_optimize_lines(
            _encode_json(lines), name, enabled_tip_mask, supported_channels
        )
        if encoded is None:
            return None
        result = json.loads(encoded)
        if (
            not isinstance(result, dict)
            or type(result.get("ok")) is not bool
            or not isinstance(result.get("text"), str)
            or type(result.get("changed")) is not bool
            or not isinstance(result.get("reordered_units"), list)
            or not all(
                isinstance(unit, list)
                and all(type(index) is int for index in unit)
                for unit in result["reordered_units"]
            )
            or not isinstance(result.get("reasons"), list)
            or not all(isinstance(reason, str) for reason in result["reasons"])
        ):
            return None
        return result
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        return None
