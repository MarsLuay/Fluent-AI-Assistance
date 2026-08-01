"""CLI for local Tecan file reading."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from collections import Counter

from .archive import inspect_archive
from .common import TECAN_EXTENSIONS, extension_counts
from .compare import compare_archives
from .gwl import inspect_gwl
from .pattern_library import (
    list_script_pattern_types,
    mine_script_patterns,
    search_script_patterns,
    summarize_script_patterns,
)
from .project_index import (
    DEFAULT_INDEX_PATH,
    build_project_index,
    search_project_index,
    summarize_project_index,
)
from .render import render_json, render_markdown
from .script import inspect_xscr
from .xmlobj import inspect_xml_object


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-reader")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="inspect a Tecan file, archive, or directory")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--format", choices=["markdown", "json"], default="markdown")
    inspect.add_argument("--output", "-o", type=Path, default=None)
    inspect.add_argument("--script-limit", type=int, default=50)
    inspect.add_argument("--object-limit", type=int, default=200)
    inspect.set_defaults(func=_cmd_inspect)

    compare = sub.add_parser("compare", help="compare two .zeia archives")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    compare.add_argument("--format", choices=["markdown", "json"], default="markdown")
    compare.add_argument("--output", "-o", type=Path, default=None)
    compare.add_argument("--script-limit", type=int, default=None)
    compare.set_defaults(func=_cmd_compare)

    index = sub.add_parser("index", help="build and query a multi-ZEIA project index")
    index_sub = index.add_subparsers(dest="index_command", required=True)

    index_build = index_sub.add_parser("build", help="index one or more .zeia files or directories")
    index_build.add_argument("paths", nargs="+", type=Path)
    index_build.add_argument("--db", "-d", type=Path, default=DEFAULT_INDEX_PATH)
    index_build.add_argument("--force", action="store_true", help="replace an existing index database")
    index_build.add_argument("--format", choices=["markdown", "json"], default="markdown")
    index_build.add_argument("--output", "-o", type=Path, default=None)
    index_build.add_argument("--script-limit", type=int, default=None)
    index_build.add_argument("--object-limit", type=int, default=None)
    index_build.set_defaults(func=_cmd_index_build)

    index_search = index_sub.add_parser("search", help="search an existing project index")
    index_search.add_argument("query")
    index_search.add_argument("--db", "-d", type=Path, default=DEFAULT_INDEX_PATH)
    index_search.add_argument("--kind", default=None, help="filter by script, command, labware, worktable, etc.")
    index_search.add_argument("--limit", type=int, default=25)
    index_search.add_argument("--format", choices=["markdown", "json"], default="markdown")
    index_search.add_argument("--output", "-o", type=Path, default=None)
    index_search.set_defaults(func=_cmd_index_search)

    index_summary = index_sub.add_parser("summary", help="summarize an existing project index")
    index_summary.add_argument("--db", "-d", type=Path, default=DEFAULT_INDEX_PATH)
    index_summary.add_argument("--format", choices=["markdown", "json"], default="markdown")
    index_summary.add_argument("--output", "-o", type=Path, default=None)
    index_summary.set_defaults(func=_cmd_index_summary)

    patterns = sub.add_parser("patterns", help="mine and query reusable script patterns")
    patterns_sub = patterns.add_subparsers(dest="patterns_command", required=True)

    patterns_mine = patterns_sub.add_parser("mine", help="mine patterns from an existing project index")
    patterns_mine.add_argument("--db", "-d", type=Path, default=DEFAULT_INDEX_PATH)
    patterns_mine.add_argument("--append", action="store_true", help="keep existing mined patterns")
    patterns_mine.add_argument("--context-before", type=int, default=1)
    patterns_mine.add_argument("--context-after", type=int, default=1)
    patterns_mine.add_argument("--format", choices=["markdown", "json"], default="markdown")
    patterns_mine.add_argument("--output", "-o", type=Path, default=None)
    patterns_mine.set_defaults(func=_cmd_patterns_mine)

    patterns_search = patterns_sub.add_parser("search", help="search mined reusable patterns")
    patterns_search.add_argument("query", nargs="?", default="")
    patterns_search.add_argument("--db", "-d", type=Path, default=DEFAULT_INDEX_PATH)
    patterns_search.add_argument("--type", dest="pattern_type", default=None)
    patterns_search.add_argument("--source-script", default=None)
    patterns_search.add_argument("--limit", type=int, default=25)
    patterns_search.add_argument("--format", choices=["markdown", "json"], default="markdown")
    patterns_search.add_argument("--output", "-o", type=Path, default=None)
    patterns_search.set_defaults(func=_cmd_patterns_search)

    patterns_summary = patterns_sub.add_parser("summary", help="summarize mined reusable patterns")
    patterns_summary.add_argument("--db", "-d", type=Path, default=DEFAULT_INDEX_PATH)
    patterns_summary.add_argument("--format", choices=["markdown", "json"], default="markdown")
    patterns_summary.add_argument("--output", "-o", type=Path, default=None)
    patterns_summary.set_defaults(func=_cmd_patterns_summary)

    patterns_types = patterns_sub.add_parser("types", help="list supported script-pattern types")
    patterns_types.add_argument("--format", choices=["markdown", "json"], default="markdown")
    patterns_types.add_argument("--output", "-o", type=Path, default=None)
    patterns_types.set_defaults(func=_cmd_patterns_types)

    args = parser.parse_args(argv)
    return args.func(args)


def _cmd_inspect(args: argparse.Namespace) -> int:
    report = inspect_path(args.path, script_limit=args.script_limit, object_limit=args.object_limit)
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    report = compare_archives(args.left, args.right, script_limit=args.script_limit)
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def _cmd_index_build(args: argparse.Namespace) -> int:
    report = build_project_index(
        args.paths,
        args.db,
        force=args.force,
        script_limit=args.script_limit,
        object_limit=args.object_limit,
    )
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def _cmd_index_search(args: argparse.Namespace) -> int:
    report = search_project_index(args.db, args.query, kind=args.kind, limit=args.limit)
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def _cmd_index_summary(args: argparse.Namespace) -> int:
    report = summarize_project_index(args.db)
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def _cmd_patterns_mine(args: argparse.Namespace) -> int:
    report = mine_script_patterns(
        args.db,
        replace=not args.append,
        context_before=args.context_before,
        context_after=args.context_after,
    )
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def _cmd_patterns_search(args: argparse.Namespace) -> int:
    report = search_script_patterns(
        args.db,
        args.query,
        pattern_type=args.pattern_type,
        source_script=args.source_script,
        limit=args.limit,
    )
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def _cmd_patterns_summary(args: argparse.Namespace) -> int:
    report = summarize_script_patterns(args.db)
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def _cmd_patterns_types(args: argparse.Namespace) -> int:
    report = list_script_pattern_types()
    _write_report(report, fmt=args.format, output=args.output)
    return 0


def inspect_path(path: Path, *, script_limit: int | None, object_limit: int | None) -> dict[str, Any]:
    if path.is_dir():
        return _inspect_directory(path, script_limit=script_limit, object_limit=object_limit)
    suffix = path.suffix.lower()
    if suffix == ".zeia":
        return inspect_archive(path, script_limit=script_limit, object_limit=object_limit)
    if suffix == ".xscr":
        return inspect_xscr(path)
    if suffix == ".gwl":
        return inspect_gwl(path)
    return inspect_xml_object(path)


def _inspect_directory(path: Path, *, script_limit: int | None, object_limit: int | None) -> dict[str, Any]:
    files = [
        item for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in TECAN_EXTENSIONS
    ]
    items = []
    script_names = []
    family_counts: Counter[str] = Counter()
    command_counts: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    for file_path in files:
        try:
            suffix = file_path.suffix.lower()
            if suffix == ".xscr":
                item = inspect_xscr(file_path)
                items.append(item)
                script_names.append(item.get("object_name") or str(file_path))
                family_counts.update(item.get("family_counts", {}))
                command_counts.update(item.get("command_counts", {}))
                warnings.update(item.get("warnings", []))
            elif suffix == ".gwl":
                items.append(inspect_gwl(file_path))
            elif suffix == ".zeia":
                item = inspect_archive(file_path, script_limit=script_limit, object_limit=object_limit)
                items.append(item)
                script_names.extend(item.get("script_names", []))
                family_counts.update(item.get("family_counts", {}))
                command_counts.update(item.get("command_counts", {}))
                warnings.update(item.get("warning_counts", {}))
            else:
                items.append(inspect_xml_object(file_path))
        except Exception as exc:
            items.append({"kind": "error", "source": str(file_path), "error": str(exc)})
    return {
        "kind": "directory",
        "source": str(path),
        "file_count": len(files),
        "extension_counts": extension_counts(str(item) for item in files),
        "script_names": script_names,
        "family_counts": dict(family_counts.most_common()),
        "command_counts": dict(command_counts.most_common(40)),
        "warning_counts": dict(warnings.most_common()),
        "items": items,
    }


def _write_report(report: dict[str, Any], *, fmt: str, output: Path | None) -> None:
    text = render_json(report) if fmt == "json" else render_markdown(report)
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    raise SystemExit(main())
