"""Command-line interface for local Tecan worklist generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tecan_common import native_acceleration
from tecan_common.performance import performance_marker

from .gwl import parse_gwl, parse_gwl_text
from .optimizer import analyze_worklist, optimize_worklist, semantic_equivalent
from .transfer import build_worklist, load_transfers, validate_transfers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worklist-builder")
    sub = parser.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert", help="convert transfer CSV to .gwl")
    convert.add_argument("csv", type=Path)
    convert.add_argument("--output", "-o", type=Path, required=True)
    convert.add_argument("--wash-policy", choices=["each", "none"], default="each")
    convert.add_argument("--batch-size", type=int, default=None)
    convert.add_argument("--well-rows", type=int, default=8)
    convert.add_argument("--strict", action="store_true")
    convert.add_argument("--json", action="store_true", dest="as_json")
    convert.set_defaults(func=_cmd_convert)

    validate = sub.add_parser("validate", help="validate transfer CSV")
    validate.add_argument("csv", type=Path)
    validate.add_argument("--well-rows", type=int, default=8)
    validate.add_argument("--strict", action="store_true")
    validate.add_argument("--json", action="store_true", dest="as_json")
    validate.set_defaults(func=_cmd_validate)

    summarize = sub.add_parser("summarize", help="summarize a .gwl file")
    summarize.add_argument("gwl", type=Path)
    summarize.add_argument("--json", action="store_true", dest="as_json")
    summarize.set_defaults(func=_cmd_summarize)

    analyze = sub.add_parser("analyze", help="analyze worklist tip selection and scheduling semantics")
    analyze.add_argument("gwl", type=Path)
    analyze.add_argument("--enabled-tip-mask")
    analyze.add_argument("--supported-channels", type=int, default=8)
    analyze.add_argument("--json", action="store_true", dest="as_json")
    analyze.set_defaults(func=_cmd_analyze)

    optimize = sub.add_parser("optimize", help="write an opt-in dependency-safe worklist ordering")
    optimize.add_argument("gwl", type=Path)
    optimize.add_argument("--output", "-o", type=Path, required=True)
    optimize.add_argument("--enabled-tip-mask")
    optimize.add_argument("--supported-channels", type=int, default=8)
    optimize.add_argument("--json", action="store_true", dest="as_json")
    optimize.set_defaults(func=_cmd_optimize)

    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_convert(args: argparse.Namespace) -> int:
    transfers = load_transfers(args.csv, well_rows=args.well_rows)
    result = validate_transfers(transfers, strict=args.strict)
    if not result.ok:
        return _emit_validation_failure(result, as_json=args.as_json)

    with performance_marker("worklist.gwl_record_assembly") as metrics:
        native = native_acceleration.worklist_build_transfers(
            [vars(transfer) for transfer in transfers],
            args.output.stem,
            args.wash_policy,
            args.batch_size,
        )
        if native is None:
            worklist = build_worklist(
                transfers,
                name=args.output.stem,
                wash_policy=args.wash_policy,
                batch_size=args.batch_size,
            )
            gwl_text = worklist.to_text()
            record_counts = worklist.counts()
        else:
            gwl_text = native["text"]
            record_counts = native["counts"]
        record_count = sum(record_counts.values())
        metrics["record_count"] = record_count

    with performance_marker("worklist.gwl_write") as metrics:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(gwl_text, encoding="utf-8")
        metrics["record_count"] = record_count

    payload = {
        "ok": True,
        "input_csv": str(args.csv),
        "output_gwl": str(args.output),
        "transfer_count": len(transfers),
        "total_volume_ul": sum(t.volume_ul for t in transfers),
        "record_counts": record_counts,
        "warnings": list(result.warnings),
    }
    _emit(payload, as_json=args.as_json)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    transfers = load_transfers(args.csv, well_rows=args.well_rows)
    result = validate_transfers(transfers, strict=args.strict)
    payload = {
        "ok": result.ok,
        "input_csv": str(args.csv),
        "transfer_count": len(transfers),
        "total_volume_ul": sum(t.volume_ul for t in transfers),
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }
    _emit(payload, as_json=args.as_json)
    return 0 if result.ok else 1


def _cmd_summarize(args: argparse.Namespace) -> int:
    with performance_marker("worklist.gwl_parse") as metrics:
        native = native_acceleration.worklist_parse_gwl(args.gwl)
        if native is None:
            worklist = parse_gwl(args.gwl)
            counts = worklist.counts()
            record_count = sum(counts.values())
        else:
            counts = native["counts"]
            record_count = native["record_count"]
        metrics["record_count"] = record_count

    with performance_marker("worklist.gwl_summary") as metrics:
        pipette_records = counts.get("A", 0) + counts.get("D", 0)
        transfer_pairs_estimate = min(counts.get("A", 0), counts.get("D", 0))
        metrics["record_count"] = record_count
    payload = {
        "ok": True,
        "gwl": str(args.gwl),
        "record_counts": counts,
        "pipette_records": pipette_records,
        "transfer_pairs_estimate": transfer_pairs_estimate,
    }
    _emit(payload, as_json=args.as_json)
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    try:
        with performance_marker("worklist.gwl_parse") as metrics:
            worklist = parse_gwl(args.gwl, permissive=True)
            lines = [record.to_line() for record in worklist.records]
            record_count = len(lines)
            metrics["record_count"] = record_count

        with performance_marker("worklist.gwl_analysis") as metrics:
            native = native_acceleration.worklist_analyze_lines(
                lines, args.enabled_tip_mask, args.supported_channels
            )
            if native is None:
                analysis = analyze_worklist(
                    worklist,
                    enabled_tip_mask=args.enabled_tip_mask,
                    supported_channels=args.supported_channels,
                )
                analysis_payload = analysis.to_dict()
                analysis_ok = not analysis.has_errors
            else:
                analysis_payload = native
                analysis_ok = native["ok"]
            metrics["record_count"] = record_count
    except (OSError, ValueError) as exc:
        return _emit_semantic_failure(str(exc), as_json=args.as_json)

    payload = {"ok": analysis_ok, "gwl": str(args.gwl)}
    for key in (
        "schema_version",
        "record_count",
        "unit_count",
        "explicit_tip_assignments",
        "automatic_tip_units",
        "enabled_tip_mask",
        "barriers",
        "parallel_windows",
        "diagnostics",
        "runtime_parallelism",
    ):
        if key in analysis_payload:
            payload[key] = analysis_payload[key]
    _emit(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


def _cmd_optimize(args: argparse.Namespace) -> int:
    try:
        with performance_marker("worklist.gwl_parse") as metrics:
            worklist = parse_gwl(args.gwl, permissive=True)
            lines = [record.to_line() for record in worklist.records]
            record_count = len(lines)
            metrics["record_count"] = record_count

        with performance_marker("worklist.gwl_optimization") as metrics:
            native = native_acceleration.worklist_optimize_lines(
                lines,
                worklist.name,
                args.enabled_tip_mask,
                args.supported_channels,
            )
            if native is None:
                result = optimize_worklist(
                    worklist,
                    enabled_tip_mask=args.enabled_tip_mask,
                    supported_channels=args.supported_channels,
                )
                optimized_text = result.worklist.to_text()
                analysis_payload = result.to_dict()
                optimization_ok = not result.analysis.has_errors
            else:
                optimized_text = native.pop("text")
                analysis_payload = native
                optimization_ok = native["ok"]
            metrics["record_count"] = record_count
    except (OSError, ValueError) as exc:
        return _emit_semantic_failure(str(exc), as_json=args.as_json)

    payload = {
        "ok": optimization_ok,
        "gwl": str(args.gwl),
        "output_gwl": str(args.output),
    }
    for key in (
        "schema_version",
        "record_count",
        "unit_count",
        "explicit_tip_assignments",
        "automatic_tip_units",
        "enabled_tip_mask",
        "barriers",
        "parallel_windows",
        "diagnostics",
        "runtime_parallelism",
        "changed",
        "reordered_units",
        "reasons",
    ):
        if key in analysis_payload:
            payload[key] = analysis_payload[key]

    if payload["ok"]:
        with performance_marker("worklist.gwl_equivalence_verification") as metrics:
            reparsed = parse_gwl_text(
                optimized_text, name=worklist.name, permissive=True
            )
            equivalent = semantic_equivalent(
                worklist,
                reparsed,
                enabled_tip_mask=args.enabled_tip_mask,
                supported_channels=args.supported_channels,
            )
            metrics["record_count"] = len(reparsed.records)
        if not equivalent:
            payload["ok"] = False
            payload.setdefault("reasons", []).append(
                "optimized output failed semantic-equivalence verification"
            )
        else:
            with performance_marker("worklist.gwl_write") as metrics:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(optimized_text, encoding="utf-8")
                metrics["record_count"] = len(reparsed.records)
    _emit(payload, as_json=args.as_json)
    return 0 if payload["ok"] else 1


def _emit_validation_failure(result, *, as_json: bool) -> int:
    payload = {
        "ok": False,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }
    _emit(payload, as_json=as_json, stream=sys.stderr)
    return 1


def _emit_semantic_failure(message: str, *, as_json: bool) -> int:
    _emit({"ok": False, "errors": [message]}, as_json=as_json, stream=sys.stderr)
    return 1


def _emit(payload: dict, *, as_json: bool, stream=None) -> None:
    stream = stream or sys.stdout
    if as_json:
        print(json.dumps(payload, indent=2), file=stream)
        return
    if payload.get("ok") is not True:
        print("Validation failed.", file=stream)
        for error in payload.get("errors", []):
            print(f"  error: {error}", file=stream)
        for warning in payload.get("warnings", []):
            print(f"  warning: {warning}", file=stream)
        return
    if "output_gwl" in payload:
        print(f"Wrote {payload['output_gwl']}")
    elif "input_csv" in payload:
        print(f"Validated {payload['input_csv']}")
    elif "gwl" in payload:
        print(f"Summarized {payload['gwl']}")
    for key in ("transfer_count", "total_volume_ul", "record_counts", "transfer_pairs_estimate"):
        if key in payload:
            print(f"{key}: {payload[key]}")
    for warning in payload.get("warnings", []):
        print(f"warning: {warning}")


if __name__ == "__main__":
    raise SystemExit(main())
