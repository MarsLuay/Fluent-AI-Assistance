"""Build fluentcoder subprocess CLI arguments for simulate and compile."""

from __future__ import annotations

from pathlib import Path

from .project_context import ProjectLike, subroutine_simulate_cli_args


def add_simulate_cli_flags(parser) -> None:
    """Register simulate-related flags on a protocol-builder argparse parser."""
    parser.add_argument(
        "--subroutine-dir",
        type=Path,
        action="append",
        default=[],
        help="register .xscr subroutines under this directory for inline simulation",
    )
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


def add_compile_cli_flags(parser) -> None:
    """Register compile-related flags on a protocol-builder argparse parser."""
    parser.add_argument(
        "--deterministic-compile",
        action="store_true",
        help="pass --deterministic to fluentcoder compile for stable WorkspaceDelta GUIDs",
    )


def append_simulate_cli_args(
    command: list[str | Path],
    *,
    context: ProjectLike | None = None,
    subroutine_dirs: list[Path] | None = None,
    record_snapshots: bool | str | None = None,
) -> list[str | Path]:
    """Extend a fluentcoder simulate command with subroutine and snapshot options."""
    for path in subroutine_dirs or []:
        command.extend(["--subroutine-dir", str(path)])
    command.extend(subroutine_simulate_cli_args(context))
    if record_snapshots is False:
        command.append("--no-snapshots")
    elif record_snapshots is True:
        command.append("--record-snapshots")
    elif record_snapshots == "delta":
        command.append("--delta-snapshots")
    return command


def append_compile_cli_args(
    command: list[str | Path],
    *,
    deterministic: bool = False,
) -> list[str | Path]:
    """Extend a fluentcoder compile command with compile options."""
    if deterministic:
        command.append("--deterministic")
    return command


def build_simulate_command(
    protocol: Path,
    *,
    as_json: bool = True,
    context: ProjectLike | None = None,
    subroutine_dirs: list[Path] | None = None,
    record_snapshots: bool | str | None = None,
    fail_on_opaque: bool = False,
    min_coverage: float | None = None,
    strict: bool = False,
) -> list[str | Path]:
    """Build a fluentcoder simulate subprocess argument list."""
    command: list[str | Path] = ["simulate", protocol]
    if as_json:
        command.append("--json")
    if fail_on_opaque:
        command.append("--fail-on-opaque")
    if min_coverage is not None:
        command.extend(["--min-coverage", str(min_coverage)])
    if strict:
        command.append("--strict")
    append_simulate_cli_args(
        command,
        context=context,
        subroutine_dirs=subroutine_dirs,
        record_snapshots=record_snapshots,
    )
    return command


def build_compile_command(
    protocol: Path,
    output: Path,
    *,
    deterministic: bool = False,
) -> list[str | Path]:
    """Build a fluentcoder compile subprocess argument list."""
    command: list[str | Path] = ["compile", protocol, "-o", output]
    append_compile_cli_args(command, deterministic=deterministic)
    return command
