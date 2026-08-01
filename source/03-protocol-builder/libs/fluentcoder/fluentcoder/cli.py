"""fluentcoder CLI.

Subcommands:

- `fluentcoder compile <protocol.py>`   — execute the script and write `.xscr`.
- `fluentcoder simulate <protocol.py>`  — run the simulator, print snapshot summary.
- `fluentcoder decompile <file.xscr>`   — emit a fluentcoder Python protocol from a .xscr.
- `fluentcoder decompile corpus-report <paths...>` — mine unsupported commands across a corpus.
- `fluentcoder catalog refresh [...]`   — rebuild the SQL catalog index.
- `fluentcoder catalog info`            — show install path, fingerprint, counts.
- `fluentcoder catalog find <pattern>`  — substring-search components by name.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional


# Repo root: .../libs/fluentcoder/fluentcoder/cli.py → parents[5]
_REPO_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_TOOL_BUILD_DIR = (
    _REPO_ROOT / "ready-to-import" / "_shared" / "temp_files" / "build"
)


# ── Entry point ────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) >= 2 and argv[0] == "decompile" and argv[1] != "corpus-report":
        argv.insert(1, "file")

    parser = argparse.ArgumentParser(prog="fluentcoder", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_compile = sub.add_parser("compile", help="render a protocol .py to .xscr")
    p_compile.add_argument("input", type=Path)
    p_compile.add_argument("--output", "-o", type=Path, default=None)
    p_compile.add_argument(
        "--deterministic",
        action="store_true",
        help="emit a stable WorkspaceDelta GUID derived from the protocol name",
    )
    p_compile.set_defaults(func=_cmd_compile)

    p_simulate = sub.add_parser("simulate", help="run the simulator and print snapshot summary")
    p_simulate.add_argument("input", type=Path)
    _add_simulate_policy_args(p_simulate)
    _add_subroutine_registry_args(p_simulate)
    _add_snapshot_args(p_simulate)
    p_simulate.set_defaults(func=_cmd_simulate)

    p_decompile = sub.add_parser(
        "decompile",
        help="parse a .xscr and emit a fluentcoder Python protocol",
    )
    decompile_sub = p_decompile.add_subparsers(dest="decompile_cmd", required=True)

    p_decompile_file = decompile_sub.add_parser(
        "file",
        help="decompile one .xscr into a fluentcoder Python protocol",
    )
    p_decompile_file.add_argument("input", type=Path)
    p_decompile_file.add_argument("--output", "-o", type=Path, default=None,
                                    help="output .py path (defaults to <input>.py)")
    p_decompile_file.add_argument("--strict", action="store_true",
                                    help="exit 1 if any step decoded as GenericStep")
    p_decompile_file.add_argument(
        "--simulate",
        action="store_true",
        help="run the simulator on the emitted Python protocol",
    )
    _add_simulate_policy_args(p_decompile_file, strict_flag="simulate-strict", strict_dest="simulate_strict")
    _add_subroutine_registry_args(p_decompile_file)
    _add_snapshot_args(p_decompile_file)
    p_decompile_file.set_defaults(func=_cmd_decompile)

    p_corpus_report = decompile_sub.add_parser(
        "corpus-report",
        help="decompile/simulate a corpus and rank unsupported commands",
    )
    p_corpus_report.add_argument("paths", nargs="+", type=Path,
                                 help="one or more .xscr files or directories")
    p_corpus_report.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_TOOL_BUILD_DIR / "corpus_report",
        help="directory for generated Python, JSON summary, and markdown report",
    )
    p_corpus_report.add_argument(
        "--include-ready-to-import",
        action="store_true",
        help="also scan Projects/Fluent-AI-Assistance/ready-to-import/**/*.xscr when present",
    )
    p_corpus_report.add_argument(
        "--ready-to-import-root",
        type=Path,
        default=None,
        help="override the ready-to-import root used with --include-ready-to-import",
    )
    p_corpus_report.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="number of parser-priority rows to include in the report",
    )
    p_corpus_report.add_argument("--json", dest="as_json", action="store_true",
                                 help="emit the mining summary as JSON")
    p_corpus_report.add_argument(
        "--fail-on-opaque",
        action="store_true",
        help="exit 1 if any GenericStep/raw XML command is opaque",
    )
    p_corpus_report.add_argument(
        "--min-coverage",
        type=float,
        default=None,
        help="exit 1 if modeled simulator coverage is below this fraction",
    )
    p_corpus_report.add_argument(
        "--simulate-strict",
        dest="simulate_strict",
        action="store_true",
        help="require a bound workspace plus strict slot/catalog semantics",
    )
    _add_subroutine_registry_args(p_corpus_report)
    p_corpus_report.set_defaults(func=_cmd_corpus_report)

    p_cat = sub.add_parser("catalog", help="catalog index management")
    cat_sub = p_cat.add_subparsers(dest="cat_cmd", required=True)

    p_refresh = cat_sub.add_parser("refresh", help="rebuild the SQL catalog index")
    p_refresh.add_argument("--install", type=Path, default=None,
                           help="FluentControl install path (defaults to env or built-in)")
    p_refresh.add_argument("--db", type=Path, default=None,
                           help="output index path (defaults to package's install_index.db)")
    p_refresh.add_argument("--all-connectors", action="store_true",
                           help="index every Connectors/*.xcon (14k+ on full installs)")
    p_refresh.set_defaults(func=_cmd_catalog_refresh)

    p_info = cat_sub.add_parser("info", help="print install path, fingerprint, category counts")
    p_info.set_defaults(func=_cmd_catalog_info)

    p_find = cat_sub.add_parser("find", help="substring search components by name")
    p_find.add_argument("pattern", help="substring to match (case-insensitive)")
    p_find.add_argument("--category", help="filter by category")
    p_find.set_defaults(func=_cmd_catalog_find)

    args = parser.parse_args(argv)
    return args.func(args)


# ── compile / simulate ─────────────────────────────────────────────


def _add_snapshot_args(parser: argparse.ArgumentParser) -> None:
    snapshot_group = parser.add_mutually_exclusive_group()
    snapshot_group.add_argument(
        "--record-snapshots",
        dest="record_snapshots",
        action="store_true",
        default=None,
        help="deep-copy twin state after every simulation step (default)",
    )
    snapshot_group.add_argument(
        "--no-snapshots",
        dest="record_snapshots",
        action="store_false",
        help="keep only the final simulation snapshot (lower memory)",
    )
    snapshot_group.add_argument(
        "--delta-snapshots",
        dest="record_snapshots",
        action="store_const",
        const="delta",
        help="store lightweight per-step delta snapshots (lower memory than full)",
    )


def _add_simulate_policy_args(
    parser: argparse.ArgumentParser,
    *,
    strict_flag: str = "strict",
    strict_dest: str | None = None,
) -> None:
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit a JSON summary of all snapshots",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="include simulator coverage in text output",
    )
    parser.add_argument(
        "--fail-on-opaque",
        action="store_true",
        help="exit 1 if any GenericStep/raw XML command is opaque",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=None,
        help="exit 1 if modeled simulator coverage is below this fraction",
    )
    parser.add_argument(
        f"--{strict_flag}",
        dest=strict_dest or strict_flag.replace("-", "_"),
        action="store_true",
        help="require a bound workspace plus strict slot/catalog semantics",
    )


def _add_subroutine_registry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subroutine-dir",
        type=Path,
        action="append",
        default=None,
        help="register all .xscr subroutines under a directory for inline simulation",
    )
    parser.add_argument(
        "--subroutine-xscr",
        type=Path,
        action="append",
        default=None,
        help="register one subroutine .xscr for inline simulation (repeatable)",
    )


def _cmd_compile(args) -> int:
    wt = _load_protocol(args.input)
    output = args.output or args.input.with_suffix(".xscr")
    wt.compile(output, deterministic=bool(args.deterministic))
    print(f"Compiled {wt.name} -> {output}")
    return 0


def _cmd_simulate(args) -> int:
    wt = _load_protocol(args.input)
    return _simulate_worktable(wt, args)


def _simulate_worktable(wt, args) -> int:
    subroutine_registry = _build_subroutine_registry(
        subroutine_dirs=getattr(args, "subroutine_dir", None),
        subroutine_xscr=getattr(args, "subroutine_xscr", None),
    )
    try:
        simulate_kwargs: dict[str, Any] = {
            "fail_on_opaque": args.fail_on_opaque,
            "min_coverage": args.min_coverage,
            "strict": _simulate_strict(args),
            "subroutine_registry": subroutine_registry,
        }
        record_snapshots = getattr(args, "record_snapshots", None)
        if record_snapshots is not None:
            simulate_kwargs["record_snapshots"] = record_snapshots
        wt.simulate(**simulate_kwargs)
    except Exception as exc:
        report = getattr(wt, "simulation_report", None)
        if args.as_json and report is not None:
            print(json.dumps(report.to_dict(), indent=2))
        if report is not None and report.failure is not None:
            print(
                f"Simulation failed [{report.failure.category}]: "
                f"{report.failure.message}",
                file=sys.stderr,
            )
        else:
            print(f"Simulation failed: {exc}", file=sys.stderr)
        return 1
    _print_simulation_result(wt, args)
    return 0


def _print_simulation_result(wt, args) -> None:
    if args.as_json:
        report = wt.simulation_report
        out = report.to_dict() if report is not None else {}
        out["snapshots"] = [
            {
                "step_index": s.step_index,
                "step_type": type(s.step).__name__,
                "labware": [lw.label for stack in s.slot_map.values() for lw in stack],
                "mca_adapter": s.mca_adapter_label,
                "mca_tip_box": s.mca_tip_box_label,
                "mca_tip_volume_total_ul": sum(t.volume_ul for t in s.mca_tips),
                "liha_tip_volume_total_ul": sum(
                    t.volume_ul for t in s.liha_tips if t is not None
                ),
            }
            for s in wt.snapshots
        ]
        print(json.dumps(out, indent=2))
        return
    for s in wt.snapshots:
        print(f"  step {s.step_index:3d} {type(s.step).__name__:24s}"
              f"  labware={sum(len(st) for st in s.slot_map.values()):2d}"
              f"  tips={len(s.mca_tips):3d}"
              f"  tip_vol={sum(t.volume_ul for t in s.mca_tips):.1f} µL")
    if args.coverage and wt.simulation_report is not None:
        report = wt.simulation_report
        print(
            "\nCoverage:"
            f"\n  executed: {report.total_executed_steps}"
            f"\n  fully simulated: {report.fully_simulated_steps}"
            f"\n  validation-only: {report.validation_only_steps}"
            f"\n  opaque/no-op: {report.opaque_noop_steps}"
            f"\n  raw XML / GenericStep: {report.raw_xml_generic_steps}"
            f"\n  modeled coverage: {report.modeled_coverage:.3f}"
        )
        if report.unsupported_command_ids:
            unsupported = ", ".join(
                f"{name}={count}" for name, count in report.unsupported_command_ids.items()
            )
            print(f"  unsupported: {unsupported}")
        for warning in report.warnings:
            print(f"  warning: {warning}")


def _cmd_decompile(args) -> int:
    from .decompiler import emit_python, parse_xscr
    from .ir.schema import GenericStep

    proto = parse_xscr(args.input)
    output = args.output or args.input.with_suffix(".py")
    src = emit_python(proto, source_xscr=str(args.input))
    output.write_text(src, encoding="utf-8")

    def _walk(steps):
        for step in steps:
            yield step
            nested = getattr(step, "steps", None)
            if nested:
                yield from _walk(nested)
            for branch in ("then_steps", "else_steps"):
                child_steps = getattr(step, branch, None)
                if child_steps:
                    yield from _walk(child_steps)

    all_steps = [s for g in proto.groups for s in _walk(g.steps)]
    n_steps = len(all_steps)
    generic_steps = [s for s in all_steps if isinstance(s, GenericStep)]
    n_generic = len(generic_steps)

    print(f"Decompiled {args.input} -> {output}")
    print(f"  groups: {len(proto.groups)}, steps: {n_steps}")
    if n_generic:
        names = sorted({s.name for s in generic_steps})
        print(f"  unrecognised steps: {n_generic} ({', '.join(names)})")
        if args.strict:
            return 1
    if args.simulate:
        wt = _load_protocol(output)
        return _simulate_worktable(wt, args)
    return 0


def _cmd_corpus_report(args) -> int:
    from .decompiler import run_corpus_report

    try:
        payload = run_corpus_report(
            args.paths,
            output_dir=args.output_dir,
            include_ready_to_import=args.include_ready_to_import,
            ready_to_import_root=args.ready_to_import_root,
            top_n=args.top_n,
            strict=_simulate_strict(args),
            fail_on_opaque=args.fail_on_opaque,
            subroutine_dirs=args.subroutine_dir,
            subroutine_xscr=args.subroutine_xscr,
        )
    except ValueError as exc:
        print(f"Corpus report failed: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Corpus report written to {payload['report_path']}")
        print(f"  protocols: {len(payload['xscr_paths'])}")
        if payload["unsupported_command_ids"]:
            top = ", ".join(
                f"{name}={count}"
                for name, count in sorted(
                    payload["unsupported_command_ids"].items(),
                    key=lambda item: (-item[1], item[0]),
                )[:5]
            )
            print(f"  top unsupported: {top}")
        if payload["generic_step_types"]:
            top_generic = ", ".join(
                f"{name}={count}"
                for name, count in sorted(
                    payload["generic_step_types"].items(),
                    key=lambda item: (-item[1], item[0]),
                )[:5]
            )
            print(f"  top generic: {top_generic}")
    return 0


def _simulate_strict(args) -> bool:
    if getattr(args, "strict", False):
        return True
    return bool(getattr(args, "simulate_strict", False))


def _build_subroutine_registry(
    *,
    subroutine_dirs: list[Path] | None,
    subroutine_xscr: list[Path] | None,
):
    from .subroutines import build_subroutine_registry

    return build_subroutine_registry(
        subroutine_dirs=subroutine_dirs,
        subroutine_xscr=subroutine_xscr,
    )


def _load_protocol(input_path: Path):
    """Load a `.py` protocol script and return its `Worktable`.

    The script is expected to define a top-level function `build_worktable()`
    that returns a `Worktable`, OR to leave a module-level `wt: Worktable`.
    """
    spec = importlib.util.spec_from_file_location(input_path.stem, input_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load {input_path}")
    module = importlib.util.module_from_spec(spec)
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = original

    if hasattr(module, "build_worktable"):
        return module.build_worktable()
    if hasattr(module, "wt"):
        return module.wt
    raise ValueError(
        f"{input_path}: expected `build_worktable()` or top-level `wt`"
    )


# ── catalog subcommands ────────────────────────────────────────────


def _cmd_catalog_refresh(args) -> int:
    from .catalog.indexer import build_index
    counts = build_index(
        install_path=args.install,
        db_path=args.db,
        include_all_connectors=getattr(args, "all_connectors", False) or None,
    )
    print("Catalog index rebuilt:")
    for k, v in counts.items():
        print(f"  {k:14s} {v}")
    return 0


def _cmd_catalog_info(args) -> int:
    from .catalog.catalog import install_info, category_counts, index_exists
    if not index_exists():
        print("Catalog index is empty. Run `fluentcoder catalog refresh`.")
        return 1
    info = install_info() or {}
    print("Install path :", info.get("install_path"))
    print("Built at     :", info.get("built_at"))
    print("Fingerprint  :", info.get("fingerprint"))
    print("Component categories:")
    for cat, n in category_counts().items():
        print(f"  {cat:14s} {n}")
    return 0


def _cmd_catalog_find(args) -> int:
    from .catalog.catalog import find_components
    rows = find_components(args.pattern)
    if args.category:
        rows = [r for r in rows if r.category == args.category]
    if not rows:
        print(f"No components match {args.pattern!r}.")
        return 1
    for r in rows:
        print(f"  [{r.category:13s}] {r.name}")
    print(f"\n{len(rows)} match(es).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
