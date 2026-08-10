"""Contract tests for install_external_files.ps1 / deploy_touchtools_media.ps1."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().lower()


def _install_external_files(bundle_root: Path) -> list[str]:
    """Python mirror of install_external_files.ps1 for offline CI."""
    manifest_path = bundle_root / "source" / "delivery_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("source/delivery_manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    deployments = list(manifest.get("external_file_deployments") or [])
    installed: list[str] = []
    if not deployments:
        return installed
    for item in deployments:
        relative = str(item.get("bundle_path") or "")
        target = str(item.get("target_path") or "")
        expected = str(item.get("sha256") or "").lower()
        if not relative or not target or len(expected) != 64:
            raise ValueError("Invalid external-file deployment record")
        source = bundle_root / relative.replace("/", "/")
        if not source.is_file():
            raise FileNotFoundError(f"Missing staged external file: {source}")
        if _sha256(source) != expected:
            raise ValueError(f"Staged external file hash mismatch: {source}")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if _sha256(Path(target)) != expected:
            raise ValueError(f"Installed external file hash mismatch: {target}")
        installed.append(target)
    return installed


def _deploy_touchtools_media(bundle_root: Path, images_root: Path) -> dict[str, int]:
    """Python mirror of deploy core (hash skip/copy) for offline CI."""
    src = bundle_root / "media" / "processed"
    if not src.is_dir():
        src = bundle_root / "media"
    if not src.is_dir():
        src = bundle_root / "source" / "media"
    if not src.is_dir():
        raise FileNotFoundError("media folder not found")

    media_subfolder = "script_media"
    deploy_cfg = bundle_root / "source" / "touchtools_deploy.json"
    metadata = bundle_root / "source" / "metadata.json"
    if deploy_cfg.is_file():
        cfg = json.loads(deploy_cfg.read_text(encoding="utf-8"))
        if cfg.get("media_subfolder"):
            media_subfolder = str(cfg["media_subfolder"])
    elif metadata.is_file():
        meta = json.loads(metadata.read_text(encoding="utf-8"))
        if meta.get("script_name"):
            media_subfolder = f"{meta['script_name']}_media"

    dest = images_root / media_subfolder
    dest.mkdir(parents=True, exist_ok=True)

    if (src / "preview__png.png").is_file():
        names = ["preview__png.png"]
    else:
        names = [p.name for p in src.iterdir() if p.is_file() and p.name != "README.md"]

    copied = skipped = failed = 0
    for name in names:
        source_file = src / name
        dest_file = dest / name
        if not source_file.is_file():
            failed += 1
            continue
        source_hash = _sha256(source_file)
        if dest_file.is_file() and _sha256(dest_file) == source_hash:
            skipped += 1
            continue
        shutil.copy2(source_file, dest_file)
        if _sha256(dest_file) != source_hash:
            failed += 1
            continue
        copied += 1
    return {"copied": copied, "skipped": skipped, "failed": failed}


class InstallExternalFilesTests(unittest.TestCase):
    def test_helper_script_exists_and_mentions_hash_contract(self) -> None:
        path = TOOLS / "install_external_files.ps1"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("external_file_deployments", text)
        self.assertIn("Get-FileHash", text)
        self.assertIn("BundleRoot", text)

    def test_python_mirror_installs_and_rejects_bad_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bundle"
            staged = root / "source" / "payload.bin"
            staged.parent.mkdir(parents=True)
            staged.write_bytes(b"hello-external")
            digest = _sha256(staged)
            target = Path(tmp) / "installed" / "payload.bin"
            manifest = {
                "external_file_deployments": [
                    {
                        "bundle_path": "source/payload.bin",
                        "target_path": str(target),
                        "sha256": digest,
                    }
                ]
            }
            (root / "source" / "delivery_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            installed = _install_external_files(root)
            self.assertEqual(installed, [str(target)])
            self.assertEqual(target.read_bytes(), b"hello-external")

            bad = dict(manifest)
            bad["external_file_deployments"][0]["sha256"] = "0" * 64
            (root / "source" / "delivery_manifest.json").write_text(
                json.dumps(bad), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                _install_external_files(root)


class DeployTouchtoolsMediaTests(unittest.TestCase):
    def test_helper_script_has_progress_and_hash(self) -> None:
        path = TOOLS / "deploy_touchtools_media.ps1"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("Write-VisibleProgress", text)
        self.assertIn("Get-FileHash", text)
        self.assertIn("function Wait-IfNeeded", text)
        self.assertIn("[switch]$NoPause", text)

    def test_python_mirror_copies_skips_and_uses_subfolder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bundle"
            media = root / "media" / "processed"
            media.mkdir(parents=True)
            (media / "slot_a.png").write_bytes(b"png-a")
            (media / "README.md").write_text("ignore", encoding="utf-8")
            (root / "source").mkdir()
            (root / "source" / "metadata.json").write_text(
                json.dumps({"script_name": "DemoScript"}), encoding="utf-8"
            )
            images_root = Path(tmp) / "TouchToolsData" / "Images"
            first = _deploy_touchtools_media(root, images_root)
            self.assertEqual(first["copied"], 1)
            self.assertEqual(first["skipped"], 0)
            dest = images_root / "DemoScript_media" / "slot_a.png"
            self.assertTrue(dest.is_file())
            second = _deploy_touchtools_media(root, images_root)
            self.assertEqual(second["copied"], 0)
            self.assertEqual(second["skipped"], 1)


class SimulatorLauncherBatTests(unittest.TestCase):
    def test_launcher_points_at_nested_simulator_module(self) -> None:
        bat = (
            Path(__file__).resolve().parents[3]
            / "run-to-start"
            / "Launch Tecan Simulator.bat"
        )
        text = bat.read_text(encoding="utf-8")
        self.assertIn(r"source\tools\simulator\launch_simulator.py", text)
        self.assertIn("Python 3 was not found", text)
        self.assertIn("EnableDelayedExpansion", text)
        self.assertIn("EXIT_CODE=!ERRORLEVEL!", text)
        launcher = (
            Path(__file__).resolve().parents[3]
            / "source"
            / "tools"
            / "simulator"
            / "launch_simulator.py"
        )
        self.assertTrue(launcher.is_file())


if __name__ == "__main__":
    unittest.main()
