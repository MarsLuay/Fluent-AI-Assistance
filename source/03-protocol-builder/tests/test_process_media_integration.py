"""Integration tests for the process-media CLI pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from fluent_pipeline.cli import _cmd_process_media
from fluent_pipeline.media_convert import (
    is_placeholder_video_gif,
    resolve_ffmpeg,
    write_placeholder_video_slot,
)
from fluent_pipeline.protocol_ir import (
    CANONICAL_IR_VERSION,
    annotate_verification_prompts_with_media,
    write_placeholder_image_slot,
    write_protocol_ir,
)
from fluent_pipeline.runner import PipelineError


def _build_test_ir() -> dict:
    ir = {
        "ir_version": CANONICAL_IR_VERSION,
        "id": "process-media-test",
        "protocol": {"name": "ProcessMediaTest"},
        "source": {"format": "test", "path": ""},
        "worktable": {"name": "780_Empty"},
        "labware": [],
        "reagents": [],
        "liquid_classes": [],
        "variables": [],
        "steps": [
            {
                "id": "step_001",
                "operation": "prompt_user",
                "command_id": "RUPStandardStatement",
                "parameters": {
                    "prompt": "1/2) Still capture check.",
                    "rup_kind": "standard",
                },
            },
            {
                "id": "step_002",
                "operation": "prompt_user",
                "command_id": "RUPStandardStatement",
                "parameters": {
                    "prompt": "2/2) Motion capture check.",
                    "rup_kind": "standard",
                },
            },
        ],
    }
    return annotate_verification_prompts_with_media(ir, default_rup_kind="standard")


def _build_empty_ir() -> dict:
    return {
        "ir_version": CANONICAL_IR_VERSION,
        "id": "process-media-empty-test",
        "protocol": {"name": "ProcessMediaEmptyTest"},
        "source": {"format": "test", "path": ""},
        "worktable": {"name": "780_Empty"},
        "labware": [],
        "reagents": [],
        "liquid_classes": [],
        "variables": [],
        "steps": [],
    }


def _write_test_png(path: Path, color: tuple[int, int, int]) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), color).save(path, format="PNG")


def _write_test_mp4(path: Path) -> None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise unittest.SkipTest("ffmpeg is not on PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise unittest.SkipTest(f"ffmpeg could not create test MP4: {completed.stderr}")


class ProcessMediaIntegrationTests(unittest.TestCase):
    def _make_bundle(self) -> tuple[Path, Path]:
        tmp = Path(tempfile.mkdtemp(prefix="process_media_test_"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))

        bundle = tmp / "bundle"
        media_dir = bundle / "media"
        unprocessed = media_dir / "unprocessed"
        source_dir = bundle / "source"
        reports = source_dir / "reports"
        unprocessed.mkdir(parents=True)
        reports.mkdir(parents=True)

        ir = _build_test_ir()
        ir_path = bundle / "process_media_test.protocol-ir.json"
        write_protocol_ir(ir, ir_path)

        write_placeholder_image_slot(media_dir / "step_001_image.png")
        write_placeholder_video_slot(media_dir / "step_002_video.gif")

        _write_test_png(unprocessed / "step_001_image.png", (40, 180, 60))
        _write_test_mp4(unprocessed / "step_002_video.mp4")

        return bundle, ir_path

    def test_cmd_process_media_converts_unprocessed_captures(self) -> None:
        bundle, ir_path = self._make_bundle()
        media_dir = bundle / "media"
        bad_ir_path = bundle / "aaa-empty.protocol-ir.json"
        write_protocol_ir(_build_empty_ir(), bad_ir_path)

        exit_code = _cmd_process_media(
            Namespace(
                target=bundle,
                ir=ir_path,
                unprocessed_dir=[],
                source_dir=[],
                no_finalize=False,
                as_json=False,
            )
        )
        self.assertEqual(exit_code, 0)

        video_slot_step1 = media_dir / "step_001_video.gif"
        video_slot_step2 = media_dir / "step_002_video.gif"
        self.assertFalse(is_placeholder_video_gif(video_slot_step1))
        self.assertFalse(is_placeholder_video_gif(video_slot_step2))
        self.assertGreater(video_slot_step1.stat().st_size, 500)
        self.assertGreater(video_slot_step2.stat().st_size, 500)

        report_path = bundle / "source" / "reports" / "media_resolution.json"
        self.assertTrue(report_path.is_file())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(int(report.get("resolved_count") or 0), 2)

    def test_cmd_process_media_requires_explicit_ir_when_multiple_candidates_exist(self) -> None:
        bundle, _ir_path = self._make_bundle()
        bad_ir_path = bundle / "aaa-empty.protocol-ir.json"
        write_protocol_ir(_build_empty_ir(), bad_ir_path)

        with self.assertRaisesRegex(PipelineError, "ir_path / --ir"):
            _cmd_process_media(
                Namespace(
                    target=bundle,
                    ir=None,
                    unprocessed_dir=[],
                    source_dir=[],
                    no_finalize=False,
                    as_json=False,
                )
            )

    def test_cli_module_process_media_entrypoint(self) -> None:
        bundle, _ir_path = self._make_bundle()
        media_dir = bundle / "media"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "fluent_pipeline.cli",
                "process-media",
                str(bundle),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stderr or completed.stdout,
        )
        payload = json.loads(completed.stdout)
        self.assertGreaterEqual(int(payload.get("resolved_count") or 0), 2)

        self.assertFalse(is_placeholder_video_gif(media_dir / "step_001_video.gif"))
        self.assertFalse(is_placeholder_video_gif(media_dir / "step_002_video.gif"))


if __name__ == "__main__":
    unittest.main()
