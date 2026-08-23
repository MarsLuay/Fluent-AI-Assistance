#!/usr/bin/env python3
"""Generate procedural fallback geometry specs for mesh-less Fluent components.

Writes per-machine output under ``public/models/fluent/local/`` (gitignored).
Do not commit host CapHolder / Falcon50 nest pins — rebuild from ZEIA DataStore
or host install.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tecan_common import xml_compat as ET

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[3]
DEFAULT_INSTALL = Path(r"C:\ProgramData\Tecan\VisionX\Database")
DEFAULT_FLUENT_MODELS = (
    PROJECT_ROOT / "source/04-protocol-simulator/public/models/fluent"
)
DEFAULT_LOCAL_MODELS = DEFAULT_FLUENT_MODELS / "local"
DEFAULT_REGISTRY = DEFAULT_LOCAL_MODELS / "registry.json"
DEFAULT_ALIASES = (
    PROJECT_ROOT / "source/03-protocol-builder/config/aliases/labware_aliases.yaml"
)
DEFAULT_OUTPUT = DEFAULT_LOCAL_MODELS / "procedural-specs.json"

from fluentcoder.catalog.xcmp import load_xcmp, load_xsit

try:
    from tecan_tools import build_fluent_registry as registry_builder
except ImportError:  # running as a flat module on PYTHONPATH=source/tools
    import build_fluent_registry as registry_builder  # type: ignore

SPEC_SCHEMA_VERSION = 1
SPEC_KIND = "fluent-procedural-geometry-specs"

GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def main() -> int:
    args = parse_args()
    output_path = Path(args.out)
    refuse_tracked_models_root_output(
        output_path, force=bool(args.force_tracked_overwrite)
    )
    payload = build_procedural_specs(
        install_path=Path(args.install),
        registry_path=Path(args.registry) if args.registry else None,
        aliases_path=Path(args.aliases) if args.aliases else None,
        only_priority=args.priority_only,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    summary = payload.get("summary", {})
    print(
        "Procedural fallback specs complete: "
        f"{summary.get('specCount', 0)} specs "
        f"({summary.get('withArrangement', 0)} with arrangement, "
        f"{summary.get('withPipettable', 0)} with pipettable) -> {output_path}"
    )
    return 0


def refuse_tracked_models_root_output(
    output_path: Path, *, force: bool = False
) -> None:
    """Refuse writing host-derived specs beside tracked meshes."""
    if force:
        return
    forbidden = (DEFAULT_FLUENT_MODELS / "procedural-specs.json").resolve()
    if output_path.resolve() == forbidden:
        raise SystemExit(
            f"Refusing to write {forbidden} (tracked models root). "
            f"Use {DEFAULT_LOCAL_MODELS / 'procedural-specs.json'} "
            "(per-machine local rebuild) or pass --force-tracked-overwrite."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install",
        default=str(DEFAULT_INSTALL),
        help="Host DB or extracted DataStore root.",
    )
    parser.add_argument(
        "--registry",
        default=str(DEFAULT_REGISTRY),
        help="registry.json for component-only filter.",
    )
    parser.add_argument(
        "--aliases", default=str(DEFAULT_ALIASES), help="Labware alias YAML."
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUTPUT), help="Output procedural-specs.json path."
    )
    parser.add_argument(
        "--priority-only",
        action="store_true",
        help=(
            "Deprecated no-op kept for CLI compat. Previously filtered CapHolder/TubeEyeX "
            "vendor tokens; now emits the full ZEIA/registry component set."
        ),
    )
    parser.add_argument(
        "--force-tracked-overwrite",
        action="store_true",
        help="Allow writing under the tracked models root (not for normal rebuilds).",
    )
    return parser.parse_args()


def build_procedural_specs(
    *,
    install_path: Path,
    registry_path: Path | None,
    aliases_path: Path | None,
    only_priority: bool,
) -> dict[str, Any]:
    datastore_root, source_type = registry_builder.resolve_datastore_root(install_path)
    components_dir = datastore_root / "SystemSpecific" / "Worktable" / "Components"
    sites_dir = datastore_root / "SystemSpecific" / "Worktable" / "Sites"
    if not components_dir.exists():
        raise FileNotFoundError(f"Components directory not found at {components_dir}")

    alias_map = (
        registry_builder.load_alias_map(aliases_path)
        if aliases_path and aliases_path.exists()
        else {}
    )
    component_targets = load_component_only_targets(registry_path)

    specs: list[dict[str, Any]] = []
    seen_component_guids: set[str] = set()

    _process_registry_targets(
        component_targets=component_targets,
        components_dir=components_dir,
        sites_dir=sites_dir,
        source_type=source_type,
        alias_map=alias_map,
        only_priority=only_priority,
        seen_component_guids=seen_component_guids,
        specs=specs,
    )

    if not only_priority:
        _process_remaining_components(
            components_dir=components_dir,
            sites_dir=sites_dir,
            source_type=source_type,
            alias_map=alias_map,
            seen_component_guids=seen_component_guids,
            specs=specs,
        )

    specs.sort(
        key=lambda row: (row.get("componentName") or "", row.get("componentGuid") or "")
    )
    with_arrangement = sum(
        1 for row in specs if (row.get("sites") or {}).get("x", 0) > 0
    )
    with_pipettable = sum(1 for row in specs if row.get("pipettable"))
    return {
        "schemaVersion": SPEC_SCHEMA_VERSION,
        "kind": SPEC_KIND,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "installPath": str(datastore_root),
            "installSourceType": source_type,
            "registryPath": str(registry_path) if registry_path else None,
            "aliasesPath": str(aliases_path) if aliases_path else None,
        },
        "summary": {
            "specCount": len(specs),
            "withArrangement": with_arrangement,
            "withPipettable": with_pipettable,
            "priorityCount": sum(1 for row in specs if row.get("priority")),
        },
        "specs": specs,
    }


def _process_registry_targets(
    *,
    component_targets: list[dict[str, Any]],
    components_dir: Path,
    sites_dir: Path,
    source_type: str,
    alias_map: dict[str, list[str]],
    only_priority: bool,
    seen_component_guids: set[str],
    specs: list[dict[str, Any]],
) -> None:
    for target in component_targets:
        component_guid = normalize_guid(target.get("componentGuid"))
        component_name = str(
            target.get("componentName") or target.get("objectName") or ""
        ).strip()
        if not component_guid:
            continue
        seen_component_guids.add(component_guid)
        if only_priority and not is_priority_component(component_name):
            continue
        xcmp_path = components_dir / f"{component_guid}.xcmp"
        if not xcmp_path.exists():
            continue
        try:
            component = load_xcmp(xcmp_path)
        except Exception as exc:  # noqa: BLE001
            specs.append(
                {
                    "kind": "unknown",
                    "source": "procedural",
                    "componentGuid": component_guid,
                    "componentName": component_name or component_guid,
                    "dimensionsMm": dimensions_array(target.get("dimensions")),
                    "sites": {"x": 0, "y": 0, "z": 0},
                    "role": "component",
                    "error": str(exc),
                }
            )
            continue
        spec = component_to_spec(
            component,
            alias_map=alias_map,
            sites_dir=sites_dir,
            source_type=source_type,
            registry_row=target,
        )
        specs.append(spec)


def _process_remaining_components(
    *,
    components_dir: Path,
    sites_dir: Path,
    source_type: str,
    alias_map: dict[str, list[str]],
    seen_component_guids: set[str],
    specs: list[dict[str, Any]],
) -> None:
    for xcmp_path in sorted(components_dir.glob("*.xcmp")):
        component_guid = normalize_guid(xcmp_path.stem)
        if not component_guid or component_guid in seen_component_guids:
            continue
        try:
            component = load_xcmp(xcmp_path)
        except Exception:  # noqa: BLE001, S112
            continue
        specs.append(
            component_to_spec(
                component,
                alias_map=alias_map,
                sites_dir=sites_dir,
                source_type=source_type,
                registry_row={
                    "componentGuid": component_guid,
                    "componentName": component.name,
                    "dimensions": None,
                },
            )
        )
        seen_component_guids.add(component_guid)


def load_component_only_targets(registry_path: Path | None) -> list[dict[str, Any]]:
    if registry_path and registry_path.exists():
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        rows = [
            row
            for row in payload.get("entries", [])
            if row.get("componentGuid")
            and not row.get("meshGuid")
            and row.get("sourceType") != "procedural"
        ]
        if rows:
            return rows
    return []


def component_to_spec(
    component,
    *,
    alias_map: dict[str, list[str]],
    sites_dir: Path,
    source_type: str,
    registry_row: dict[str, Any],
) -> dict[str, Any]:
    component_guid = normalize_guid(component.guid)
    component_name = component.name
    kind, role = infer_kind_role(component_name, component.functional_group)
    dim = (
        component.dim_mm
        or dimensions_tuple(registry_row.get("dimensions"))
        or (0.0, 0.0, 0.0)
    )
    arrangement = component.arrangement
    sites = {"x": 0, "y": 0, "z": 0}
    site_spacing_mm = None
    position_in_parent_mm = None
    site_offsets: list[dict[str, Any]] = []
    site_templates: list[dict[str, Any]] = []
    if arrangement is not None:
        sites = {
            "x": arrangement.sites_in_x,
            "y": arrangement.sites_in_y,
            "z": arrangement.sites_in_z,
        }
        site_spacing_mm = {
            "x": arrangement.site_spacing_mm[0],
            "y": abs(arrangement.site_spacing_mm[1]),
            "z": arrangement.site_spacing_mm[2],
        }
        position_in_parent_mm = {
            "x": arrangement.position_in_parent_mm[0],
            "y": arrangement.position_in_parent_mm[1],
            "z": arrangement.position_in_parent_mm[2],
        }
        site_offsets = [
            {"index": index, "x": coords[0], "y": coords[1], "z": coords[2]}
            for index, coords in sorted(arrangement.site_offsets_mm.items())
        ]
        template_map = parse_site_template_identifiers(component.file_path)
        seen_templates: set[str] = set()
        for index, template_guid in sorted(template_map.items()):
            template_row = load_site_template_summary(sites_dir, template_guid)
            if template_guid in seen_templates:
                continue
            seen_templates.add(template_guid)
            site_templates.append(
                {"index": index, "siteTemplateGuid": template_guid, **template_row}
            )

    pipettable_spec = pipettable_to_spec(component.pipettable)
    aliases = sorted(set(alias_map.get(component_name, [])))

    spec: dict[str, Any] = {
        "kind": kind,
        "source": "procedural",
        "componentGuid": component_guid,
        "componentName": component_name,
        "dimensionsMm": [round(dim[0], 3), round(dim[1], 3), round(dim[2], 3)],
        "sites": sites,
        "role": role,
        "priority": is_priority_component(component_name),
        "functionalGroup": component.functional_group,
        "footprint": component.footprint,
        "renderer": component.renderer,
        "aliases": aliases,
        "sourcePath": component.file_path.name,
        "sourceType": source_type,
        "siteIds": sorted(
            {
                normalize_guid(guid)
                for guid in component.site_guids
                if normalize_guid(guid)
            }
        ),
    }
    if site_spacing_mm is not None:
        spec["siteSpacingMm"] = site_spacing_mm
    if position_in_parent_mm is not None:
        spec["positionInParentMm"] = position_in_parent_mm
    if site_offsets:
        spec["siteOffsets"] = site_offsets
    if site_templates:
        spec["siteTemplates"] = site_templates
    if pipettable_spec:
        spec["pipettable"] = pipettable_spec
    return spec


def pipettable_to_spec(pipettable) -> dict[str, Any] | None:
    if pipettable is None:
        return None
    primary = pipettable.primary_cavity
    return {
        "wells": {"x": pipettable.x_wells, "y": pipettable.y_wells},
        "spacingMm": {
            "x": pipettable.x_spacing_mm,
            "y": abs(pipettable.y_spacing_mm),
        },
        "firstWellMm": {
            "x": pipettable.first_well_mm[0],
            "y": pipettable.first_well_mm[1],
            "z": pipettable.first_well_mm[2],
        },
        "wellCount": pipettable.well_count,
        "cavity": primary.to_geometry() if primary else None,
    }


def parse_site_template_identifiers(xcmp_path: Path) -> dict[int, str]:
    tree = ET.parse(xcmp_path)
    root = tree.getroot()
    identifiers: dict[int, str] = {}
    for node in root.iter():
        if local_name(node.tag) != "SiteTemplateIdentifiers":
            continue
        for child in node:
            key_node = None
            value_node = None
            for part in child:
                local = local_name(part.tag)
                if local == "Key":
                    key_node = part
                elif local == "Value":
                    value_node = part
            if key_node is None or value_node is None:
                continue
            try:
                index = int("".join(key_node.itertext()).strip())
            except ValueError:
                continue
            template_guid = normalize_guid("".join(value_node.itertext()).strip())
            if template_guid:
                identifiers[index] = template_guid
    return identifiers


def load_site_template_summary(sites_dir: Path, template_guid: str) -> dict[str, Any]:
    path = sites_dir / f"{template_guid}.xsit"
    if not path.exists():
        return {"locationGroupName": None, "typeName": None, "dimensionsMm": None}
    try:
        site = load_xsit(path)
        dimensions = parse_xsit_dimensions(path)
        return {
            "locationGroupName": site.location_group_name,
            "typeName": site.type_name,
            "dimensionsMm": dimensions,
            "connectorGuids": list(site.connector_guids),
        }
    except Exception:  # noqa: BLE001
        return {
            "locationGroupName": None,
            "typeName": None,
            "dimensionsMm": parse_xsit_dimensions(path),
        }


def parse_xsit_dimensions(path: Path) -> list[float] | None:
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        for node in root.iter():
            if local_name(node.tag) != "Dimension":
                continue
            coords = {}
            for child in node:
                name = local_name(child.tag)
                if name in {"X", "Y", "Z"} and child.text:
                    coords[name] = float(child.text)
            if {"X", "Y", "Z"}.issubset(coords):
                return [
                    round(coords["X"], 3),
                    round(coords["Y"], 3),
                    round(coords["Z"], 3),
                ]
    except Exception:  # noqa: BLE001
        return None
    return None


def infer_kind_role(name: str, functional_group: str | None) -> tuple[str, str]:
    text = f"{name} {functional_group or ''}".lower()
    # Prefer FunctionalGroup from ZEIA .xcmp — no CapHolder / TubeEyeX vendor invent.
    if functional_group:
        group = functional_group.lower()
        if group.startswith("carrier."):
            return "carrier", group.split(".", 1)[-1].replace(".", "_")
        if group.startswith("labware."):
            return "labware", group.split(".", 1)[-1].replace(".", "_")
        if group.startswith("device."):
            return "device", group.split(".", 1)[-1].replace(".", "_")
    if "barcode" in text and "plate" in text:
        return "plate", "barcode_plate"
    if "reference plate" in text:
        return "plate", "reference_plate"
    if "diti" in text:
        if "lid" in text:
            return "lid", "diti_lid"
        if "nest" in text:
            return "nest", "diti_nest"
        return "labware", "diti"
    if re.search(r"\blid\b", text):
        return "lid", "lid"
    if "nest" in text:
        return "nest", "nest"
    if "waste" in text:
        return "waste", "waste"
    if "adapter" in text:
        return "adapter", "adapter"
    if "scanner" in text:
        return "device", "scanner"
    if "manifold" in text:
        return "device", "manifold"
    if "incubator" in text:
        return "device", "incubator"
    if "plate" in text:
        return "plate", "plate"
    if "trough" in text or "reservoir" in text:
        return "reservoir", "reservoir"
    if "rack" in text:
        return "carrier", "rack"
    return "structural", "component"


def is_priority_component(name: str) -> bool:
    """Retired vendor-token filter (CapHolder / TubeEyeX / …).

    Always True so ``--priority-only`` does not invent a host lab subset.
    Emit all ZEIA/registry component-only targets instead.
    """
    del name
    return True


def dimensions_array(dimensions: dict[str, Any] | None) -> list[float]:
    if not dimensions:
        return [0.0, 0.0, 0.0]
    return [
        float(dimensions.get("xMm") or 0.0),
        float(dimensions.get("yMm") or 0.0),
        float(dimensions.get("zMm") or 0.0),
    ]


def dimensions_tuple(
    dimensions: dict[str, Any] | None,
) -> tuple[float, float, float] | None:
    if not dimensions:
        return None
    return (
        float(dimensions.get("xMm") or 0.0),
        float(dimensions.get("yMm") or 0.0),
        float(dimensions.get("zMm") or 0.0),
    )


def normalize_guid(value: str | None) -> str:
    match = GUID_RE.search(value or "")
    return match.group(0).lower() if match else ""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


if __name__ == "__main__":
    raise SystemExit(main())
