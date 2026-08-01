"""Placeholder GIF slots and GIF conversion for prompt media."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from .runner import PipelineError

PLACEHOLDER_VIDEO_LABEL = "Replace with GIF"

VIDEO_SOURCE_EXTENSIONS = (
    ".3gp",
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
    ".wmv",
)

IMAGE_SOURCE_EXTENSIONS = (
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
)

# Instrument-validated defaults for RUP Worktable ``CustomDetailImageFilePath``.
# Raw ffmpeg GIFs glitch in the TouchTools Worktable renderer; only
# Pillow-normalized full-frame GIFs were stable on instrument RUP Standard
# and Worktable detail views after Worktable-safe encoding.
# Treat raw ffmpeg output as unsafe even if it is already 640x480, because mixed
# frame durations and disposal/tile metadata can still break Worktable playback.
# The passing Step4 movie cases used the full clip, not a shortened preview:
# 640x480, 6 fps, uniform 160 ms frames, disposal=2, full-frame tiles.
WORKTABLE_GIF_FPS = 6
WORKTABLE_GIF_WIDTH = 640
WORKTABLE_GIF_HEIGHT = 480
WORKTABLE_GIF_INTERMEDIATE_MAX_WIDTH = 480
WORKTABLE_GIF_MAX_SECONDS = None

# Minimal valid animated GIF (1x1, 2 frames) when Pillow is unavailable.
_MINIMAL_PLACEHOLDER_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff"
    "21f90401000000002c00000000010001000002"
    "0244010021f90401010000002c000000000100"
    "01000002024c01003b"
)


def write_placeholder_video_slot(dest: Path) -> None:
    """Write a plain centered-text GIF placeholder for a video media slot."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        dest.write_bytes(_MINIMAL_PLACEHOLDER_GIF)
        return

    size = (480, 360)
    image = Image.new("RGBA", size, (32, 32, 48, 255))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except OSError:
        font = ImageFont.load_default()
    title = PLACEHOLDER_VIDEO_LABEL
    bbox = draw.textbbox((0, 0), title, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((size[0] - tw) // 2, (size[1] - th) // 2), title, fill=(240, 240, 255), font=font)

    dest.parent.mkdir(parents=True, exist_ok=True)
    image.convert("P", palette=Image.ADAPTIVE).save(
        dest,
        save_all=False,
    )


def resolve_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def is_placeholder_video_gif(path: Path) -> bool:
    """Return True when ``path`` is missing, empty, or still the bundled placeholder GIF."""
    if not path.is_file() or path.stat().st_size == 0:
        return True
    data = path.read_bytes()
    if data == _MINIMAL_PLACEHOLDER_GIF:
        return True
    if not data.startswith(b"GIF8"):
        return False
    with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as handle:
        reference = Path(handle.name)
    try:
        write_placeholder_video_slot(reference)
        return reference.read_bytes() == data
    finally:
        reference.unlink(missing_ok=True)


def find_dropped_video_source(media_dir: Path, slot: str) -> Path | None:
    """Return a non-empty dropped video file for a slot, e.g. ``step_009_video.mp4``."""
    for ext in VIDEO_SOURCE_EXTENSIONS:
        candidate = media_dir / f"{slot}{ext}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def is_placeholder_image_slot(path: Path) -> bool:
    """Return True when ``path`` is missing, empty, or still the bundled placeholder PNG."""
    from .protocol_ir import PLACEHOLDER_IMAGE_TEMPLATE_PATH, _MINIMAL_PLACEHOLDER_PNG, write_placeholder_image_slot

    if not path.is_file() or path.stat().st_size == 0:
        return True
    data = path.read_bytes()
    if data == _MINIMAL_PLACEHOLDER_PNG:
        return True
    template = PLACEHOLDER_IMAGE_TEMPLATE_PATH
    if template.is_file() and data == template.read_bytes():
        return True
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        reference = Path(handle.name)
    try:
        write_placeholder_image_slot(reference)
        return reference.read_bytes() == data
    finally:
        reference.unlink(missing_ok=True)


def worktable_fallback_video_slots(
    specs: list[dict[str, Any]],
    media_dir: Path,
) -> set[str]:
    """Video slots that will feed Worktable detail media.

    Worktable prompts prefer the still-image slot. When that slot is still a
    placeholder, or when a video slot is explicitly marked ``worktable_display``,
    the GIF/video slot becomes the Worktable detail source and must use the
    conservative :func:`convert_video_to_worktable_gif` pipeline.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        slot = str(spec.get("slot") or "").strip()
        if not slot:
            continue
        step_key = str(spec.get("step_id") or slot.rsplit("_", 1)[0])
        grouped[step_key].append(spec)

    fallback: set[str] = set()
    for group in grouped.values():
        image_spec = next(
            (item for item in group if str(item.get("kind") or "").lower() == "image"),
            None,
        )
        video_spec = next(
            (item for item in group if str(item.get("kind") or "").lower() == "video"),
            None,
        )
        if video_spec is None:
            continue
        video_slot = str(video_spec.get("slot") or "").strip()
        if video_spec.get("worktable_display"):
            fallback.add(video_slot)
            continue
        if image_spec is None:
            continue
        image_filename = str(image_spec.get("filename") or "").strip() if image_spec else ""
        image_path = media_dir / image_filename if image_filename else None
        if image_spec is None or is_placeholder_image_slot(image_path):
            fallback.add(video_slot)
    return {slot for slot in fallback if slot}


def is_worktable_safe_gif(path: Path) -> bool:
    """Heuristic check for instrument-stable Worktable GIF encoding."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if not path.read_bytes()[:6] in {b"GIF87a", b"GIF89a"}:
        return False
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        return False

    try:
        with Image.open(path) as image:
            if image.size != (WORKTABLE_GIF_WIDTH, WORKTABLE_GIF_HEIGHT):
                return False
            durations: set[int] = set()
            disposals: set[int | None] = set()
            partial_tiles = 0
            for frame in ImageSequence.Iterator(image):
                durations.add(int(frame.info.get("duration", 0) or 0))
                disposals.add(getattr(frame, "disposal_method", frame.info.get("disposal", None)))
                if getattr(frame, "tile", None):
                    box = tuple(frame.tile[0][1])
                    if box != (0, 0, image.size[0], image.size[1]):
                        partial_tiles += 1
            if partial_tiles:
                return False
            if disposals - {2, None}:
                return False
            if len(durations) > 1:
                return False
    except Exception:
        return False
    return True


def convert_dropped_video_slots(
    media_dir: Path,
    specs: list[dict[str, Any]],
    *,
    fps: int = 10,
    max_width: int = 640,
    worktable_video_slots: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert dropped video files into GIF slot files when the GIF is still a placeholder."""
    if worktable_video_slots is None:
        worktable_slots = worktable_fallback_video_slots(specs, media_dir)
    else:
        worktable_slots = worktable_video_slots
    results: list[dict[str, Any]] = []
    for spec in specs or []:
        if str(spec.get("kind") or "").lower() != "video":
            continue
        slot = str(spec.get("slot") or "").strip()
        filename = str(spec.get("filename") or "").strip()
        if not slot or not filename:
            continue
        gif_path = media_dir / filename
        if not is_placeholder_video_gif(gif_path):
            continue
        source = find_dropped_video_source(media_dir, slot)
        if source is None:
            continue
        try:
            if slot in worktable_slots:
                info = convert_video_to_worktable_gif(
                    source,
                    gif_path,
                    fps=WORKTABLE_GIF_FPS,
                    overwrite=True,
                )
            else:
                info = convert_video_to_gif(
                    source,
                    gif_path,
                    fps=fps,
                    max_width=max_width,
                    overwrite=True,
                )
            from .workflow_events import emit_workflow_event

            emit_workflow_event(
                {
                    "stage": "process_media",
                    "status": "converted",
                    "slot": slot,
                    "source": str(source),
                    "destination": str(gif_path),
                    "bytes": info.get("bytes"),
                    "worktable_safe": slot in worktable_slots,
                }
            )
            results.append(
                {
                    **info,
                    "slot": slot,
                    "status": "converted",
                    "worktable_safe": slot in worktable_slots,
                }
            )
        except PipelineError as exc:
            results.append(
                {
                    "slot": slot,
                    "source": str(source),
                    "destination": str(gif_path),
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return results


def normalize_worktable_detail_gifs(
    media_dir: Path,
    specs: list[dict[str, Any]],
    *,
    worktable_video_slots: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Rewrite Worktable-bound GIF slots that are not already instrument-safe."""
    if worktable_video_slots is None:
        worktable_slots = worktable_fallback_video_slots(specs, media_dir)
    else:
        worktable_slots = worktable_video_slots
    results: list[dict[str, Any]] = []
    for spec in specs or []:
        if str(spec.get("kind") or "").lower() != "video":
            continue
        slot = str(spec.get("slot") or "").strip()
        filename = str(spec.get("filename") or "").strip()
        if not slot or slot not in worktable_slots or not filename:
            continue
        gif_path = media_dir / filename
        if not gif_path.is_file() or gif_path.stat().st_size == 0:
            continue
        if is_placeholder_video_gif(gif_path):
            continue
        if is_worktable_safe_gif(gif_path):
            results.append({"slot": slot, "destination": str(gif_path), "status": "skipped"})
            continue
        try:
            from PIL import Image, ImageOps

            with Image.open(gif_path) as probe:
                probe.verify()
        except Exception:
            results.append(
                {
                    "slot": slot,
                    "destination": str(gif_path),
                    "status": "skipped",
                    "reason": "not_a_valid_gif",
                }
            )
            continue
        try:
            info = normalize_worktable_gif(
                gif_path,
                gif_path,
                fps=WORKTABLE_GIF_FPS,
                width=WORKTABLE_GIF_WIDTH,
                height=WORKTABLE_GIF_HEIGHT,
                overwrite=True,
            )
            results.append({**info, "slot": slot, "status": "normalized"})
        except PipelineError as exc:
            results.append(
                {
                    "slot": slot,
                    "destination": str(gif_path),
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return results


def convert_image_to_png(
    source: Path,
    dest: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert a still capture into a PNG prompt media slot."""
    source = Path(source)
    dest = Path(dest)
    if not source.is_file():
        raise PipelineError(f"Image source not found: {source}")
    if dest.suffix.lower() != ".png":
        raise PipelineError(f"Image slot destination must be a .png path, got: {dest.name}")
    if dest.exists() and not overwrite:
        raise PipelineError(f"Refusing to overwrite existing PNG: {dest} (pass overwrite=True)")

    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    ffmpeg = resolve_ffmpeg()
    if suffix in {".heic", ".heif", ".avif"} and ffmpeg:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-update",
            "1",
            "-frames:v",
            "1",
            str(dest),
        ]
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f"ffmpeg timed out converting {source.name} to PNG") from exc
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            raise PipelineError(
                f"ffmpeg failed converting {source.name} to PNG"
                + (f": {stderr}" if stderr else "")
            )
    else:
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:
            if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
                shutil.copy2(source, dest)
            else:
                raise PipelineError(
                    "Pillow or ffmpeg is required to convert image captures to PNG"
                ) from exc
        else:
            with Image.open(source) as image:
                ImageOps.exif_transpose(image).convert("RGB").save(dest, format="PNG")

    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"Image conversion produced an empty PNG: {dest}")

    return {
        "source": str(source),
        "destination": str(dest),
        "bytes": dest.stat().st_size,
        "pipeline": "ffmpeg" if suffix in {".heic", ".heif", ".avif"} and ffmpeg else "pillow",
    }


def convert_video_to_gif(
    source: Path,
    dest: Path,
    *,
    fps: int = 10,
    max_width: int = 640,
    max_seconds: float | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert a video file to an animated GIF for a prompt media slot."""
    source = Path(source)
    dest = Path(dest)
    if not source.is_file():
        raise PipelineError(f"Video source not found: {source}")
    if dest.exists() and not overwrite:
        raise PipelineError(f"Refusing to overwrite existing GIF: {dest} (pass overwrite=True)")
    if dest.suffix.lower() != ".gif":
        raise PipelineError(f"Video slot destination must be a .gif path, got: {dest.name}")

    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise PipelineError(
            "ffmpeg is required for video-to-gif. Install ffmpeg and ensure it is on PATH."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    filter_expr = f"fps={max(1, int(fps))},scale={max(1, int(max_width))}:-1:flags=lanczos"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
    ]
    if max_seconds is not None and float(max_seconds) > 0:
        cmd.extend(["-t", f"{float(max_seconds):g}"])
    cmd.extend(
        [
            "-vf",
            filter_expr,
            "-loop",
            "0",
            str(dest),
        ]
    )
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"ffmpeg timed out converting {source.name} to GIF") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise PipelineError(
            f"ffmpeg failed converting {source.name} to GIF"
            + (f": {stderr}" if stderr else "")
        )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"ffmpeg produced an empty GIF: {dest}")

    return {
        "source": str(source),
        "destination": str(dest),
        "fps": fps,
        "max_width": max_width,
        "max_seconds": max_seconds,
        "bytes": dest.stat().st_size,
        "ffmpeg": ffmpeg,
    }


def convert_video_to_worktable_gif(
    source: Path,
    dest: Path,
    *,
    fps: int = WORKTABLE_GIF_FPS,
    width: int = WORKTABLE_GIF_WIDTH,
    height: int = WORKTABLE_GIF_HEIGHT,
    intermediate_max_width: int = WORKTABLE_GIF_INTERMEDIATE_MAX_WIDTH,
    max_seconds: float | None = WORKTABLE_GIF_MAX_SECONDS,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert video into the instrument-validated Worktable GIF format.

    Pipeline: ffmpeg portrait clip -> :func:`normalize_worktable_gif`.

    This avoids raw ffmpeg GIF traits that flicker in RUP Worktable
    (``disposal=1``, partial-frame tiles, mixed frame durations, unpadded
    portrait canvas). Raw ffmpeg outputs failed instrument Worktable preview
    even when resized to 640x480; Pillow-normalized variants passed.
    """
    source = Path(source)
    dest = Path(dest)
    if dest.exists() and not overwrite:
        raise PipelineError(f"Refusing to overwrite existing GIF: {dest} (pass overwrite=True)")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "worktable_raw.gif"
        convert_video_to_gif(
            source,
            raw,
            fps=fps,
            max_width=intermediate_max_width,
            max_seconds=max_seconds,
            overwrite=True,
        )
        normalized = normalize_worktable_gif(
            raw,
            dest,
            fps=fps,
            width=width,
            height=height,
            overwrite=True,
        )
    return {
        **normalized,
        "source_video": str(source),
        "pipeline": "video->normalize-worktable-gif",
        "intermediate_max_width": intermediate_max_width,
        "max_seconds": max_seconds,
    }


def normalize_worktable_gif(
    source: Path,
    dest: Path,
    *,
    fps: int = 6,
    width: int = 640,
    height: int = 480,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Rewrite a GIF into the conservative format used for RUP Worktable prompts.

    RUP Standard handles animated GIFs more reliably than RUP Worktable's custom
    detail image renderer. Instrument testing showed only this normalized form
    was non-glitchy: fixed 640x480 canvas, flattened RGB frames, uniform frame
    duration, ``disposal=2`` full-frame rewrites, no transparency, and
    ``optimize=False``. Raw ffmpeg GIFs failed Worktable preview even when
    resized to 640x480.
    """
    source = Path(source)
    dest = Path(dest)
    if not source.is_file():
        raise PipelineError(f"GIF source not found: {source}")
    if dest.exists() and not overwrite:
        raise PipelineError(f"Refusing to overwrite existing GIF: {dest} (pass overwrite=True)")
    if source.suffix.lower() != ".gif" or dest.suffix.lower() != ".gif":
        raise PipelineError("Worktable GIF normalization requires .gif source and destination paths")

    try:
        from PIL import Image, ImageSequence
    except ImportError as exc:
        raise PipelineError("Pillow is required for normalize-worktable-gif") from exc

    target_width = max(1, int(width))
    target_height = max(1, int(height))
    frame_duration = max(1, round(1000 / max(1, int(fps))))
    frames = []

    try:
        resampling = Image.Resampling.LANCZOS
    except AttributeError:  # pragma: no cover - compatibility for old Pillow.
        resampling = Image.LANCZOS

    with Image.open(source) as im:
        source_width, source_height = im.size
        source_frames = getattr(im, "n_frames", 1)
        scale = min(target_width / source_width, target_height / source_height)
        scaled_width = max(1, int(round(source_width * scale)))
        scaled_height = max(1, int(round(source_height * scale)))
        left = (target_width - scaled_width) // 2
        top = (target_height - scaled_height) // 2

        for frame in ImageSequence.Iterator(im):
            rgba = frame.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, "white")
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            scaled = flattened.resize((scaled_width, scaled_height), resampling)
            canvas = Image.new("RGB", (target_width, target_height), "white")
            canvas.paste(scaled, (left, top))
            frames.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=128))

    if not frames:
        raise PipelineError(f"No frames found in GIF: {source}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        dest,
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration,
        loop=0,
        disposal=2,
        optimize=False,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"Worktable GIF normalization produced an empty file: {dest}")

    return {
        "source": str(source),
        "destination": str(dest),
        "source_width": source_width,
        "source_height": source_height,
        "source_frames": source_frames,
        "width": target_width,
        "height": target_height,
        "fps": fps,
        "duration_ms": frame_duration,
        "bytes": dest.stat().st_size,
    }
