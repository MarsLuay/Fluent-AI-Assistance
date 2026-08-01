import tempfile
import unittest
from pathlib import Path
import unittest.mock

from fluent_pipeline.media_convert import (
    PLACEHOLDER_VIDEO_LABEL,
    convert_dropped_video_slots,
    convert_video_to_gif,
    convert_video_to_worktable_gif,
    find_dropped_video_source,
    is_placeholder_image_slot,
    is_placeholder_video_gif,
    is_worktable_safe_gif,
    normalize_worktable_detail_gifs,
    normalize_worktable_gif,
    resolve_ffmpeg,
    worktable_fallback_video_slots,
    write_placeholder_video_slot,
)
from fluent_pipeline.protocol_ir import write_placeholder_image_slot
from fluent_pipeline.runner import PipelineError


class MediaConvertTests(unittest.TestCase):
    def test_write_placeholder_video_slot_writes_gif(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "step_001_video.gif"
            write_placeholder_video_slot(dest)
            data = dest.read_bytes()
            self.assertGreater(len(data), 10)
            self.assertTrue(data.startswith(b"GIF87a") or data.startswith(b"GIF89a"))

    def test_write_placeholder_video_slot_is_single_centered_text_frame(self):
        try:
            from PIL import Image, ImageSequence
        except ImportError:
            self.skipTest("Pillow is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "step_001_video.gif"
            write_placeholder_video_slot(dest)
            with Image.open(dest) as image:
                frames = list(ImageSequence.Iterator(image))
                self.assertEqual(len(frames), 1)
                self.assertEqual(image.size, (480, 360))

    def test_generated_image_placeholder_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "step_001_image.png"
            write_placeholder_image_slot(dest)
            self.assertTrue(is_placeholder_image_slot(dest))

    def test_convert_image_to_png_applies_exif_orientation(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        from fluent_pipeline.media_convert import convert_image_to_png

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "capture.jpg"
            dest = Path(tmp) / "slot.png"
            image = Image.new("RGB", (4, 2), "red")
            exif = Image.Exif()
            exif[274] = 6
            image.save(source, format="JPEG", exif=exif)

            convert_image_to_png(source, dest, overwrite=True)

            with Image.open(dest) as converted:
                self.assertEqual(converted.size, (2, 4))

    def test_is_placeholder_video_gif_detects_generated_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "step_001_video.gif"
            write_placeholder_video_slot(dest)
            self.assertTrue(is_placeholder_video_gif(dest))

    def test_is_placeholder_video_gif_rejects_real_gif_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "step_001_video.gif"
            dest.write_bytes(b"GIF89a-real-motion")
            self.assertFalse(is_placeholder_video_gif(dest))

    def test_find_dropped_video_source_prefers_existing_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            (media_dir / "step_001_video.mkv").write_bytes(b"video")
            found = find_dropped_video_source(media_dir, "step_001_video")
            self.assertEqual(found, media_dir / "step_001_video.mkv")

    def test_convert_dropped_video_slots_skips_when_gif_is_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            (media_dir / "step_001_video.mp4").write_bytes(b"video")
            (media_dir / "step_001_video.gif").write_bytes(b"GIF89a-real-motion")
            specs = [{"slot": "step_001_video", "filename": "step_001_video.gif", "kind": "video"}]
            with unittest.mock.patch("fluent_pipeline.media_convert.convert_video_to_gif") as convert_mock:
                results = convert_dropped_video_slots(media_dir, specs)
            convert_mock.assert_not_called()
            self.assertEqual(results, [])

    def test_convert_video_to_gif_requires_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"not-a-real-video")
            dest = Path(tmp) / "out.gif"
            with unittest.mock.patch("fluent_pipeline.media_convert.resolve_ffmpeg", return_value=None):
                with self.assertRaises(PipelineError):
                    convert_video_to_gif(source, dest, overwrite=True)

    def test_convert_video_to_gif_rejects_non_gif_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"x")
            dest = Path(tmp) / "out.mp4"
            with self.assertRaises(PipelineError):
                convert_video_to_gif(source, dest, overwrite=True)

    def test_convert_video_to_gif_applies_max_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"video")
            dest = Path(tmp) / "out.gif"
            captured: dict[str, list[str]] = {}

            def fake_run(cmd, **_kwargs):
                captured["cmd"] = cmd
                dest.write_bytes(b"GIF89a")
                return unittest.mock.Mock(returncode=0, stderr="")

            with unittest.mock.patch(
                "fluent_pipeline.media_convert.resolve_ffmpeg",
                return_value="ffmpeg",
            ), unittest.mock.patch(
                "fluent_pipeline.media_convert.subprocess.run",
                side_effect=fake_run,
            ):
                result = convert_video_to_gif(source, dest, overwrite=True, max_seconds=5)

            self.assertEqual(result["max_seconds"], 5)
            cmd = captured["cmd"]
            self.assertIn("-t", cmd)
            self.assertEqual(cmd[cmd.index("-t") + 1], "5")

    def test_convert_video_to_worktable_gif_defaults_to_full_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"video")
            dest = Path(tmp) / "out.gif"
            with unittest.mock.patch(
                "fluent_pipeline.media_convert.convert_video_to_gif",
            ) as convert_mock, unittest.mock.patch(
                "fluent_pipeline.media_convert.normalize_worktable_gif",
                return_value={
                    "destination": str(dest),
                    "bytes": 123,
                    "width": 640,
                    "height": 480,
                    "fps": 6,
                    "duration_ms": 167,
                },
            ):
                result = convert_video_to_worktable_gif(source, dest, overwrite=True)

            self.assertIsNone(convert_mock.call_args.kwargs["max_seconds"])
            self.assertIsNone(result["max_seconds"])

    @unittest.skipUnless(resolve_ffmpeg(), "ffmpeg not on PATH")
    def test_convert_video_to_gif_with_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            # Tiny valid mp4 is heavy to embed; ffmpeg accepts any input path and
            # fails clearly when the file is not a video. This test only runs when
            # ffmpeg exists and a real sample is provided beside the test module.
            sample = Path(__file__).with_name("fixtures") / "sample_clip.mp4"
            if not sample.is_file():
                self.skipTest("sample_clip.mp4 fixture not present")
            dest = Path(tmp) / "step_001_video.gif"
            result = convert_video_to_gif(sample, dest, overwrite=True, fps=8, max_width=320)
            self.assertTrue(dest.is_file())
            self.assertGreater(result["bytes"], 0)
            self.assertTrue(dest.read_bytes().startswith(b"GIF8"))

    def test_normalize_worktable_gif_flattens_portrait_animation(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not available")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "portrait.gif"
            dest = Path(tmp) / "worktable.gif"
            frames = []
            for color in ("red", "blue", "green"):
                image = Image.new("RGB", (120, 220), color)
                frames.append(image.convert("P", palette=Image.ADAPTIVE, colors=16))
            frames[0].save(
                source,
                save_all=True,
                append_images=frames[1:],
                duration=150,
                loop=0,
            )

            result = normalize_worktable_gif(source, dest, fps=5, overwrite=True)

            self.assertEqual(result["source_width"], 120)
            self.assertEqual(result["source_height"], 220)
            self.assertEqual(result["width"], 640)
            self.assertEqual(result["height"], 480)
            with Image.open(dest) as normalized:
                self.assertEqual(normalized.size, (640, 480))
                self.assertEqual(getattr(normalized, "n_frames", 1), 3)
                normalized.seek(0)
                self.assertEqual(normalized.info.get("duration"), 200)

    def test_worktable_fallback_video_slots_when_image_is_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            from fluent_pipeline.protocol_ir import write_placeholder_image_slot

            write_placeholder_image_slot(media_dir / "step_001_image.png")
            specs = [
                {
                    "step_id": "step_001",
                    "slot": "step_001_image",
                    "kind": "image",
                    "filename": "step_001_image.png",
                },
                {
                    "step_id": "step_001",
                    "slot": "step_001_video",
                    "kind": "video",
                    "filename": "step_001_video.gif",
                },
            ]
            self.assertEqual(worktable_fallback_video_slots(specs, media_dir), {"step_001_video"})

    def test_worktable_display_video_slot_overrides_real_still_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            (media_dir / "step_001_image.png").write_bytes(b"real-capture")
            specs = [
                {
                    "step_id": "step_001",
                    "slot": "step_001_image",
                    "kind": "image",
                    "filename": "step_001_image.png",
                },
                {
                    "step_id": "step_001",
                    "slot": "step_001_video",
                    "kind": "video",
                    "filename": "step_001_video.gif",
                    "worktable_display": True,
                },
            ]
            self.assertEqual(worktable_fallback_video_slots(specs, media_dir), {"step_001_video"})

    def test_convert_dropped_video_slots_uses_worktable_pipeline_for_fallback_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            from fluent_pipeline.protocol_ir import write_placeholder_image_slot, write_placeholder_video_slot

            write_placeholder_image_slot(media_dir / "step_001_image.png")
            write_placeholder_video_slot(media_dir / "step_001_video.gif")
            (media_dir / "step_001_video.mov").write_bytes(b"video")
            specs = [
                {
                    "step_id": "step_001",
                    "slot": "step_001_image",
                    "kind": "image",
                    "filename": "step_001_image.png",
                },
                {
                    "step_id": "step_001",
                    "slot": "step_001_video",
                    "kind": "video",
                    "filename": "step_001_video.gif",
                },
            ]
            with unittest.mock.patch(
                "fluent_pipeline.media_convert.convert_video_to_worktable_gif",
                return_value={
                    "destination": str(media_dir / "step_001_video.gif"),
                    "bytes": 999,
                    "pipeline": "video->normalize-worktable-gif",
                },
            ) as worktable_mock, unittest.mock.patch(
                "fluent_pipeline.media_convert.convert_video_to_gif",
            ) as standard_mock:
                results = convert_dropped_video_slots(media_dir, specs)
            worktable_mock.assert_called_once()
            standard_mock.assert_not_called()
            self.assertEqual(results[0]["status"], "converted")
            self.assertTrue(results[0]["worktable_safe"])

    def test_is_worktable_safe_gif_detects_normalized_output(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not available")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "portrait.gif"
            dest = Path(tmp) / "worktable.gif"
            frames = []
            for color in ("red", "blue"):
                image = Image.new("RGB", (120, 220), color)
                frames.append(image.convert("P", palette=Image.ADAPTIVE, colors=16))
            frames[0].save(
                source,
                save_all=True,
                append_images=frames[1:],
                duration=150,
                loop=0,
            )
            normalize_worktable_gif(source, dest, fps=6, overwrite=True)
            self.assertTrue(is_worktable_safe_gif(dest))
            self.assertFalse(is_worktable_safe_gif(source))

    def test_worktable_gif_rejects_raw_ffmpeg_like_640x480_traits(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not available")

        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp)
            rawish = media_dir / "step_001_video.gif"
            frames = [
                Image.new("RGB", (640, 480), "red").convert("P", palette=Image.ADAPTIVE, colors=16),
                Image.new("RGB", (640, 480), "blue").convert("P", palette=Image.ADAPTIVE, colors=16),
            ]
            frames[0].save(
                rawish,
                save_all=True,
                append_images=frames[1:],
                duration=[160, 170],
                loop=0,
                disposal=1,
                optimize=False,
            )

            self.assertFalse(is_worktable_safe_gif(rawish))

            specs = [
                {
                    "step_id": "step_001",
                    "slot": "step_001_video",
                    "kind": "video",
                    "filename": "step_001_video.gif",
                    "worktable_display": True,
                }
            ]
            results = normalize_worktable_detail_gifs(media_dir, specs)

            self.assertEqual(results[0]["status"], "normalized")
            self.assertTrue(is_worktable_safe_gif(rawish))


if __name__ == "__main__":
    unittest.main()
