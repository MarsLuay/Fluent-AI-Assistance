"""Tests for bundle media layout helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.bundle_media as bundle_media
from tecan_common import native_acceleration
from fluent_pipeline.bundle_media import (
    assign_step_label_media_to_final_prompts,
    organize_bundle_touchtools_media,
    process_prompt_media_captures,
    resolve_prompt_media,
    stage_generation_media_originals,
)
from fluent_pipeline.media_convert import is_placeholder_image_slot, is_placeholder_video_gif, write_placeholder_video_slot
from fluent_pipeline.protocol_ir import write_placeholder_image_slot


def test_media_kind_normalization_uses_native_ascii_with_python_fallback() -> None:
    values = [" Gif ", "VIDEO", " Audio ", " Image ", "Straße", "\u0130mage"]
    pattern = re.compile(r"(?P<legacy_kind>.*)(?P<slot_kind>)")
    matches = [pattern.match(value) for value in values]
    expected = []
    for value in values:
        token = value.strip().lower()
        expected.append("video" if token in {"gif", "video"} else token)

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        fallback = [bundle_media._media_kind_from_slot_match(match) for match in matches]
    assert fallback == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_casefold_trim",
            wraps=native_acceleration.ascii_casefold_trim,
        ) as native_call:
            actual = [bundle_media._media_kind_from_slot_match(match) for match in matches]
            assert native_call.call_count == len(values)
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_casefold_trim",
            return_value=None,
        ):
            unsupported_fallback = [
                bundle_media._media_kind_from_slot_match(match) for match in matches
            ]
    assert unsupported_fallback == expected


def test_deployable_media_name_and_suffix_use_native_lower_fallback(tmp_path: Path) -> None:
    cases = [
        ("photo.GIF", True),
        ("README.MD", False),
        ("media_manifest.JSON", False),
        ("PhotoÜ.GIF", True),
        ("Photo.İ", False),
    ]
    paths = []
    for name, _ in cases:
        path = tmp_path / name
        path.write_bytes(b"media")
        paths.append(path)

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = [bundle_media._is_deployable_prompt_media(path) for path in paths]
    assert expected == [allowed for _, allowed in cases]

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = [bundle_media._is_deployable_prompt_media(path) for path in paths]
        native_call.assert_any_call("photo.GIF")
        native_call.assert_any_call("PhotoÜ.GIF")
        native_call.assert_any_call(".İ")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = [
                bundle_media._is_deployable_prompt_media(path) for path in paths
            ]
    assert forced_fallback == expected


def test_media_inventory_name_and_suffix_use_native_lower_fallback(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    for name in ("README.MD", "media_manifest.JSON", "PhotoÜ.PNG"):
        (source_dir / name).write_bytes(b"media")

    def collect() -> dict[str, tuple[Path, int]]:
        return bundle_media._collect_media_inventory_records(
            touchtools_dir=None,
            bundle_dirs=[],
            build_dir=None,
            media_dir=tmp_path / "media",
            extra_source_dirs=[source_dir],
        )

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = collect()
    assert set(expected) == {"PhotoÜ.PNG"}
    assert expected["PhotoÜ.PNG"] == (source_dir / "PhotoÜ.PNG", 100)

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = collect()
        native_call.assert_any_call("README.MD")
        native_call.assert_any_call("media_manifest.JSON")
        native_call.assert_any_call("PhotoÜ.PNG")
        native_call.assert_any_call(".PNG")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = collect()
    assert forced_fallback == expected


def test_workflow_slot_base_uses_native_lower_with_unicode_fallback() -> None:
    values = ["STEP123Image", "ſtep123Image", "step_12_video", ""]

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = [bundle_media._workflow_slot_base(value) for value in values]
    assert expected == ["step123", "ſtep123", "step012", ""]

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = [bundle_media._workflow_slot_base(value) for value in values]
        native_call.assert_any_call("STEP123")
        native_call.assert_any_call("ſtep123")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = [
                bundle_media._workflow_slot_base(value) for value in values
            ]
    assert forced_fallback == expected


def test_visible_prompt_media_kind_mapping_uses_native_lower_fallback() -> None:
    ir = {
        "steps": [
            {
                "operation": "prompt_user",
                "parameters": {
                    "media_placeholders": [
                        {"kind": "IMAGE", "slot": "image-slot"},
                        {"kind": "VIDÉO", "slot": "unicode-slot"},
                    ]
                },
            }
        ]
    }

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = bundle_media._visible_prompt_media_by_number(ir)
    assert expected == {1: {"image": "image-slot", "vidéo": "unicode-slot"}}

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = bundle_media._visible_prompt_media_by_number(ir)
        native_call.assert_any_call("IMAGE")
        native_call.assert_any_call("VIDÉO")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = bundle_media._visible_prompt_media_by_number(ir)
    assert forced_fallback == expected


def test_resolve_prompt_media_sorts_image_kind_with_native_lower(
    tmp_path: Path,
) -> None:
    specs = [
        {"kind": "VIDEO", "filename": "video.gif", "slot": "video"},
        {"kind": "IMAGE", "filename": "image.png", "slot": "image"},
    ]

    def resolve() -> list[str]:
        with (
            patch("fluent_pipeline.protocol_ir.media_slot_specs", return_value=specs),
            patch(
                "fluent_pipeline.protocol_ir.required_media_slot_specs",
                side_effect=lambda values: values,
            ),
            patch(
                "fluent_pipeline.media_convert.worktable_fallback_video_slots",
                return_value=set(),
            ),
            patch.object(
                bundle_media, "_collect_media_inventory_records", return_value={}
            ),
        ):
            report = resolve_prompt_media({"steps": []}, tmp_path / "media")
        return [item["filename"] for item in report["unresolved"]]

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = resolve()
    assert expected == ["image.png", "video.gif"]

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = resolve()
        native_call.assert_any_call("IMAGE")
        native_call.assert_any_call("VIDEO")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = resolve()
    assert forced_fallback == expected


def test_unresolved_policy_uses_native_lower_with_unicode_fallback(tmp_path: Path) -> None:
    values = [" Fail_Gate ", "FälL_Gate"]

    def resolved_modes() -> list[str]:
        return [
            resolve_prompt_media(
                {"steps": []},
                tmp_path / "media",
                policy={"on_unresolved": value},
            )["on_unresolved"]
            for value in values
        ]

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = resolved_modes()
    assert expected == ["fail_gate", "fäll_gate"]

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = resolved_modes()
        native_call.assert_any_call("Fail_Gate")
        native_call.assert_any_call("FälL_Gate")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = resolved_modes()
    assert forced_fallback == expected


def test_stage_generation_media_originals_copies_dropped_video(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    media_dir = build_dir / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "step_001_video.gif").write_bytes(b"gif")
    (media_dir / "step_001_video.mp4").write_bytes(b"video-bytes")
    ops = [
        {
            "status": "converted",
            "source": str(media_dir / "step_001_video.mp4"),
            "destination": str(media_dir / "step_001_video.gif"),
        }
    ]
    specs = [{"kind": "VIDEO", "filename": "step_001_video.gif", "slot": "step_001_video"}]

    def stage_originals() -> Path:
        return stage_generation_media_originals(build_dir, media_dir, ops, specs=specs)

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected_dir = stage_originals()
    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            originals_dir = stage_originals()
            native_call.assert_any_call("VIDEO")
    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback_dir = stage_originals()

    assert originals_dir == expected_dir == forced_fallback_dir
    assert (originals_dir / "step_001_video.mp4").is_file()
    provenance = json.loads((build_dir / "source" / "reports" / "media_provenance.json").read_text())
    assert provenance["entries"][0]["operation"] == "video_to_gif"


def test_organize_bundle_touchtools_media_splits_layout(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    media_dir = bundle / "media"
    source_dir = bundle / "source"
    media_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    (media_dir / "step_001_video.gif").write_bytes(b"deploy-gif")
    originals = source_dir / "media-originals"
    originals.mkdir(parents=True)
    (originals / "capture.mov").write_bytes(b"raw-video")
    (source_dir / "reports").mkdir(parents=True)
    (source_dir / "reports" / "media_provenance.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "processed": "step_001_video.gif",
                        "unprocessed": "capture.mov",
                        "operation": "video_to_gif",
                        "status": "converted",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = organize_bundle_touchtools_media(media_dir, source_dir)
    assert manifest["processed_count"] == 1
    assert (media_dir / "processed" / "step_001_video.gif").is_file()
    assert manifest["unprocessed_count"] == 1
    assert (media_dir / "unprocessed" / "capture.mov").is_file()
    assert (media_dir / "media_manifest.json").is_file()


def test_bundle_media_organizing_uses_native_suffix_lower_fallback(
    tmp_path: Path,
) -> None:
    def organize(run_name: str) -> tuple[dict[str, object], list[str], list[str]]:
        run_dir = tmp_path / run_name
        media_dir = run_dir / "media"
        source_dir = run_dir / "source"
        media_dir.mkdir(parents=True)
        source_dir.mkdir()
        (media_dir / "step_001_video.GIF").write_bytes(b"video")
        originals = source_dir / bundle_media.SOURCE_MEDIA_ORIGINALS_DIR
        originals.mkdir()
        (originals / "capture.MOV").write_bytes(b"raw-video")
        (source_dir / "protocol.ir.json").write_text(
            json.dumps(
                {
                    "steps": [
                        {"parameters": {"video_path": "media/step_001_video.GIF"}},
                        {"parameters": {"video_path": "media/missing.GIF"}},
                        {"parameters": {"image_path": "media/missing.PNG"}},
                    ]
                }
            ),
            encoding="utf-8",
        )
        manifest = organize_bundle_touchtools_media(media_dir, source_dir)
        processed = sorted(path.name for path in (media_dir / "processed").iterdir())
        unprocessed = sorted(path.name for path in (media_dir / "unprocessed").iterdir())
        return manifest, processed, unprocessed

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = organize("python")
    assert expected[0]["processed_count"] == 3
    assert expected[0]["unprocessed_count"] == 1
    assert expected[1] == ["missing.GIF", "missing.PNG", "step_001_video.GIF"]
    assert expected[2] == ["capture.MOV"]

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = organize("native")
        native_call.assert_any_call(".GIF")
        native_call.assert_any_call(".PNG")
        native_call.assert_any_call(".MOV")
        native_call.assert_any_call("step_001_video.GIF")
        native_call.assert_any_call("missing.GIF")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = organize("fallback")
    assert forced_fallback == expected


def test_organize_bundle_touchtools_media_processes_only_ir_references(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    media_dir = bundle / "media"
    source_dir = bundle / "source"
    media_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    (media_dir / "step001Gif.gif").write_bytes(b"deploy-gif")
    (media_dir / "step001Audio.mp3").write_bytes(b"ID3audio")
    (media_dir / "step002Image.png").write_bytes(b"deploy-image")
    (media_dir / "step_001_video.gif").write_bytes(b"legacy-stale")
    (media_dir / "step999Image.jpeg").write_bytes(b"raw-extra")
    originals = source_dir / "media-originals"
    originals.mkdir(parents=True)
    (originals / "step020image.jpeg").write_bytes(b"raw-exact-name")
    (originals / "step022audio.mp3").write_bytes(b"raw-audio-exact-name")
    (originals / "before_normalize__step001Gif.gif").write_bytes(b"backup")
    (source_dir / "protocol.ir.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "id": "step_001",
                        "operation": "prompt_user",
                        "parameters": {
                            "media_placeholders": [
                                {"kind": "video", "slot": "step001Gif", "path": "media/step001Gif.gif"}
                            ],
                            "sound_file": "media/step001Audio.mp3",
                        },
                    },
                    {
                        "id": "step_002",
                        "operation": "prompt_user",
                        "parameters": {
                            "image_path": (
                                r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
                                r"\Demo_media\step002Image.png"
                            )
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    manifest = organize_bundle_touchtools_media(media_dir, source_dir)

    assert manifest["processed_count"] == 3
    assert sorted(manifest["required_processed_files"]) == [
        "step001Audio.mp3",
        "step001Gif.gif",
        "step002Image.png",
    ]
    assert (media_dir / "processed" / "step001Gif.gif").is_file()
    assert (media_dir / "processed" / "step001Audio.mp3").is_file()
    assert (media_dir / "processed" / "step002Image.png").is_file()
    assert not (media_dir / "processed" / "step_001_video.gif").exists()
    assert not (media_dir / "unprocessed" / "step_001_video.gif").exists()
    assert not (media_dir / "unprocessed" / "step999Image.jpeg").exists()
    assert (media_dir / "unprocessed" / "step020image.jpeg").is_file()
    assert (media_dir / "unprocessed" / "step022audio.mp3").is_file()
    assert not (media_dir / "unprocessed" / "before_normalize__step001Gif.gif").exists()


def test_organize_bundle_touchtools_media_seeds_missing_required_placeholders(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    media_dir = bundle / "media"
    source_dir = bundle / "source"
    media_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    (source_dir / "protocol.ir.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "id": "step_004",
                        "operation": "prompt_user",
                        "command_id": "RUPStandardStatement",
                        "parameters": {
                            "media_placeholders": [
                                {
                                    "kind": "image",
                                    "slot": "step_004_image",
                                    "path": "media/step_004_image.png",
                                },
                                {
                                    "kind": "video",
                                    "slot": "step_004_video",
                                    "path": "media/step_004_video.gif",
                                },
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    manifest = organize_bundle_touchtools_media(media_dir, source_dir)

    assert manifest["processed_count"] == 2
    assert sorted(manifest["required_processed_files"]) == [
        "step_004_image.png",
        "step_004_video.gif",
    ]
    assert is_placeholder_image_slot(media_dir / "processed" / "step_004_image.png")
    assert is_placeholder_video_gif(media_dir / "processed" / "step_004_video.gif")


def test_organize_bundle_touchtools_media_replaces_placeholder_with_resolved_media(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    media_dir = bundle / "media"
    source_dir = bundle / "source"
    processed_dir = media_dir / "processed"
    processed_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    write_placeholder_image_slot(processed_dir / "step_004_image.png")
    write_placeholder_video_slot(processed_dir / "step_004_video.gif")
    (media_dir / "step_004_image.png").write_bytes(b"real-image")
    (source_dir / "protocol.ir.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "id": "step_004",
                        "operation": "prompt_user",
                        "command_id": "RUPStandardStatement",
                        "parameters": {
                            "media_placeholders": [
                                {
                                    "kind": "image",
                                    "slot": "step_004_image",
                                    "path": "media/step_004_image.png",
                                },
                                {
                                    "kind": "video",
                                    "slot": "step_004_video",
                                    "path": "media/step_004_video.gif",
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    manifest = organize_bundle_touchtools_media(media_dir, source_dir)

    assert manifest["processed_count"] == 2
    assert (processed_dir / "step_004_image.png").read_bytes() == b"real-image"
    assert is_placeholder_video_gif(processed_dir / "step_004_video.gif")


def test_assign_step_label_media_uses_final_visible_prompt_number() -> None:
    ir = {
        "steps": [
            _prompt("step_001", media=False),
            _prompt("step_002", media=True),
            _prompt("step_003", media=False),
            _prompt("step_004", media=True),
        ]
    }

    assignments = assign_step_label_media_to_final_prompts(
        ir,
        [Path("step2.HEIC"), Path("step4.mov")],
    )

    assert assignments == [
        {
            "input": "step2.HEIC",
            "status": "mapped",
            "prompt_number": 2,
            "step_id": "step_002",
            "image_slot": None,
            "video_slot": "step_002_video",
            "image_output": None,
            "video_output": "step_002_video.gif",
            "mapping_basis": "final_visible_prompt_number",
        },
        {
            "input": "step4.mov",
            "status": "mapped",
            "prompt_number": 4,
            "step_id": "step_004",
            "image_slot": None,
            "video_slot": "step_004_video",
            "image_output": None,
            "video_output": "step_004_video.gif",
            "mapping_basis": "final_visible_prompt_number",
        },
    ]


def test_assign_step_label_media_skips_final_prompt_without_media() -> None:
    ir = {"steps": [_prompt("step_001", media=False), _prompt("step_002", media=True)]}

    assignments = assign_step_label_media_to_final_prompts(ir, [Path("step1.HEIC")])

    assert assignments == [
        {
            "input": "step1.HEIC",
            "status": "skipped",
            "prompt_number": 1,
            "reason": "final prompt number has no media slots",
        }
    ]


def test_assign_step_label_media_marks_worktable_motion_display() -> None:
    ir = {"steps": [_prompt("step_001", media=True, rup_kind="worktable")]}
    ir["steps"][0]["parameters"]["media_placeholders"][0]["kind"] = "VIDEO"

    assignments = assign_step_label_media_to_final_prompts(ir, [Path("step1.mov")])

    assert assignments == [
        {
            "input": "step1.mov",
            "status": "mapped",
            "prompt_number": 1,
            "step_id": "step_001",
            "image_slot": None,
            "video_slot": "step_001_video",
            "image_output": None,
            "video_output": "step_001_video.gif",
            "mapping_basis": "final_visible_prompt_number",
            "worktable_display_video": True,
            "worktable_safe_required": True,
        }
    ]
    video = ir["steps"][0]["parameters"]["media_placeholders"][0]
    assert video["worktable_display"] is True
    assert video["requires_worktable_gif_normalization"] is True


def test_assignment_suffix_and_worktable_kind_use_native_lower_fallback() -> None:
    def assign() -> tuple[list[dict[str, object]], bool, bool]:
        ir = {"steps": [_prompt("step_001", media=True, rup_kind="worktable")]}
        params = ir["steps"][0]["parameters"]
        params["rup_kind"] = " WORKTABLE "
        params["media_placeholders"][0]["kind"] = "VIDEO"
        assignments = assign_step_label_media_to_final_prompts(
            ir, [Path("step1.MOV")]
        )
        video = params["media_placeholders"][0]
        return (
            assignments,
            video.get("worktable_display") is True,
            video.get("requires_worktable_gif_normalization") is True,
        )

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = assign()
    assert expected[0][0]["worktable_display_video"] is True
    assert expected[1:] == (True, True)

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = assign()
        native_call.assert_any_call("VIDEO")
        native_call.assert_any_call("WORKTABLE")
        native_call.assert_any_call(".MOV")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = assign()
    assert forced_fallback == expected


def _write_test_png(path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 480), (220, 40, 40)).save(path, format="PNG")


def test_resolve_prompt_media_heic_image_slot(tmp_path: Path, monkeypatch) -> None:
    source_dir = tmp_path / "unprocessed"
    source_dir.mkdir()
    heic = source_dir / "step_004_image.heic"
    heic.write_bytes(b"fake-heic")

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    write_placeholder_image_slot(media_dir / "step_004_image.png")

    def _fake_convert(source: Path, dest: Path, *, overwrite: bool = False) -> dict[str, object]:
        dest.write_bytes(b"converted-png")
        return {"source": str(source), "destination": str(dest), "bytes": dest.stat().st_size}

    monkeypatch.setattr(
        "fluent_pipeline.media_convert.convert_image_to_png",
        _fake_convert,
    )

    ir = _media_ir("step_004", kinds=["image"])
    report = resolve_prompt_media(
        ir,
        media_dir,
        build_dir=tmp_path / "build",
        policy={"extra_source_dirs": [str(source_dir)]},
    )

    assert report["resolved_count"] == 1
    assert report["actions"][0]["action"] == "converted_image_to_png"
    assert media_dir.joinpath("step_004_image.png").read_bytes() == b"converted-png"


def test_process_prompt_media_captures_stages_video_and_finalizes(tmp_path: Path, monkeypatch) -> None:
    unprocessed = tmp_path / "media" / "unprocessed"
    unprocessed.mkdir(parents=True)
    (unprocessed / "step_010_video.MOV").write_bytes(b"fake-video")

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    ir = _media_ir("step_010", kinds=["video"])

    def _fake_video_to_gif(source: Path, dest: Path, **kwargs) -> dict[str, object]:
        dest.write_bytes(b"GIF89a-converted")
        return {"source": str(source), "destination": str(dest), "bytes": dest.stat().st_size}

    monkeypatch.setattr("fluent_pipeline.media_convert.convert_video_to_gif", _fake_video_to_gif)

    finalized = {"called": False}

    def _fake_finalize(ir_obj, media_path, *, build_dir=None):
        finalized["called"] = True
        return media_path, [{"slot": "step_010_video", "status": "converted"}]

    monkeypatch.setattr(
        "fluent_pipeline.generation_workflow.finalize_media_slot_files",
        _fake_finalize,
    )

    report = process_prompt_media_captures(
        ir,
        media_dir,
        unprocessed_dirs=[unprocessed],
        finalize=True,
    )

    assert report["resolved_count"] == 1
    assert finalized["called"] is True
    assert report["converted_count"] == 1


def test_raw_video_capture_uses_native_lower_with_fallback(tmp_path: Path) -> None:
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    (source_dir / "step_010_video.MOV").write_bytes(b"capture")
    media_dir = tmp_path / "media"
    specs = [{"kind": "VIDEO", "slot": "step_010_video"}]

    def stage_captures() -> list[dict[str, str]]:
        return bundle_media._stage_raw_video_captures(
            media_dir, specs, source_dirs=[source_dir]
        )

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = stage_captures()
    assert len(expected) == 1
    assert Path(expected[0]["staged"]).name == "step_010_video.mov"

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = stage_captures()
        native_call.assert_any_call("VIDEO")
        native_call.assert_any_call(".MOV")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = stage_captures()
    assert forced_fallback == expected


def test_visual_source_kind_uses_native_lower_with_fallback(tmp_path: Path) -> None:
    source = tmp_path / "step_020_video.MP4"
    source.write_bytes(b"video")
    spec = {
        "filename": "step_020_video.gif",
        "slot": "step_020_video",
        "kind": "VIDEO",
    }
    inventory = {source.name: source}
    policy = {
        "allow_cross_kind_fallback": False,
        "allow_prompt_number_remap": False,
        "legacy_aliases": {},
    }

    def resolve_source() -> tuple[Path | None, str, str]:
        return bundle_media._resolve_visual_source_for_slot(
            spec, inventory, policy=policy, ir={"steps": []}
        )

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = resolve_source()
    assert expected == (source, "slot_stem", source.name)

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = resolve_source()
        native_call.assert_any_call("VIDEO")
        native_call.assert_any_call(".MP4")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = resolve_source()
    assert forced_fallback == expected


def test_visual_deployment_uses_native_suffix_lower_with_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "capture.MOV"
    destination = tmp_path / "slot.GIF"
    source.write_bytes(b"video")

    def fake_video_to_gif(
        source_path: Path, dest_path: Path, *, overwrite: bool = False
    ) -> dict[str, str]:
        dest_path.write_bytes(b"GIF89a-test")
        return {"source": str(source_path), "destination": str(dest_path)}

    monkeypatch.setattr(
        "fluent_pipeline.media_convert.convert_video_to_gif", fake_video_to_gif
    )

    def deploy() -> str:
        return bundle_media._deploy_visual_source_to_slot(
            source, destination, slot_kind="video"
        )

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = deploy()
    assert expected == "converted_video_to_gif"
    assert destination.read_bytes() == b"GIF89a-test"

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = deploy()
        native_call.assert_any_call(".MOV")
        native_call.assert_any_call(".GIF")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = deploy()
    assert forced_fallback == expected


def test_resolve_prompt_media_cross_kind_image_fills_video_slot(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_test_png(source_dir / "step_010_image.png")

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    write_placeholder_video_slot(media_dir / "step_010_video.gif")

    ir = _media_ir("step_010", kinds=["video"])
    report = resolve_prompt_media(
        ir,
        media_dir,
        build_dir=tmp_path / "build",
        policy={"extra_source_dirs": [str(source_dir)]},
    )

    assert report["resolved_count"] == 1
    assert report["actions"][0]["match_method"] == "cross_kind"
    assert not is_placeholder_video_gif(media_dir / "step_010_video.gif")


def test_resolve_prompt_media_prefer_real_replaces_placeholder(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_test_png(source_dir / "step_004_image.png")

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    placeholder = media_dir / "step_004_image.png"
    write_placeholder_image_slot(placeholder)

    ir = _media_ir("step_004", kinds=["image"])
    report = resolve_prompt_media(
        ir,
        media_dir,
        build_dir=tmp_path / "build",
        policy={"extra_source_dirs": [str(source_dir)]},
    )

    assert report["resolved_count"] == 1
    assert not is_placeholder_image_slot(placeholder)


def test_resolve_prompt_media_exact_raw_overwrites_stale_processed(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_capture = raw_dir / "step011Gif.gif"
    write_placeholder_video_slot(raw_capture)

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "step011Gif.gif").write_bytes(b"stale-processed-gif")

    touchtools_dir = tmp_path / "touchtools"
    touchtools_dir.mkdir()
    (touchtools_dir / "step011Gif.gif").write_bytes(b"stale-deployed-gif")

    ir = {
        "steps": [
            {
                "id": "step_011",
                "operation": "prompt_user",
                "parameters": {
                    "media_annotated": True,
                    "media_placeholders": [{"kind": "video", "slot": "step011Gif"}],
                },
            }
        ]
    }

    report = resolve_prompt_media(
        ir,
        media_dir,
        build_dir=tmp_path / "build",
        touchtools_dir=touchtools_dir,
        policy={"extra_source_dirs": [str(raw_dir)]},
    )

    assert report["resolved_count"] == 1
    assert report["actions"][0]["match_method"] == "exact"
    assert (media_dir / "step011Gif.gif").read_bytes() == raw_capture.read_bytes()


def test_resolve_prompt_media_prompt_number_remap(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_test_png(source_dir / "step_034_image.png")

    steps = [_prompt(f"step_{index:03d}", media=False) for index in range(1, 34)]
    steps.append(_prompt("step_035", media=True, kinds=["image"]))
    ir = {"steps": steps}

    media_dir = tmp_path / "media"
    report = resolve_prompt_media(
        ir,
        media_dir,
        build_dir=tmp_path / "build",
        policy={"extra_source_dirs": [str(source_dir)]},
    )

    assert report["resolved_count"] == 1
    assert report["actions"][0]["match_method"] == "orphaned_step_id_redirect"
    assert (media_dir / "step_035_image.png").is_file()


def test_resolve_prompt_media_visible_prompt_label_remap(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_test_png(source_dir / "step34.png")

    steps = [_prompt(f"step_{index:03d}", media=False) for index in range(1, 34)]
    steps.append(_prompt("step_035", media=True, kinds=["image"]))
    ir = {"steps": steps}

    media_dir = tmp_path / "media"
    report = resolve_prompt_media(
        ir,
        media_dir,
        build_dir=tmp_path / "build",
        policy={"extra_source_dirs": [str(source_dir)]},
    )

    assert report["resolved_count"] == 1
    assert report["actions"][0]["match_method"] == "prompt_number_remap"
    assert (media_dir / "step_035_image.png").is_file()


def test_resolve_prompt_media_legacy_aliases(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_test_png(source_dir / "step_034_image.png")

    ir = _media_ir("step_035", kinds=["image"])
    media_dir = tmp_path / "media"
    report = resolve_prompt_media(
        ir,
        media_dir,
        build_dir=tmp_path / "build",
        policy={
            "extra_source_dirs": [str(source_dir)],
            "allow_prompt_number_remap": False,
            "legacy_aliases": {"step_034_image.png": "step_035_image"},
        },
    )

    assert report["resolved_count"] == 1
    assert report["actions"][0]["match_method"] == "legacy_alias"
    assert (media_dir / "step_035_image.png").is_file()


def test_audio_suffix_normalization_uses_native_lower_with_fallback(
    tmp_path: Path,
) -> None:
    suffixes = [".MP3", ".WAV", ".İ"]
    candidate = tmp_path / "candidate.MP3"
    incumbent = tmp_path / "incumbent.txt"
    candidate.write_bytes(b"audio")
    incumbent.write_bytes(b"other")

    def normalize() -> tuple[list[str], bool]:
        names = [
            bundle_media._audio_slot_filename("step_049", suffix)
            for suffix in suffixes
        ]
        with patch.object(
            bundle_media, "_is_real_visual_media_source", return_value=False
        ):
            prefers_audio = bundle_media._inventory_preferred_source(
                candidate, incumbent
            )
        return names, prefers_audio

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected = normalize()
    assert expected == (
        [f"step049Audio{suffix.lower()}" for suffix in suffixes],
        True,
    )

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            bundle_media,
            "_native_ascii_lower",
            wraps=native_acceleration.ascii_lower,
        ) as native_call:
            actual = normalize()
        for suffix in suffixes:
            native_call.assert_any_call(suffix)
        native_call.assert_any_call(".MP3")
        native_call.assert_any_call(".txt")
    assert actual == expected

    with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(bundle_media, "_native_ascii_lower", return_value=None):
            forced_fallback = normalize()
    assert forced_fallback == expected


def test_resolve_prompt_media_attaches_audio(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "step049audio.MP3").write_bytes(b"ID3" + b"audio-bytes")

    ir = {
        "steps": [
            {
                "id": "step_999",
                "operation": "prompt_user",
                "parameters": {
                    "media_placeholders": [
                        {"kind": "video", "slot": "step049Gif", "path": "media/step049Gif.gif"}
                    ]
                },
            }
        ]
    }
    media_dir = tmp_path / "media"
    report = resolve_prompt_media(
        ir,
        media_dir,
        build_dir=tmp_path / "build",
        policy={"extra_source_dirs": [str(source_dir)]},
    )

    audio_actions = [item for item in report["actions"] if item.get("type") == "audio"]
    assert len(audio_actions) == 1
    assert audio_actions[0]["match_method"] == "media_slot"
    assert (media_dir / "step049Audio.mp3").is_file()
    params = ir["steps"][0]["parameters"]
    assert params["sound_file"] == "media/step049Audio.mp3"
    assert (tmp_path / "build" / "source" / "reports" / "media_resolution.json").is_file()


def _media_ir(step_id: str, *, kinds: list[str], rup_kind: str = "standard") -> dict:
    placeholders = [{"kind": kind, "slot": f"{step_id}_{kind}"} for kind in kinds]
    command_id = "RUPStandardStatement" if rup_kind == "standard" else "RUPWorktableStatement"
    return {
        "steps": [
            {
                "id": step_id,
                "operation": "prompt_user",
                "command_id": command_id,
                "parameters": {
                    "prompt": "Prompt",
                    "rup_kind": rup_kind,
                    "media_annotated": True,
                    "media_placeholders": placeholders,
                },
            }
        ]
    }


def _prompt(
    step_id: str,
    *,
    media: bool,
    rup_kind: str | None = None,
    kinds: list[str] | None = None,
) -> dict:
    params = {"prompt": "Prompt"}
    command_id = "UserPromptStatement"
    if rup_kind:
        params["rup_kind"] = rup_kind
        if rup_kind == "worktable":
            command_id = "RUPWorktableStatement"
    if media:
        selected_kinds = kinds or ["video"]
        params["media_placeholders"] = [
            {"kind": kind, "slot": f"{step_id}_{kind}"} for kind in selected_kinds
        ]
        params["media_annotated"] = True
    return {"id": step_id, "operation": "prompt_user", "command_id": command_id, "parameters": params}
