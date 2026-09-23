"""Shared RGA transfer assessment at validation, generation, and simulation boundaries.

The route and topology analyzers deliberately remain independent of command XML.
This adapter carries their source-backed results alongside an IR move without
turning vectors, grips, or storage details into FluentControl parameters.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .rga_routing import DIRECT_SUPPORTED, REGRIP_RESOLVED, analyze_rga_route
from .rga_topology import analyze_storage_transition, normalize_rga_topology


RGA_TRANSFER_SCHEMA_VERSION = "tecan.rga_transfer_assessment.v1"
_ROUTE_OK = {DIRECT_SUPPORTED, REGRIP_RESOLVED}
_TOPOLOGY_OK = {"logical_transition_supported_by_source"}
_RGA_INPUT_KEYS = {
    "rga_route_input",
    "rga_route_evidence",
    "route_evidence",
    "rga_topology_input",
    "rga_topology_evidence",
    "topology_evidence",
    "rga_route_assessment",
    "route_assessment",
    "rga_topology_transition",
    "topology_transition",
    "rga_require_evidence",
    "require_rga_evidence",
    "rga_require_topology",
}


def assess_rga_transfer_step(step: Mapping[str, Any]) -> dict[str, Any]:
    """Assess one ``move_plate`` step using only explicit source evidence.

    Raw evidence is accepted under the ``*_input``/``*_evidence`` keys.  A
    caller may also provide a previously computed assessment under the
    ``*_assessment``/``*_transition`` keys.  Missing evidence is retained as
    ``not_evaluated`` unless the step explicitly requires it; no route or
    vector is inferred from names, coordinates, or command shape.
    """

    params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
    operation = str(step.get("operation") or "").strip()
    if operation != "move_plate":
        return {
            "schema_version": RGA_TRANSFER_SCHEMA_VERSION,
            "status": "not_applicable",
            "needs_review": False,
            "review_reasons": [],
        }

    route_input = _mapping(_first_value(params, "rga_route_input", "rga_route_evidence", "route_evidence"))
    route = _mapping(_first_value(params, "rga_route_assessment", "route_assessment"))
    if route is None and route_input is not None:
        route = _analyze_route_input(route_input, params)

    topology_input = _mapping(
        _first_value(params, "rga_topology_input", "rga_topology_evidence", "topology_evidence")
    )
    transition = _mapping(_first_value(params, "rga_topology_transition", "topology_transition"))
    topology = None
    if topology_input is not None:
        topology = normalize_rga_topology(
            _mapping(topology_input.get("topology")) or topology_input
        )
        if transition is None:
            transition = _analyze_topology_input(topology_input, topology, route, params)

    route_required = bool(
        params.get("rga_require_evidence")
        or params.get("require_rga_evidence")
        or route_input is not None
        or route is not None
    )
    topology_required = bool(
        params.get("rga_require_topology")
        or topology_input is not None
        or transition is not None
    )
    review_reasons: list[str] = []
    if route_required:
        if route is None:
            review_reasons.append("route_evidence_missing")
        else:
            route_status = str(route.get("status") or "unknown")
            if route_status not in _ROUTE_OK:
                review_reasons.append(f"route_status:{route_status}")
            if _has_route_ambiguity(route):
                review_reasons.append("route_selection_ambiguous")
    if topology_required:
        if transition is None:
            review_reasons.append("topology_transition_missing")
        else:
            topology_status = str(transition.get("status") or "unknown")
            if topology_status not in _TOPOLOGY_OK:
                review_reasons.append(f"topology_status:{topology_status}")

    params_labware = str(params.get("labware") or step.get("target_labware") or "").strip()
    source_site = _mapping(params.get("source_site")) or _mapping((route or {}).get("source_site"))
    destination_site = _mapping(params.get("destination_site")) or _mapping((route or {}).get("destination_site"))
    logical_occupancy = {
        "status": "logical_only",
        "labware_id": params_labware,
        "source": {
            "site_id": str(
                params.get("source_site_id")
                or (source_site or {}).get("identity")
                or params.get("source_location")
                or ""
            ),
            "location": str(params.get("source_location") or ""),
            "site": params.get("source_site_index") or params.get("source_site_number"),
        },
        "destination": {
            "site_id": str(
                params.get("destination_site_id")
                or (destination_site or {}).get("identity")
                or params.get("destination_location")
                or params.get("to_location")
                or ""
            ),
            "location": str(params.get("destination_location") or params.get("to_location") or ""),
            "site": params.get("destination_site") or params.get("to_site"),
        },
        "physical_verification_required": True,
    }
    source_dependencies = {
        "route_catalog_fingerprint": (route or {}).get("catalog_fingerprint"),
        "topology_fingerprint": (transition or {}).get("topology_fingerprint"),
        "source_site_id": logical_occupancy["source"]["site_id"],
        "destination_site_id": logical_occupancy["destination"]["site_id"],
        "command_provenance": dict((route or {}).get("command_provenance") or {}),
    }
    source_dependencies = {
        key: value for key, value in source_dependencies.items() if value not in (None, "", {})
    }
    physical_limitations = [
        {
            "effect": "pathfinder_collision_proof",
            "message": "Route analysis does not prove PathFinder geometry or collision safety.",
            "requires_hardware_verification": True,
        },
        {
            "effect": "gripper_clearance_and_retention",
            "message": "Logical occupancy does not prove gripper force, clearance, or carrier retention.",
            "requires_hardware_verification": True,
        },
    ]
    status = "needs_review" if review_reasons else (
        "ready" if route is not None or transition is not None else "not_evaluated"
    )
    return {
        "schema_version": RGA_TRANSFER_SCHEMA_VERSION,
        "status": status,
        "needs_review": bool(review_reasons),
        "review_reasons": sorted(set(review_reasons)),
        "route_assessment": deepcopy(route),
        "topology_transition": deepcopy(transition),
        "logical_occupancy": logical_occupancy,
        "source_dependencies": source_dependencies,
        "physical_limitations": physical_limitations,
    }


def attach_rga_assessments_to_ir(
    ir: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Attach derived assessments to move steps and return a compact report."""

    records: list[dict[str, Any]] = []
    for index, step in enumerate(ir.get("steps") or []):
        if not isinstance(step, dict) or str(step.get("operation") or "") != "move_plate":
            continue
        assessment = assess_rga_transfer_step(step)
        if assessment["status"] == "not_evaluated" and not _has_rga_inputs(step):
            continue
        params = dict(step.get("parameters") or {})
        params["rga_assessment"] = assessment
        if assessment.get("route_assessment") is not None:
            params["rga_route_assessment"] = assessment["route_assessment"]
        if assessment.get("topology_transition") is not None:
            params["rga_topology_transition"] = assessment["topology_transition"]
        params["rga_logical_occupancy"] = assessment["logical_occupancy"]
        params["rga_physical_limitations"] = assessment["physical_limitations"]
        params["rga_source_dependencies"] = assessment["source_dependencies"]
        params["rga_needs_review"] = assessment["needs_review"]
        step["parameters"] = params
        records.append({"step_index": index, "step_id": step.get("id"), **assessment})
    statuses = [str(item.get("status") or "") for item in records]
    report = {
        "schema_version": RGA_TRANSFER_SCHEMA_VERSION,
        "status": "needs_review" if any(item.get("needs_review") for item in records) else "passed",
        "transfer_count": len(records),
        "needs_review_count": sum(1 for item in records if item.get("needs_review")),
        "not_evaluated_count": statuses.count("not_evaluated"),
        "transfers": records,
    }
    return ir, report


def attach_rga_simulation_metadata(
    simulation_data: dict[str, Any] | None,
    ir: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Add logical RGA evidence to an existing offline simulation report."""

    if not isinstance(simulation_data, dict):
        return simulation_data
    transfers: list[dict[str, Any]] = []
    for index, step in enumerate(ir.get("steps") or []):
        if not isinstance(step, Mapping) or str(step.get("operation") or "") != "move_plate":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
        assessment = _mapping(params.get("rga_assessment"))
        if assessment is None:
            continue
        route = _mapping(assessment.get("route_assessment")) or {}
        transition = _mapping(assessment.get("topology_transition")) or {}
        transfers.append(
            {
                "step_index": index,
                "step_id": step.get("id"),
                "status": assessment.get("status"),
                "needs_review": bool(assessment.get("needs_review")),
                "review_reasons": list(assessment.get("review_reasons") or []),
                "route": {
                    "status": route.get("status", "not_evaluated"),
                    "candidate_vectors": list(route.get("candidate_vectors") or []),
                    "shared_vectors": list(route.get("shared_vectors") or []),
                    "grip_mode_intersection": list(route.get("grip_mode_intersection") or []),
                    "route_candidates": list(route.get("route_candidates") or []),
                    "selected_route": route.get("selected_route"),
                    "regrip_path": route.get("regrip_path"),
                },
                "topology_transition": transition,
                "logical_occupancy": assessment.get("logical_occupancy") or {},
                "source_dependencies": assessment.get("source_dependencies") or {},
                "physical_limitations": assessment.get("physical_limitations") or [],
            }
        )
    if not transfers:
        return simulation_data
    simulation_data["rga_transfers"] = transfers
    warnings = simulation_data.setdefault("warnings", [])
    for transfer in transfers:
        if transfer["needs_review"]:
            warning = (
                f"RGA transfer step {transfer['step_index'] + 1} needs review: "
                + ", ".join(transfer["review_reasons"])
            )
            if warning not in warnings:
                warnings.append(warning)
    return simulation_data


def render_rga_assessment_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact, source-evidence-focused generation report."""

    lines = [
        "# RGA transfer assessments",
        "",
        f"- Status: `{report.get('status') or 'not_evaluated'}`",
        f"- Transfers assessed: `{report.get('transfer_count', 0)}`",
        f"- Transfers needing review: `{report.get('needs_review_count', 0)}`",
        "",
        "Route metadata is logical/source-backed evidence only; it does not prove PathFinder collision safety or hardware readiness.",
        "",
    ]
    for item in report.get("transfers") or []:
        if not isinstance(item, Mapping):
            continue
        lines.extend(
            [
                f"## Step {int(item.get('step_index', 0)) + 1}",
                "",
                f"- Status: `{item.get('status')}`",
                f"- Review reasons: `{', '.join(str(value) for value in item.get('review_reasons') or []) or 'none'}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _analyze_route_input(route_input: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any] | None:
    if route_input.get("status") and not route_input.get("source_site") and not route_input.get("destination_site"):
        return dict(route_input)
    source = _mapping(route_input.get("source_site")) or _mapping(params.get("source_site"))
    destination = _mapping(route_input.get("destination_site")) or _mapping(params.get("destination_site"))
    catalog = _mapping(route_input.get("catalog")) or _mapping(params.get("rga_catalog")) or {}
    return analyze_rga_route(
        source,
        destination,
        catalog=catalog,
        target_catalog_fingerprint=route_input.get("target_catalog_fingerprint")
        or params.get("rga_target_catalog_fingerprint"),
        expected_catalog_fingerprint=route_input.get("expected_catalog_fingerprint")
        or params.get("rga_expected_catalog_fingerprint"),
        command_provenance=_mapping(route_input.get("command_provenance"))
        or _mapping(params.get("command_provenance")),
        pathfinder_status=str(route_input.get("pathfinder_status") or "not_evaluated"),
        physical_verification_required=True,
    )


def _analyze_topology_input(
    topology_input: Mapping[str, Any],
    topology: Mapping[str, Any],
    route: Mapping[str, Any] | None,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    source_site_id = str(
        topology_input.get("source_site_id")
        or params.get("source_site_id")
        or (_mapping((route or {}).get("source_site")) or {}).get("identity")
        or ""
    )
    destination_site_id = str(
        topology_input.get("destination_site_id")
        or params.get("destination_site_id")
        or (_mapping((route or {}).get("destination_site")) or {}).get("identity")
        or ""
    )
    return analyze_storage_transition(
        topology,
        source_site_id=source_site_id,
        destination_site_id=destination_site_id,
        labware_id=str(params.get("labware") or ""),
        route_analysis=route,
    )


def _has_route_ambiguity(route: Mapping[str, Any]) -> bool:
    findings = {str(item) for item in route.get("findings") or []}
    return bool(
        route.get("selected_route") is None
        and (
            "multiple_direct_routes_remain_ambiguous" in findings
            or "multiple_regrip_routes_remain_ambiguous" in findings
        )
    )


def _has_rga_inputs(step: Mapping[str, Any]) -> bool:
    params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
    return any(key in params for key in _RGA_INPUT_KEYS)


def _first_value(params: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = params.get(key)
        if value is not None:
            return value
    return None


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None
