"""Command-line interface for local Tecan worklist generation."""

import argparse
import json
import sys
from pathlib import Path

from .gwl import parse_gwl
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

    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_convert(args: argparse.Namespace) -> int:
    transfers = load_transfers(args.csv, well_rows=args.well_rows)
    result = validate_transfers(transfers, strict=args.strict)
    if not result.ok:
        return _emit_validation_failure(result, as_json=args.as_json)

    worklist = build_worklist(
        transfers,
        name=args.output.stem,
        wash_policy=args.wash_policy,
        batch_size=args.batch_size,
    )
    worklist.write(args.output)
    payload = {
        "ok": True,
        "input_csv": str(args.csv),
        "output_gwl": str(args.output),
        "transfer_count": len(transfers),
        "total_volume_ul": sum(t.volume_ul for t in transfers),
        "record_counts": worklist.counts(),
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
    worklist = parse_gwl(args.gwl)
    counts = worklist.counts()
    pipette_records = counts.get("A", 0) + counts.get("D", 0)
    payload = {
        "ok": True,
        "gwl": str(args.gwl),
        "record_counts": counts,
        "pipette_records": pipette_records,
        "transfer_pairs_estimate": min(counts.get("A", 0), counts.get("D", 0)),
    }
    _emit(payload, as_json=args.as_json)
    return 0


def _emit_validation_failure(result, *, as_json: bool) -> int:
    payload = {
        "ok": False,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }
    _emit(payload, as_json=as_json, stream=sys.stderr)
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
