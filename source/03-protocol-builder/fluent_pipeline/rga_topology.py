"""Source-backed stacked-labware and storage-carrier topology analysis."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


VERIFIED = "verified"
UNKNOWN = "unknown"
AMBIGUOUS = "ambiguous"
INVALID = "invalid"


def normalize_rga_topology(source: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize explicit topology evidence without name-based inference."""

    source = source or {}
    stack_relationships = _normalize_stack_relationships(source.get("stack_relationships"))
    storage_sites = _normalize_storage_sites(source.get("storage_sites"))
    occupancy = _normalize_occupancy(source.get("occupancy"))
    diagnostics: list[str] = []

    orders = [site["order"] for site in storage_sites if site.get("order") is not None]
    if storage_sites and (len(orders) != len(storage_sites) or len(set(orders)) != len(orders)):
        order_status = AMBIGUOUS
        diagnostics.append("storage_order_ambiguous")
    elif orders:
        order_status = VERIFIED
    else:
        order_status = UNKNOWN

    capacity = source.get("capacity")
    if isinstance(capacity, int) and capacity >= 0 and len(occupancy) > capacity:
        diagnostics.append("storage_capacity_overflow")
    for item in storage_sites:
        if not item["identity"]:
            diagnostics.append("storage_site_unresolved")
    status = INVALID if "storage_capacity_overflow" in diagnostics else (
        AMBIGUOUS if diagnostics or order_status == AMBIGUOUS else VERIFIED if stack_relationships or storage_sites else UNKNOWN
    )
    normalized = {
        "schema_version": "tecan.rga_topology.v1",
        "status": status,
        "carrier_identity": _identity(source),
        "carrier_type": source.get("carrier_type"),
        "stack_relationships": stack_relationships,
        "storage_sites": storage_sites,
        "storage_order_status": order_status,
        "occupancy": occupancy,
        "capacity": capacity,
        "diagnostics": sorted(set(diagnostics)),
        "provenance": source.get("provenance") or {},
        "route_analysis_fingerprint": source.get("route_analysis_fingerprint"),
    }
    fingerprint_input = {key: value for key, value in normalized.items() if key != "provenance"}
    normalized["fingerprint"] = _fingerprint(fingerprint_input)
    return normalized


def analyze_storage_transition(
    topology: Mapping[str, Any],
    *,
    source_site_id: str,
    destination_site_id: str,
    labware_id: str,
    route_analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess a logical storage transition while preserving route uncertainty."""

    sites = {str(site.get("identity")): site for site in topology.get("storage_sites") or []}
    result = {
        "schema_version": "tecan.rga_topology_transition.v1",
        "status": "unknown",
        "source_site_id": source_site_id,
        "destination_site_id": destination_site_id,
        "labware_id": labware_id,
        "topology_fingerprint": topology.get("fingerprint"),
        "diagnostics": [],
        "rga_route_status": str((route_analysis or {}).get("status") or "not_evaluated"),
        "route_catalog_fingerprint": (route_analysis or {}).get("catalog_fingerprint"),
        "physical_verification": "required",
    }
    if source_site_id not in sites or destination_site_id not in sites:
        result["diagnostics"] = ["storage_site_unresolved"]
        return result
    if topology.get("status") in {INVALID, AMBIGUOUS}:
        result["diagnostics"] = list(topology.get("diagnostics") or []) or ["topology_not_proven"]
        return result
    result["status"] = "logical_transition_supported_by_source"
    result["source_site"] = sites[source_site_id]
    result["destination_site"] = sites[destination_site_id]
    return result


def _identity(source: Mapping[str, Any]) -> str:
    return str(source.get("guid") or source.get("id") or source.get("carrier_guid") or "").strip()


def _normalize_stack_relationships(values: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        records.append(
            {
                "parent_id": str(value.get("parent_id") or value.get("base_id") or ""),
                "child_id": str(value.get("child_id") or value.get("labware_id") or ""),
                "relationship": str(value.get("relationship") or "stacked")
                if value.get("relationship") or value.get("relationship") == ""
                else "stacked",
                "site_id": str(value.get("site_id") or ""),
                "orientation": value.get("orientation"),
                "level": value.get("level"),
                "source_provenance": value.get("source_provenance") or value.get("provenance"),
                "raw": value.get("raw"),
            }
        )
    return sorted(records, key=lambda item: (item["parent_id"], item["child_id"], item["site_id"]))


def _normalize_storage_sites(values: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        records.append(
            {
                "identity": str(value.get("guid") or value.get("id") or value.get("site_guid") or "").strip(),
                "carrier_type": value.get("carrier_type"),
                "site_index": value.get("site_index"),
                "group_id": value.get("group_id") or value.get("labware_group_id"),
                "order": value.get("order") if isinstance(value.get("order"), int) else None,
                "transfer_site_id": value.get("transfer_site_id"),
                "orientation": value.get("orientation"),
                "source_provenance": value.get("source_provenance") or value.get("provenance"),
                "raw": value.get("raw"),
            }
        )
    return sorted(records, key=lambda item: (str(item["order"]), item["identity"]))


def _normalize_occupancy(values: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        records.append(
            {
                "site_id": str(value.get("site_id") or ""),
                "labware_id": str(value.get("labware_id") or ""),
                "stack_level": value.get("stack_level"),
                "orientation": value.get("orientation"),
                "source_provenance": value.get("source_provenance") or value.get("provenance"),
            }
        )
    return sorted(records, key=lambda item: (item["site_id"], str(item["stack_level"]), item["labware_id"]))


def _fingerprint(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
