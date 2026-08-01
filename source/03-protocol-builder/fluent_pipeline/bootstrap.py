"""Canonical workspace bootstrap and installation helpers."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import PROJECT_DIR, SHARED_TEMP_DIR, TECAN_AI_DIR, fluentcoder_python, fluentcoder_root
from .runner import PipelineError


DESKTOP_AUTOMATION_VENDOR_DIR = PROJECT_DIR / "vendor" / "desktop-automation"
DESKTOP_AUTOMATION_REQUIREMENTS_FILE = DESKTOP_AUTOMATION_VENDOR_DIR / "requirements.txt"
DESKTOP_AUTOMATION_CONSTRAINTS_FILE = DESKTOP_AUTOMATION_VENDOR_DIR / "constraints.txt"
DESKTOP_AUTOMATION_WHEELHOUSE = DESKTOP_AUTOMATION_VENDOR_DIR / "wheels"
DESKTOP_AUTOMATION_WHEELHOUSE_MARKER = DESKTOP_AUTOMATION_WHEELHOUSE / ".complete"

DESKTOP_AUTOMATION_REQUIREMENTS = (
    "pywinauto",
    "comtypes",
    "psutil",
    "pyautogui",
    "mss",
    "pillow",
    "pyperclip",
)

# Keep the desktop automation stack pinned so the wheelhouse and install flow do
# not drift independently of one another.
DESKTOP_AUTOMATION_CONSTRAINTS = (
    "pywinauto==0.6.9",
    "comtypes==1.4.6",
    "psutil==7.2.2",
    "pyautogui==0.9.54",
    "mss==10.2.0",
    "Pillow==12.3.0",
    "pyperclip==1.11.0",
)

SETUP_COMMAND_TIMEOUT_SECONDS = 15 * 60
SETUP_COMMAND_HEARTBEAT_SECONDS = 30


@dataclass(frozen=True)
class WorkspacePackage:
    """One local package in the canonical install graph."""

    distribution: str
    path: Path
    dependencies: tuple[str, ...] = ()
    extras: tuple[str, ...] = ()

    def editable_requirement(self) -> str:
        return _requirement_spec(self.path, self.extras)

    def wheel_requirement(self) -> str:
        return _requirement_spec(self.distribution, self.extras)


def workspace_packages() -> tuple[WorkspacePackage, ...]:
    """Return the canonical local install graph."""

    return (
        WorkspacePackage("tecan-common", TECAN_AI_DIR / "source" / "00-shared"),
        WorkspacePackage(
            "tecan-worklist-builder",
            TECAN_AI_DIR / "source" / "02-worklist-builder",
            dependencies=("tecan-common",),
        ),
        WorkspacePackage(
            "tecan-project-reader",
            TECAN_AI_DIR / "source" / "01-project-reader",
            dependencies=("tecan-common",),
        ),
        WorkspacePackage("fluentcoder", fluentcoder_root(), extras=("dev",)),
        WorkspacePackage(
            "tecan-protocol-builder",
            PROJECT_DIR,
            dependencies=("tecan-common", "fluentcoder"),
            extras=("mcp", "dev"),
        ),
        WorkspacePackage(
            "tecan-tools",
            TECAN_AI_DIR / "source" / "tools",
            dependencies=("fluentcoder", "tecan-protocol-builder"),
        ),
    )


def bootstrap_workspace(
    python: Path | None = None,
    *,
    include_desktop_automation: bool = True,
    cwd: Path = PROJECT_DIR,
) -> None:
    """Bootstrap the shared repo venv using the canonical install graph."""

    python = python or fluentcoder_python()
    upgrade_pip(python, cwd=cwd)
    install_editable_workspace(python, cwd=cwd)
    if include_desktop_automation:
        install_desktop_automation_dependencies(python, cwd=cwd)
    run_pip_check(python, cwd=cwd)


def upgrade_pip(python: Path, *, cwd: Path = PROJECT_DIR) -> None:
    _run_setup_command([str(python), "-m", "pip", "install", "--upgrade", "pip"], cwd=cwd)


def install_editable_workspace(python: Path, *, cwd: Path = PROJECT_DIR) -> None:
    packages = workspace_packages()
    _validate_workspace_package_order(packages)
    for package in packages:
        _run_setup_command(
            [str(python), "-m", "pip", "install", "-e", package.editable_requirement()],
            cwd=cwd,
        )


def build_workspace_wheelhouse(python: Path, wheelhouse: Path, *, cwd: Path = PROJECT_DIR) -> None:
    """Build workspace wheels without leaving setuptools ``build/`` trees in sources.

    Sources are staged into OS temp for PEP 517 builds. Each package also sets
    ``setup.cfg`` ``[build] build-base`` under
    ``ready-to-import/_shared/temp_files/build/setuptools/<package>/`` so
    in-place ``python -m build`` / setuptools runs do not recreate
    ``source/*/build/``.
    """
    packages = workspace_packages()
    _validate_workspace_package_order(packages)
    wheelhouse.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tecan-wheel-sources-") as raw_stage_root:
        stage_root = Path(raw_stage_root)
        for index, package in enumerate(packages, start=1):
            staged_package = _stage_wheel_source(package.path, stage_root, index=index)
            _run_setup_command(
                [
                    str(python),
                    "-m",
                    "pip",
                    "wheel",
                    "--wheel-dir",
                    str(wheelhouse),
                    "--find-links",
                    str(wheelhouse),
                    _requirement_spec(staged_package, package.extras),
                ],
                cwd=cwd,
            )


def _stage_wheel_source(source: Path, stage_root: Path, *, index: int) -> Path:
    """Copy one local package to disposable OS temp space for PEP 517 builds."""
    destination = stage_root / f"{index:02d}-{source.name}"
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            "build",
            "dist",
            "*.egg-info",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".venv",
        ),
    )
    _rewrite_staged_setuptools_build_base(destination, package_name=source.name)
    return destination


def _rewrite_staged_setuptools_build_base(staged_package: Path, *, package_name: str) -> None:
    """Point staged setup.cfg build-base at an absolute writable repo temp path.

    Relative ``build-base`` entries are correct from the live source tree but break
    after ``_stage_wheel_source`` copies packages into OS temp (paths no longer
    resolve into ``ready-to-import/.../setuptools``).
    """
    setup_cfg = staged_package / "setup.cfg"
    if not setup_cfg.is_file():
        return
    build_base = (SHARED_TEMP_DIR / "build" / "setuptools" / package_name).resolve()
    build_base.mkdir(parents=True, exist_ok=True)
    text = setup_cfg.read_text(encoding="utf-8")
    rewritten = re.sub(
        r"(?m)^(build-base\s*=\s*).*$",
        rf"\g<1>{build_base.as_posix()}",
        text,
        count=1,
    )
    if rewritten != text:
        setup_cfg.write_text(rewritten, encoding="utf-8")


def install_workspace_from_wheelhouse(
    python: Path,
    wheelhouse: Path,
    *,
    cwd: Path = PROJECT_DIR,
) -> None:
    packages = workspace_packages()
    _validate_workspace_package_order(packages)
    if not wheelhouse.exists():
        raise PipelineError(f"workspace wheelhouse does not exist: {wheelhouse}")
    for package in packages:
        _run_setup_command(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                package.wheel_requirement(),
            ],
            cwd=cwd,
        )


def install_desktop_automation_dependencies(python: Path, *, cwd: Path = PROJECT_DIR) -> None:
    ensure_desktop_automation_manifests()
    DESKTOP_AUTOMATION_WHEELHOUSE.mkdir(parents=True, exist_ok=True)
    manifest_text = _desktop_automation_manifest_text()

    if _desktop_automation_wheelhouse_is_current(manifest_text):
        _run_setup_command(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(DESKTOP_AUTOMATION_WHEELHOUSE),
                "-c",
                str(DESKTOP_AUTOMATION_CONSTRAINTS_FILE),
                "-r",
                str(DESKTOP_AUTOMATION_REQUIREMENTS_FILE),
            ],
            cwd=cwd,
        )
        return

    _clear_desktop_automation_wheelhouse()
    build_wheels = [
        str(python),
        "-m",
        "pip",
        "wheel",
        "--wheel-dir",
        str(DESKTOP_AUTOMATION_WHEELHOUSE),
        "--find-links",
        str(DESKTOP_AUTOMATION_WHEELHOUSE),
        "-c",
        str(DESKTOP_AUTOMATION_CONSTRAINTS_FILE),
        "-r",
        str(DESKTOP_AUTOMATION_REQUIREMENTS_FILE),
    ]
    build_succeeded = False
    try:
        _run_setup_command(build_wheels, cwd=cwd)
        DESKTOP_AUTOMATION_WHEELHOUSE_MARKER.write_text(manifest_text, encoding="utf-8")
        build_succeeded = True
    except PipelineError as exc:
        print(
            "Warning: could not populate desktop automation wheelhouse; "
            f"trying online install for this run only. {exc}",
            file=sys.stderr,
        )

    if build_succeeded:
        _run_setup_command(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(DESKTOP_AUTOMATION_WHEELHOUSE),
                "-c",
                str(DESKTOP_AUTOMATION_CONSTRAINTS_FILE),
                "-r",
                str(DESKTOP_AUTOMATION_REQUIREMENTS_FILE),
            ],
            cwd=cwd,
        )
        return

    _run_setup_command(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-c",
            str(DESKTOP_AUTOMATION_CONSTRAINTS_FILE),
            "-r",
            str(DESKTOP_AUTOMATION_REQUIREMENTS_FILE),
        ],
        cwd=cwd,
    )


def run_pip_check(python: Path, *, cwd: Path = PROJECT_DIR) -> None:
    _run_setup_command([str(python), "-m", "pip", "check"], cwd=cwd)


def ensure_desktop_automation_manifests() -> None:
    DESKTOP_AUTOMATION_VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    _write_text_if_changed(DESKTOP_AUTOMATION_REQUIREMENTS_FILE, _requirements_text())
    _write_text_if_changed(DESKTOP_AUTOMATION_CONSTRAINTS_FILE, _constraints_text())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the local Fluent AI-Assistance workspace.")
    parser.add_argument(
        "--skip-desktop-automation",
        action="store_true",
        help="install only the editable workspace packages before checking",
    )
    args = parser.parse_args(argv)
    try:
        bootstrap_workspace(include_desktop_automation=not args.skip_desktop_automation)
    except PipelineError as exc:
        print(f"Bootstrap error: {exc}", file=sys.stderr)
        return 2
    return 0


def _requirements_text() -> str:
    return "\n".join(DESKTOP_AUTOMATION_REQUIREMENTS) + "\n"


def _constraints_text() -> str:
    return "\n".join(DESKTOP_AUTOMATION_CONSTRAINTS) + "\n"


def _desktop_automation_manifest_text() -> str:
    return "\n".join(
        (
            "[requirements]",
            _requirements_text().rstrip("\n"),
            "",
            "[constraints]",
            _constraints_text().rstrip("\n"),
            "",
        )
    )


def _desktop_automation_wheelhouse_has_wheels() -> bool:
    return any(DESKTOP_AUTOMATION_WHEELHOUSE.glob("*.whl"))


def _desktop_automation_wheelhouse_is_current(manifest_text: str) -> bool:
    if not DESKTOP_AUTOMATION_WHEELHOUSE_MARKER.exists() or not _desktop_automation_wheelhouse_has_wheels():
        return False
    try:
        return DESKTOP_AUTOMATION_WHEELHOUSE_MARKER.read_text(encoding="utf-8") == manifest_text
    except OSError:
        return False


def _clear_desktop_automation_wheelhouse() -> None:
    for wheel in DESKTOP_AUTOMATION_WHEELHOUSE.glob("*.whl"):
        wheel.unlink(missing_ok=True)
    DESKTOP_AUTOMATION_WHEELHOUSE_MARKER.unlink(missing_ok=True)


def _validate_workspace_package_order(packages: Sequence[WorkspacePackage]) -> None:
    seen: set[str] = set()
    for package in packages:
        missing = [dependency for dependency in package.dependencies if dependency not in seen]
        if missing:
            deps = ", ".join(missing)
            raise PipelineError(f"workspace install graph is out of order for {package.distribution}: missing {deps}")
        seen.add(package.distribution)


def _write_text_if_changed(path: Path, text: str) -> None:
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except OSError:
            pass
    path.write_text(text, encoding="utf-8")


def _requirement_spec(base: Path | str, extras: tuple[str, ...]) -> str:
    spec = str(base)
    if extras:
        spec = f"{spec}[{','.join(extras)}]"
    return spec


def _run_setup_command(command: list[str], *, cwd: Path) -> None:
    command_line = (
        subprocess.list2cmdline(command) if os.name == "nt" else " ".join(shlex.quote(part) for part in command)
    )
    print(f"$ {command_line}", flush=True)
    process = subprocess.Popen(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started_at = time.monotonic()
    while True:
        elapsed = time.monotonic() - started_at
        remaining = SETUP_COMMAND_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            if stdout.strip():
                print(stdout.rstrip(), flush=True)
            if stderr.strip():
                print(stderr.rstrip(), file=sys.stderr, flush=True)
            raise PipelineError(
                f"setup command timed out after {SETUP_COMMAND_TIMEOUT_SECONDS}s: {command[0]}"
            )
        try:
            stdout, stderr = process.communicate(timeout=min(SETUP_COMMAND_HEARTBEAT_SECONDS, remaining))
            break
        except subprocess.TimeoutExpired:
            print(f"  ... setup command still running after {int(time.monotonic() - started_at)}s", flush=True)

    if stdout.strip():
        print(stdout.rstrip(), flush=True)
    if stderr.strip():
        print(stderr.rstrip(), file=sys.stderr, flush=True)
    if process.returncode:
        raise PipelineError(f"setup command failed with exit code {process.returncode}: {command[0]}")


if __name__ == "__main__":
    raise SystemExit(main())
