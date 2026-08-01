"""Build a labware catalog from an imported ZEIA worktable geometry.

Site-specific FluentControl object names (custom labware, local folders) belong
in this generated artifact under the local context/build tree — never as
hardcoded product catalog entries. ``ready-to-import/`` is gitignored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .fluent_naming import strip_fluent_instance_suffix
from .runner import write_json
from .worktable_datastore import (
    discover_worktable_datastore,
    resolve_worktable_datastore,
)

LABWARE_CATALOG_SCHEMA_VERSION = "tecan.labware_catalog.v1"
LABWARE_CATALOG_FILENAME = "labware_catalog.json"
_WORKTABLE_REL = Path("SystemSpecific") / "Worktable"


def build_labware_catalog_from_geometry(
    geometry: Mapping[str, Any] | None,
    *,
    source: str = "worktable_geometry",
) -> dict[str, Any]:
    """Convert ``manifest['worktable_geometry']`` into a portable catalog JSON."""
    geometry = geometry if isinstance(geometry, Mapping) else {}
    components = [item for item in (geometry.get("components") or []) if isinstance(item, Mapping)]
    workspaces = [item for item in (geometry.get("workspaces") or []) if isinstance(item, Mapping)]
    sites_by_guid = {
        str(item.get("guid") or "").strip(): item
        for item in (geometry.get("sites") or [])
        if isinstance(item, Mapping) and str(item.get("guid") or "").strip()
    }

    aliases_by_guid: dict[str, set[str]] = {}
    aliases_by_name: dict[str, set[str]] = {}
    for workspace in workspaces:
        for placement in workspace.get("placements") or []:
            if not isinstance(placement, Mapping):
                continue
            label = str(placement.get("label") or placement.get("name") or "").strip()
            catalog = str(
                placement.get("catalog")
                or placement.get("component_name")
                or placement.get("name")
                or ""
            ).strip()
            guid = str(placement.get("component_guid") or "").strip().casefold()
            if label:
                if guid:
                    aliases_by_guid.setdefault(guid, set()).add(label)
                if catalog:
                    aliases_by_name.setdefault(_norm(catalog), set()).add(label)
            if catalog and guid:
                aliases_by_guid.setdefault(guid, set()).add(catalog)

    entries: list[dict[str, Any]] = []
    for component in components:
        name = str(component.get("name") or component.get("object_name") or "").strip()
        if not name:
            continue
        guid = str(component.get("guid") or "").strip()
        mesh_guids = _string_list(component.get("mesh_guids"))
        mesh_names = _string_list(component.get("mesh_names"))
        dimension = component.get("dimension_mm") if isinstance(component.get("dimension_mm"), Mapping) else {}
        arrangement = _primary_arrangement(component.get("arrangements") or [])
        pipettable = component.get("pipettable") if isinstance(component.get("pipettable"), Mapping) else {}
        rows, cols, pitch_x, pitch_y = _well_or_site_grid(arrangement, pipettable)
        aliases = sorted(
            {
                name,
                *aliases_by_guid.get(guid.casefold(), set()),
                *aliases_by_name.get(_norm(name), set()),
            },
            key=str.casefold,
        )
        # Instance labels already included; also expose bare type for [001]-style lookups.
        for alias in list(aliases):
            bare = strip_fluent_instance_suffix(alias)
            if bare and bare not in aliases:
                aliases.append(bare)

        site_templates = _site_templates(component.get("arrangements") or [], sites_by_guid)
        grip = _grip_payload(component, arrangement)
        compatible_names = _string_list(component.get("compatible_component_names"))
        compatible_guids = _string_list(component.get("compatible_component_guids"))
        for nested in _string_list(component.get("sub_component_names")):
            if nested not in compatible_names:
                compatible_names.append(nested)

        entries.append(
            _clean(
                {
                    "name": name,
                    "guid": guid or None,
                    "mesh_guid": mesh_guids[0] if mesh_guids else None,
                    "mesh_guids": mesh_guids or None,
                    "mesh_names": mesh_names or None,
                    "aliases": aliases,
                    "functional_group": component.get("functional_group"),
                    "footprint": component.get("footprint"),
                    "renderer": component.get("renderer"),
                    "physical_width_mm": _float(dimension.get("x")),
                    "physical_depth_mm": _float(dimension.get("y")),
                    "physical_height_mm": _float(dimension.get("z")),
                    "rows": rows,
                    "cols": cols,
                    "pitch_x_mm": pitch_x,
                    "pitch_y_mm": pitch_y,
                    "well_diameter_mm": _float(pipettable.get("well_diameter_mm")),
                    "well_depth_mm": _float(pipettable.get("well_depth_mm")),
                    "well_shape": pipettable.get("well_shape"),
                    "max_volume_ul": _float(pipettable.get("max_volume_ul")),
                    "pipettable": _clean_mapping(pipettable) or None,
                    "grip": grip,
                    "site_templates": site_templates or None,
                    "compatible_components": _compatible_component_rows(
                        names=compatible_names,
                        guids=compatible_guids,
                    )
                    or None,
                    "compatible_component_names": compatible_names or None,
                    "compatible_component_guids": compatible_guids or None,
                    "custom_attributes": _clean_mapping(component.get("custom_attributes")) or None,
                    "source_path": component.get("path"),
                }
            )
        )

    entries.sort(key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("guid") or "")))
    return {
        "schema_version": LABWARE_CATALOG_SCHEMA_VERSION,
        "source": source,
        "entry_count": len(entries),
        "entries": entries,
    }


def write_labware_catalog(
    destination: Path,
    geometry: Mapping[str, Any] | None,
    *,
    source: str = "worktable_geometry",
    context_root: Path | str | None = None,
    datastore_root: Path | str | None = None,
) -> Path | None:
    """Write ``labware_catalog.json`` from geometry and/or Components ``*.xcmp`` walk."""
    catalog = build_labware_catalog_for_package(
        geometry,
        context_root=context_root or Path(destination).parent,
        datastore_root=datastore_root,
        source=source,
    )
    if not catalog or not catalog.get("entries"):
        return None
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, catalog)
    return destination


def write_labware_catalog_for_context(
    context_root: Path,
    geometry: Mapping[str, Any] | None,
) -> Path | None:
    """Write the catalog next to ``manifest.json`` under a project context root."""
    return write_labware_catalog(
        Path(context_root) / LABWARE_CATALOG_FILENAME,
        geometry,
        context_root=context_root,
    )


def build_labware_catalog_for_package(
    geometry: Mapping[str, Any] | None = None,
    *,
    context_root: Path | str | None = None,
    datastore_root: Path | str | None = None,
    source: str = "worktable_geometry",
) -> dict[str, Any] | None:
    """Prefer geometry-mined catalog; fall back to Components ``*.xcmp`` for large ZEIA."""
    geometry_catalog = build_labware_catalog_from_geometry(geometry, source=source)
    if geometry_catalog.get("entries"):
        return geometry_catalog
    root = resolve_worktable_datastore(datastore_root)
    if root is None and context_root is not None:
        root = discover_worktable_datastore(context_root)
    if root is None:
        return geometry_catalog if geometry_catalog.get("entries") else None
    return build_labware_catalog_from_datastore(root, source="zeia_components")


def build_labware_catalog_from_datastore(
    datastore_root: Path | str | None,
    *,
    source: str = "zeia_components",
    max_xml_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any] | None:
    """Walk ``Components/*.xcmp`` (+ Sites for site dims) when detailed geometry was skipped."""
    root = resolve_worktable_datastore(datastore_root)
    if root is None:
        return None
    components_dir = root / _WORKTABLE_REL / "Components"
    if not components_dir.is_dir():
        return None

    from .worktable_geometry import parse_component, parse_site

    components: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in sorted(components_dir.glob("*.xcmp")):
        try:
            components.append(parse_component(path, max_xml_bytes=max_xml_bytes))
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path), "error": str(exc)})
    if not components:
        return None

    sites_by_guid: dict[str, dict[str, Any]] = {}
    sites_dir = root / _WORKTABLE_REL / "Sites"
    needed_sites: set[str] = set()
    for component in components:
        for arrangement in component.get("arrangements") or []:
            if not isinstance(arrangement, Mapping):
                continue
            identifiers = arrangement.get("site_template_identifiers")
            if not isinstance(identifiers, Mapping):
                continue
            for guid in identifiers.values():
                text = str(guid or "").strip()
                if text:
                    needed_sites.add(text)
        for guid in component.get("site_guids") or []:
            text = str(guid or "").strip()
            if text:
                needed_sites.add(text)
    if sites_dir.is_dir():
        for site_guid in sorted(needed_sites):
            path = sites_dir / f"{site_guid}.xsit"
            if not path.is_file():
                continue
            try:
                site = parse_site(path, max_xml_bytes=max_xml_bytes)
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": str(path), "error": str(exc)})
                continue
            guid = str(site.get("guid") or site_guid).strip()
            if guid:
                sites_by_guid[guid] = site

    geometry = {
        "components": components,
        "sites": list(sites_by_guid.values()),
        "workspaces": [],
    }
    catalog = build_labware_catalog_from_geometry(geometry, source=source)
    if errors:
        catalog["parse_errors"] = errors[:50]
        catalog["parse_error_count"] = len(errors)
    return catalog


def alias_maps_from_labware_catalog(catalog: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    """Derive labware/catalog alias maps from a ZEIA-built catalog (instance → type)."""
    labware: dict[str, str] = {}
    catalog_aliases: dict[str, str] = {}
    if not isinstance(catalog, Mapping):
        return {"labware_aliases": labware, "catalog_aliases": catalog_aliases}
    for entry in catalog.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        for alias in entry.get("aliases") or []:
            text = str(alias or "").strip()
            if not text:
                continue
            bare = strip_fluent_instance_suffix(text)
            if text != name:
                labware[text] = name
            if bare and bare != text:
                labware[text] = name
                catalog_aliases.setdefault(text, name)
            if bare and bare != name:
                catalog_aliases.setdefault(bare, name)
            catalog_aliases.setdefault(text, name)
        catalog_aliases.setdefault(name, name)
    return {"labware_aliases": labware, "catalog_aliases": catalog_aliases}


def load_labware_catalog(path: Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).is_file():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _well_or_site_grid(
    arrangement: Mapping[str, Any],
    pipettable: Mapping[str, Any],
) -> tuple[int | None, int | None, float | None, float | None]:
    if pipettable:
        rows = int(pipettable.get("rows") or 0) or None
        cols = int(pipettable.get("cols") or 0) or None
        return (
            rows,
            cols,
            _float(pipettable.get("pitch_x_mm")),
            _abs_float(pipettable.get("pitch_y_mm")),
        )
    if not arrangement:
        return None, None, None, None
    rows = int(arrangement.get("sites_in_y") or arrangement.get("sites_in_x") or 0) or 1
    cols = int(arrangement.get("sites_in_x") or 0) or 1
    if arrangement.get("sites_in_y") in (None, 0) and arrangement.get("sites_in_x"):
        rows = 1
        cols = int(arrangement.get("sites_in_x") or 1)
    spacing = arrangement.get("site_spacing_mm") if isinstance(arrangement.get("site_spacing_mm"), Mapping) else {}
    return rows, cols, _float(spacing.get("x")), _float(spacing.get("y"))


def _site_templates(arrangements: Any, sites_by_guid: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for arrangement in arrangements or []:
        if not isinstance(arrangement, Mapping):
            continue
        identifiers = arrangement.get("site_template_identifiers")
        if not isinstance(identifiers, Mapping):
            continue
        for index, guid in sorted(identifiers.items(), key=lambda item: _sort_index(item[0])):
            text = str(guid or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            site = sites_by_guid.get(text) or {}
            rows.append(
                _clean(
                    {
                        "index": _int_or_none(index),
                        "guid": text,
                        "location_group_name": site.get("location_group_name") or site.get("pin_name"),
                        "type_name": site.get("type_name"),
                        "site_kind": site.get("site_kind"),
                        "dimension_mm": site.get("dimension_mm"),
                    }
                )
            )
    return rows


def _grip_payload(component: Mapping[str, Any], arrangement: Mapping[str, Any]) -> dict[str, Any] | None:
    modes: dict[str, list[str]] = {}
    for item in component.get("arrangements") or []:
        if not isinstance(item, Mapping):
            continue
        allowed = item.get("allowed_grip_modes")
        if not isinstance(allowed, Mapping):
            continue
        for site_index, cgas in allowed.items():
            values = [str(cga).strip() for cga in (cgas or []) if str(cga).strip()]
            if values:
                modes[str(site_index)] = values
    if not modes and isinstance(arrangement.get("allowed_grip_modes"), Mapping):
        for site_index, cgas in arrangement["allowed_grip_modes"].items():
            values = [str(cga).strip() for cga in (cgas or []) if str(cga).strip()]
            if values:
                modes[str(site_index)] = values
    custom = component.get("custom_attributes") if isinstance(component.get("custom_attributes"), Mapping) else {}
    force = custom.get("Force")
    payload = _clean(
        {
            "allowed_modes": modes or None,
            "force": force,
        }
    )
    return payload or None


def _compatible_component_rows(*, names: list[str], guids: list[str]) -> list[dict[str, Any]]:
    """Emit guid/name refs without inventing guid↔name pairings."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for guid in guids:
        key = (guid, "")
        if key in seen:
            continue
        seen.add(key)
        rows.append({"guid": guid})
    for name in names:
        key = ("", name)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name})
    return rows


def _primary_arrangement(arrangements: Any) -> dict[str, Any]:
    for item in arrangements or []:
        if isinstance(item, Mapping):
            return dict(item)
    return {}


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _abs_float(value: Any) -> float | None:
    number = _float(value)
    return abs(number) if number is not None else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sort_index(value: Any) -> tuple[int, str]:
    try:
        return (0, f"{int(value):08d}")
    except (TypeError, ValueError):
        return (1, str(value))


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _clean_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return _clean(dict(value))


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}
