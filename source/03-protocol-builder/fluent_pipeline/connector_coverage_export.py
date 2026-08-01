"""Build connector coverage from ZEIA/import worktable geometry.

Profiles are mined from components that actually appear in this geometry /
install — never assumed Resolvex / A200 / CapHolder (or any other) family list.
Resolved GUIDs and connector counts are written under the local context (like
``labware_catalog.json``) — never baked into shipped product source.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .runner import write_json

CONNECTOR_COVERAGE_SCHEMA_VERSION = "tecan.connector_coverage.v1"
CONNECTOR_COVERAGE_FILENAME = "connector_coverage.json"

# Deprecated empty: coverage profiles are mined from ZEIA/install geometry only.
# Kept so older imports of the name do not invent vendor families.
CONNECTOR_COUNT_PROFILES: tuple[dict[str, Any], ...] = ()

_PROFILE_ID_RE = re.compile(r"[^a-z0-9]+")


def build_profiles_from_component_counts(
    component_names: Mapping[str, str],
    per_component_counts: Mapping[str, int],
    *,
    minimum_count: int = 1,
    source: str = "geometry",
) -> list[dict[str, Any]]:
    """One coverage row per component that has connectors in this ZEIA/install."""
    profiles: list[dict[str, Any]] = []
    guids = sorted(
        {
            str(guid).strip().casefold()
            for guid in set(component_names) | set(per_component_counts)
            if str(guid or "").strip()
        }
    )
    for guid in guids:
        actual = int(per_component_counts.get(guid) or 0)
        if actual <= 0:
            continue
        name = str(component_names.get(guid) or "").strip() or guid
        profile_id = _profile_id_for(name, guid)
        profiles.append(
            {
                "id": profile_id,
                "componentGuid": guid,
                "componentName": name,
                "namePatterns": [name.casefold()],
                "matchedComponentGuids": [guid],
                "matchedComponentNames": [name],
                "expectedCount": minimum_count,
                "minimumCount": minimum_count,
                "actualCount": actual,
                "matches": actual >= minimum_count,
                "source": source,
            }
        )
    profiles.sort(key=lambda row: (str(row.get("componentName") or ""), str(row.get("id") or "")))
    return profiles


def build_connector_coverage_from_geometry(
    geometry: Mapping[str, Any] | None,
    *,
    source: str = "worktable_geometry",
) -> dict[str, Any]:
    """Mine connector coverage profiles from this ZEIA/install geometry only."""
    geometry = geometry if isinstance(geometry, Mapping) else {}
    components = [item for item in (geometry.get("components") or []) if isinstance(item, Mapping)]
    connectors = [item for item in (geometry.get("connectors") or []) if isinstance(item, Mapping)]
    sites = [item for item in (geometry.get("sites") or []) if isinstance(item, Mapping)]

    component_names: dict[str, str] = {}
    site_owner: dict[str, str] = {}
    for component in components:
        guid = _norm_guid(component.get("guid"))
        name = str(component.get("name") or component.get("object_name") or "").strip()
        if guid and name:
            component_names[guid] = name
        if not guid:
            continue
        for site_guid in component.get("site_guids") or []:
            site_key = _norm_guid(site_guid)
            if site_key and site_key not in site_owner:
                site_owner[site_key] = guid

    per_component_counts: Counter[str] = Counter()
    for connector in connectors:
        child = _norm_guid(connector.get("component_guid"))
        site = _norm_guid(connector.get("site_guid"))
        parent = site_owner.get(site) if site else None
        # Prefer the site parent (snap host). Fall back to connector component when
        # ownership is unknown so sparse geometry still yields coverage rows.
        if parent:
            per_component_counts[parent] += 1
            if child and child != parent and connector.get("component_name"):
                component_names.setdefault(child, str(connector.get("component_name")))
        elif child:
            per_component_counts[child] += 1
            if connector.get("component_name"):
                component_names.setdefault(child, str(connector.get("component_name")))

    for site in sites:
        site_guid = _norm_guid(site.get("guid"))
        parent = site_owner.get(site_guid) if site_guid else None
        if not parent:
            continue
        listed = site.get("connector_guids") or []
        if isinstance(listed, (list, tuple)) and listed and per_component_counts[parent] == 0:
            per_component_counts[parent] = len([item for item in listed if str(item or "").strip()])

    for component in components:
        guid = _norm_guid(component.get("guid"))
        if not guid:
            continue
        listed = component.get("connector_guids") or []
        if isinstance(listed, (list, tuple)) and listed and per_component_counts[guid] == 0:
            per_component_counts[guid] = len([item for item in listed if str(item or "").strip()])

    profiles = build_profiles_from_component_counts(
        component_names,
        per_component_counts,
        minimum_count=1,
        source=source,
    )
    matched = sum(1 for row in profiles if row.get("matches"))
    return {
        "schema_version": CONNECTOR_COVERAGE_SCHEMA_VERSION,
        "source": source,
        "summary": {
            "profileCount": len(profiles),
            "matchedProfiles": matched,
            "componentCount": len(component_names),
            "connectorCount": len(connectors),
        },
        "profiles": profiles,
    }


def write_connector_coverage(
    destination: Path,
    geometry: Mapping[str, Any] | None,
    *,
    source: str = "worktable_geometry",
) -> Path | None:
    """Write ``connector_coverage.json`` when geometry has components. Return path or None."""
    geometry = geometry if isinstance(geometry, Mapping) else {}
    if not geometry.get("components"):
        return None
    payload = build_connector_coverage_from_geometry(geometry, source=source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, payload)
    return destination


def write_connector_coverage_for_context(
    context_root: Path,
    geometry: Mapping[str, Any] | None,
) -> Path | None:
    """Write coverage next to ``manifest.json`` / ``labware_catalog.json`` under a context root."""
    return write_connector_coverage(Path(context_root) / CONNECTOR_COVERAGE_FILENAME, geometry)


def _profile_id_for(name: str, guid: str) -> str:
    slug = _PROFILE_ID_RE.sub("_", (name or "").casefold()).strip("_")
    if slug:
        return slug[:80]
    short = (guid or "").replace("-", "")[:12]
    return f"component_{short or 'unknown'}"


def _component_guids_matching_names(
    component_names: dict[str, str],
    patterns: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Deprecated helper kept for callers that still filter by name substring."""
    if not patterns:
        return []
    matched: list[str] = []
    for guid, name in component_names.items():
        text = (name or "").casefold()
        if text and any(pattern.casefold() in text for pattern in patterns):
            if guid not in matched:
                matched.append(guid)
    matched.sort()
    return matched


def _norm_guid(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return text if text and text != "00000000-0000-0000-0000-000000000000" else ""
