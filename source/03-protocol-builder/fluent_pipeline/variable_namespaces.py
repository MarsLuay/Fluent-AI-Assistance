"""FluentControl variable-declaration namespace normalization."""

from __future__ import annotations

import re
from typing import Any

from .xml_compat import ET


ARRAYS_NAMESPACE = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
SCRIPTING_ENGINE_NAMESPACE = "http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine"
VARIABLE_HANDLING_NAMESPACE = "http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"

_ROOT_TAG_RE = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?VxData\b[^>]*>")
_XML_PREFIX_RE = r"[A-Za-z_][\w.-]*"
_VARIABLE_BOUNDARY_RE = re.compile(
    rf"<(?P<closing>/)?(?:{_XML_PREFIX_RE}:)?VariableDeclarations\b[^>]*>"
)
_VARIABLE_ITEM_RE = re.compile(
    r'<(?:[A-Za-z_][\w.-]*:)?anyType\b(?=[^>]*\b(?:xsi|i):type="[^"]*VariableDefinitionHelper")[^>]*>'
)

_CANONICAL_NAMESPACE_PREFIXES = (
    ("d2p1", ARRAYS_NAMESPACE),
    ("d3p1", VARIABLE_HANDLING_NAMESPACE),
    ("i", XSI_NAMESPACE),
    ("ns3", SCRIPTING_ENGINE_NAMESPACE),
)


def _ensure_namespace(tag: str, prefix: str, namespace: str) -> tuple[str, bool]:
    declaration = f'xmlns:{prefix}="{namespace}"'
    if declaration in tag:
        return tag, False
    return re.sub(r"(<[^\s>]+)", rf'\1 {declaration}', tag, count=1), True


def _declared_prefixes(text: str, namespace: str) -> set[str]:
    return set(re.findall(rf'\bxmlns:({_XML_PREFIX_RE})="{re.escape(namespace)}"', text))


def _rewrite_namespace_prefix(text: str, old: str, new: str) -> str:
    if old == new:
        return text
    text = re.sub(rf'\bxmlns:{re.escape(old)}=', f"xmlns:{new}=", text)
    text = re.sub(rf'(?P<bracket></?){re.escape(old)}:', rf'\g<bracket>{new}:', text)
    text = re.sub(rf'\b{re.escape(old)}:', f"{new}:", text)
    return text


def _known_namespace_prefixes(text: str) -> dict[str, set[str]]:
    """Return document-level aliases for namespaces FluentControl needs locally.

    ElementTree may choose a different generated alias (for example ``ns4``)
    when the ScriptEngine namespace shares a document with other data-contract
    namespaces.  That alias is valid in the full VxData document, but is lost
    when FluentControl deserializes only VariableDeclarations.InnerXml.
    """
    return {
        namespace: _declared_prefixes(text, namespace)
        for _canonical, namespace in _CANONICAL_NAMESPACE_PREFIXES
    }


def _prefix_is_rebound_to_another_namespace(text: str, prefix: str, namespace: str) -> bool:
    return any(
        declaration != namespace
        for declaration in re.findall(
            rf'\bxmlns:{re.escape(prefix)}="([^"]+)"',
            text,
        )
    )


def _canonicalize_known_prefixes(
    text: str,
    *,
    inherited_namespace_prefixes: dict[str, set[str]] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    fixups: list[dict[str, str]] = []
    inherited_namespace_prefixes = inherited_namespace_prefixes or {}
    for canonical, namespace in _CANONICAL_NAMESPACE_PREFIXES:
        prefixes = _declared_prefixes(text, namespace)
        prefixes.update(inherited_namespace_prefixes.get(namespace, set()))
        for prefix in sorted(prefixes):
            if prefix == canonical:
                continue
            # A declaration inside the fragment is closer in XML scope than a
            # VxData-level alias.  Do not rewrite a genuinely different local
            # binding just because the root reused the same generated prefix.
            if _prefix_is_rebound_to_another_namespace(text, prefix, namespace):
                continue
            text = _rewrite_namespace_prefix(text, prefix, canonical)
            fixups.append(
                {
                    "attribute": f"xmlns:{prefix}",
                    "from": prefix,
                    "to": canonical,
                }
            )
    return text, fixups


def _localize_variable_declaration_fragment(
    fragment: str,
    *,
    inherited_namespace_prefixes: dict[str, set[str]],
) -> tuple[str, list[dict[str, str]]]:
    fixups: list[dict[str, str]] = []

    text, prefix_fixups = _canonicalize_known_prefixes(
        fragment,
        inherited_namespace_prefixes=inherited_namespace_prefixes,
    )
    fixups.extend(prefix_fixups)

    def localize_collection(match: re.Match[str]) -> str:
        tag, changed = _ensure_namespace(match.group(0), "ns3", SCRIPTING_ENGINE_NAMESPACE)
        if changed:
            fixups.append(
                {
                    "attribute": "xmlns:ns3",
                    "from": "ancestor",
                    "to": SCRIPTING_ENGINE_NAMESPACE,
                }
            )
        return tag

    text = re.sub(
        r"<ns3:VariableDeclarations\b[^>]*>",
        localize_collection,
        text,
    )
    text = re.sub(
        r'\b(?:xsi|i):type="[^"]*VariableDefinitionHelper"',
        'i:type="d3p1:VariableDefinitionHelper"',
        text,
    )

    def localize_item(match: re.Match[str]) -> str:
        tag = match.group(0)
        for prefix, namespace in (
            ("d3p1", VARIABLE_HANDLING_NAMESPACE),
            ("d2p1", ARRAYS_NAMESPACE),
            ("i", XSI_NAMESPACE),
        ):
            tag, changed = _ensure_namespace(tag, prefix, namespace)
            if changed:
                fixups.append(
                    {
                        "attribute": f"xmlns:{prefix}",
                        "from": "ancestor",
                        "to": namespace,
                    }
                )
        return tag

    text = _VARIABLE_ITEM_RE.sub(localize_item, text)
    return text, fixups


def _root_prefix_is_used_outside_variable_declarations(text: str, prefix: str) -> bool:
    """Whether removing a root binding would break non-variable XML.

    The namespace pass is intentionally scoped to VariableDeclarations, but
    VxWorkspaceData can also use canonical data-contract aliases (for example
    ``i:nil`` on CameraView).  Keep a root declaration whenever that alias is
    still referenced outside the self-contained variable fragment.
    """
    span = variable_declaration_fragment_span(text)
    if span is None:
        return True
    start, end, _fragment = span
    outside_fragment = text[:start] + text[end:]
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_.-]){re.escape(prefix)}:",
            outside_fragment,
        )
    )


def localize_variable_declaration_namespaces(text: str) -> tuple[str, list[dict[str, str]]]:
    """Make VariableDeclarations safe for FluentControl's InnerXml deserializer.

    FluentControl deserializes this property independently from the VxData
    document. Namespace declarations inherited from VxData are therefore not
    available. Match native exports by declaring the scripting namespace on the
    collection and the array, variable-helper, and xsi namespaces on each item.

    Prefix canonicalization is scoped to the VariableDeclarations fragment so
    unrelated VxWorkspaceData serialization arrays (for example ``ns2:string``)
    keep their document-level namespace bindings.
    """
    if "VariableDefinitionHelper" not in text:
        return text, []

    original = text
    span = variable_declaration_fragment_span(text)
    if span is None:
        return text, []

    start, end, fragment = span
    localized_fragment, fixups = _localize_variable_declaration_fragment(
        fragment,
        inherited_namespace_prefixes=_known_namespace_prefixes(text),
    )
    if localized_fragment == fragment:
        return text, fixups

    text = text[:start] + localized_fragment + text[end:]
    root_prefixes_needed_outside_variables = {
        prefix
        for prefix, _namespace in _CANONICAL_NAMESPACE_PREFIXES
        if _root_prefix_is_used_outside_variable_declarations(text, prefix)
    }

    def normalize_root(match: re.Match[str]) -> str:
        tag = match.group(0)
        for prefix, namespace in _CANONICAL_NAMESPACE_PREFIXES:
            if prefix in root_prefixes_needed_outside_variables:
                continue
            pattern = rf'\s+xmlns:{re.escape(prefix)}="{re.escape(namespace)}"'
            tag, count = re.subn(pattern, "", tag)
            if count:
                fixups.append(
                    {
                        "attribute": f"xmlns:{prefix}",
                        "from": "VxData",
                        "to": "VariableDeclarations fragment",
                    }
                )
        return tag

    text = _ROOT_TAG_RE.sub(normalize_root, text, count=1)
    assert_variable_declarations_are_standalone(text)
    return text, fixups


def variable_declaration_fragment_span(text: str) -> tuple[int, int, str] | None:
    start: int | None = None
    depth = 0
    for match in _VARIABLE_BOUNDARY_RE.finditer(text):
        if start is None:
            if match.group("closing"):
                continue
            start = match.start()
        if match.group("closing"):
            depth -= 1
            if depth == 0:
                return start, match.end(), text[start : match.end()]
        elif not match.group(0).rstrip().endswith("/>"):
            depth += 1
    return None


def variable_declaration_fragment(text: str) -> str | None:
    span = variable_declaration_fragment_span(text)
    if span is None:
        return None
    return span[2]


def variable_declaration_fragment_error(text: str) -> str | None:
    fragment = variable_declaration_fragment(text)
    if fragment is None:
        return "VariableDeclarations fragment was not found."
    try:
        ET.fromstring(fragment)
    except ET.ParseError as exc:
        return str(exc)
    return None


def assert_variable_declarations_are_standalone(text: str) -> None:
    error = variable_declaration_fragment_error(text)
    if error:
        raise ValueError(
            "VariableDeclarations cannot be deserialized independently by FluentControl: "
            f"{error}"
        )
