"""RuntimeController.GetVariableValue startup-variable snapshot (api-v2-028).

After ``PrepareMethod``, snapshot ``GetVariableValue(name)`` for each
query-at-startup variable and compare runtime values to IR/spec defaults.
Pairs with ``ResolveExpression`` and ``CloseQueryAtStartUpDialog`` (api-v2-027)
for full startup-dialog coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from .query_variable_audit import _coerce_bool, _is_variable_name


class VariableValueReader(Protocol):
    """Minimal runtime surface for ``GetVariableValue``."""

    def get_variable_value(self, name: str) -> str | None:
        ...


@dataclass(frozen=True)
class StartupVariableSnapshot:
    """Comparison result for one query-at-startup variable."""

    name: str
    expected: str
    actual: str | None
    matched: bool
    note: str = ""


@dataclass
class StartupVariableSnapshotReport:
    status: str = "skipped"
    expected: dict[str, str] = field(default_factory=dict)
    actual: dict[str, str | None] = field(default_factory=dict)
    snapshots: list[StartupVariableSnapshot] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "expected": dict(self.expected),
            "actual": dict(self.actual),
            "snapshots": [
                {
                    "name": item.name,
                    "expected": item.expected,
                    "actual": item.actual,
                    "matched": item.matched,
                    "note": item.note,
                }
                for item in self.snapshots
            ],
            "mismatches": list(self.mismatches),
            "missing": list(self.missing),
            "messages": list(self.messages),
        }


def query_at_startup_expectations(
    ir: Mapping[str, Any],
    *,
    spec: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Collect expected defaults for variables flagged query-at-startup in IR/spec."""
    expectations: dict[str, str] = {}

    def add_record(record: Any) -> None:
        if not isinstance(record, Mapping):
            return
        name = str(record.get("name") or "").strip()
        if not name or not _is_variable_name(name):
            return
        query = record.get("query_at_startup")
        if query is None:
            query = record.get("query_on_startup")
        if not _coerce_bool(query):
            return
        expectations[name] = normalize_variable_value(_default_from_record(record))

    for record in ir.get("variables") or []:
        add_record(record)

    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    for script in source.get("selected_source_scripts") or []:
        if not isinstance(script, Mapping):
            continue
        for record in script.get("startup_variables") or []:
            add_record(record)

    _apply_simulation_value_overrides(expectations, ir.get("simulation_values") or [])
    if spec:
        generation = spec.get("generation") if isinstance(spec.get("generation"), Mapping) else {}
        recipe = spec.get("verification_recipe") if isinstance(spec.get("verification_recipe"), Mapping) else {}
        _apply_simulation_value_overrides(expectations, spec.get("simulation_values") or [])
        _apply_simulation_value_overrides(expectations, generation.get("simulation_values") or [])
        _apply_simulation_value_overrides(expectations, recipe.get("simulation_values") or [])

    return expectations


def snapshot_startup_variable_values(
    reader: VariableValueReader,
    names: Sequence[str],
) -> dict[str, str | None]:
    """Call ``GetVariableValue`` for each name and return normalized snapshots."""
    actual: dict[str, str | None] = {}
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        value = reader.get_variable_value(name)
        actual[name] = None if value is None else normalize_variable_value(value)
    return actual


def compare_startup_variable_snapshots(
    expected: Mapping[str, str],
    actual: Mapping[str, str | None],
) -> StartupVariableSnapshotReport:
    """Diff expected IR/spec defaults against post-prepare runtime values."""
    report = StartupVariableSnapshotReport(expected=dict(expected))
    if not expected:
        report.status = "skipped"
        report.messages.append("No query-at-startup variables were declared in IR/spec.")
        return report

    report.actual = dict(actual)
    all_matched = True
    for name, expected_value in expected.items():
        runtime_value = actual.get(name)
        matched = runtime_value is not None and variable_values_match(expected_value, runtime_value)
        note = ""
        if runtime_value is None:
            report.missing.append(name)
            note = "GetVariableValue returned no value."
            all_matched = False
        elif not matched:
            report.mismatches.append(
                f"{name}: expected {expected_value!r}, runtime {runtime_value!r}"
            )
            all_matched = False
        report.snapshots.append(
            StartupVariableSnapshot(
                name=name,
                expected=expected_value,
                actual=runtime_value,
                matched=matched,
                note=note,
            )
        )

    report.status = "passed" if all_matched else "failed"
    if all_matched:
        report.messages.append(
            f"All {len(expected)} query-at-startup variable value(s) matched IR/spec defaults."
        )
    else:
        report.messages.append(
            "One or more query-at-startup variable values did not match IR/spec defaults."
        )
    return report


def run_startup_variable_value_check(
    reader: VariableValueReader,
    expectations: Mapping[str, str],
) -> StartupVariableSnapshotReport:
    """Snapshot and compare query-at-startup variables via ``GetVariableValue``."""
    expected = {str(key): normalize_variable_value(value) for key, value in expectations.items() if str(key).strip()}
    actual = snapshot_startup_variable_values(reader, expected.keys())
    return compare_startup_variable_snapshots(expected, actual)


def normalize_variable_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    text = str(value).strip()
    if text.casefold() in {"true", "yes", "on"}:
        return "True"
    if text.casefold() in {"false", "no", "off"}:
        return "False"
    return text


def variable_values_match(expected: str, actual: str) -> bool:
    left = normalize_variable_value(expected)
    right = normalize_variable_value(actual)
    if left == right:
        return True
    if left.casefold() == right.casefold():
        return True
    try:
        return float(left) == float(right)
    except ValueError:
        return False


def _apply_simulation_value_overrides(
    expectations: dict[str, str],
    records: Sequence[Any],
) -> None:
    for record in records:
        if not isinstance(record, Mapping):
            continue
        name = str(record.get("name") or record.get("variable") or "").strip()
        if not name or not _is_variable_name(name):
            continue
        if name not in expectations:
            continue
        value = record.get("value")
        if value is None and "sim_value" in record:
            value = record.get("sim_value")
        if value is not None:
            expectations[name] = normalize_variable_value(value)


def _default_from_record(record: Mapping[str, Any]) -> Any:
    for key in ("default_value", "value"):
        value = record.get(key)
        if value is not None and str(value).strip() != "":
            return value
    for key in ("values", "default_values"):
        values = record.get(key)
        if isinstance(values, list) and values:
            return values[0]
    return 0


def expectations_as_tuple(expectations: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Serialize IR/spec expectations for ``FluentContextCheckConfig``."""
    return tuple(
        sorted((str(name), normalize_variable_value(value)) for name, value in expectations.items() if str(name).strip())
    )


def expectations_from_tuple(items: Sequence[tuple[str, str] | Sequence[str]]) -> dict[str, str]:
    return {str(name): normalize_variable_value(value) for name, value in items}


def live_startup_variable_snapshot_from_report(report: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(report, Mapping):
        return None
    details = report.get("details")
    if isinstance(details, Mapping):
        snapshot = details.get("startup_variable_snapshot")
        if isinstance(snapshot, Mapping):
            return dict(snapshot)
    return None
