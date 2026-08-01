"""Typed loaders for FluentControl worktable XML files.

Surfaces the fields fluentcoder cares about — geometry, well grid, arrangement
sites, functional group — without exposing the full Tecan XML namespace zoo
to callers.

Three file types covered:

- `.xcmp` (Worktable Component) — labware OR carrier definition
- `.xwsp` (Workspace) — a configured worktable with sites + occupancy
- `.xsit` (Site) — a single site definition (lightweight; not parsed deeply
  in v1.1)

The TecanXML is verbose and namespaced. We use `defusedxml.ElementTree` and
match on local-name only (ignore namespace URIs) so the parser is robust to
namespace-prefix drift across FC versions.
"""

from __future__ import annotations

import math
from .. import xml_compat as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional


# ── XML helpers (namespace-agnostic) ───────────────────────────────


def _local(tag: str) -> str:
    """Strip the XML namespace from an element tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find(elem: Optional[ET.Element], local_name: str) -> Optional[ET.Element]:
    """Find first descendant with the given local-name (BFS, namespace-agnostic)."""
    if elem is None:
        return None
    for child in elem.iter():
        if isinstance(child.tag, str) and _local(child.tag) == local_name:
            return child
    return None


def _findall(elem: Optional[ET.Element], local_name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [
        c for c in elem.iter()
        if isinstance(c.tag, str) and _local(c.tag) == local_name
    ]


def _direct_children(elem: Optional[ET.Element], local_name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [c for c in list(elem) if isinstance(c.tag, str) and _local(c.tag) == local_name]


def _direct_child(elem: Optional[ET.Element], local_name: str) -> Optional[ET.Element]:
    return next(iter(_direct_children(elem, local_name)), None)


def _direct_child_text(elem: Optional[ET.Element], local_name: str) -> Optional[str]:
    return _text(_direct_child(elem, local_name))



def _text(elem: Optional[ET.Element]) -> Optional[str]:
    if elem is None:
        return None
    text = elem.text
    return text.strip() if isinstance(text, str) and text.strip() else None


def _vec3(elem: Optional[ET.Element]) -> Optional[tuple[float, float, float]]:
    """Read an X/Y/Z vector from an element (children with local-name X, Y, Z)."""
    if elem is None:
        return None
    coords: dict[str, float] = {}
    for child in elem:
        if not isinstance(child.tag, str):
            continue
        name = _local(child.tag)
        if name in ("X", "Y", "Z") and child.text:
            try:
                coords[name] = float(child.text)
            except ValueError:
                pass
    if {"X", "Y", "Z"}.issubset(coords):
        return (coords["X"], coords["Y"], coords["Z"])
    return None


def _int_text(elem: Optional[ET.Element]) -> Optional[int]:
    txt = _text(elem)
    if txt is None:
        return None
    try:
        return int(txt)
    except ValueError:
        return None


def _float_text(elem: Optional[ET.Element]) -> Optional[float]:
    txt = _text(elem)
    if txt is None:
        return None
    try:
        return float(txt)
    except ValueError:
        return None


# ── XCMP data model ────────────────────────────────────────────────


@dataclass(frozen=True)
class XcmpCavity:
    """One well's geometric cavity (truncated cone is the common case)."""

    shape: str                              # 'TruncatedCone' | 'Cylinder' | 'Cuboid' | …
    height_mm: Optional[float] = None
    diameter_top_mm: Optional[float] = None
    diameter_bottom_mm: Optional[float] = None
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    area_top_mm2: Optional[float] = None
    area_bottom_mm2: Optional[float] = None
    width_top_mm: Optional[float] = None
    width_base_mm: Optional[float] = None

    @property
    def volume_ul(self) -> Optional[float]:
        """Geometric cavity volume (microliters = mm³)."""
        if (
            self.shape == "TruncatedCone"
            and self.height_mm is not None
            and self.diameter_top_mm is not None
            and self.diameter_bottom_mm is not None
        ):
            r1 = self.diameter_bottom_mm / 2.0
            r2 = self.diameter_top_mm / 2.0
            return (math.pi * self.height_mm / 3.0) * (r1 * r1 + r1 * r2 + r2 * r2)
        if self.shape == "Cylinder" and self.height_mm is not None and self.diameter_top_mm is not None:
            r = self.diameter_top_mm / 2.0
            return math.pi * r * r * self.height_mm
        if self.shape == "Cuboid" and self.height_mm is not None and self.length_mm is not None and self.width_mm is not None:
            return self.height_mm * self.length_mm * self.width_mm
        if (
            self.shape == "TruncatedPyramid"
            and self.height_mm is not None
            and self.area_top_mm2 is not None
            and self.area_bottom_mm2 is not None
        ):
            return (
                self.height_mm
                / 3.0
                * (
                    self.area_bottom_mm2
                    + math.sqrt(self.area_bottom_mm2 * self.area_top_mm2)
                    + self.area_top_mm2
                )
            )
        if (
            self.shape == "Trapezoid"
            and self.height_mm is not None
            and self.length_mm is not None
            and self.width_top_mm is not None
            and self.width_base_mm is not None
        ):
            return self.height_mm * self.length_mm * ((self.width_top_mm + self.width_base_mm) / 2.0)
        return None

    @property
    def footprint_kind(self) -> str:
        """Coarse top-view footprint for renderers."""
        if self.shape in {"Cylinder", "TruncatedCone"}:
            return "round"
        if self.shape in {"Cuboid", "TruncatedPyramid", "Trapezoid"}:
            return "rectangular"
        return "unknown"

    def to_geometry(self) -> dict[str, float | str | None]:
        """Serializable shape facts useful to deck/body renderers."""
        return {
            "shape": self.shape,
            "footprint": self.footprint_kind,
            "height_mm": self.height_mm,
            "diameter_top_mm": self.diameter_top_mm,
            "diameter_bottom_mm": self.diameter_bottom_mm,
            "length_mm": self.length_mm,
            "width_mm": self.width_mm,
            "area_top_mm2": self.area_top_mm2,
            "area_bottom_mm2": self.area_bottom_mm2,
            "width_top_mm": self.width_top_mm,
            "width_base_mm": self.width_base_mm,
            "volume_ul": self.volume_ul,
        }


@dataclass(frozen=True)
class XcmpPipettable:
    """Well-grid and per-well geometry for labware that holds liquid."""

    x_wells: int                                              # cols
    y_wells: int                                              # rows
    x_spacing_mm: float
    y_spacing_mm: float                                       # may be negative
    first_well_mm: tuple[float, float, float]                 # A1 position
    cavity: Optional[XcmpCavity] = None
    cavity_shapes: tuple[XcmpCavity, ...] = ()
    z_heights: dict[str, float] = field(default_factory=dict) # ZTravel/ZStart/etc.

    @property
    def well_count(self) -> int:
        return self.x_wells * self.y_wells

    @property
    def cavity_volume_ul(self) -> Optional[float]:
        shapes = self.cavity_shapes or ((self.cavity,) if self.cavity else ())
        volumes = [shape.volume_ul for shape in shapes if shape is not None and shape.volume_ul is not None]
        if volumes:
            return sum(volumes)
        return None

    @property
    def primary_cavity(self) -> Optional[XcmpCavity]:
        shapes = self.cavity_shapes or ((self.cavity,) if self.cavity else ())
        real_shapes = [shape for shape in shapes if shape is not None]
        if not real_shapes:
            return None
        return max(
            real_shapes,
            key=lambda shape: (
                shape.volume_ul or 0.0,
                shape.height_mm or 0.0,
            ),
        )

    @property
    def well_height_mm(self) -> Optional[float]:
        shapes = self.cavity_shapes or ((self.cavity,) if self.cavity else ())
        heights = [shape.height_mm for shape in shapes if shape is not None and shape.height_mm is not None]
        if heights:
            return sum(heights)
        z_values = [self.z_heights.get(key) for key in ("ZStart", "ZTravel", "ZDispense")]
        z_values = [value for value in z_values if value is not None]
        if z_values:
            return max(z_values)
        return None

    def well_geometry(self) -> dict:
        primary = self.primary_cavity
        return {
            "grid": {
                "rows": self.y_wells,
                "columns": self.x_wells,
                "x_spacing_mm": self.x_spacing_mm,
                "y_spacing_mm": self.y_spacing_mm,
                "well_count": self.well_count,
                "first_well_mm": self.first_well_mm,
            },
            "shape": primary.shape if primary else "unknown",
            "footprint": primary.footprint_kind if primary else "unknown",
            "height_mm": self.well_height_mm,
            "volume_ul": self.cavity_volume_ul,
            "cavity_shapes": [shape.to_geometry() for shape in (self.cavity_shapes or ((self.cavity,) if self.cavity else ()))],
        }


@dataclass(frozen=True)
class XcmpArrangement:
    """Site grid for carriers (and the trivial 1×1 of a single labware)."""

    sites_in_x: int
    sites_in_y: int
    sites_in_z: int
    site_spacing_mm: tuple[float, float, float]
    position_in_parent_mm: tuple[float, float, float]
    site_offsets_mm: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    allowed_grip_modes: dict[int, tuple[str, ...]] = field(default_factory=dict)

    @property
    def site_count(self) -> int:
        return self.sites_in_x * self.sites_in_y * self.sites_in_z


@dataclass(frozen=True)
class XcmpComponent:
    """Top-level view of a `.xcmp` file."""

    guid: str
    name: str
    file_path: Path
    dim_mm: Optional[tuple[float, float, float]] = None
    functional_group: Optional[str] = None
    footprint: Optional[str] = None
    renderer: Optional[str] = None
    is_lid: bool = False
    arrangement: Optional[XcmpArrangement] = None
    pipettable: Optional[XcmpPipettable] = None
    sub_component_names: tuple[str, ...] = ()
    mesh_object_names: tuple[str, ...] = ()
    site_guids: tuple[str, ...] = ()
    custom_attrs: dict[str, str] = field(default_factory=dict)


# ── XCMP parser ────────────────────────────────────────────────────


def load_xcmp(path: Path | str) -> XcmpComponent:
    """Parse a `.xcmp` file into a typed component view."""
    return _load_xcmp_cached(str(Path(path).resolve()))


@lru_cache(maxsize=2048)
def _load_xcmp_cached(path_str: str) -> XcmpComponent:
    path = Path(path_str)
    tree = ET.parse(path)
    root = tree.getroot()
    payload = _find(root, "Payload")
    if payload is None:
        raise ValueError(f"{path}: no <Payload> element")

    name = _text(_find(payload, "ObjectName")) or path.stem

    payload_data = _find(payload, "PayloadData")
    template = _find(payload_data, "CarrierOrLabwareTemplate") if payload_data is not None else None

    guid = _text(_find(template, "GUID")) or path.stem

    dim_mm = _vec3(_direct_children(template, "Dimension")[0]) if template is not None and _direct_children(template, "Dimension") else None
    functional_group = _text(_find(template, "FunctionalGroup")) if template is not None else None
    footprint = _text(_find(template, "FootPrint")) if template is not None else None
    renderer = _text(_find(template, "Renderer")) if template is not None else None
    is_lid_text = _text(_find(template, "IsLid")) if template is not None else None
    is_lid = is_lid_text == "true"

    arrangement = _parse_arrangement(template)
    pipettable = _parse_pipettable(template)

    # Inner sub-component ObjectNames (nested labware references inside arrangements).
    sub_component_names: list[str] = []
    for inner_name_el in _findall(template, "ObjectName") if template is not None else []:
        # Filter out the top-level repeats and the guid-shaped names.
        text = _text(inner_name_el)
        if text and text != name and not _looks_like_guid(text):
            sub_component_names.append(text)

    # Mesh ObjectNames from <Reference TypeId='WorktableMesh'>.
    mesh_names: list[str] = []
    site_guids: list[str] = []
    for ref in _direct_children(payload, "Reference"):
        type_id = _text(_find(ref, "TypeId"))
        ref_name = _text(_find(ref, "ObjectName"))
        ref_guid = _text(_find(ref, "Guid"))
        if type_id == "WorktableMesh" and ref_name and not _looks_like_guid(ref_name):
            mesh_names.append(ref_name)
        if type_id == "WorktableSite" and ref_guid:
            site_guids.append(ref_guid)

    custom_attrs = _parse_custom_attrs(template)

    return XcmpComponent(
        guid=guid,
        name=name,
        file_path=path,
        dim_mm=dim_mm,
        functional_group=functional_group,
        footprint=footprint,
        renderer=renderer,
        is_lid=is_lid,
        arrangement=arrangement,
        pipettable=pipettable,
        sub_component_names=tuple(sub_component_names),
        mesh_object_names=tuple(mesh_names),
        site_guids=tuple(site_guids),
        custom_attrs=custom_attrs,
    )


def _parse_arrangement(template: Optional[ET.Element]) -> Optional[XcmpArrangement]:
    if template is None:
        return None
    arrs = _find(template, "Arrangements")
    if arrs is None:
        return None
    template_el = _find(arrs, "ArrangementTemplate")
    if template_el is None:
        return None

    sx = _int_text(_find(template_el, "SitesInX")) or 0
    sy = _int_text(_find(template_el, "SitesInY")) or 0
    sz = _int_text(_find(template_el, "SitesInZ")) or 0
    if sx == 0 and sy == 0 and sz == 0:
        return None

    spacing_x = _float_text(_find(template_el, "SiteSpacingInX")) or 0.0
    spacing_y = _float_text(_find(template_el, "SiteSpacingInY")) or 0.0
    spacing_z = _float_text(_find(template_el, "SiteSpacingInZ")) or 0.0

    pos_in_parent = _vec3(_find(template_el, "PositionInParent")) or (0.0, 0.0, 0.0)

    site_offsets: dict[int, tuple[float, float, float]] = {}
    site_offsets_el = _find(template_el, "SiteOffsets")
    if site_offsets_el is not None:
        for kv in list(site_offsets_el):
            if not isinstance(kv.tag, str):
                continue
            key = _int_text(_find(kv, "Key"))
            value = _vec3(_find(kv, "Value"))
            if key is not None and value is not None:
                site_offsets[key] = value

    grip_modes: dict[int, tuple[str, ...]] = {}
    allowed = _find(template_el, "AllowedGripModes")
    if allowed is not None:
        for site_kv in list(allowed):
            if not isinstance(site_kv.tag, str):
                continue
            site_idx = _int_text(_find(site_kv, "Key"))
            if site_idx is None:
                continue
            cgas: list[str] = []
            value_el = _find(site_kv, "Value")
            for cga_kv in list(value_el if value_el is not None else []):
                if not isinstance(cga_kv.tag, str):
                    continue
                cga = _text(_find(cga_kv, "Key"))
                if cga:
                    cgas.append(cga)
            if cgas:
                grip_modes[site_idx] = tuple(cgas)

    return XcmpArrangement(
        sites_in_x=sx, sites_in_y=sy, sites_in_z=sz,
        site_spacing_mm=(spacing_x, spacing_y, spacing_z),
        position_in_parent_mm=pos_in_parent,
        site_offsets_mm=site_offsets,
        allowed_grip_modes=grip_modes,
    )


def _parse_pipettable(template: Optional[ET.Element]) -> Optional[XcmpPipettable]:
    if template is None:
        return None
    pip = _find(template, "Pipettable")
    if pip is None:
        return None

    x_wells = _int_text(_find(pip, "XNumberOfWells"))
    y_wells = _int_text(_find(pip, "YNumberOfWells"))
    if not x_wells or not y_wells:
        return None
    x_spacing = _float_text(_find(pip, "XSpacing")) or 0.0
    y_spacing = _float_text(_find(pip, "YSpacing")) or 0.0
    first_well = _vec3(_find(pip, "PositionOfFirstWell")) or (0.0, 0.0, 0.0)

    # First Cavity in Compartments. Some real labware uses a stack of shapes
    # (e.g. a round lead-in above a rectangular DWP well), so preserve all
    # segments and keep the first one in `cavity` for compatibility.
    cavity_shapes: list[XcmpCavity] = []
    cavity = None
    cavity_el = _find(pip, "Cavity")
    if cavity_el is not None:
        for shape_el in _findall(cavity_el, "CavityShape"):
            shape_type = (
                shape_el.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}type")
                or "Unknown"
            )
            shape_type = shape_type.split(":")[-1]  # strip namespace prefix
            diameter = _float_text(_find(shape_el, "Diameter"))
            diameter_top = _float_text(_find(shape_el, "DiameterTop"))
            diameter_bottom = _float_text(_find(shape_el, "DiameterBottom"))
            cavity_shapes.append(XcmpCavity(
                shape=shape_type,
                height_mm=_float_text(_find(shape_el, "Height")),
                diameter_top_mm=diameter_top if diameter_top is not None else diameter,
                diameter_bottom_mm=diameter_bottom if diameter_bottom is not None else diameter,
                length_mm=_float_text(_find(shape_el, "Length")),
                width_mm=_float_text(_find(shape_el, "Width")),
                area_top_mm2=_float_text(_find(shape_el, "AreaTop")),
                area_bottom_mm2=_float_text(_find(shape_el, "AreaBottom")),
                width_top_mm=_float_text(_find(shape_el, "WidthTop")),
                width_base_mm=_float_text(_find(shape_el, "WidthBase")),
            ))
        if cavity_shapes:
            cavity = cavity_shapes[0]

    z_heights: dict[str, float] = {}
    z_el = _find(pip, "ZHeights")
    if z_el is not None:
        for kv in list(z_el):
            if not isinstance(kv.tag, str):
                continue
            k = _text(_find(kv, "Key"))
            v = _float_text(_find(kv, "Value"))
            if k and v is not None:
                z_heights[k] = v

    return XcmpPipettable(
        x_wells=x_wells, y_wells=y_wells,
        x_spacing_mm=x_spacing, y_spacing_mm=y_spacing,
        first_well_mm=first_well,
        cavity=cavity,
        cavity_shapes=tuple(cavity_shapes),
        z_heights=z_heights,
    )


def _parse_custom_attrs(template: Optional[ET.Element]) -> dict[str, str]:
    """Pull simple scalar custom attributes (Force, VendorName, PartNumber, …)."""
    if template is None:
        return {}
    out: dict[str, str] = {}
    custom = _find(template, "CustomAttributes")
    if custom is None:
        return out
    for kv in list(custom):
        if not isinstance(kv.tag, str):
            continue
        key = _text(_find(kv, "Key"))
        value_el = _find(kv, "Value")
        string_content = _text(_find(value_el, "StringContent"))
        if not key or string_content is None:
            continue
        # StringContent is itself escaped XML like <int>20</int>; pull the inner text.
        try:
            inner = ET.fromstring(string_content)
            txt = _text(inner)
            if txt is not None:
                out[key] = txt
        except ET.ParseError:
            out[key] = string_content
    return out


def _looks_like_guid(text: str) -> bool:
    """Crude GUID detector — used to filter ObjectName entries that are GUIDs."""
    if len(text) != 36:
        return False
    if text.count("-") != 4:
        return False
    return all(c in "0123456789abcdefABCDEF-" for c in text)


ZERO_GUID = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class XsitSite:
    """Lightweight public metadata from an installed `.xsit` definition."""

    guid: str
    file_path: Path
    location_group_name: Optional[str]
    type_name: Optional[str]
    connector_guids: tuple[str, ...] = ()


def load_xsit(path: Path | str) -> XsitSite:
    """Parse a `.xsit` file into lightweight site metadata."""
    return _load_xsit_cached(str(Path(path).resolve()))


@lru_cache(maxsize=2048)
def _load_xsit_cached(path_str: str) -> XsitSite:
    path = Path(path_str)
    tree = ET.parse(path)
    root = tree.getroot()
    payload = _find(root, "Payload")
    if payload is None:
        raise ValueError(f"{path}: no <Payload> element")

    payload_data = _find(payload, "PayloadData")
    site_template = _find(payload_data, "SiteTemplate") if payload_data is not None else None
    if site_template is None:
        raise ValueError(f"{path}: no <SiteTemplate> payload")

    connector_guids: list[str] = []
    for ref in _direct_children(payload, "Reference"):
        if _text(_find(ref, "TypeId")) != "WorktableConnector":
            continue
        ref_guid = _text(_find(ref, "Guid"))
        if ref_guid:
            connector_guids.append(ref_guid)

    return XsitSite(
        guid=_text(_find(site_template, "GUID")) or path.stem,
        file_path=path,
        location_group_name=_text(_find(site_template, "LocationGroupName")),
        type_name=_text(_find(site_template, "TypeName")),
        connector_guids=tuple(connector_guids),
    )


@lru_cache(maxsize=4096)
def _load_xsit_for_workspace_site(site_guid: str, sites_dir_str: str) -> Optional[XsitSite]:
    if not site_guid or site_guid == ZERO_GUID:
        return None
    path = Path(sites_dir_str) / f"{site_guid}.xsit"
    if not path.exists():
        return None
    try:
        return load_xsit(path)
    except Exception:
        return None


@lru_cache(maxsize=2048)
def _load_component_site_guids_ordered(path_str: str) -> tuple[str, ...]:
    """Return child-site template GUIDs in component arrangement order."""
    path = Path(path_str)
    tree = ET.parse(path)
    root = tree.getroot()
    payload = _find(root, "Payload")
    payload_data = _find(payload, "PayloadData") if payload is not None else None
    template = _find(payload_data, "CarrierOrLabwareTemplate") if payload_data is not None else None
    if template is None:
        return ()

    ordered: list[str] = []
    arrangements = _find(template, "Arrangements")
    for arrangement in _direct_children(arrangements, "ArrangementTemplate"):
        site_ids = _find(arrangement, "SiteTemplateIdentifiers")
        if site_ids is None:
            continue
        indexed_guids: list[tuple[int, str]] = []
        for kv in list(site_ids):
            if not isinstance(kv.tag, str):
                continue
            key = _int_text(_find(kv, "Key"))
            guid = _text(_find(kv, "Value"))
            if key is None or not guid or guid == ZERO_GUID:
                continue
            indexed_guids.append((key, guid))
        if indexed_guids:
            ordered.extend(guid for _, guid in sorted(indexed_guids))

    if ordered:
        return tuple(ordered)

    # Fallback for simple components that only expose top-level site references.
    return load_xcmp(path).site_guids


@lru_cache(maxsize=2048)
def _load_component_site_location_names_cached(
    path_str: str,
    sites_dir_str: str,
) -> tuple[str, ...]:
    names: list[str] = []
    for site_guid in _load_component_site_guids_ordered(path_str):
        site = _load_xsit_for_workspace_site(site_guid, sites_dir_str)
        if site is not None and site.location_group_name:
            names.append(site.location_group_name)
    return tuple(names)


def load_component_site_location_names(path: Path | str) -> tuple[str, ...]:
    """Return a component's child location names in authoring order."""
    resolved = Path(path).resolve()
    sites_dir = resolved.parent.parent / "Sites"
    return _load_component_site_location_names_cached(str(resolved), str(sites_dir))


# ── Workspace (.xwsp) loader ───────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceOccupant:
    """One occupied site in a `.xwsp`."""

    site_path: tuple[int, ...]                # full nested site path
    site_index: int                           # leaf index
    catalog_name: str                         # the labware name (the `current_label`)
    base_location_identifier: Optional[str]   # public logical location name
    base_location_connector_identifier: Optional[str]


@dataclass(frozen=True)
class XwspWorkspace:
    """Top-level view of a `.xwsp` workspace file."""

    guid: str
    name: str
    file_path: Path
    base_worktable_guid: Optional[str]
    base_worktable_name: Optional[str]
    occupants: tuple[WorkspaceOccupant, ...]
    available_sites: tuple[tuple[tuple[int, ...], Optional[str]], ...]
    """All site frames visited in the walk, occupied or not.

    Each tuple is `(site_path, public_location_name)`. Use this to
    enumerate the workspace's *valid* slots before any labware is placed.
    """
    location_names: tuple[str, ...]
    referenced_labware_names: tuple[str, ...]


def load_xwsp(path: Path | str) -> XwspWorkspace:
    """Parse a `.xwsp` file into typed workspace state."""
    return _load_xwsp_cached(str(Path(path).resolve()))


@lru_cache(maxsize=128)
def _load_xwsp_cached(path_str: str) -> XwspWorkspace:
    path = Path(path_str)
    tree = ET.parse(path)
    root = tree.getroot()
    payload = _find(root, "Payload")
    if payload is None:
        raise ValueError(f"{path}: no <Payload> element")

    name = _text(_find(payload, "ObjectName")) or path.stem
    guid = path.stem
    base_worktable_guid, base_worktable_name = _extract_base_worktable_reference(payload)

    sites_dir = path.parent.parent / "Sites"
    occupants, available_sites = _walk_workspace_occupancy(root, sites_dir=sites_dir)
    location_names = _collect_location_names(root)
    location_names.update(
        location_name for _, location_name in available_sites if location_name
    )
    location_names.update(
        occ.base_location_identifier for occ in occupants if occ.base_location_identifier
    )
    labware_names = _collect_labware_names(root)

    return XwspWorkspace(
        guid=guid,
        name=name,
        file_path=path,
        base_worktable_guid=base_worktable_guid,
        base_worktable_name=base_worktable_name,
        occupants=tuple(occupants),
        available_sites=tuple(available_sites),
        location_names=tuple(sorted(location_names)),
        referenced_labware_names=tuple(sorted(labware_names)),
    )


def _extract_base_worktable_reference(
    payload: ET.Element,
) -> tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of the workspace's base worktable component.

    Workspace document identity is the `.xwsp` file stem. Any referenced
    component GUID belongs to the installed worktable parts, not the workspace
    document itself.
    """
    candidates: list[tuple[str, str]] = []
    for ref in _direct_children(payload, "Reference"):
        if _text(_find(ref, "TypeId")) != "WorktableComponent":
            continue
        ref_guid = _text(_find(ref, "Guid"))
        ref_name = _text(_find(ref, "ObjectName"))
        if ref_guid and ref_name and not _looks_like_guid(ref_name):
            candidates.append((ref_guid, ref_name))
    if len(candidates) == 1:
        return candidates[0]
    return None, None


def _walk_workspace_occupancy(
    root: ET.Element,
    *,
    sites_dir: Optional[Path] = None,
) -> tuple[list[WorkspaceOccupant], list[tuple[tuple[int, ...], Optional[str]]]]:
    """Walk the workspace's site tree.

    Returns `(occupants, available_sites)`:
    - `occupants` — sites with a labware name attached.
    - `available_sites` — every site frame visited (occupied or not), as
      `(site_path, base_location_identifier)`. Use to enumerate valid slots
      before any labware is placed.
    """
    entries: list[WorkspaceOccupant] = []
    available: list[tuple[tuple[int, ...], Optional[str]]] = []
    sites_dir_str = str(sites_dir) if sites_dir is not None else ""

    def _extract_label(elem: Optional[ET.Element]) -> Optional[str]:
        if elem is None:
            return None
        labware_name_map: dict[str, str] = {}
        for kv in list(elem):
            if not isinstance(kv.tag, str):
                continue
            if not _local(kv.tag).startswith("KeyValueOfstringstring"):
                continue
            k = _text(_find(kv, "Key"))
            v = _text(_find(kv, "Value"))
            if k and v:
                labware_name_map[k] = v
        return (
            labware_name_map.get("initial")
            or labware_name_map.get("default")
            or (next(iter(labware_name_map.values())) if labware_name_map else None)
        )

    def _resolve_public_location_name(
        value_node: ET.Element,
        component_node: ET.Element,
    ) -> Optional[str]:
        template_guid = _direct_child_text(value_node, "BaseTemplateGuid")
        if template_guid and sites_dir_str:
            site = _load_xsit_for_workspace_site(template_guid, sites_dir_str)
            if site is not None and site.location_group_name:
                return site.location_group_name

        raw_base_location = _direct_child_text(component_node, "BaseLocationIdentifier")
        if (
            raw_base_location
            and raw_base_location != ZERO_GUID
            and not _looks_like_guid(raw_base_location)
        ):
            return raw_base_location
        return None

    def _walk(container: Optional[ET.Element], site_path: tuple[int, ...]) -> None:
        if container is None:
            return
        for node in container:
            if not isinstance(node.tag, str):
                continue
            if not _local(node.tag).startswith("KeyValueOfintSite"):
                continue
            key = _int_text(_direct_child(node, "Key"))
            value_node = _direct_child(node, "Value")
            if key is None or value_node is None:
                continue

            current_path = site_path + (key,)
            connected = _direct_child(value_node, "ConnectedComponent")
            if connected is not None and connected.attrib.get(
                "{http://www.w3.org/2001/XMLSchema-instance}nil"
            ) == "true":
                connected = None
            component_node = connected if connected is not None else value_node

            public_loc = _resolve_public_location_name(value_node, component_node)
            available.append((current_path, public_loc))

            label = _extract_label(_direct_child(component_node, "LabwareName"))
            if label:
                base_conn = _direct_child_text(component_node, "BaseLocationConnectorIdentifier")
                entries.append(WorkspaceOccupant(
                    site_path=current_path,
                    site_index=key,
                    catalog_name=label,
                    base_location_identifier=public_loc,
                    base_location_connector_identifier=base_conn,
                ))

            nested = _direct_child(component_node, "Sites")
            if nested is not None:
                _walk(nested, current_path)
            arrangements = _direct_child(component_node, "Arrangements")
            if arrangements is not None:
                for arrangement in list(arrangements):
                    if not isinstance(arrangement.tag, str):
                        continue
                    if _local(arrangement.tag) != "Arrangement":
                        continue
                    arr_sites = _find(arrangement, "Sites")
                    if arr_sites is not None:
                        _walk(arr_sites, current_path)

    # The workspace's actual site tree is anchored under
    #   <Worktables>/<KeyValueOfstringWorktable*>/<Value>/<Frame>/
    #     <Arrangements>/<Arrangement>/<Sites>
    # Each KeyValueOfstringWorktable entry is one named worktable; its Frame
    # acts like the top-level component, with nested arrangements enumerating
    # carriers, sub-carriers, and leaf placement sites.
    payload = _find(root, "Payload")
    payload_data = _find(payload, "PayloadData") if payload is not None else None
    worktables = next(
        (
            elem
            for elem in (payload_data.iter() if payload_data is not None else [])
            if isinstance(elem.tag, str) and _local(elem.tag) == "Worktables"
        ),
        None,
    )
    if worktables is not None:
        for kv in worktables:
            if not isinstance(kv.tag, str):
                continue
            value_node = _find(kv, "Value")
            if value_node is None:
                continue
            frame = _find(value_node, "Frame")
            if frame is None:
                continue
            arrangements = _find(frame, "Arrangements")
            if arrangements is None:
                continue
            for arrangement in list(arrangements):
                if not isinstance(arrangement.tag, str):
                    continue
                if _local(arrangement.tag) != "Arrangement":
                    continue
                arr_sites = _find(arrangement, "Sites")
                if arr_sites is not None:
                    _walk(arr_sites, tuple())
    return entries, available


def _collect_location_names(root: ET.Element) -> set[str]:
    out: set[str] = set()
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        if _local(elem.tag) != "LocationGroupName":
            continue
        text = _text(elem)
        if text:
            out.add(text)
    return out


def _collect_labware_names(root: ET.Element) -> set[str]:
    out: set[str] = set()
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        if _local(elem.tag) != "LabwareName":
            continue
        for child in elem.iter():
            if isinstance(child.tag, str) and _local(child.tag) == "Value":
                txt = _text(child)
                if txt:
                    out.add(txt)
    return out
