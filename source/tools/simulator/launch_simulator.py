"""Launch the Tecan Protocol Simulator with terminal startup progress."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from tecan_tools.common.terminal_progress import StepProgress, run_subprocess_with_fraction, wait_with_fraction

SOURCE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOURCE_ROOT.parent if SOURCE_ROOT.name == "source" else SOURCE_ROOT
DEFAULT_PORT = 5173
DEFAULT_HOST = "127.0.0.1"
HOST_PATTERN = re.compile(r"^(?:localhost|[\d.]+|[A-Za-z0-9.-]+)$")


def discover_sample_zeia(repo_root: Path) -> Path | None:
    """Find an optional local ZEIA for simulator sample caching."""
    env_path = os.environ.get("TECAN_SIMULATOR_SAMPLE_ZEIA", "").strip()
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        return candidate if candidate.is_file() else None

    ready_root = repo_root / "ready-to-import"
    if not ready_root.is_dir():
        return None
    found: list[Path] = []
    for bundle in sorted(ready_root.iterdir()):
        if not bundle.is_dir() or bundle.name.startswith("."):
            continue
        for rel in (
            ("source", "original-sources"),
            ("original_sources",),
            ("source", "original_sources"),
        ):
            folder = bundle.joinpath(*rel)
            if not folder.is_dir():
                continue
            found.extend(sorted(folder.glob("*.zeia")))
    return found[0] if found else None


def sample_zeia_cache_manifest(repo_root: Path, zeia_path: Path) -> Path:
    stem = zeia_path.stem.lower()
    safe = "".join(ch if ch.isalnum() else "-" for ch in stem).strip("-") or "sample"
    return repo_root / "source/04-protocol-simulator/.cache/zeia-samples" / safe / "manifest.json"

STARTUP_STEPS = [
    "Locate simulator app",
    "Stop existing server",
    "Install dependencies",
    "Start dev server",
    "Prepare sample registry",
]
STARTUP_STEP_WEIGHTS = [8, 7, 35, 25, 25]
SAMPLE_REGISTRY_ATTEMPTS = 1200
SAMPLE_REGISTRY_POLL_SECONDS = 0.25


def npm_executable() -> str:
    npm = shutil.which("npm")
    if not npm:
        raise FileNotFoundError("npm")
    return npm


def npm_command(*args: str) -> list[str]:
    return [npm_executable(), *args]


def validated_host(host: str) -> str:
    value = str(host or "").strip()
    if not value or not HOST_PATTERN.fullmatch(value):
        raise ValueError(f"invalid host: {host!r}")
    return value


def validated_port(port: int) -> int:
    value = int(port)
    if value < 1 or value > 65535:
        raise ValueError(f"invalid port: {port!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the Tecan Protocol Simulator.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    parser.add_argument("--skip-install", action="store_true", help="skip npm install when node_modules is missing")
    parser.add_argument("--app-dir", type=Path, default=None, help="override path to 04-protocol-simulator")
    args = parser.parse_args()
    host = validated_host(args.host)
    port = validated_port(args.port)

    progress = StepProgress(STARTUP_STEPS, title="Tecan Protocol Simulator", weights=STARTUP_STEP_WEIGHTS)

    progress.begin()
    app_dir = resolve_app_dir(args.app_dir) or prompt_for_app_dir()
    if app_dir is None:
        print("Could not find source/04-protocol-simulator/package.json.")
        print("Put the launcher inside the Fluent AI-Assistance folder, or pass --app-dir.")
        return 1
    if shutil.which("npm") is None:
        print("Node.js/npm was not found on PATH. Install Node.js LTS, then reopen this launcher.")
        return 1
    progress.done(f"found {app_dir}")

    base_url = f"http://{host}:{port}/"
    samples_url = f"{base_url}api/samples"

    progress.begin()
    stopped = stop_existing_server(port)
    progress.done("stopped existing server" if stopped else "no server was running")

    progress.begin()
    if not (app_dir / "node_modules").exists():
        if args.skip_install:
            print("  node_modules is missing and --skip-install was set.")
            return 1
        result = run_subprocess_with_fraction(
            progress,
            npm_command("install"),
            cwd=app_dir,
            label="Installing npm packages",
            estimate_seconds=120,
        )
        if result != 0:
            print("  npm install failed.")
            return result
        progress.done("dependencies installed")
    else:
        progress.done("dependencies already installed")

    progress.detail("Checking optional ZEIA sample cache")
    cache_result = ensure_zeia_sample_cache(app_dir, REPO_ROOT.resolve(), progress)
    if cache_result == "built":
        progress.detail("Built ZEIA sample cache for faster startup")
    elif cache_result == "ready":
        progress.detail("ZEIA sample cache is ready")
    else:
        progress.detail("No local sample ZEIA; skipping sample cache")

    progress.begin()
    # Host/port are validated above and passed only via env so Popen argv stays constant.
    command = npm_command("run", "dev:no-open")
    progress.detail(f"App folder: {app_dir}")
    progress.detail("Keep this window open while using the simulator.")
    server_env = os.environ.copy()
    server_env["TECAN_LAUNCHER_PROGRESS"] = "1"
    server_env["TECAN_SIMULATOR_HOST"] = host
    server_env["TECAN_SIMULATOR_PORT"] = str(port)
    server_env["TECAN_SIMULATOR_STRICT_PORT"] = "1"
    bundle_path = resolve_simulator_bundle_path(REPO_ROOT.resolve())
    if bundle_path is not None:
        server_env["TECAN_SIMULATOR_BUNDLE"] = str(bundle_path)
        progress.detail(f"Launch bundle: {bundle_path}")
    server = subprocess.Popen(
        command,
        cwd=app_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=server_env,
        shell=False,
    )
    if not wait_with_fraction(
        progress,
        label="Starting Vite dev server",
        attempts=120,
        interval=0.25,
        ready=lambda: ping_url(base_url),
    ):
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        print("  The dev server did not become ready.")
        return 1
    progress.done(f"server ready at {base_url}")

    progress.begin()
    if not wait_with_fraction(
        progress,
        label="Building sample registry",
        attempts=SAMPLE_REGISTRY_ATTEMPTS,
        interval=SAMPLE_REGISTRY_POLL_SECONDS,
        ready=lambda: ping_url(samples_url),
    ):
        print("  Sample registry warmup did not finish in time; the app may still load slowly.")
        progress.done("warmup timed out")
    else:
        progress.done("sample registry ready")

    progress.finish(label="Simulator ready")

    if not args.no_open:
        webbrowser.open(base_url, new=0, autoraise=True)

    print()
    print("Simulator is running. Press Ctrl+C to stop.")
    try:
        return server.wait()
    except KeyboardInterrupt:
        server.terminate()
        try:
            return server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            return 130


def ensure_zeia_sample_cache(app_dir: Path, repo_root: Path, progress: StepProgress) -> str:
    """Return 'ready', 'built', or 'skipped'."""
    zeia_path = discover_sample_zeia(repo_root)
    if zeia_path is None:
        return "skipped"
    manifest_path = sample_zeia_cache_manifest(repo_root, zeia_path)
    if manifest_path.is_file() and zeia_cache_matches_source(manifest_path, zeia_path, repo_root):
        return "ready"

    progress.detail("Building ZEIA sample cache (one-time; speeds up later launches)")
    result = run_subprocess_with_fraction(
        progress,
        npm_command("run", "build:zeia-sample-cache"),
        cwd=app_dir,
        label="Extracting ZEIA sample files",
        estimate_seconds=180,
    )
    if result != 0:
        progress.detail("ZEIA cache build failed; simulator will rebuild on first load")
        return "skipped"
    return "built"


def zeia_cache_matches_source(manifest_path: Path, zeia_path: Path, repo_root: Path) -> bool:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fingerprint = manifest.get("sourceFingerprint") or {}
        stat = zeia_path.stat()
        mtime_ms = int(stat.st_mtime * 1000)
        try:
            relative = zeia_path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            relative = str(zeia_path)
        return (
            manifest.get("sourceZeiaPath") == relative
            and fingerprint.get("size") == stat.st_size
            and int(fingerprint.get("mtimeMs") or 0) == mtime_ms
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False


def resolve_simulator_bundle_path(repo_root: Path) -> Path | None:
    env_path = os.environ.get("TECAN_SIMULATOR_BUNDLE", "").strip()
    if env_path:
        candidate = Path(env_path).resolve()
        return candidate if candidate.exists() else None

    candidates = [
        repo_root / "run-to-start" / "simulator-bundle",
        repo_root / "run-to-start" / "ai-bundle",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def resolve_app_dir(override: Path | None) -> Path | None:
    if override is not None:
        candidate = override.resolve()
        return candidate if (candidate / "package.json").exists() else None

    search_roots = [Path(__file__).resolve().parent, REPO_ROOT]
    for root in search_roots:
        found = find_app_dir(root)
        if found is not None:
            return found

    cursor = REPO_ROOT.resolve()
    while cursor != cursor.parent:
        found = find_app_dir(cursor)
        if found is not None:
            return found
        cursor = cursor.parent
    return None


def prompt_for_app_dir() -> Path | None:
    print()
    print("Could not automatically find source/04-protocol-simulator.")
    print("A folder picker will open. Select either:")
    print("  - the Fluent AI-Assistance project folder, or")
    print("  - the source/04-protocol-simulator folder.")
    selected = choose_folder_dialog()
    if not selected:
        return None
    return find_app_dir(Path(selected))


def choose_folder_dialog() -> str:
    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$dialog.Description = 'Select the Fluent AI-Assistance project folder or source\\04-protocol-simulator folder'; "
                "$dialog.ShowNewFolderButton = $false; "
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
                "Write-Output $dialog.SelectedPath }"
            ),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result.stdout.strip()

    if sys.platform == "darwin":
        command = [
            "osascript",
            "-e",
            'POSIX path of (choose folder with prompt "Select the Fluent AI-Assistance project folder or source/04-protocol-simulator folder")',
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result.stdout.strip()

    try:
        return input("Enter path to Fluent AI-Assistance or source/04-protocol-simulator: ").strip()
    except EOFError:
        return ""


def find_app_dir(base: Path) -> Path | None:
    base = base.resolve()
    if (base / "package.json").exists() and base.name == "04-protocol-simulator":
        return base
    candidates = [
        base / "source" / "04-protocol-simulator",
        base / "Fluent AI-Assistance" / "source" / "04-protocol-simulator",
        base.parent / "source" / "04-protocol-simulator",
    ]
    for candidate in candidates:
        if (candidate / "package.json").exists():
            return candidate.resolve()
    return None


def stop_existing_server(port: int) -> bool:
    stopped = False
    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$ownerPids = @(Get-NetTCPConnection -LocalPort "
                f"{port} -State Listen -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty OwningProcess -Unique); "
                "$ownerPids"
            ),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            for line in result.stdout.splitlines():
                pid = line.strip()
                if not pid.isdigit():
                    continue
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
                    ],
                    check=False,
                )
                stopped = True
        except Exception:
            return False
        return stopped

    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            pid = line.strip()
            if not pid:
                continue
            subprocess.run(["kill", pid], check=False)
            stopped = True
    except Exception:
        return False
    return stopped


def ping_url(url: str) -> bool:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=1) as response:
            return 200 <= response.status < 500
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 500
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
