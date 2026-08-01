"""Generic readers for catalog-like XML objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import re

from .common import first_text, parse_xml_text, read_text, texts_by_name


def inspect_xml_object(path: str | Path, *, source_name: str | None = None) -> dict[str, Any]:
    text = read_text(path)
    root = parse_xml_text(text)
    return inspect_xml_object_text(text, source_name=source_name or str(path), suffix=Path(source_name or path).suffix)


def inspect_xml_object_text(text: str, *, source_name: str, suffix: str = "") -> dict[str, Any]:
    root = parse_xml_text(text)
    grouped = texts_by_name(
        root,
        {
            "ObjectName",
            "TypeId",
            "FunctionalGroup",
            "FootPrint",
            "Renderer",
            "Guid",
            "GUID",
            "Description",
            "BaseWorktableName",
            "BaseWorktableGuid",
            "LiquidClassName",
            "Name",
            "ComponentGuid",
            "SiteGuid",
        },
    )
    object_name = first_text(root, "ObjectName") or first_text(root, "Name")
    pin_refs = _pin_refs(text)
    asset_refs = _asset_refs(text)
    return {
        "kind": _kind_from_suffix(suffix),
        "source": source_name,
        "object_name": object_name,
        "type_id": first_text(root, "TypeId"),
        "functional_group": first_text(root, "FunctionalGroup"),
        "footprint": first_text(root, "FootPrint"),
        "renderer": first_text(root, "Renderer"),
        "description": first_text(root, "Description"),
        "component_guid": first_text(root, "ComponentGuid"),
        "site_guid": first_text(root, "SiteGuid"),
        "names": grouped.get("Name", [])[:20],
        "guids": [*grouped.get("Guid", []), *grouped.get("GUID", [])][:10],
        "pin_refs": pin_refs,
        "asset_refs": asset_refs,
        "custom_part": bool(pin_refs or asset_refs or "custom" in object_name.casefold()),
    }


def _kind_from_suffix(suffix: str) -> str:
    suffix = suffix.lower()
    return {
        ".xcmp": "component",
        ".xwsp": "workspace",
        ".xlqc": "liquid_class",
        ".xlcp": "liquid_class_map",
        ".xsit": "site",
        ".xcon": "connector",
        ".xml": "xml",
    }.get(suffix, suffix.lstrip(".") or "xml")


def _pin_refs(text: str) -> list[str]:
    refs = set(re.findall(r"\b(?:GIO\d+_Pin\d+|Worktable_[A-Za-z0-9_]*Pin[A-Za-z0-9_]*|WorktablePin_[A-Za-z0-9_]+)\b", text))
    return sorted(refs)


def _asset_refs(text: str) -> list[str]:
    refs = set()
    for match in re.findall(r"[^<>\"]+\.(?:bmp|gif|jpe?g|png|tiff?)", text, flags=re.IGNORECASE):
        value = match.strip()
        if value:
            refs.add(Path(value.replace("\\", "/")).name)
    return sorted(refs)
