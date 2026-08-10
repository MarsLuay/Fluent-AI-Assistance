"""Pure ranking and provenance helpers for automatic authoring inference."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from .models import Confidence, InferenceCandidate, InferenceDecision, InferenceOrigin


INFERENCE_SCHEMA_VERSION = "tecan.inference.v1"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_PATH_RE = re.compile(r"^\$?(?:\.([A-Za-z_][A-Za-z0-9_]*))+$")
_MISSING = object()


def intent_similarity(intent: str, candidate: str) -> int:
    """Return a deterministic lexical relevance score."""
    intent_text = str(intent or "").casefold()
    candidate_text = str(candidate or "").casefold()
    if not intent_text or not candidate_text:
        return 0
    intent_tokens = set(_TOKEN_RE.findall(intent_text))
    candidate_tokens = set(_TOKEN_RE.findall(candidate_text))
    overlap = len(intent_tokens.intersection(candidate_tokens))
    score = overlap * 20
    if candidate_text in intent_text:
        score += 100
    if intent_text in candidate_text:
        score += 40
    return score


def records_to_candidates(
    records: Iterable[Any],
    *,
    value_keys: Sequence[str] = ("name", "object_name", "label", "path"),
    origin: InferenceOrigin = InferenceOrigin.CONTEXT_FALLBACK,
    source_prefix: str,
    reason: str,
    intent: str = "",
    priority: int = 100,
) -> tuple[InferenceCandidate, ...]:
    """Convert context inventory rows to ranked, serializable candidates."""
    candidates: list[InferenceCandidate] = []
    for index, raw in enumerate(records):
        record = raw if isinstance(raw, Mapping) else {"value": raw}
        value = _first_value(record, (*value_keys, "value"))
        if not _has_value(value):
            continue
        source = str(
            _first_value(
                record,
                ("qualified_name", "extracted_path", "entry", "source_path", "guid"),
            )
            or f"{source_prefix}:{index}"
        )
        searchable = " ".join(
            str(item)
            for item in (
                value,
                record.get("role"),
                record.get("catalog"),
                record.get("object_name"),
                record.get("dependencies"),
                record.get("family_counts"),
            )
            if item not in (None, "", [], {})
        )
        candidates.append(
            InferenceCandidate(
                value=value,
                origin=origin,
                source=source,
                reason=reason,
                priority=priority,
                score=intent_similarity(intent, searchable),
                evidence={
                    key: deepcopy(value)
                    for key, value in record.items()
                    if key
                    in {
                        "name",
                        "object_name",
                        "label",
                        "guid",
                        "role",
                        "catalog",
                        "location",
                        "position",
                        "qualified_name",
                        "entry",
                        "extracted_path",
                    }
                    and value not in (None, "", [], {})
                },
            )
        )
    return tuple(candidates)


def rank_candidates(candidates: Iterable[InferenceCandidate]) -> tuple[InferenceCandidate, ...]:
    """Deduplicate candidates and sort strongest evidence first."""
    strongest: dict[str, InferenceCandidate] = {}
    for candidate in candidates:
        key = _canonical_value(candidate.value)
        current = strongest.get(key)
        if current is None or _candidate_sort_key(candidate) < _candidate_sort_key(current):
            strongest[key] = candidate
    return tuple(sorted(strongest.values(), key=_candidate_sort_key))


def infer_value(
    path: str,
    *,
    explicit: Any = _MISSING,
    candidates: Iterable[InferenceCandidate] = (),
    fallback: InferenceCandidate | None = None,
) -> InferenceDecision:
    """Select one value while preserving all ranked alternatives."""
    if explicit is not _MISSING and _has_value(explicit):
        return InferenceDecision(
            path=path,
            value=deepcopy(explicit),
            origin=InferenceOrigin.EXPLICIT,
            confidence=Confidence.HIGH,
            source="request.spec",
            reason="The request supplied this value explicitly.",
            review_required=False,
        )

    ranked = rank_candidates((*tuple(candidates), *((fallback,) if fallback else ())))
    if not ranked:
        return InferenceDecision(
            path=path,
            value=None,
            origin=InferenceOrigin.UNRESOLVED,
            confidence=Confidence.LOW,
            source="none",
            reason="No imported-context or reusable-shape candidate was available.",
            review_required=True,
            unresolved=True,
        )

    selected = ranked[0]
    return InferenceDecision(
        path=path,
        value=deepcopy(selected.value),
        origin=selected.origin,
        confidence=_confidence_for(selected, ranked),
        source=selected.source,
        reason=selected.reason,
        review_required=selected.origin is not InferenceOrigin.EXPLICIT,
        candidates=ranked,
    )


def apply_inference_decisions(
    document: Mapping[str, Any],
    decisions: Iterable[InferenceDecision],
) -> dict[str, Any]:
    """Return a copy with resolved decision values applied to dotted paths."""
    result = deepcopy(dict(document))
    for decision in decisions:
        if decision.unresolved:
            continue
        _set_path(result, decision.path, deepcopy(decision.value))
    return result


def build_inference_report(
    decisions: Iterable[InferenceDecision],
    *,
    context: str | None,
    task: str,
) -> dict[str, Any]:
    """Build the canonical machine-readable inference ledger."""
    normalized = tuple(decisions)
    unresolved = [decision.path for decision in normalized if decision.unresolved]
    inferred = [
        decision.path
        for decision in normalized
        if decision.origin not in {InferenceOrigin.EXPLICIT, InferenceOrigin.UNRESOLVED}
    ]
    review_required = any(decision.review_required for decision in normalized)
    return {
        "schema_version": INFERENCE_SCHEMA_VERSION,
        "status": "degraded" if unresolved else "resolved",
        "context": context or None,
        "task": task,
        "review_required": review_required,
        "inferred_count": len(inferred),
        "unresolved_count": len(unresolved),
        "inferred_paths": inferred,
        "unresolved_paths": unresolved,
        "decisions": [decision.to_dict() for decision in normalized],
    }


def merge_inference_reports(
    *reports: Mapping[str, Any] | None,
    context: str | None = None,
    task: str = "",
) -> dict[str, Any]:
    """Merge stage-local ledgers into one path-addressed report."""
    decisions: dict[str, dict[str, Any]] = {}
    for report in reports:
        if not isinstance(report, Mapping):
            continue
        if context is None and report.get("context"):
            context = str(report["context"])
        if not task and report.get("task"):
            task = str(report["task"])
        for raw in report.get("decisions") or []:
            if not isinstance(raw, Mapping):
                continue
            path = str(raw.get("path") or "").strip()
            if path:
                decisions[path] = deepcopy(dict(raw))
    ordered = [decisions[path] for path in sorted(decisions)]
    unresolved = [item["path"] for item in ordered if item.get("unresolved")]
    inferred = [
        item["path"]
        for item in ordered
        if item.get("origin") not in {
            InferenceOrigin.EXPLICIT.value,
            InferenceOrigin.UNRESOLVED.value,
        }
    ]
    return {
        "schema_version": INFERENCE_SCHEMA_VERSION,
        "status": "degraded" if unresolved else "resolved",
        "context": context or None,
        "task": task,
        "review_required": any(bool(item.get("review_required")) for item in ordered),
        "inferred_count": len(inferred),
        "unresolved_count": len(unresolved),
        "inferred_paths": inferred,
        "unresolved_paths": unresolved,
        "decisions": ordered,
    }


def render_inference_markdown(report: Mapping[str, Any]) -> str:
    """Render the inference ledger as a compact review artifact."""
    inferred_count = int(report.get("inferred_count") or 0)
    unresolved_count = int(report.get("unresolved_count") or 0)
    status = str(report.get("status") or "degraded")
    lines = [
        "# Automatic inference",
        "",
        f"- Status: `{status}`",
        f"- Inferred details: `{inferred_count}`",
        f"- Unresolved details: `{unresolved_count}`",
        f"- Review required: `{'yes' if report.get('review_required') else 'no'}`",
        "",
        "## Decisions",
        "",
        "| Path | Selected value | Origin | Confidence | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    decisions = [item for item in report.get("decisions") or [] if isinstance(item, Mapping)]
    if decisions:
        for decision in decisions:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        decision.get("path"),
                        _display_value(decision.get("value")),
                        decision.get("origin"),
                        decision.get("confidence"),
                        decision.get("source"),
                    )
                )
                + " |"
            )
    else:
        lines.append("| — | — | — | — | No automatic decisions were needed. |")
    if unresolved_count:
        lines.extend(
            [
                "",
                "## Final-generation boundary",
                "",
                "A candidate script may still be generated, but unresolved details must not be certified for instrument use.",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _confidence_for(
    selected: InferenceCandidate,
    ranked: Sequence[InferenceCandidate],
) -> Confidence:
    if len(ranked) > 1:
        next_candidate = ranked[1]
        if (selected.priority, selected.score) == (next_candidate.priority, next_candidate.score):
            return Confidence.LOW
    if selected.origin in {InferenceOrigin.EXACT_SOURCE, InferenceOrigin.SOURCE_CONSENSUS}:
        return Confidence.HIGH
    if selected.origin is InferenceOrigin.CONTEXT_ROLE:
        return Confidence.MEDIUM
    if selected.origin in {
        InferenceOrigin.CONTEXT_FALLBACK,
        InferenceOrigin.TEMPLATE_DEFAULT,
        InferenceOrigin.PROMPT_FALLBACK,
    }:
        return Confidence.LOW
    return Confidence.MEDIUM


def _candidate_sort_key(candidate: InferenceCandidate) -> tuple[int, int, str, str]:
    return (
        -int(candidate.priority),
        -int(candidate.score),
        _canonical_value(candidate.value),
        str(candidate.source).casefold(),
    )


def _canonical_value(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=True)


def _display_value(value: Any) -> str:
    if value is None:
        return "unresolved"
    if isinstance(value, str):
        return value
    return _canonical_value(value)


def _markdown_cell(value: Any) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _has_value(value: Any) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _first_value(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if _has_value(value):
            return value
    return None


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    if not _PATH_RE.match(path):
        raise ValueError(f"Unsupported inference path: {path!r}")
    keys = [part for part in path.removeprefix("$").split(".") if part]
    current = document
    for key in keys[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[keys[-1]] = value
