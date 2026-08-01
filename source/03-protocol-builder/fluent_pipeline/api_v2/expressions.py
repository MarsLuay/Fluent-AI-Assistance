"""ResolveExpression runtime assertions (api-v2-065)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExpressionCheck:
    expression: str
    expected: str | None = None
    label: str = ""


@dataclass(frozen=True)
class ExpressionCheckResult:
    expression: str
    label: str
    ok: bool
    actual: str
    expected: str | None
    summary: str


def run_expression_checks(
    runtime: Any,
    checks: list[ExpressionCheck],
) -> list[ExpressionCheckResult]:
    """Evaluate IR-declared expressions via ``ResolveExpression`` after prepare/run."""
    resolve = getattr(runtime, "ResolveExpression", None) or getattr(runtime, "resolve_expression", None)
    if resolve is None:
        return [
            ExpressionCheckResult(
                expression=check.expression,
                label=check.label or check.expression,
                ok=False,
                actual="",
                expected=check.expected,
                summary="Runtime adapter does not expose ResolveExpression.",
            )
            for check in checks
        ]

    results: list[ExpressionCheckResult] = []
    for check in checks:
        label = check.label or check.expression
        try:
            actual = str(resolve(check.expression) or "")
        except Exception as exc:
            results.append(
                ExpressionCheckResult(
                    expression=check.expression,
                    label=label,
                    ok=False,
                    actual="",
                    expected=check.expected,
                    summary=f"ResolveExpression failed: {exc}",
                )
            )
            continue

        if check.expected is None:
            ok = bool(actual.strip())
            summary = "ResolveExpression returned a value." if ok else "ResolveExpression was empty."
        else:
            ok = actual.strip() == str(check.expected).strip()
            summary = "Expression matched expected value." if ok else (
                f"Expected {check.expected!r}, got {actual!r}."
            )

        results.append(
            ExpressionCheckResult(
                expression=check.expression,
                label=label,
                ok=ok,
                actual=actual,
                expected=check.expected,
                summary=summary,
            )
        )
    return results
