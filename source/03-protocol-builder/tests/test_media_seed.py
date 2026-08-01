import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.bundle_media import seed_prompt_media_from_bundle
from fluent_pipeline.media_convert import is_placeholder_video_gif
from fluent_pipeline.protocol_ir import write_placeholder_image_slot, write_placeholder_video_slot


class MediaSeedTests(unittest.TestCase):
    def test_seeds_real_processed_media_by_slot_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "prior_bundle"
            processed = bundle / "media" / "processed"
            processed.mkdir(parents=True)
            real_gif = processed / "step_009_video.gif"
            real_gif.write_bytes(b"GIF89a" + b"x" * 500_000)
            (processed / "extra_unused.gif").write_bytes(b"GIF89a" + b"y" * 500_000)

            media_dir = root / "build" / "media"
            report = seed_prompt_media_from_bundle(
                bundle,
                media_dir,
                build_dir=root / "build",
                slot_basenames={"step_009_video.gif"},
            )
            self.assertEqual(report["copied_count"], 1)
            self.assertTrue((media_dir / "step_009_video.gif").is_file())
            self.assertFalse(is_placeholder_video_gif(media_dir / "step_009_video.gif"))
            self.assertFalse((media_dir / "extra_unused.gif").exists())

    def test_skips_placeholder_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "prior_bundle"
            processed = bundle / "media" / "processed"
            processed.mkdir(parents=True)
            placeholder = processed / "step_009_video.gif"
            write_placeholder_video_slot(placeholder)

            media_dir = root / "build" / "media"
            report = seed_prompt_media_from_bundle(
                bundle,
                media_dir,
                slot_basenames={"step_009_video.gif"},
            )
            self.assertEqual(report["copied_count"], 0)
            self.assertFalse((media_dir / "step_009_video.gif").exists())

    def test_does_not_clobber_existing_real_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "prior_bundle"
            processed = bundle / "media" / "processed"
            processed.mkdir(parents=True)
            (processed / "step_009_video.gif").write_bytes(b"GIF89a" + b"from_bundle" * 100_000)

            media_dir = root / "build" / "media"
            media_dir.mkdir(parents=True)
            human = media_dir / "step_009_video.gif"
            human.write_bytes(b"GIF89a" + b"human_drop" * 100_000)

            report = seed_prompt_media_from_bundle(
                bundle,
                media_dir,
                slot_basenames={"step_009_video.gif"},
            )
            self.assertEqual(report["copied_count"], 0)
            self.assertIn("step_009_video.gif", report["skipped_existing"])
            self.assertTrue(human.read_bytes().startswith(b"GIF89a" + b"human_drop"))

    def test_overwrites_existing_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "prior_bundle"
            processed = bundle / "media" / "processed"
            processed.mkdir(parents=True)
            (processed / "step_004_image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"real" * 50_000)

            media_dir = root / "build" / "media"
            media_dir.mkdir(parents=True)
            placeholder = media_dir / "step_004_image.png"
            write_placeholder_image_slot(placeholder)

            report = seed_prompt_media_from_bundle(
                bundle,
                media_dir,
                slot_basenames={"step_004_image.png"},
            )
            self.assertEqual(report["copied_count"], 1)
            self.assertGreater(placeholder.stat().st_size, 10_000)

    def test_seeds_unprocessed_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "prior_bundle"
            unprocessed = bundle / "media" / "unprocessed"
            unprocessed.mkdir(parents=True)
            capture = unprocessed / "step_009_video.mp4"
            capture.write_bytes(b"fake-mp4-bytes")

            build_dir = root / "build"
            seed_prompt_media_from_bundle(bundle, build_dir / "media", build_dir=build_dir)
            copied = build_dir / "source" / "media-originals" / "step_009_video.mp4"
            self.assertTrue(copied.is_file())


if __name__ == "__main__":
    unittest.main()
