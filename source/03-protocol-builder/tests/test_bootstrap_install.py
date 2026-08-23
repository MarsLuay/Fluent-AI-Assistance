from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

from fluent_pipeline import bootstrap

PROJECT_DIR = Path(__file__).resolve().parents[1]


def _venv_python(venv_root: Path) -> Path:
    return venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_script(venv_python: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return venv_python.parent / f"{name}{suffix}"


def _create_venv(venv_root: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    return _venv_python(venv_root)


def _wheel_metadata(wheelhouse: Path, distribution: str) -> str:
    wheel_name = distribution.replace("-", "_")
    wheel = next(wheelhouse.glob(f"{wheel_name}-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata_path = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        return archive.read(metadata_path).decode("utf-8")


def test_clean_environment_bootstrap_smoke(tmp_path: Path, capfd) -> None:
    python = _create_venv(tmp_path / "editable")
    env = dict(os.environ)
    env["FLUENTCODER_PYTHON"] = str(python)

    with capfd.disabled():
        subprocess.run(
            [str(python), "-m", "fluent_pipeline.bootstrap", "--skip-desktop-automation"],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )
        subprocess.run(
            [str(python), "-m", "fluent_pipeline.cli", "--help"],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )


def test_wheel_build_and_non_editable_install_smoke(tmp_path: Path, capfd) -> None:
    build_python = _create_venv(tmp_path / "wheel-build")
    wheelhouse = tmp_path / "wheelhouse"

    with capfd.disabled():
        bootstrap.build_workspace_wheelhouse(build_python, wheelhouse)

        install_python = _create_venv(tmp_path / "wheel-install")
        offline_cwd = tmp_path / "offline-cwd"
        offline_cwd.mkdir()
        bootstrap.install_workspace_from_wheelhouse(install_python, wheelhouse, cwd=offline_cwd)
        bootstrap.run_pip_check(install_python, cwd=offline_cwd)

    for package in bootstrap.workspace_packages():
        requires_dist = (
            line
            for line in _wheel_metadata(wheelhouse, package.distribution).splitlines()
            if line.startswith("Requires-Dist:")
        )
        assert all("file:" not in requirement for requirement in requires_dist)

    env = dict(os.environ)
    env["FLUENTCODER_PYTHON"] = str(install_python)

    with capfd.disabled():
        subprocess.run(
            [str(_venv_script(install_python, "protocol-builder")), "--help"],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )
        subprocess.run(
            [str(_venv_script(install_python, "tecan-ai-mcp")), "--help"],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )
        subprocess.run(
            [
                str(install_python),
                "-c",
                (
                    "from tecan_common.command_registry import load_command_registry; "
                    "from fluent_pipeline.readiness_gates import readiness_gates; "
                    "assert load_command_registry()['commands']; "
                    "assert len(readiness_gates()) == 27"
                ),
            ],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )
        subprocess.run(
            [str(install_python), "-m", "fluent_pipeline.bootstrap", "--skip-desktop-automation"],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )

    assert not (PROJECT_DIR / "build").exists()
