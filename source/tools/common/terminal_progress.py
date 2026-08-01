"""Terminal progress helpers for Fluent AI-Assistance launchers."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TextIO


def enable_ansi(stream: TextIO | None = None) -> None:
    target = stream or sys.stdout
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 4)
    except Exception:
        return
    if hasattr(target, "reconfigure"):
        try:
            target.reconfigure(encoding="utf-8")
        except Exception:
            pass


class ProgressBar:
    def __init__(self, total: int, *, label: str = "", width: int = 28, prefix: str = "  ") -> None:
        self.total = max(total, 1)
        self.current = 0
        self.label = label
        self.width = width
        self.prefix = prefix
        enable_ansi()

    def update(self, n: int = 1, *, label: str | None = None) -> None:
        self.current = min(self.current + n, self.total)
        if label is not None:
            self.label = label
        self._render()

    def set(self, value: int, *, label: str | None = None) -> None:
        self.current = min(max(value, 0), self.total)
        if label is not None:
            self.label = label
        self._render()

    def finish(self, *, label: str | None = None) -> None:
        self.current = self.total
        if label is not None:
            self.label = label
        self._render()
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _render(self) -> None:
        ratio = self.current / self.total
        filled = int(self.width * ratio)
        bar = "#" * filled + "-" * (self.width - filled)
        pct = int(ratio * 100)
        line = f"{self.prefix}[{bar}] {pct:3d}% {self.label}"
        sys.stdout.write(f"\r{line.ljust(96)}")
        sys.stdout.flush()


class Spinner:
    def __init__(self, label: str) -> None:
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        enable_ansi()

    def __enter__(self) -> Spinner:
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        sys.stdout.write("\r" + " " * 96 + "\r")
        sys.stdout.flush()

    def _spin(self) -> None:
        frames = "|/-\\"
        index = 0
        while not self._stop.is_set():
            sys.stdout.write(f"\r  {frames[index % len(frames)]} {self.label}")
            sys.stdout.flush()
            index += 1
            time.sleep(0.12)


class StepFraction:
    def __init__(self, progress: StepProgress, label: str) -> None:
        self._progress = progress
        self.label = label

    def set(self, fraction: float) -> None:
        self._progress.set_fraction(fraction, label=self.label)


class StepProgress:
    """Overall launcher progress across numbered steps with a master progress bar."""

    def __init__(self, steps: list[str], *, title: str, weights: list[int] | None = None, width: int = 32) -> None:
        self.steps = steps
        self.title = title
        self._index = 0
        self._weights = self._normalize_weights(weights)
        self._bounds = self._build_bounds(self._weights)
        self._master: ProgressBar | None = None
        if self._weights is not None:
            self._master = ProgressBar(100, label="Starting", width=width, prefix="")
        enable_ansi()
        print(title)
        print("-" * len(title))
        if self._master is not None:
            print()
            self._master.set(0, label="Starting")

    def begin(self, message: str | None = None) -> None:
        if self._index >= len(self.steps):
            return
        label = message or self.steps[self._index]
        step_no = self._index + 1
        print(f"\n[{step_no}/{len(self.steps)}] {label}")
        sys.stdout.flush()
        if self._master is not None:
            self._master.set(self._bounds[self._index], label=label)
        self._index += 1

    def done(self, detail: str = "done") -> None:
        if self._master is not None:
            boundary = self._bounds[min(self._index, len(self._bounds) - 1)]
            step_label = self.steps[self._index - 1] if self._index > 0 else "Starting"
            self._master.set(boundary, label=step_label)
            sys.stdout.write("\n")
            sys.stdout.flush()
        print(f"  {detail}")
        sys.stdout.flush()

    def detail(self, message: str) -> None:
        if self._master is not None:
            sys.stdout.write("\n")
            sys.stdout.flush()
        print(f"  {message}")
        sys.stdout.flush()

    def finish(self, *, label: str = "Ready") -> None:
        if self._master is None:
            return
        self._master.finish(label=label)

    def set_fraction(self, fraction: float, *, label: str) -> None:
        if self._master is None:
            return
        start = self._bounds[self._index - 1] if self._index > 0 else 0
        end = self._bounds[min(self._index, len(self._bounds) - 1)]
        clamped = min(max(fraction, 0.0), 1.0)
        value = start + int(clamped * max(end - start, 1))
        self._master.set(value, label=label)

    @contextmanager
    def step_work(self, label: str) -> Iterator[StepFraction]:
        worker = StepFraction(self, label)
        try:
            yield worker
            worker.set(1.0)
        finally:
            if self._master is not None:
                sys.stdout.write("\n")
                sys.stdout.flush()

    @staticmethod
    def _normalize_weights(weights: list[int] | None) -> list[int] | None:
        if not weights:
            return None
        total = sum(weights)
        if total <= 0:
            return None
        normalized = [int(round(weight * 100 / total)) for weight in weights]
        drift = 100 - sum(normalized)
        if drift:
            normalized[-1] += drift
        return normalized

    @staticmethod
    def _build_bounds(weights: list[int] | None) -> list[int]:
        if not weights:
            return [0]
        bounds = [0]
        running = 0
        for weight in weights:
            running += weight
            bounds.append(running)
        bounds[-1] = 100
        return bounds


def run_subprocess_with_fraction(
    progress: StepProgress,
    command: list[str],
    *,
    cwd: Path,
    label: str,
    estimate_seconds: float = 90.0,
) -> int:
    with progress.step_work(label) as work:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = time.time()
        while process.poll() is None:
            elapsed = time.time() - started
            work.set(min(elapsed / max(estimate_seconds, 1.0), 0.95))
            time.sleep(0.2)
        work.set(1.0)
        return int(process.returncode or 0)


def wait_with_fraction(
    progress: StepProgress,
    *,
    label: str,
    attempts: int,
    interval: float,
    ready: Callable[[], bool],
) -> bool:
    with progress.step_work(label) as work:
        for attempt in range(1, attempts + 1):
            if ready():
                work.set(1.0)
                return True
            work.set(attempt / max(attempts, 1))
            time.sleep(interval)
        work.set(1.0)
        return ready()
