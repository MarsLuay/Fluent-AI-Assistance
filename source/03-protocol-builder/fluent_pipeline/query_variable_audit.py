"""Query-at-startup variable audit (api-v2-026).

After ``PrepareMethod``, ``IRuntimeController.GetQueryVariableNames()`` returns
variable names that FluentControl will prompt for at method start. This module
diffs that live list against protocol IR ``query_variable`` steps /
``query_at_startup`` declarations and ``request.spec.yaml`` simulation seeds,
then surfaces mismatches in ``validation_diff.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


QUERY_VARIABLE_AUDIT_VERSION = "tecan.query_variable_audit.v1"
VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class QueryVariableAudit:
    status: str
    summary: str
    live_names: tuple[str, ...]
    expected_from_ir: tuple[str, ...]
    expected_from_spec: tuple[str, ...]
    expected_combined: tuple[str, ...]
    missing_from_live: tuple[str, ...]
    extra_in_live: tuple[str, ...]
    runtime_available: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": QUERY_VARIABLE_AUDIT_VERSION,
            "status": self.status,
            "summary": self.summary,
            "runtime_available": self.runtime_available,
            "live_names": list(self.live_names),
            "expected_from_ir": list(self.expected_from_ir),
            "expected_from_spec": list(self.expected_from_spec),
            "expected_combined": list(self.expected_combined),
            "missing_from_live": list(self.missing_from_live),
            "extra_in_live": list(self.extra_in_live),
        }


def normalize_query_variable_names(raw: Any) -> tuple[str, ...]:
    """Coerce ``GetQueryVariableNames()`` output to a deduped sorted name tuple."""
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)):
        text = str(raw).strip()
        return (text,) if text and _is_variable_name(text) else ()
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if not text or not _is_variable_name(text) or text in seen:
            continue
        seen.add(text)
        names.append(text)
    return tuple(sorted(names))


def expected_query_names_from_ir(protocol_ir: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect startup-query variable names modeled in protocol IR."""
    names: set[str] = set()
    for step in protocol_ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("operation") or "") != "query_variable":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        name = str(params.get("variable") or "").strip()
        if _is_variable_name(name):
            names.add(name)
    for record in protocol_ir.get("variables") or []:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        if not _is_variable_name(name):
            continue
        if _coerce_bool(record.get("query_at_startup")) or record.get("queries"):
            names.add(name)
    return tuple(sorted(names))


def expected_query_names_from_spec(request_spec: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect startup-query names seeded from request.spec simulation values."""
    spec = request_spec
    names: set[str] = set()
    recipe = spec.get("verification_recipe") if isinstance(spec.get("verification_recipe"), dict) else {}
    for record in recipe.get("simulation_values") or []:
        _collect_spec_variable_name(names, record)
    for record in spec.get("simulation_values") or []:
        _collect_spec_variable_name(names, record)
    generation = spec.get("generation") if isinstance(spec.get("generation"), dict) else {}
    for record in generation.get("simulation_values") or []:
        _collect_spec_variable_name(names, record)
    return tuple(sorted(names))


def build_query_variable_audit(
    *,
    protocol_ir: Mapping[str, Any],
    request_spec: Mapping[str, Any],
    live_names: Sequence[str] | None = None,
) -> QueryVariableAudit:
    """Diff live ``GetQueryVariableNames()`` output against IR and request spec."""
    expected_ir = expected_query_names_from_ir(protocol_ir)
    expected_spec = expected_query_names_from_spec(request_spec)
    expected_combined = tuple(sorted(set(expected_ir) | set(expected_spec)))
    live = normalize_query_variable_names(live_names)
    runtime_available = live_names is not None

    if not runtime_available:
        if not expected_combined:
            return QueryVariableAudit(
                status="skipped",
                summary="No query-at-startup variables modeled; runtime audit not requested.",
                live_names=(),
                expected_from_ir=expected_ir,
                expected_from_spec=expected_spec,
                expected_combined=expected_combined,
                missing_from_live=(),
                extra_in_live=(),
                runtime_available=False,
            )
        return QueryVariableAudit(
            status="needs-review",
            summary=(
                "Query-at-startup variables are modeled offline but "
                "GetQueryVariableNames() was not captured (verify query-at-startup "
                "variables manually in Script Editor on the instrument PC)."
            ),
            live_names=(),
            expected_from_ir=expected_ir,
            expected_from_spec=expected_spec,
            expected_combined=expected_combined,
            missing_from_live=expected_combined,
            extra_in_live=(),
            runtime_available=False,
        )

    live_set = set(live)
    expected_set = set(expected_combined)
    missing = tuple(sorted(expected_set - live_set))
    extra = tuple(sorted(live_set - expected_set))

    if not missing and not extra:
        if expected_combined or live:
            summary = "Live GetQueryVariableNames() matches modeled query-at-startup variables."
            status = "passed"
        else:
            summary = "GetQueryVariableNames() returned no startup prompts and none were modeled."
            status = "passed"
    elif missing and not extra:
        summary = (
            "FluentControl startup query list is missing modeled variable(s): "
            + ", ".join(missing)
            + ". Import/deploy the method or review query_variable IR before operator run."
        )
        status = "failed"
    elif extra and not missing:
        summary = (
            "FluentControl reports extra startup query variable(s) not modeled in IR/spec: "
            + ", ".join(extra)
            + ". Review before unattended run (pair with CloseQueryAtStartUpDialog when defaults are approved)."
        )
        status = "needs-review"
    else:
        summary = (
            "FluentControl startup query list differs from IR/spec "
            f"(missing: {', '.join(missing)}; extra: {', '.join(extra)})."
        )
        status = "failed"

    return QueryVariableAudit(
        status=status,
        summary=summary,
        live_names=live,
        expected_from_ir=expected_ir,
        expected_from_spec=expected_spec,
        expected_combined=expected_combined,
        missing_from_live=missing,
        extra_in_live=extra,
        runtime_available=True,
    )


def live_query_names_from_fluent_report(report: Mapping[str, Any] | None) -> tuple[str, ...] | None:
    """Extract ``GetQueryVariableNames()`` results from a Fluent runtime report."""
    if not isinstance(report, Mapping):
        return None
    details = report.get("details")
    if isinstance(details, Mapping):
        if "query_variable_names" in details:
            return normalize_query_variable_names(details.get("query_variable_names"))
        audit = details.get("query_variable_audit")
        if isinstance(audit, Mapping) and "live_names" in audit:
            return normalize_query_variable_names(audit.get("live_names"))
    external = report.get("external_json")
    if isinstance(external, Mapping) and "query_variable_names" in external:
        return normalize_query_variable_names(external.get("query_variable_names"))
    if "query_variable_names" in report:
        return normalize_query_variable_names(report.get("query_variable_names"))
    if not report.get("ok") and report.get("status") in {"unavailable", "skipped"}:
        return None
    if report.get("status") in {"passed", "failed"} and isinstance(details, Mapping):
        return None
    return None


def audit_query_variables_for_workflow(
    *,
    protocol_ir: Mapping[str, Any],
    request_spec: Mapping[str, Any],
    fluent_report: Mapping[str, Any] | None,
) -> QueryVariableAudit:
    live = live_query_names_from_fluent_report(fluent_report)
    return build_query_variable_audit(
        protocol_ir=protocol_ir,
        request_spec=request_spec,
        live_names=live,
    )


def render_query_variable_audit_markdown(audit: Mapping[str, Any]) -> str:
    lines = [
        "## Query-at-startup variable audit",
        "",
        f"- Status: `{audit.get('status')}`",
        f"- Summary: {audit.get('summary') or ''}",
        f"- Runtime captured: `{bool(audit.get('runtime_available'))}`",
        "",
    ]
    for title, key in (
        ("Live (GetQueryVariableNames)", "live_names"),
        ("Expected from IR", "expected_from_ir"),
        ("Expected from request.spec", "expected_from_spec"),
        ("Missing from live runtime", "missing_from_live"),
        ("Extra in live runtime", "extra_in_live"),
    ):
        values = audit.get(key) or []
        rendered = ", ".join(f"`{name}`" for name in values) if values else "*(none)*"
        lines.append(f"- {title}: {rendered}")
    lines.append("")
    return "\n".join(lines)


def validation_diff_check_for_query_audit(audit: QueryVariableAudit) -> dict[str, Any]:
    return {
        "id": "query_variable_runtime_audit",
        "status": audit.status,
        "summary": audit.summary,
        "details": {
            "live_names": list(audit.live_names),
            "expected_combined": list(audit.expected_combined),
            "missing_from_live": list(audit.missing_from_live),
            "extra_in_live": list(audit.extra_in_live),
            "runtime_available": audit.runtime_available,
        },
    }


def _collect_spec_variable_name(names: set[str], record: Any) -> None:
    if not isinstance(record, dict):
        return
    for key in ("variable", "name"):
        value = str(record.get(key) or "").strip()
        if _is_variable_name(value):
            names.add(value)
            return


def _is_variable_name(value: str) -> bool:
    return bool(value) and VARIABLE_NAME_RE.fullmatch(value) is not None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}
