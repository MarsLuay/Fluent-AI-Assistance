"""Atomic persistence for project-context metadata."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator


class ProjectStore:
    """Persist manifests, reports, and active-context selection atomically."""

    def __init__(self, *, active_context_file: Path) -> None:
        self.active_context_file = Path(active_context_file)

    def write_json(self, path: Path, payload: Any) -> None:
        self.write_text(path, json.dumps(payload, indent=2, sort_keys=False))

    def read_json(self, path: Path) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def write_text(self, path: Path, value: str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _exclusive_lock(path):
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(value.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, path)
                _fsync_directory(path.parent)
            finally:
                if temporary_path.exists():
                    temporary_path.unlink()

    def set_active_context(self, name: str) -> None:
        self.write_text(self.active_context_file, name)

    def clear_active_context(self) -> None:
        with _exclusive_lock(self.active_context_file):
            try:
                self.active_context_file.unlink()
            except FileNotFoundError:
                pass
            else:
                _fsync_directory(self.active_context_file.parent)

    def active_context_name(self) -> str | None:
        try:
            value = self.active_context_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return value or None


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Serialize writes to one project artifact across local processes."""
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle)


def _lock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
