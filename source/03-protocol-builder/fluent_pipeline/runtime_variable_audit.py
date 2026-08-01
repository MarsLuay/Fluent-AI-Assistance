"""Method variable inventory audit (api-v2-027).

When live FluentControl runtime capture is available,
``IRuntimeController.GetVariableNames()`` returns method variable names. This
module diffs that list against offline XSCR ``startup_variables`` and protocol
IR ``variables`` / source ``startup_variables``, then surfaces mismatches in
``validation_diff.md``.
"""

from __future__ import annotations

import re
from . import xml_compat as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .query_variable_audit import normalize_query_variable_names


RUNTIME_VARIABLE_AUDIT_VERSION = "tecan.runtime_variable_audit.v1"
API_V2_ISSUE_ID = "api-v2-027"
VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class RuntimeVariableAudit:
    status: str
    summary: str
    live_names: tuple[str, ...]
    live_query_names: tuple[str, ...]
    expected_from_xscr: tuple[str, ...]
    expected_from_ir: tuple[str, ...]
    expected_combined: tuple[str, ...]
    missing_from_live: tuple[str, ...]
    extra_in_live: tuple[str, ...]
    runtime_available: bool
    xscr_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_VARIABLE_AUDIT_VERSION,
            "issue_id": API_V2_ISSUE_ID,
            "status": self.status,
            "summary": self.summary,
            "runtime_available": self.runtime_available,
            "xscr_path": self.xscr_path,
            "live_names": list(self.live_names),
            "live_query_names": list(self.live_query_names),
            "expected_from_xscr": list(self.expected_from_xscr),
            "expected_from_ir": list(self.expected_from_ir),
            "expected_combined": list(self.expected_combined),
            "missing_from_live": list(self.missing_from_live),
            "extra_in_live": list(self.extra_in_live),
        }


def normalize_variable_names(raw: Any) -> tuple[str, ...]:
    """Coerce ``GetVariableNames()`` output to a deduped sorted name tuple."""
    return normalize_query_variable_names(raw)


def expected_variable_names_from_xscr(xscr_path: Path | None) -> tuple[str, ...]:
    """Collect variable names declared in a compiled XSCR."""
    if xscr_path is None or not xscr_path.exists():
        return ()
    try:
        root = ET.parse(xscr_path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return ()
    names: set[str] = set()
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "anyType":
            type_hint = " ".join(str(value) for value in element.attrib.values())
            if "VariableDefinitionHelper" not in type_hint:
                continue
        elif local != "VariableDefinitionHelper":
            direct_names = {_local_name(child.tag) for child in list(element)}
            if not {"Name", "TypeName", "QueryOnStartup"}.issubset(direct_names):
                continue
        name = _first_text(element, "Name")
        if _is_variable_name(name):
            names.add(name)
    return tuple(sorted(names))


def expected_variable_names_from_ir(protocol_ir: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect startup/method variable names modeled in protocol IR."""
    names: set[str] = set()
    for record in protocol_ir.get("variables") or []:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        if _is_variable_name(name):
            names.add(name)
    source = protocol_ir.get("source") if isinstance(protocol_ir.get("source"), dict) else {}
    for script in source.get("selected_source_scripts") or []:
        if not isinstance(script, dict):
            continue
        for record in script.get("startup_variables") or []:
            if not isinstance(record, dict):
                continue
            name = str(record.get("name") or "").strip()
            if _is_variable_name(name):
                names.add(name)
    return tuple(sorted(names))


def build_runtime_variable_audit(
    *,
    protocol_ir: Mapping[str, Any],
    xscr_path: Path | None = None,
    live_names: Sequence[str] | None = None,
    live_query_names: Sequence[str] | None = None,
) -> RuntimeVariableAudit:
    """Diff live ``GetVariableNames()`` output against offline XSCR/IR inventory."""
    expected_xscr = expected_variable_names_from_xscr(xscr_path)
    expected_ir = expected_variable_names_from_ir(protocol_ir)
    expected_combined = tuple(sorted(set(expected_xscr) | set(expected_ir)))
    live = normalize_variable_names(live_names)
    query_live = normalize_query_variable_names(live_query_names)
    runtime_available = live_names is not None
    xscr_text = str(xscr_path) if xscr_path else ""

    if not runtime_available:
        if not expected_combined:
            return RuntimeVariableAudit(
                status="skipped",
                summary="No method variables modeled offline; runtime audit not requested.",
                live_names=(),
                live_query_names=(),
                expected_from_xscr=expected_xscr,
                expected_from_ir=expected_ir,
                expected_combined=expected_combined,
                missing_from_live=(),
                extra_in_live=(),
                runtime_available=False,
                xscr_path=xscr_text,
            )
        return RuntimeVariableAudit(
            status="needs-review",
            summary=(
                "Method variables are modeled offline but GetVariableNames() was not captured "
                "(verify variable inventory manually in Script Editor on the instrument PC)."
            ),
            live_names=(),
            live_query_names=(),
            expected_from_xscr=expected_xscr,
            expected_from_ir=expected_ir,
            expected_combined=expected_combined,
            missing_from_live=expected_combined,
            extra_in_live=(),
            runtime_available=False,
            xscr_path=xscr_text,
        )

    live_set = set(live)
    expected_set = set(expected_combined)
    missing = tuple(sorted(expected_set - live_set))
    extra = tuple(sorted(live_set - expected_set))

    if not missing and not extra:
        if expected_combined or live:
            summary = "Live GetVariableNames() matches offline XSCR/IR startup_variables."
            status = "passed"
        else:
            summary = "GetVariableNames() returned no variables and none were modeled offline."
            status = "passed"
    elif missing and not extra:
        summary = (
            "FluentControl method variable list is missing offline name(s): "
            + ", ".join(missing)
            + ". Import/deploy the method or review IR/XSCR startup_variables before operator run."
        )
        status = "failed"
    elif extra and not missing:
        summary = (
            "FluentControl reports extra method variable(s) not modeled offline: "
            + ", ".join(extra)
            + ". Review method_touchtools_readiness startup-variable inventory."
        )
        status = "needs-review"
    else:
        summary = (
            "FluentControl method variable list differs from offline XSCR/IR "
            f"(missing: {', '.join(missing)}; extra: {', '.join(extra)})."
        )
        status = "failed"

    if query_live and not live_set.issuperset(set(query_live)):
        missing_query = tuple(sorted(set(query_live) - live_set))
        summary += f" GetQueryVariableNames() reported {', '.join(missing_query)} not present in GetVariableNames()."
        status = "failed" if status == "passed" else status

    return RuntimeVariableAudit(
        status=status,
        summary=summary,
        live_names=live,
        live_query_names=query_live,
        expected_from_xscr=expected_xscr,
        expected_from_ir=expected_ir,
        expected_combined=expected_combined,
        missing_from_live=missing,
        extra_in_live=extra,
        runtime_available=True,
        xscr_path=xscr_text,
    )


def live_variable_names_from_fluent_report(report: Mapping[str, Any] | None) -> tuple[str, ...] | None:
    """Extract ``GetVariableNames()`` results from a Fluent runtime report."""
    if not isinstance(report, Mapping):
        return None
    details = report.get("details")
    if isinstance(details, Mapping):
        if "variable_names" in details:
            return normalize_variable_names(details.get("variable_names"))
        audit = details.get("runtime_variable_audit")
        if isinstance(audit, Mapping) and "live_names" in audit:
            return normalize_variable_names(audit.get("live_names"))
    external = report.get("external_json")
    if isinstance(external, Mapping) and "variable_names" in external:
        return normalize_variable_names(external.get("variable_names"))
    if "variable_names" in report:
        return normalize_variable_names(report.get("variable_names"))
    if not report.get("ok") and report.get("status") in {"unavailable", "skipped"}:
        return None
    if report.get("status") in {"passed", "failed"} and isinstance(details, Mapping):
        return None
    return None


def live_query_names_from_fluent_report(report: Mapping[str, Any] | None) -> tuple[str, ...] | None:
    if not isinstance(report, Mapping):
        return None
    details = report.get("details")
    if isinstance(details, Mapping) and "query_variable_names" in details:
        return normalize_query_variable_names(details.get("query_variable_names"))
    return None


def audit_runtime_variables_for_workflow(
    *,
    protocol_ir: Mapping[str, Any],
    xscr_path: Path | None = None,
    fluent_report: Mapping[str, Any] | None,
) -> RuntimeVariableAudit:
    live = live_variable_names_from_fluent_report(fluent_report)
    query_live = live_query_names_from_fluent_report(fluent_report)
    if xscr_path is None and isinstance(fluent_report, Mapping):
        raw = fluent_report.get("xscr_path")
        if raw:
            xscr_path = Path(str(raw))
    return build_runtime_variable_audit(
        protocol_ir=protocol_ir,
        xscr_path=xscr_path,
        live_names=live,
        live_query_names=query_live,
    )


def render_runtime_variable_audit_markdown(audit: Mapping[str, Any]) -> str:
    """Markdown section for ``validation_diff.md`` runtime variable audit output."""
    lines = [
        "## FluentControl variable inventory (GetVariableNames)",
        "",
        f"- Issue: `{audit.get('issue_id') or API_V2_ISSUE_ID}`",
        f"- Status: `{audit.get('status')}`",
        f"- Summary: {audit.get('summary') or ''}",
        f"- Runtime captured: `{bool(audit.get('runtime_available'))}`",
    ]
    if audit.get("xscr_path"):
        lines.append(f"- XSCR: `{audit.get('xscr_path')}`")
    lines.append("")
    for title, key in (
        ("Live (GetVariableNames)", "live_names"),
        ("Live query subset (GetQueryVariableNames)", "live_query_names"),
        ("Expected from XSCR", "expected_from_xscr"),
        ("Expected from IR", "expected_from_ir"),
        ("Missing from live runtime", "missing_from_live"),
        ("Extra in live runtime", "extra_in_live"),
    ):
        values = audit.get(key) or []
        rendered = ", ".join(f"`{name}`" for name in values) if values else "*(none)*"
        lines.append(f"- {title}: {rendered}")
    lines.append("")
    return "\n".join(lines)


def render_fluent_variables_cli_output(audit: Mapping[str, Any]) -> str:
    """Human-readable runtime variable inventory report (legacy CLI helper)."""
    header = [
        "# FluentControl Variable Inventory",
        "",
        f"- API: `RuntimeController.GetVariableNames()` ({API_V2_ISSUE_ID})",
        f"- Status: `{audit.get('status')}`",
        f"- Summary: {audit.get('summary') or ''}",
        "",
    ]
    return "\n".join(header) + render_runtime_variable_audit_markdown(audit)


def validation_diff_check_for_runtime_audit(audit: RuntimeVariableAudit) -> dict[str, Any]:
    return {
        "id": "runtime_variable_audit",
        "status": audit.status,
        "summary": audit.summary,
        "details": {
            "issue_id": API_V2_ISSUE_ID,
            "live_names": list(audit.live_names),
            "live_query_names": list(audit.live_query_names),
            "expected_combined": list(audit.expected_combined),
            "missing_from_live": list(audit.missing_from_live),
            "extra_in_live": list(audit.extra_in_live),
            "runtime_available": audit.runtime_available,
            "xscr_path": audit.xscr_path,
        },
    }


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _first_text(element: ET.Element, local_name: str) -> str:
    for child in element.iter():
        if _local_name(child.tag) == local_name and child.text:
            text = child.text.strip()
            if text:
                return text
    return ""


def _is_variable_name(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and VARIABLE_NAME_RE.fullmatch(text) is not None
