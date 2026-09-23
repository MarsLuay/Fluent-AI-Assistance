"""Conservative, source-backed route analysis for RGA labware transfers.

This module analyzes carrier/site capability records; it does not alter
``RgaTransferLabwareStep`` and it does not model PathFinder or physical motion.
"""

from __future__ import annotations

from typing import Any, Mapping


DIRECT_SUPPORTED = "direct_supported_by_source"
REGRIP_RESOLVED = "regrip_required_and_resolved"
MISSING_VECTOR_EVIDENCE = "route_unknown_missing_vector_evidence"
NO_COMMON_ROUTE = "no_common_vector_or_grip_mode"
UNRESOLVED_SITE = "source_or_destination_unresolved"
TARGET_MISMATCH = "target_configuration_mismatch"


def analyze_rga_route(
    source_site: Mapping[str, Any] | None,
    destination_site: Mapping[str, Any] | None,
    *,
    catalog: Mapping[str, Any] | None = None,
    target_catalog_fingerprint: str | None = None,
    expected_catalog_fingerprint: str | None = None,
    command_provenance: Mapping[str, Any] | None = None,
    pathfinder_status: str = "not_evaluated",
    physical_verification_required: bool = True,
) -> dict[str, Any]:
    """Return deterministic route metadata from explicit site capabilities.

    Site names are retained for reporting only. Identity resolution requires a
    GUID/id, and an absent capability is never converted into a guessed route.
    """

    source = _normalize_site(source_site)
    destination = _normalize_site(destination_site)
    catalog = catalog or {}
    catalog_fingerprint = _catalog_fingerprint(catalog)
    result: dict[str, Any] = {
        "schema_version": "tecan.rga_route_analysis.v1",
        "status": UNRESOLVED_SITE,
        "source_site": source,
        "destination_site": destination,
        "candidate_vectors": [],
        "shared_vectors": [],
        "grip_mode_intersection": [],
        "route_candidates": [],
        "selected_route": None,
        "regrip_path": None,
        "unresolved": [],
        "findings": [],
        "catalog_fingerprint": catalog_fingerprint,
        "command_provenance": dict(command_provenance or {}),
        "pathfinder": {"status": pathfinder_status, "owned_by": "pathfinder_analysis"},
        "physical_verification": {
            "status": "required" if physical_verification_required else "not_requested",
            "owned_by": "physical_verification",
        },
    }

    if expected_catalog_fingerprint and target_catalog_fingerprint != expected_catalog_fingerprint:
        result["status"] = TARGET_MISMATCH
        result["unresolved"] = ["target_catalog_fingerprint_drift"]
        result["target_catalog_fingerprint"] = target_catalog_fingerprint
        result["expected_catalog_fingerprint"] = expected_catalog_fingerprint
        return result

    if not source["identity"] or not destination["identity"]:
        result["unresolved"] = ["source_or_destination_identity"]
        return result

    source_vectors = _capability_values(source, "allowed_vector_ids")
    destination_vectors = _capability_values(destination, "allowed_vector_ids")
    source_grips = _capability_values(source, "allowed_grip_modes")
    destination_grips = _capability_values(destination, "allowed_grip_modes")
    result["candidate_vectors"] = sorted(set(source_vectors) | set(destination_vectors))
    result["shared_vectors"] = sorted(set(source_vectors) & set(destination_vectors))
    result["grip_mode_intersection"] = sorted(set(source_grips) & set(destination_grips))

    source_vector_evidence = str(source.get("allowed_vector_evidence") or "unknown")
    destination_vector_evidence = str(destination.get("allowed_vector_evidence") or "unknown")
    if not source_vectors or not destination_vectors or "unknown" in {source_vector_evidence, destination_vector_evidence}:
        result["status"] = MISSING_VECTOR_EVIDENCE
        result["unresolved"] = ["source_or_destination_vector_evidence"]
        return result

    vector_records = _vector_records(catalog)
    shared = result["shared_vectors"]
    missing_records = [vector_id for vector_id in shared if vector_id not in vector_records]
    if missing_records:
        result["status"] = MISSING_VECTOR_EVIDENCE
        result["unresolved"] = ["vector_definition:" + vector_id for vector_id in missing_records]
        return result

    if not shared or not result["grip_mode_intersection"]:
        regrip_candidates = _regrip_candidates(source, destination, catalog)
        if regrip_candidates:
            result["status"] = REGRIP_RESOLVED
            result["route_candidates"] = regrip_candidates
            result["regrip_path"] = regrip_candidates[0] if len(regrip_candidates) == 1 else None
            if len(regrip_candidates) > 1:
                result["findings"] = ["multiple_regrip_routes_remain_ambiguous"]
            return result
        result["status"] = NO_COMMON_ROUTE
        result["findings"] = [
            "no_common_vector" if not shared else "no_common_grip_mode",
        ]
        return result

    for vector_id in shared:
        for grip_mode in result["grip_mode_intersection"]:
            result["route_candidates"].append(
                {
                    "kind": "direct",
                    "vector_id": vector_id,
                    "grip_mode": grip_mode,
                }
            )
    result["status"] = DIRECT_SUPPORTED
    if len(result["route_candidates"]) == 1:
        result["selected_route"] = result["route_candidates"][0]
    else:
        result["findings"] = ["multiple_direct_routes_remain_ambiguous"]
    consistency = _endpoint_consistency_findings(source, destination, result["shared_vectors"], vector_records)
    result["findings"].extend(consistency)
    return result


def _normalize_site(site: Mapping[str, Any] | None) -> dict[str, Any]:
    site = site or {}
    identity = str(site.get("guid") or site.get("id") or site.get("site_guid") or "").strip()
    vector_ids = _as_strings(site.get("allowed_vector_ids"))
    vector_evidence = site.get("allowed_vector_evidence")
    if vector_evidence is None:
        vector_evidence = "present" if vector_ids else "unknown"
    return {
        "identity": identity,
        "name": str(site.get("name") or site.get("site_name") or "").strip(),
        "allowed_vector_ids": vector_ids,
        "allowed_vector_evidence": vector_evidence,
        "allowed_grip_modes": _as_strings(site.get("allowed_grip_modes")),
        "regrip_site_ids": _as_strings(site.get("regrip_site_ids")),
        "placement_adjustment": site.get("placement_adjustment") or site.get("placement_adjustment_mm"),
        "raw": dict(site),
    }


def _capability_values(site: Mapping[str, Any], key: str) -> list[str]:
    return sorted(set(_as_strings(site.get(key))))


def _as_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _catalog_fingerprint(catalog: Mapping[str, Any]) -> str | None:
    return str(catalog.get("fingerprint") or catalog.get("rga_routing", {}).get("fingerprint") or "") or None


def _vector_records(catalog: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = catalog.get("vectors")
    if raw is None and isinstance(catalog.get("rga_routing"), Mapping):
        raw = catalog["rga_routing"].get("vectors")
    records: dict[str, Mapping[str, Any]] = {}
    for item in raw or []:
        if not isinstance(item, Mapping):
            continue
        identity = str(item.get("id") or item.get("guid") or item.get("vector_id") or "").strip()
        if identity:
            records[identity] = item
    return records


def _regrip_candidates(
    source: Mapping[str, Any],
    destination: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw = catalog.get("regrip_routes") or catalog.get("regrip_paths")
    if raw is None and isinstance(catalog.get("rga_routing"), Mapping):
        raw = catalog["rga_routing"].get("regrip_routes") or catalog["rga_routing"].get("regrip_paths")
    candidates: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("source_site_guid") or item.get("source_site_id") or "")
        destination_id = str(item.get("destination_site_guid") or item.get("destination_site_id") or "")
        if source_id != source.get("identity") or destination_id != destination.get("identity"):
            continue
        candidates.append(
            {
                "kind": "regrip",
                "station_id": str(item.get("station_id") or item.get("regrip_site_id") or ""),
                "vector_ids": _as_strings(item.get("vector_ids")),
                "grip_modes": _as_strings(item.get("grip_modes")),
                "source_provenance": item.get("source_provenance") or item.get("provenance"),
            }
        )
    return sorted(candidates, key=lambda item: (item.get("station_id", ""), item.get("source_provenance") or ""))


def _endpoint_consistency_findings(
    source: Mapping[str, Any],
    destination: Mapping[str, Any],
    vector_ids: list[str],
    vector_records: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    findings: list[str] = []
    for site in (source, destination):
        adjustment = site.get("placement_adjustment")
        if not isinstance(adjustment, Mapping):
            continue
        for vector_id in vector_ids:
            endpoint = vector_records.get(vector_id, {}).get("end_position")
            if isinstance(endpoint, Mapping) and _vector_differs(endpoint, adjustment):
                findings.append("vector_endpoint_placement_inconsistent")
                break
    return sorted(set(findings))


def _vector_differs(left: Mapping[str, Any], right: Mapping[str, Any], *, tolerance: float = 1e-9) -> bool:
    for axis in ("x", "y", "z"):
        try:
            if abs(float(left[axis]) - float(right[axis])) > tolerance:
                return True
        except (KeyError, TypeError, ValueError):
            return True
    return False
