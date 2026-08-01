"""Parse FluentControl ``.xcon`` (worktable connector) files.

Connectors bind a component (carrier/labware) to a site pin. A typical
install ships 14k+ connector files; fluentcoder exposes on-demand parsing via
``load_xcon`` / ``parse_connector``. By default ``build_index`` stores only
connectors referenced by indexed ``.xsit`` files; opt in to a full
``Connectors/*.xcon`` walk with ``include_all_connectors=True`` or
``FLUENTCODER_INDEX_ALL_CONNECTORS=1``.
"""

from __future__ import annotations

from .. import xml_compat as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .xcmp import _find, _text, _vec3


@dataclass(frozen=True)
class XconConnector:
    """Parsed metadata for one ``.xcon`` connector graph edge."""

    guid: str
    name: str
    component_guid: str
    site_guid: str
    file_path: Path
    description: Optional[str] = None
    is_default: bool = False
    position_mm: Optional[tuple[float, float, float]] = None
    orientation: Optional[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] = None


def load_xcon(path: Path | str) -> XconConnector:
    """Parse a ``.xcon`` file into connector metadata."""
    return _load_xcon_cached(str(Path(path).resolve()))


def parse_connector(path: Path | str) -> XconConnector:
    """Alias for :func:`load_xcon`."""
    return load_xcon(path)


@lru_cache(maxsize=2048)
def _load_xcon_cached(path_str: str) -> XconConnector:
    path = Path(path_str)
    tree = ET.parse(path_str)
    root = tree.getroot()

    payload = _find(root, "Payload")
    if payload is None:
        raise ValueError(f"{path}: no <Payload> element")

    name = _text(_find(payload, "ObjectName")) or path.stem

    payload_data = _find(payload, "PayloadData")
    template = _find(payload_data, "ConnectorTemplate") if payload_data is not None else None
    if template is None:
        raise ValueError(f"{path}: no <ConnectorTemplate> payload")

    guid = _text(_find(template, "GUID")) or path.stem
    component_guid = _text(_find(template, "ComponentGuid")) or ""
    site_guid = _text(_find(template, "SiteGuid")) or ""
    if not component_guid or not site_guid:
        raise ValueError(f"{path}: missing ComponentGuid or SiteGuid")

    is_default_text = (_text(_find(template, "IsDefaultConnector")) or "").casefold()
    is_default = is_default_text in {"true", "1"}
    position_mm = _vec3(_find(template, "PositionInParent"))
    orientation = _parse_orientation(_find(template, "Orientation"))

    return XconConnector(
        guid=guid,
        name=name,
        component_guid=component_guid,
        site_guid=site_guid,
        file_path=path,
        description=_text(_find(template, "Description")),
        is_default=is_default,
        position_mm=position_mm,
        orientation=orientation,
    )


def _parse_orientation(
    orientation_el: Optional[ET.Element],
) -> Optional[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]]:
    if orientation_el is None:
        return None
    matrix_el = _find(orientation_el, "Matrix")
    if matrix_el is None:
        return None
    rows: list[tuple[float, float, float]] = []
    for row_el in matrix_el.iter():
        if not isinstance(row_el.tag, str):
            continue
        if row_el.tag.rsplit("}", 1)[-1] != "ArrayOfdouble":
            continue
        values: list[float] = []
        for child in row_el:
            if child.text is None:
                continue
            try:
                values.append(float(child.text))
            except ValueError:
                continue
        if len(values) == 3:
            rows.append((values[0], values[1], values[2]))
    if len(rows) != 3:
        return None
    return (rows[0], rows[1], rows[2])
