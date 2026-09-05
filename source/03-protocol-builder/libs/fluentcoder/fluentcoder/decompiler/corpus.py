"""Deterministic harness for decompiled `.xscr` corpus validation."""


from __future__ import annotations
from fluentcoder.simulator.options import SimulationOptions


import importlib.util
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..ir.schema import GenericStep
from .codegen import emit_python
from .xscr_parser import parse_xscr


@dataclass
class CorpusReportData:
    xscr_paths: list[Path]
    summary: dict[str, Any]
    unsupported_totals: Counter[str]
    generic_totals: Counter[str]
    parser_priorities: list[dict[str, Any]]
    include_ready_to_import: bool
    ready_to_import_root: Path | None
    unsupported_protocols: dict[str, set[str]] | None = None
    generic_protocols: dict[str, set[str]] | None = None


@dataclass(frozen=True)
class CorpusResult:
    name: str
    xscr_path: str
    generated_python: str | None
    status: str
    classification: str
    modeled_coverage: float | None = None
    total_executed_steps: int | None = None
    unsupported_command_ids: dict[str, int] = field(default_factory=dict)
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "xscr_path": self.xscr_path,
            "generated_python": self.generated_python,
            "status": self.status,
            "classification": self.classification,
            "modeled_coverage": self.modeled_coverage,
            "total_executed_steps": self.total_executed_steps,
            "unsupported_command_ids": dict(self.unsupported_command_ids),
            "failure": None if self.failure is None else dict(self.failure),
        }


def run_decompiled_corpus(
    paths: Iterable[Path | str],
    *,
    output_dir: Path | str,
    strict: bool = True,
    fail_on_opaque: bool = True,
    subroutine_dirs: list[Path] | None = None,
    subroutine_xscr: list[Path] | None = None,
    subroutine_registry=None,
) -> list[CorpusResult]:
    """Decompile, execute, and simulate a fixed `.xscr` corpus."""
    from ..subroutines import build_subroutine_registry

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    registry = subroutine_registry
    if registry is None and (subroutine_dirs or subroutine_xscr):
        registry = build_subroutine_registry(
            subroutine_dirs=subroutine_dirs,
            subroutine_xscr=subroutine_xscr,
        )

    results: list[CorpusResult] = []
    for raw_path in paths:
        xscr_path = Path(raw_path)
        generated_python = output_root / f"{xscr_path.stem}_decompiled.py"
        wt = None
        try:
            proto = parse_xscr(xscr_path)
            generated_python.write_text(
                emit_python(proto, source_xscr=str(xscr_path)),
                encoding="utf-8",
            )
            module = _load_module(generated_python, alias=f"corpus_{xscr_path.stem}")
            wt = module.build_worktable()
            wt.simulate(SimulationOptions(strict=strict,
                fail_on_opaque=fail_on_opaque,
                subroutine_registry=registry,))
        except Exception as exc:
            simulation_report = getattr(wt, "simulation_report", None)
            failure = (
                simulation_report.failure.to_dict()
                if simulation_report is not None and simulation_report.failure is not None
                else _synthetic_failure(exc)
            )
            opaque = (
                {}
                if simulation_report is None
                else dict(simulation_report.unsupported_command_ids)
            )
            results.append(
                CorpusResult(
                    name=xscr_path.stem,
                    xscr_path=str(xscr_path),
                    generated_python=str(generated_python) if generated_python.exists() else None,
                    status="failed" if failure is not None else "passed",
                    classification=_classify_failure_bucket(failure["category"]),
                    modeled_coverage=(
                        None
                        if simulation_report is None
                        else simulation_report.modeled_coverage
                    ),
                    total_executed_steps=(
                        None
                        if simulation_report is None
                        else simulation_report.total_executed_steps
                    ),
                    unsupported_command_ids=opaque,
                    failure=failure,
                )
            )
            continue

        report = wt.simulation_report
        if report is None:
            raise RuntimeError("Corpus simulation completed without a simulation report.")
        results.append(
            CorpusResult(
                name=xscr_path.stem,
                xscr_path=str(xscr_path),
                generated_python=str(generated_python),
                status=report.status,
                classification="passes_strictly" if report.status == "passed" else "other",
                modeled_coverage=report.modeled_coverage,
                total_executed_steps=report.total_executed_steps,
                unsupported_command_ids=dict(report.unsupported_command_ids),
                failure=None if report.failure is None else report.failure.to_dict(),
            )
        )
    return results


def summarize_corpus_results(results: Iterable[CorpusResult]) -> dict[str, Any]:
    items = [result.to_dict() for result in results]
    counts = Counter(result["classification"] for result in items)
    status_counts = Counter(result["status"] for result in items)
    return {
        "protocols": items,
        "classification_counts": dict(sorted(counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
    }


def default_ready_to_import_root() -> Path | None:
    """Return the workspace ready-to-import tree when present."""
    root = Path(__file__).resolve().parents[5] / "ready-to-import"
    return root if root.is_dir() else None


def resolve_xscr_paths(
    paths: Iterable[Path | str],
    *,
    include_ready_to_import: bool = False,
    ready_to_import_root: Path | str | None = None,
) -> list[Path]:
    """Expand files/directories/globs and optionally include ready-to-import scripts."""
    resolved: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        normalized = path.resolve()
        if normalized.suffix.lower() != ".xscr" or normalized in seen:
            return
        seen.add(normalized)
        resolved.append(normalized)

    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            for candidate in sorted(path.rglob("*.xscr")):
                _add(candidate)
            continue
        if any(ch in str(path) for ch in "*?[]"):
            for candidate in sorted(path.parent.glob(path.name)):
                if candidate.is_file():
                    _add(candidate)
            continue
        if path.is_file():
            _add(path)

    if include_ready_to_import:
        root = (
            Path(ready_to_import_root)
            if ready_to_import_root is not None
            else default_ready_to_import_root()
        )
        if root is not None and root.is_dir():
            for candidate in sorted(root.rglob("*.xscr")):
                _add(candidate)

    return resolved


def aggregate_unsupported_command_ids(
    results: Iterable[CorpusResult],
) -> tuple[Counter[str], dict[str, set[str]]]:
    """Count opaque simulator command IDs and the protocols that hit each one."""
    totals: Counter[str] = Counter()
    protocols_by_id: dict[str, set[str]] = {}
    for result in results:
        for command_id, count in result.unsupported_command_ids.items():
            totals[command_id] += count
            protocols_by_id.setdefault(command_id, set()).add(result.name)
    return totals, protocols_by_id


def count_generic_step_types(
    xscr_paths: Iterable[Path | str],
) -> tuple[Counter[str], dict[str, set[str]]]:
    """Count decompiler ``GenericStep`` type names and the protocols that contain them."""
    totals: Counter[str] = Counter()
    protocols_by_type: dict[str, set[str]] = {}
    for raw_path in xscr_paths:
        xscr_path = Path(raw_path)
        try:
            proto = parse_xscr(xscr_path)
        except Exception as exc:
            totals[f"_parse_error:{exc}"] += 1
            protocols_by_type.setdefault(f"_parse_error:{xscr_path.name}", set()).add(xscr_path.stem)
            continue
        local = Counter(
            step.step_type
            for step in _walk_protocol_steps(proto.groups)
            if isinstance(step, GenericStep)
        )
        for step_type, count in local.items():
            totals[step_type] += count
            protocols_by_type.setdefault(step_type, set()).add(xscr_path.stem)
    return totals, protocols_by_type


def suggest_parser_priorities(
    unsupported_totals: Counter[str],
    generic_totals: Counter[str],
    *,
    unsupported_protocols: dict[str, set[str]] | None = None,
    generic_protocols: dict[str, set[str]] | None = None,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """Rank command/type names for future parser work."""
    unsupported_protocols = unsupported_protocols or {}
    generic_protocols = generic_protocols or {}
    command_ids = set(unsupported_totals) | set(generic_totals)
    ranked: list[dict[str, Any]] = []
    for command_id in command_ids:
        opaque_count = unsupported_totals.get(command_id, 0)
        generic_count = generic_totals.get(command_id, 0)
        protocol_names = sorted(
            unsupported_protocols.get(command_id, set())
            | generic_protocols.get(command_id, set())
        )
        ranked.append(
            {
                "command_id": command_id,
                "opaque_count": opaque_count,
                "generic_count": generic_count,
                "priority_score": (opaque_count * 2) + generic_count + len(protocol_names),
                "protocol_count": len(protocol_names),
                "protocols": protocol_names,
            }
        )
    ranked.sort(
        key=lambda item: (
            -item["priority_score"],
            -item["opaque_count"],
            -item["generic_count"],
            item["command_id"],
        )
    )
    return ranked[:top_n]


def render_corpus_report_markdown(data: CorpusReportData) -> str:
    """Render a markdown mining report for unsupported decompiler/simulator gaps."""
    unsupported_protocols = data.unsupported_protocols or {}
    generic_protocols = data.generic_protocols or {}
    lines = [
        "# Decompiler Corpus Report",
        "",
        "## Summary",
        f"- Protocols scanned: {len(data.xscr_paths)}",
        f"- Ready-to-import included: {'yes' if data.include_ready_to_import else 'no'}",
    ]
    if data.include_ready_to_import and data.ready_to_import_root is not None:
        lines.append(f"- Ready-to-import root: `{data.ready_to_import_root}`")
    lines.extend(
        [
            f"- Status counts: {data.summary['status_counts']}",
            f"- Classification counts: {data.summary['classification_counts']}",
            "",
            "## Top Unsupported Command IDs",
            "",
            "Opaque simulator command IDs ranked by total hits across the corpus.",
            "",
            "| Rank | Command ID | Opaque hits | Protocols |",
            "| ---: | --- | ---: | ---: |",
        ]
    )
    if data.unsupported_totals:
        for rank, (command_id, count) in enumerate(
            data.unsupported_totals.most_common(20),
            start=1,
        ):
            protocol_count = len(unsupported_protocols.get(command_id, set()))
            lines.append(f"| {rank} | `{command_id}` | {count} | {protocol_count} |")
    else:
        lines.append("| - | _none_ | 0 | 0 |")

    lines.extend(
        [
            "",
            "## Top GenericStep Type Names",
            "",
            "Decompiler fallback step types ranked by parse-time occurrences.",
            "",
            "| Rank | Step type | Generic hits | Protocols |",
            "| ---: | --- | ---: | ---: |",
        ]
    )
    if data.generic_totals:
        for rank, (step_type, count) in enumerate(data.generic_totals.most_common(20), start=1):
            protocol_count = len(generic_protocols.get(step_type, set()))
            lines.append(f"| {rank} | `{step_type}` | {count} | {protocol_count} |")
    else:
        lines.append("| - | _none_ | 0 | 0 |")

    lines.extend(
        [
            "",
            "## Suggested Parser Priority",
            "",
            "Higher scores weight opaque simulator failures twice and add protocol breadth.",
            "",
            "| Priority | Command ID | Score | Opaque | Generic | Protocols |",
            "| ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    if data.parser_priorities:
        for rank, item in enumerate(data.parser_priorities, start=1):
            lines.append(
                f"| {rank} | `{item['command_id']}` | {item['priority_score']} | "
                f"{item['opaque_count']} | {item['generic_count']} | {item['protocol_count']} |"
            )
    else:
        lines.append("| - | _none_ | 0 | 0 | 0 | 0 |")

    lines.extend(["", "## Inputs", ""])
    for path in data.xscr_paths:
        lines.append(f"- `{path}`")
    lines.append("")
    return "\n".join(lines)


def discover_subroutine_dirs(xscr_paths: Iterable[Path | str]) -> list[Path]:
    """Collect ``subroutines`` directories adjacent to corpus ``.xscr`` inputs."""
    discovered: set[Path] = set()
    for raw_path in xscr_paths:
        path = Path(raw_path).resolve()
        for parent in path.parents:
            candidate = parent / "subroutines"
            if not candidate.is_dir():
                continue
            if any(candidate.rglob("*.xscr")):
                discovered.add(candidate.resolve())
    return sorted(discovered)


def run_corpus_report(
    paths: Iterable[Path | str],
    *,
    output_dir: Path | str,
    include_ready_to_import: bool = False,
    ready_to_import_root: Path | str | None = None,
    top_n: int = 20,
    strict: bool = True,
    fail_on_opaque: bool = True,
    subroutine_dirs: list[Path] | None = None,
    subroutine_xscr: list[Path] | None = None,
) -> dict[str, Any]:
    """Decompile/simulate a corpus and emit unsupported-command mining artifacts."""
    xscr_paths = resolve_xscr_paths(
        paths,
        include_ready_to_import=include_ready_to_import,
        ready_to_import_root=ready_to_import_root,
    )
    if not xscr_paths:
        raise ValueError("No .xscr inputs resolved from the provided paths.")

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    effective_subroutine_dirs = list(subroutine_dirs or [])
    for directory in discover_subroutine_dirs(xscr_paths):
        if directory not in effective_subroutine_dirs:
            effective_subroutine_dirs.append(directory)
    results = run_decompiled_corpus(
        xscr_paths,
        output_dir=output_root / "generated",
        strict=strict,
        fail_on_opaque=fail_on_opaque,
        subroutine_dirs=effective_subroutine_dirs or None,
        subroutine_xscr=subroutine_xscr,
    )
    summary = summarize_corpus_results(results)
    unsupported_totals, unsupported_protocols = aggregate_unsupported_command_ids(results)
    generic_totals, generic_protocols = count_generic_step_types(xscr_paths)
    parser_priorities = suggest_parser_priorities(
        unsupported_totals,
        generic_totals,
        unsupported_protocols=unsupported_protocols,
        generic_protocols=generic_protocols,
        top_n=top_n,
    )
    ready_root = (
        Path(ready_to_import_root)
        if ready_to_import_root is not None
        else default_ready_to_import_root()
    )
    report_data = CorpusReportData(
        xscr_paths=xscr_paths,
        summary=summary,
        unsupported_totals=unsupported_totals,
        generic_totals=generic_totals,
        parser_priorities=parser_priorities,
        include_ready_to_import=include_ready_to_import,
        ready_to_import_root=ready_root,
        unsupported_protocols=unsupported_protocols,
        generic_protocols=generic_protocols,
    )
    report_markdown = render_corpus_report_markdown(report_data)
    report_path = output_root / "corpus_report.md"
    report_path.write_text(report_markdown, encoding="utf-8")
    return {
        "xscr_paths": [str(path) for path in xscr_paths],
        "summary": summary,
        "unsupported_command_ids": dict(unsupported_totals),
        "generic_step_types": dict(generic_totals),
        "parser_priorities": parser_priorities,
        "report_path": str(report_path),
        "report_markdown": report_markdown,
    }


def _walk_protocol_steps(groups) -> Iterator[Any]:
    for group in groups:
        yield from _walk_steps(group.steps)


def _walk_steps(steps) -> Iterator[Any]:
    for step in steps:
        yield step
        nested = getattr(step, "steps", None)
        if nested:
            yield from _walk_steps(nested)
        for branch in ("then_steps", "else_steps"):
            child_steps = getattr(step, branch, None)
            if child_steps:
                yield from _walk_steps(child_steps)


def _load_module(path: Path, *, alias: str) -> Any:
    spec = importlib.util.spec_from_file_location(alias, str(path))
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = original
    return module


def _synthetic_failure(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    lowered = message.lower()
    if "not bound to a specific fluentcontrol workspace" in lowered:
        category = "workspace_binding"
    elif "is not on workspace" in lowered:
        category = "workspace_slot"
    elif "worktableworkspace reference" in lowered:
        category = "workspace_binding"
    elif "catalog" in lowered or "installed labware" in lowered or "not found in catalog index" in lowered:
        category = "catalog"
    elif "opaque" in lowered:
        category = "opaque_policy"
    elif "coverage" in lowered:
        category = "coverage_policy"
    else:
        category = "simulation_state"
    return {
        "category": category,
        "exception_type": type(exc).__name__,
        "message": message,
        "step_index": None,
        "step_type": None,
        "command_id": None,
    }


def _classify_failure_bucket(category: str) -> str:
    if category in {"workspace_binding", "workspace_slot", "catalog"}:
        return "workspace_or_catalog"
    if category == "opaque_policy":
        return "unsupported_command"
    if category in {"liquid_state", "source_volume_short", "well_overflow"}:
        return "liquid_semantics"
    return "other"
