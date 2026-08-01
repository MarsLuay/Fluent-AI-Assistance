"""Subprocess helpers for running fluentcoder without using API keys."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import PROJECT_DIR, fluentcoder_python, fluentcoder_root


class PipelineError(RuntimeError):
    """Raised when the local fluentcoder environment cannot be used."""


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def command_line(self) -> str:
        if os.name == "nt":
            return subprocess.list2cmdline(self.command)
        return " ".join(shlex.quote(part) for part in self.command)


@dataclass(frozen=True)
class LogWatchResult:
    result: CommandResult
    log_path: Path
    script_filter: str
    captured_lines: tuple[str, ...]
    notes: tuple[str, ...]


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _validate_environment() -> tuple[Path, Path]:
    root = fluentcoder_root()
    python = fluentcoder_python(root)
    if not root.exists():
        raise PipelineError(f"fluentcoder root not found: {root}")
    if not python.exists():
        raise PipelineError(
            f"shared repo venv python not found: {python}. "
            "Run `python3 -m fluent_pipeline.cli doctor --install-missing "
            "--report ready-to-import/<project>/temp_files/doctor.md` first."
        )
    return root, python


def run_python(arguments: Iterable[str | Path], *, timeout: int = 120) -> CommandResult:
    root, python = _validate_environment()
    command = (str(python), *(str(arg) for arg in arguments))
    completed = subprocess.run(
        command,
        cwd=root,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return CommandResult(
        command=tuple(command),
        cwd=root,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_fluentcoder(
    arguments: Iterable[str | Path],
    *,
    catalog_db: Path | None = None,
    timeout: int = 600,
) -> CommandResult:
    command, cwd = _fluentcoder_command(arguments, catalog_db=catalog_db)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return CommandResult(
        command=tuple(command),
        cwd=cwd,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_fluentcoder_with_log_watch(
    arguments: Iterable[str | Path],
    *,
    catalog_db: Path | None = None,
    log_path: Path,
    script_filter: str | None = None,
    timeout: int = 600,
) -> LogWatchResult:
    """Run fluentcoder while capturing lines appended to a FluentControl/log file."""
    command, cwd = _fluentcoder_command(arguments, catalog_db=catalog_db)
    log_path = log_path.expanduser().resolve()
    captured: list[str] = []
    notes: list[str] = []
    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_watch_log_file,
        args=(log_path, script_filter or "", stop_event, captured, notes),
        daemon=True,
    )
    watcher.start()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        stop_event.set()
        watcher.join(timeout=2)
    result = CommandResult(
        command=tuple(command),
        cwd=cwd,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    return LogWatchResult(
        result=result,
        log_path=log_path,
        script_filter=script_filter or "",
        captured_lines=tuple(captured),
        notes=tuple(notes),
    )


def _fluentcoder_command(
    arguments: Iterable[str | Path],
    *,
    catalog_db: Path | None = None,
) -> tuple[tuple[str, ...], Path]:
    root, python = _validate_environment()
    if catalog_db is not None:
        command = (
            str(python),
            "-m",
            "fluent_pipeline.fluentcoder_project_runner",
            "--catalog-db",
            str(catalog_db),
            "--",
            *(str(arg) for arg in arguments),
        )
        cwd = PROJECT_DIR
    else:
        command = (str(python), "-m", "fluentcoder.cli", *(str(arg) for arg in arguments))
        cwd = root
    return tuple(command), cwd


def _subprocess_env() -> dict[str, str]:
    return dict(os.environ)


def _watch_log_file(
    log_path: Path,
    script_filter: str,
    stop_event: threading.Event,
    captured: list[str],
    notes: list[str],
) -> None:
    position = 0
    if log_path.exists():
        try:
            position = log_path.stat().st_size
        except OSError as exc:
            notes.append(f"could not stat log file {log_path}: {exc}")
            return
    else:
        notes.append(f"log file did not exist at start: {log_path}")
    pending = ""
    needle = script_filter.casefold().strip()
    while not stop_event.is_set():
        position, pending = _read_log_delta(log_path, position, pending, needle, captured, notes)
        time.sleep(0.1)
    _read_log_delta(log_path, position, pending, needle, captured, notes, flush_pending=True)


def _read_log_delta(
    log_path: Path,
    position: int,
    pending: str,
    needle: str,
    captured: list[str],
    notes: list[str],
    *,
    flush_pending: bool = False,
) -> tuple[int, str]:
    if not log_path.exists():
        return position, pending
    try:
        current_size = log_path.stat().st_size
        if current_size < position:
            position = 0
            pending = ""
            notes.append(f"log file was truncated or rotated: {log_path}")
        if current_size == position and not flush_pending:
            return position, pending
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(position)
            chunk = handle.read()
            position = handle.tell()
    except OSError as exc:
        notes.append(f"could not read log file {log_path}: {exc}")
        return position, pending
    if not chunk and not flush_pending:
        return position, pending
    text = pending + chunk
    lines = text.splitlines(keepends=True)
    pending = ""
    if lines and not lines[-1].endswith(("\n", "\r")) and not flush_pending:
        pending = lines.pop()
    for line in lines:
        clean = line.rstrip("\r\n")
        if not clean:
            continue
        if needle and needle not in clean.casefold():
            continue
        captured.append(clean)
    if flush_pending and pending:
        clean = pending.strip()
        if clean and (not needle or needle in clean.casefold()):
            captured.append(clean)
        pending = ""
    return position, pending


def parse_json_stdout(result: CommandResult) -> dict[str, Any] | None:
    text = result.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def write_json(path: Path, data: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
