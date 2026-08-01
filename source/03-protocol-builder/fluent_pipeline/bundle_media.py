"""Ready-to-import bundle media layout: processed/, unprocessed/, provenance."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Collection
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

ASSET_SUFFIXES = {".bmp", ".gif", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
AUDIO_SUFFIXES = {".mp3", ".wav"}
IMAGE_CAPTURE_SUFFIXES = {
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
}
VIDEO_CAPTURE_SUFFIXES = {
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
}

DEFAULT_MEDIA_RESOLVE_POLICY: dict[str, Any] = {
    "prefer_real_over_placeholder": True,
    "allow_cross_kind_fallback": True,
    "allow_prompt_number_remap": True,
    "legacy_aliases": {},
    "on_unresolved": "warn",
    "extra_source_dirs": [],
}

SOURCE_MEDIA_ORIGINALS_DIR = "media-originals"
MEDIA_ASSIGNMENT_JSON = "media_assignment.json"
MEDIA_PROVENANCE_JSON = "media_provenance.json"
LEGACY_ORIGINAL_DIRS = (
    "download-media-raw",
    "media-assigned-originals",
    "media-worktable-originals",
)
STEP_LABEL_MEDIA_RE = re.compile(r"^step(?P<number>\d+)(?:[_ -].*)?\.(?P<ext>[^.]+)$", re.IGNORECASE)
STEP_SLOT_MEDIA_RE = re.compile(
    r"^step[_-]?(?P<number>\d+)(?:[_-](?P<legacy_kind>image|video|audio)|(?P<slot_kind>Image|Gif|Video|Audio))\.(?P<ext>[^.]+)$",
    re.IGNORECASE,
)


def _bundle_prompt_media_sources(bundle_dir: Path) -> list[Path]:
    roots: list[Path] = []
    for rel in ("media/processed", "source/media"):
        candidate = bundle_dir / rel
        if candidate.is_dir():
            roots.append(candidate)
    flat_media = bundle_dir / "media"
    if flat_media.is_dir() and any(child.is_file() for child in flat_media.iterdir()):
        roots.append(flat_media)
    return roots


def _is_deployable_prompt_media(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if path.name.lower() in {"readme.md", "media_manifest.json"}:
        return False
    return path.suffix.lower() in ASSET_SUFFIXES


def _is_real_visual_media_source(path: Path) -> bool:
    from .media_convert import is_placeholder_image_slot, is_placeholder_video_gif

    if not path.is_file() or path.stat().st_size == 0:
        return False
    suffix = path.suffix.lower()
    if suffix == ".gif":
        return not is_placeholder_video_gif(path)
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        return not is_placeholder_image_slot(path)
    if suffix in IMAGE_CAPTURE_SUFFIXES or suffix in VIDEO_CAPTURE_SUFFIXES:
        return True
    return suffix in ASSET_SUFFIXES


def _should_replace_with_real_media(
    source: Path,
    destination: Path,
    *,
    prefer_real_over_placeholder: bool = True,
) -> bool:
    if not _is_real_visual_media_source(source):
        return False
    if not destination.is_file() or destination.stat().st_size == 0:
        return True
    if not prefer_real_over_placeholder:
        return False
    from .media_convert import is_placeholder_image_slot, is_placeholder_video_gif

    if destination.suffix.lower() == ".gif":
        return is_placeholder_video_gif(destination)
    if destination.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        return is_placeholder_image_slot(destination)
    return False


def _should_copy_seeded_media(source: Path, destination: Path) -> bool:
    return _should_replace_with_real_media(source, destination, prefer_real_over_placeholder=True)


def seed_prompt_media_from_bundle(
    bundle_dir: Path,
    media_dir: Path,
    *,
    build_dir: Path | None = None,
    extra_bundle_dirs: list[Path] | None = None,
    slot_basenames: Collection[str] | None = None,
) -> dict[str, Any]:
    """Copy real prompt media from prior ready-to-import bundle(s) into a new build."""
    from .media_convert import is_placeholder_image_slot, is_placeholder_video_gif

    bundle_dirs: list[Path] = []
    for candidate in [bundle_dir, *(extra_bundle_dirs or [])]:
        resolved = candidate.resolve()
        if resolved not in bundle_dirs:
            bundle_dirs.append(resolved)

    media_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    skipped_placeholder: list[str] = []
    skipped_existing: list[str] = []
    wanted = {str(name) for name in (slot_basenames or []) if str(name).strip()}

    slot_sources: dict[str, Path] = {}
    for root_bundle in bundle_dirs:
        for root in _bundle_prompt_media_sources(root_bundle):
            for path in sorted(root.iterdir()):
                if not _is_deployable_prompt_media(path):
                    continue
                if wanted and path.name not in wanted:
                    continue
                if path.name in slot_sources:
                    continue
                suffix = path.suffix.lower()
                if suffix == ".gif" and is_placeholder_video_gif(path):
                    continue
                if suffix in {".png", ".jpg", ".jpeg"} and is_placeholder_image_slot(path):
                    continue
                slot_sources[path.name] = path

    for name, source in sorted(slot_sources.items()):
        destination = media_dir / name
        if not _should_copy_seeded_media(source, destination):
            skipped_existing.append(name)
            continue
        shutil.copy2(source, destination)
        copied.append({"file": name, "source": str(source)})

    unprocessed_copied = 0
    if build_dir is not None:
        for root_bundle in bundle_dirs:
            unprocessed_dir = root_bundle / "media" / "unprocessed"
            originals_dir = build_dir / "source" / SOURCE_MEDIA_ORIGINALS_DIR
            if not unprocessed_dir.is_dir():
                continue
            originals_dir.mkdir(parents=True, exist_ok=True)
            for path in sorted(unprocessed_dir.iterdir()):
                if not path.is_file():
                    continue
                target = originals_dir / path.name
                if target.exists():
                    continue
                shutil.copy2(path, target)
                unprocessed_copied += 1

    return {
        "source_bundle": str(bundle_dirs[0]) if bundle_dirs else None,
        "source_bundles": [str(path) for path in bundle_dirs],
        "copied": copied,
        "copied_count": len(copied),
        "skipped_placeholder": skipped_placeholder,
        "skipped_existing": skipped_existing,
        "unprocessed_copied": unprocessed_copied,
    }


def _touchtools_source_name_candidates(slot_basename: str) -> list[str]:
    name = str(slot_basename or "").strip()
    if not name:
        return []
    candidates = [name]
    match = re.match(r"^(step_\d+)_(video\.gif|image\.png)$", name, re.IGNORECASE)
    if match:
        prefix = match.group(1)
        candidates.extend(
            [
                f"{prefix}_video.gif",
                f"{prefix}_image.png",
            ]
        )
    match = re.match(r"^step(\d+)(Gif\.gif|Image\.png)$", name, re.IGNORECASE)
    if match:
        padded = match.group(1)
        kind = match.group(2).casefold()
        prefix = f"step_{padded}"
        legacy_name = f"{prefix}_video.gif" if kind == "gif.gif" else f"{prefix}_image.png"
        candidates.append(legacy_name)
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def resolve_touchtools_media_seed_dir(
    *,
    manifest: Mapping[str, Any] | None,
    protocol_name: str | None,
    override_dir: str | Path | None = None,
) -> Path | None:
    """Resolve a TouchTools ``Images/<Script>_media`` folder for media seeding."""
    if override_dir:
        path = Path(str(override_dir)).expanduser()
        if path.is_dir():
            return path.resolve()

    env_dir = os.environ.get("TECAN_MEDIA_SEED_DIR", "").strip()
    if env_dir:
        path = Path(env_dir).expanduser()
        if path.is_dir():
            return path.resolve()

    protocol_stem = str(protocol_name or "").strip()
    if protocol_stem:
        default = (
            Path(r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images")
            / f"{protocol_stem}_media"
        )
        if default.is_dir():
            return default.resolve()

    asset_refs: list[str] = []
    if isinstance(manifest, Mapping):
        summary = manifest.get("custom_part_summary")
        if isinstance(summary, Mapping):
            asset_refs.extend(str(value) for value in summary.get("asset_refs") or [] if str(value).strip())
        for script in manifest.get("scripts") or []:
            if not isinstance(script, Mapping):
                continue
            deps = script.get("dependencies")
            if not isinstance(deps, Mapping):
                continue
            asset_refs.extend(str(value) for value in deps.get("custom_asset_refs") or [] if str(value).strip())

    folder_counts: dict[Path, int] = {}
    for ref in asset_refs:
        lowered = ref.replace("/", "\\").casefold()
        if "_media\\" not in lowered and "_media/" not in lowered.casefold():
            continue
        path = Path(ref)
        parent = path.parent
        if parent.is_dir():
            folder_counts[parent.resolve()] = folder_counts.get(parent.resolve(), 0) + 1

    if not folder_counts:
        return None
    return max(folder_counts.items(), key=lambda item: item[1])[0]


def seed_prompt_media_from_touchtools(
    media_dir: Path,
    touchtools_dir: Path,
    *,
    slot_basenames: Collection[str],
) -> dict[str, Any]:
    """Copy deployed TouchTools media into build slots (with image/video basename aliases)."""
    media_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    missing: list[str] = []
    skipped_existing: list[str] = []

    for slot_name in sorted({str(name) for name in slot_basenames if str(name).strip()}):
        destination = media_dir / slot_name
        source_path: Path | None = None
        source_name = ""
        for candidate in _touchtools_source_name_candidates(slot_name):
            path = touchtools_dir / candidate
            if not _is_deployable_prompt_media(path):
                continue
            suffix = path.suffix.lower()
            if suffix == ".gif":
                from .media_convert import is_placeholder_video_gif

                if is_placeholder_video_gif(path):
                    continue
            elif suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
                from .media_convert import is_placeholder_image_slot

                if is_placeholder_image_slot(path):
                    continue
            source_path = path
            source_name = candidate
            break
        if source_path is None:
            missing.append(slot_name)
            continue
        if not _should_copy_seeded_media(source_path, destination):
            skipped_existing.append(slot_name)
            continue
        shutil.copy2(source_path, destination)
        copied.append(
            {
                "file": slot_name,
                "source": str(source_path),
                "source_name": source_name,
            }
        )

    return {
        "touchtools_dir": str(touchtools_dir),
        "copied": copied,
        "copied_count": len(copied),
        "missing": missing,
        "skipped_existing": skipped_existing,
    }


def stage_generation_media_originals(
    build_dir: Path,
    media_dir: Path,
    media_ops: list[dict[str, Any]],
    *,
    specs: list[dict[str, Any]] | None = None,
) -> Path:
    """Copy raw captures and pre-process GIFs into ``source/media-originals/`` during generate."""
    originals_dir = build_dir / "source" / SOURCE_MEDIA_ORIGINALS_DIR
    originals_dir.mkdir(parents=True, exist_ok=True)
    copied: set[str] = set()
    provenance_entries: list[dict[str, Any]] = []

    for entry in media_ops or []:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        source = Path(str(entry.get("source") or ""))
        destination = str(entry.get("destination") or entry.get("output") or "")
        processed_name = Path(destination).name if destination else ""
        if status == "converted" and source.is_file():
            _copy_unique(originals_dir / source.name, source, copied)
            provenance_entries.append(
                {
                    "processed": processed_name,
                    "unprocessed": source.name,
                    "operation": "video_to_gif",
                    "status": status,
                }
            )
        if status == "normalized" and processed_name:
            before_name = f"before_normalize__{processed_name}"
            before_path = originals_dir / before_name
            if not before_path.exists():
                # Normalization overwrote the slot in place; if a sibling backup exists in media_dir, keep it.
                pass
            provenance_entries.append(
                {
                    "processed": processed_name,
                    "unprocessed": before_name,
                    "operation": "normalize_worktable_gif",
                    "status": status,
                }
            )

    for spec in specs or []:
        if str(spec.get("kind") or "").lower() != "video":
            continue
        filename = str(spec.get("filename") or "")
        if not filename:
            continue
        gif_path = media_dir / filename
        if not gif_path.is_file():
            continue
        for suffix in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"):
            dropped = media_dir / f"{Path(filename).stem}{suffix}"
            if dropped.is_file():
                _copy_unique(originals_dir / dropped.name, dropped, copied)
                provenance_entries.append(
                    {
                        "processed": filename,
                        "unprocessed": dropped.name,
                        "operation": "dropped_video_source",
                        "status": "present",
                    }
                )
                break
            upper = media_dir / f"{Path(filename).stem}{suffix.upper()}"
            if upper.is_file():
                _copy_unique(originals_dir / upper.name, upper, copied)
                provenance_entries.append(
                    {
                        "processed": filename,
                        "unprocessed": upper.name,
                        "operation": "dropped_video_source",
                        "status": "present",
                    }
                )
                break

    reports_dir = build_dir / "source" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / MEDIA_PROVENANCE_JSON).write_text(
        json.dumps(
            {
                "originals_dir": f"source/{SOURCE_MEDIA_ORIGINALS_DIR}/",
                "entries": provenance_entries,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return originals_dir


def _merge_media_resolve_policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_MEDIA_RESOLVE_POLICY)
    if isinstance(policy, Mapping):
        merged.update({key: policy[key] for key in policy})
    legacy = merged.get("legacy_aliases")
    merged["legacy_aliases"] = dict(legacy) if isinstance(legacy, Mapping) else {}
    extra = merged.get("extra_source_dirs")
    merged["extra_source_dirs"] = list(extra) if isinstance(extra, list) else []
    return merged


def _parse_prompt_number_from_basename(name: str) -> int | None:
    match = STEP_LABEL_MEDIA_RE.match(name)
    if match:
        return int(match.group("number"))
    match = STEP_SLOT_MEDIA_RE.match(name)
    if match:
        return int(match.group("number"))
    return None


def _media_kind_from_slot_match(match: re.Match[str]) -> str:
    token = str(match.group("legacy_kind") or match.group("slot_kind") or "").strip().lower()
    if token in {"gif", "video"}:
        return "video"
    return token


def _workflow_slot_base(slot: object) -> str:
    text = str(slot or "").strip()
    match = re.match(r"^(step\d{3})(?:Image|Gif|Video|Audio|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    match = re.match(r"^step[_-]?(\d{1,3})(?:[_-](?:image|video|audio)|$)", text, re.IGNORECASE)
    if match:
        return f"step{int(match.group(1)):03d}"
    return ""


def _step_visual_media_slot_bases(step: Mapping[str, Any]) -> set[str]:
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    placeholders = params.get("media_placeholders")
    if not isinstance(placeholders, list):
        return set()
    bases: set[str] = set()
    for item in placeholders:
        if not isinstance(item, Mapping):
            continue
        base = _workflow_slot_base(item.get("slot"))
        if base:
            bases.add(base)
    return bases


def _canonical_workflow_slot_base(number: int) -> str:
    return f"step{number:03d}"


def _audio_slot_filename(slot_base: str, suffix: str) -> str:
    normalized = _workflow_slot_base(slot_base) or str(slot_base or "").strip()
    if not normalized:
        normalized = "step000"
    return f"{normalized}Audio{suffix.lower()}"


def _visible_prompt_media_by_number(ir: Mapping[str, Any]) -> dict[int, dict[str, str]]:
    slots_by_number: dict[int, dict[str, str]] = {}
    prompt_steps = [
        step
        for step in ir.get("steps", [])  # type: ignore[union-attr]
        if isinstance(step, dict) and step.get("operation") == "prompt_user"
    ]
    for number, step in enumerate(prompt_steps, start=1):
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        placeholders = params.get("media_placeholders")
        if not isinstance(placeholders, list) or not placeholders:
            continue
        slots: dict[str, str] = {}
        for item in placeholders:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").lower()
            slot = str(item.get("slot") or "").strip()
            if kind and slot:
                slots[kind] = slot
        if slots:
            slots_by_number[number] = slots
    return slots_by_number


def _visible_prompt_steps(ir: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for step in ir.get("steps", [])  # type: ignore[union-attr]
        if isinstance(step, dict) and step.get("operation") == "prompt_user"
    ]


def _step_id_number(step_id: str) -> int | None:
    match = re.match(r"^step_(\d+)$", str(step_id or "").strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _step_has_visual_media_slots(step: Mapping[str, Any]) -> bool:
    if step.get("operation") != "prompt_user":
        return False
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    placeholders = params.get("media_placeholders")
    return isinstance(placeholders, list) and bool(placeholders)


def _first_media_prompt_after_step_number(ir: Mapping[str, Any], after_num: int) -> dict[str, Any] | None:
    """First ``prompt_user`` step with media whose ``step_NNN`` id is greater than ``after_num``."""
    for step in ir.get("steps", []):  # type: ignore[union-attr]
        if not isinstance(step, dict) or not _step_has_visual_media_slots(step):
            continue
        step_num = _step_id_number(str(step.get("id") or ""))
        if step_num is None or step_num <= after_num:
            continue
        return step
    return None


def _slot_prefix_from_legacy_target(target: str) -> str:
    text = str(target or "").strip()
    return re.sub(r"_(image|video|audio)$", "", text, flags=re.IGNORECASE)


def _collect_media_inventory(
    *,
    touchtools_dir: Path | None,
    bundle_dirs: Collection[Path],
    build_dir: Path | None,
    media_dir: Path,
    extra_source_dirs: Collection[str | Path],
) -> dict[str, Path]:
    records = _collect_media_inventory_records(
        touchtools_dir=touchtools_dir,
        bundle_dirs=bundle_dirs,
        build_dir=build_dir,
        media_dir=media_dir,
        extra_source_dirs=extra_source_dirs,
    )
    return {name: path for name, (path, _priority) in records.items()}


def _collect_media_inventory_records(
    *,
    touchtools_dir: Path | None,
    bundle_dirs: Collection[Path],
    build_dir: Path | None,
    media_dir: Path,
    extra_source_dirs: Collection[str | Path],
) -> dict[str, tuple[Path, int]]:
    inventory: dict[str, tuple[Path, int]] = {}
    priorities: dict[str, int] = {}
    roots: list[tuple[Path, int]] = []
    if touchtools_dir is not None and touchtools_dir.is_dir():
        roots.append((touchtools_dir, 10))
    for bundle in bundle_dirs:
        resolved = Path(bundle).resolve()
        roots.extend((root, 20) for root in _bundle_prompt_media_sources(resolved))
        unprocessed = resolved / "media" / "unprocessed"
        if unprocessed.is_dir():
            roots.append((unprocessed, 90))
    if build_dir is not None:
        originals = build_dir / "source" / SOURCE_MEDIA_ORIGINALS_DIR
        if originals.is_dir():
            roots.append((originals, 95))
    if media_dir.is_dir():
        roots.append((media_dir, 30))
    for extra in extra_source_dirs:
        candidate = Path(str(extra)).expanduser()
        if candidate.is_dir():
            roots.append((candidate, 100))

    for root, priority in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.stat().st_size == 0:
                continue
            if path.name.lower() in {"readme.md", "media_manifest.json"}:
                continue
            suffix = path.suffix.lower()
            if (
                suffix in ASSET_SUFFIXES
                or suffix in AUDIO_SUFFIXES
                or suffix in IMAGE_CAPTURE_SUFFIXES
                or suffix in VIDEO_CAPTURE_SUFFIXES
            ):
                existing_record = inventory.get(path.name)
                existing = existing_record[0] if existing_record is not None else None
                if existing is None or _inventory_preferred_source(
                    path,
                    existing,
                    candidate_priority=priority,
                    incumbent_priority=priorities.get(path.name, 0),
                ):
                    inventory[path.name] = (path, priority)
                    priorities[path.name] = priority
    return inventory


def _inventory_preferred_source(
    candidate: Path,
    incumbent: Path,
    *,
    candidate_priority: int = 0,
    incumbent_priority: int = 0,
) -> bool:
    candidate_real = _is_real_visual_media_source(candidate) or candidate.suffix.lower() in AUDIO_SUFFIXES
    incumbent_real = _is_real_visual_media_source(incumbent) or incumbent.suffix.lower() in AUDIO_SUFFIXES
    if incumbent_priority > candidate_priority:
        return False
    if candidate_priority >= 90 and candidate_priority > incumbent_priority:
        return True
    if candidate_real and not incumbent_real:
        return True
    if candidate_real and incumbent_real and candidate_priority > incumbent_priority:
        return True
    return False


def _convert_still_to_gif(source: Path, destination: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        shutil.copy2(source, destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            image.convert("RGB").save(destination, format="GIF")
    except OSError:
        shutil.copy2(source, destination)


def _convert_gif_to_still(source: Path, destination: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        shutil.copy2(source, destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert("RGB").save(destination, format="PNG")


def _deploy_visual_source_to_slot(
    source: Path,
    destination: Path,
    *,
    slot_kind: str,
    worktable_video: bool = False,
) -> str:
    src_suffix = source.suffix.lower()
    dest_suffix = destination.suffix.lower()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if src_suffix == dest_suffix:
        shutil.copy2(source, destination)
        return "copied"
    if slot_kind == "image" and dest_suffix == ".png":
        if src_suffix in IMAGE_CAPTURE_SUFFIXES:
            from .media_convert import convert_image_to_png

            convert_image_to_png(source, destination, overwrite=True)
            return "converted_image_to_png"
    if slot_kind == "video" and dest_suffix == ".gif":
        if src_suffix in IMAGE_CAPTURE_SUFFIXES:
            _convert_still_to_gif(source, destination)
            return "converted_still_to_gif"
        if src_suffix in VIDEO_CAPTURE_SUFFIXES:
            from .media_convert import convert_video_to_gif, convert_video_to_worktable_gif

            if worktable_video:
                convert_video_to_worktable_gif(source, destination, overwrite=True)
                return "converted_video_to_worktable_gif"
            convert_video_to_gif(source, destination, overwrite=True)
            return "converted_video_to_gif"
    if slot_kind == "video" and src_suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        _convert_still_to_gif(source, destination)
        return "converted_still_to_gif"
    if slot_kind == "image" and src_suffix == ".gif":
        _convert_gif_to_still(source, destination)
        return "converted_gif_to_still"
    shutil.copy2(source, destination)
    return "copied"


def _resolve_visual_source_for_slot(
    spec: Mapping[str, Any],
    inventory: Mapping[str, Path],
    *,
    policy: Mapping[str, Any],
    ir: Mapping[str, Any],
    priorities: Mapping[str, int] | None = None,
) -> tuple[Path | None, str, str]:
    filename = str(spec.get("filename") or "").strip()
    slot = str(spec.get("slot") or "").strip()
    kind = str(spec.get("kind") or "").lower()
    if not filename or not slot:
        return None, "", ""

    if filename in inventory and inventory[filename].is_file() and inventory[filename].stat().st_size > 0:
        source = inventory[filename]
        if _is_real_visual_media_source(source) or int((priorities or {}).get(filename, 0)) >= 90:
            return source, "exact", filename

    for basename, source in inventory.items():
        if not _is_real_visual_media_source(source):
            continue
        if Path(basename).stem.casefold() != slot.casefold():
            continue
        suffix = source.suffix.lower()
        if kind == "image" and suffix in IMAGE_CAPTURE_SUFFIXES:
            return source, "slot_stem", basename
        if kind == "video" and suffix in VIDEO_CAPTURE_SUFFIXES:
            return source, "slot_stem", basename

    if policy.get("allow_cross_kind_fallback", True):
        for candidate in _touchtools_source_name_candidates(filename):
            if candidate in inventory and _is_real_visual_media_source(inventory[candidate]):
                return inventory[candidate], "cross_kind", candidate

    legacy_aliases = policy.get("legacy_aliases") if isinstance(policy.get("legacy_aliases"), Mapping) else {}
    from .protocol_ir import media_slot_filename

    for legacy_basename, target_slot_prefix in legacy_aliases.items():
        expected = media_slot_filename(str(target_slot_prefix).strip(), kind)
        if expected == filename and legacy_basename in inventory:
            source = inventory[legacy_basename]
            if _is_real_visual_media_source(source):
                return source, "legacy_alias", str(legacy_basename)

    if policy.get("allow_prompt_number_remap", True):
        spec_step_id = str(spec.get("step_id") or "")
        spec_num = _step_id_number(spec_step_id)
        for basename, source in inventory.items():
            if not _is_real_visual_media_source(source):
                continue
            slot_match = STEP_SLOT_MEDIA_RE.match(basename)
            if slot_match:
                if spec_num is None:
                    continue
                source_num = int(slot_match.group("number"))
                source_step = next(
                    (
                        step
                        for step in ir.get("steps", [])  # type: ignore[union-attr]
                        if isinstance(step, dict) and _step_id_number(str(step.get("id") or "")) == source_num
                    ),
                    None,
                )
                if source_step is not None and _step_has_visual_media_slots(source_step):
                    continue
                redirect_target = _first_media_prompt_after_step_number(ir, source_num)
                if redirect_target is None or str(redirect_target.get("id") or "") != spec_step_id:
                    continue
                return source, "orphaned_step_id_redirect", basename
            label_match = STEP_LABEL_MEDIA_RE.match(basename)
            if not label_match:
                continue
            prompt_number = int(label_match.group("number"))
            slots_by_prompt = _visible_prompt_media_by_number(ir)
            prompt_slots = slots_by_prompt.get(prompt_number, {})
            if prompt_slots.get(kind) == slot:
                return source, "prompt_number_remap", basename

    return None, "", ""


def _resolve_audio_for_ir(
    ir: dict[str, Any],
    inventory: Mapping[str, Path],
    media_dir: Path,
    *,
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    legacy_aliases = policy.get("legacy_aliases") if isinstance(policy.get("legacy_aliases"), Mapping) else {}
    prompt_steps = _visible_prompt_steps(ir)

    for basename, source in sorted(inventory.items()):
        if source.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        match = STEP_SLOT_MEDIA_RE.match(basename)
        if not match or _media_kind_from_slot_match(match) != "audio":
            continue

        prompt_number = int(match.group("number"))
        source_slot_base = _canonical_workflow_slot_base(prompt_number)
        target_step: dict[str, Any] | None = None
        target_filename = _audio_slot_filename(source_slot_base, source.suffix)
        match_method = "step_id"

        legacy_target = legacy_aliases.get(basename)
        if legacy_target:
            target_prefix = _slot_prefix_from_legacy_target(str(legacy_target))
            target_step = next(
                (step for step in prompt_steps if str(step.get("id") or "") == target_prefix),
                None,
            )
            if target_step is not None:
                target_filename = _audio_slot_filename(target_prefix, source.suffix)
                match_method = "legacy_alias"

        if target_step is None:
            target_step = next(
                (
                    step
                    for step in prompt_steps
                    if source_slot_base in _step_visual_media_slot_bases(step)
                ),
                None,
            )
            if target_step is not None:
                match_method = "media_slot"

        if target_step is None and policy.get("allow_prompt_number_remap", True):
            if 1 <= prompt_number <= len(prompt_steps):
                candidate = prompt_steps[prompt_number - 1]
                target_step = candidate
                step_id = str(candidate.get("id") or f"step_{prompt_number:03d}")
                target_filename = _audio_slot_filename(step_id, source.suffix)
                match_method = "prompt_number_remap"

        if target_step is None:
            target_step = next(
                (step for step in prompt_steps if _step_id_number(str(step.get("id") or "")) == prompt_number),
                None,
            )
            if target_step is not None:
                step_id = str(target_step.get("id") or "")
                target_filename = _audio_slot_filename(step_id, source.suffix)

        if target_step is None:
            continue

        destination = media_dir / target_filename
        params = target_step.get("parameters")
        if not isinstance(params, dict):
            params = {}
            target_step["parameters"] = params
        relative_path = f"media/{target_filename}"
        media_dir.mkdir(parents=True, exist_ok=True)
        try:
            same_source = source.resolve() == destination.resolve()
        except OSError:
            same_source = False
        if same_source and destination.is_file() and destination.stat().st_size > 0 and params.get("sound_file") == relative_path:
            continue
        shutil.copy2(source, destination)
        params["sound_file"] = relative_path
        actions.append(
            {
                "type": "audio",
                "source": str(source),
                "source_basename": basename,
                "destination": target_filename,
                "step_id": target_step.get("id"),
                "match_method": match_method,
                "sound_file": relative_path,
            }
        )
    return actions


def resolve_prompt_media(
    ir: dict[str, Any],
    media_dir: Path,
    *,
    build_dir: Path | None = None,
    policy: Mapping[str, Any] | None = None,
    touchtools_dir: Path | None = None,
    bundle_dirs: Collection[Path] | None = None,
    slot_basenames: Collection[str] | None = None,
) -> dict[str, Any]:
    """Resolve missing or placeholder prompt media after bundle/TouchTools seeding."""
    from .media_convert import worktable_fallback_video_slots
    from .protocol_ir import media_slot_specs, required_media_slot_specs

    resolved_policy = _merge_media_resolve_policy(policy)
    prefer_real = bool(resolved_policy.get("prefer_real_over_placeholder", True))
    on_unresolved = str(resolved_policy.get("on_unresolved") or "warn").strip().lower()

    all_specs = media_slot_specs(ir)
    specs = required_media_slot_specs(all_specs)
    specs = sorted(
        specs,
        key=lambda item: (
            0 if str(item.get("kind") or "").lower() == "image" else 1,
            str(item.get("filename") or ""),
        ),
    )
    worktable_slots = worktable_fallback_video_slots(all_specs, media_dir)
    wanted = {str(name) for name in (slot_basenames or []) if str(name).strip()}
    if wanted:
        specs = [spec for spec in specs if str(spec.get("filename") or "") in wanted]

    inventory_records = _collect_media_inventory_records(
        touchtools_dir=touchtools_dir,
        bundle_dirs=list(bundle_dirs or []),
        build_dir=build_dir,
        media_dir=media_dir,
        extra_source_dirs=resolved_policy.get("extra_source_dirs") or [],
    )
    inventory = {name: path for name, (path, _priority) in inventory_records.items()}
    priorities = {name: priority for name, (_path, priority) in inventory_records.items()}

    actions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    warnings: list[str] = []

    media_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        filename = str(spec.get("filename") or "").strip()
        if not filename:
            continue
        destination = media_dir / filename
        source, match_method, source_basename = _resolve_visual_source_for_slot(
            spec,
            inventory,
            policy=resolved_policy,
            ir=ir,
            priorities=priorities,
        )
        if source is None:
            if destination.is_file() and destination.stat().st_size > 0 and _is_real_visual_media_source(destination):
                continue
            unresolved.append(
                {
                    "slot": spec.get("slot"),
                    "filename": filename,
                    "kind": spec.get("kind"),
                    "step_id": spec.get("step_id"),
                }
            )
            continue
        try:
            same_source = source.resolve() == destination.resolve()
        except OSError:
            same_source = False
        exact_slot_source = match_method in {"exact", "slot_stem"} and not same_source
        if not exact_slot_source and not _should_replace_with_real_media(
            source,
            destination,
            prefer_real_over_placeholder=prefer_real,
        ):
            continue
        action_kind = _deploy_visual_source_to_slot(
            source,
            destination,
            slot_kind=str(spec.get("kind") or "").lower(),
            worktable_video=str(spec.get("slot") or "").strip() in worktable_slots,
        )
        from .workflow_events import emit_workflow_event

        emit_workflow_event(
            {
                "stage": "process_media",
                "status": "resolved",
                "slot": spec.get("slot"),
                "filename": filename,
                "action": action_kind,
                "match_method": match_method,
                "source_basename": source_basename,
            }
        )
        actions.append(
            {
                "type": "visual",
                "slot": spec.get("slot"),
                "filename": filename,
                "source": str(source),
                "source_basename": source_basename,
                "match_method": match_method,
                "action": action_kind,
            }
        )

    audio_actions = _resolve_audio_for_ir(ir, inventory, media_dir, policy=resolved_policy)
    actions.extend(audio_actions)

    if unresolved and on_unresolved == "fail_gate":
        warnings.append(f"{len(unresolved)} required media slot(s) remain unresolved")
    elif unresolved:
        warnings.append(f"{len(unresolved)} required media slot(s) could not be auto-resolved")

    report = {
        "policy": resolved_policy,
        "actions": actions,
        "resolved_count": len(actions),
        "unresolved": unresolved,
        "warnings": warnings,
        "inventory_sources": len(inventory),
        "on_unresolved": on_unresolved,
    }
    if build_dir is not None:
        reports_dir = build_dir / "source" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "media_resolution.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def _stage_raw_video_captures(
    media_dir: Path,
    specs: list[dict[str, Any]],
    *,
    source_dirs: Collection[Path],
) -> list[dict[str, str]]:
    """Copy raw video captures beside GIF slots so dropped-video conversion can run."""
    from .media_convert import VIDEO_SOURCE_EXTENSIONS

    media_dir.mkdir(parents=True, exist_ok=True)
    slot_names = {
        str(spec.get("slot") or "").strip()
        for spec in specs
        if str(spec.get("kind") or "").lower() == "video" and str(spec.get("slot") or "").strip()
    }
    staged: list[dict[str, str]] = []
    for root in source_dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.stat().st_size == 0:
                continue
            if path.suffix.lower() not in VIDEO_CAPTURE_SUFFIXES:
                continue
            stem = path.stem
            if stem not in slot_names:
                continue
            destination = media_dir / f"{stem}{path.suffix.lower()}"
            try:
                same_source = path.resolve() == destination.resolve()
            except OSError:
                same_source = False
            if same_source and destination.is_file() and destination.stat().st_size > 0:
                continue
            shutil.copy2(path, destination)
            staged.append({"source": str(path), "staged": str(destination)})
    return staged


def process_prompt_media_captures(
    ir: dict[str, Any],
    media_dir: Path,
    *,
    build_dir: Path | None = None,
    policy: Mapping[str, Any] | None = None,
    touchtools_dir: Path | None = None,
    bundle_dirs: Collection[Path] | None = None,
    unprocessed_dirs: Collection[Path] | None = None,
    slot_basenames: Collection[str] | None = None,
    finalize: bool = True,
) -> dict[str, Any]:
    """Resolve raw operator captures (images/videos) into prompt media slots.

  Scans ``media/unprocessed/``, ``source/media-originals/``, bundle dirs, and
  optional extra source folders. Converts still captures (HEIC/PNG/JPEG/...) to
  PNG slots, videos (MOV/MP4/...) to GIF slots (Worktable-safe when required),
  then optionally runs dropped-video conversion and Worktable GIF normalization.
    """
    from .protocol_ir import media_slot_specs, required_media_slot_specs
    from .workflow_events import workflow_phase

    resolved_policy = dict(_merge_media_resolve_policy(policy))
    extra_dirs = list(resolved_policy.get("extra_source_dirs") or [])
    for path in unprocessed_dirs or []:
        candidate = Path(path)
        if candidate.is_dir():
            extra_dirs.append(str(candidate))
    resolved_policy["extra_source_dirs"] = extra_dirs

    specs = required_media_slot_specs(media_slot_specs(ir))
    stage_dirs: list[Path] = []
    for path in extra_dirs:
        candidate = Path(str(path)).expanduser()
        if candidate.is_dir():
            stage_dirs.append(candidate)
    if build_dir is not None:
        originals = build_dir / "source" / SOURCE_MEDIA_ORIGINALS_DIR
        if originals.is_dir():
            stage_dirs.append(originals)
    with workflow_phase("process_media", "stage raw video captures beside GIF slots", step="stage"):
        staged = _stage_raw_video_captures(media_dir, specs, source_dirs=stage_dirs)

    with workflow_phase("process_media", "resolve raw captures into media slots", step="resolve"):
        resolution = resolve_prompt_media(
            ir,
            media_dir,
            build_dir=build_dir,
            policy=resolved_policy,
            touchtools_dir=touchtools_dir,
            bundle_dirs=bundle_dirs,
            slot_basenames=slot_basenames,
        )

    finalize_report: dict[str, Any] = {}
    if finalize:
        from .generation_workflow import finalize_media_slot_files

        with workflow_phase("process_media", "finalize GIF slots and Worktable normalization", step="finalize"):
            finalized_dir, media_ops = finalize_media_slot_files(
                ir,
                media_dir,
                build_dir=build_dir,
            )
        finalize_report = {
            "media_dir": str(finalized_dir) if finalized_dir is not None else str(media_dir),
            "media_ops": media_ops,
            "converted_count": sum(1 for item in media_ops if item.get("status") == "converted"),
            "normalized_count": sum(1 for item in media_ops if item.get("status") == "normalized"),
            "failed_count": sum(1 for item in media_ops if item.get("status") == "failed"),
        }

    return {
        "staged_video_sources": staged,
        "staged_count": len(staged),
        "resolution": resolution,
        "resolved_count": resolution.get("resolved_count"),
        "unresolved": resolution.get("unresolved"),
        **finalize_report,
    }


def organize_bundle_touchtools_media(
    bundle_media_dir: Path,
    source_dir: Path,
) -> dict[str, Any]:
    """Split bundle media into deploy-ready ``processed/`` and raw ``unprocessed/`` copies."""
    processed_dir = bundle_media_dir / "processed"
    unprocessed_dir = bundle_media_dir / "unprocessed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    unprocessed_dir.mkdir(parents=True, exist_ok=True)

    required_names = _referenced_prompt_media_filenames(source_dir)
    asset_suffixes = set(ASSET_SUFFIXES) | set(AUDIO_SUFFIXES) | {".mp4", ".webm"}
    media_like_suffixes = asset_suffixes | set(IMAGE_CAPTURE_SUFFIXES) | set(VIDEO_CAPTURE_SUFFIXES)
    processed_names: list[str] = []
    for path in list(bundle_media_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.lower() in {"readme.md", "media_manifest.json"}:
            continue
        if path.suffix.lower() not in media_like_suffixes:
            continue
        if required_names and path.name not in required_names:
            path.unlink()
            continue
        if path.suffix.lower() not in asset_suffixes:
            path.unlink()
            continue
        destination = processed_dir / path.name
        if destination.exists():
            if _should_replace_with_real_media(path, destination):
                destination.unlink()
                shutil.move(path, destination)
                processed_names.append(path.name)
            else:
                path.unlink()
        else:
            shutil.move(path, destination)
            processed_names.append(path.name)

    if not processed_names and processed_dir.is_dir():
        processed_names = sorted(
            path.name
            for path in processed_dir.iterdir()
            if path.is_file() and path.name.lower() not in {"readme.md", "media_manifest.json"}
        )
    missing_required = sorted(
        name
        for name in required_names
        if name not in processed_names and not (processed_dir / name).is_file()
    )
    if missing_required:
        from .media_convert import write_placeholder_video_slot
        from .protocol_ir import write_placeholder_image_slot

        for name in missing_required:
            suffix = Path(name).suffix.lower()
            destination = processed_dir / name
            if suffix == ".gif":
                write_placeholder_video_slot(destination)
            elif suffix in {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
                write_placeholder_image_slot(destination)
            else:
                continue
            processed_names.append(name)
        processed_names = sorted(dict.fromkeys(processed_names))
    processed_names = sorted(
        path.name
        for path in processed_dir.iterdir()
        if path.is_file() and path.name.lower() not in {"readme.md", "media_manifest.json"}
    )

    copied_unprocessed: set[str] = set()
    for rel in (SOURCE_MEDIA_ORIGINALS_DIR, *LEGACY_ORIGINAL_DIRS):
        root = source_dir / rel
        if not root.is_dir():
            continue
        prefix = ""
        if rel == "media-worktable-originals":
            prefix = "worktable_original__"
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name.startswith("before_normalize__"):
                continue
            if path.suffix.lower() not in (
                set(IMAGE_CAPTURE_SUFFIXES)
                | set(VIDEO_CAPTURE_SUFFIXES)
                | set(AUDIO_SUFFIXES)
            ):
                continue
            name = f"{prefix}{path.name}" if prefix and not path.name.startswith(prefix) else path.name
            if name in copied_unprocessed:
                continue
            copied_unprocessed.add(name)
            shutil.copy2(path, unprocessed_dir / name)

    provenance = _load_json(source_dir / "reports" / MEDIA_PROVENANCE_JSON)
    assignment = _load_json(source_dir / MEDIA_ASSIGNMENT_JSON)
    processed_files = _processed_file_records(
        processed_names,
        provenance=provenance,
        assignment=assignment,
        processed_dir=processed_dir,
    )

    manifest = {
        "layout": {
            "processed": "media/processed/",
            "unprocessed": "media/unprocessed/",
            "deploy_source": "media/processed/",
        },
        "processed_count": len(processed_names),
        "unprocessed_count": sum(1 for path in unprocessed_dir.iterdir() if path.is_file()),
        "required_processed_files": sorted(required_names),
        "processed_files": processed_files,
        "notes": [
            "processed/ contains TouchTools deploy files referenced by the packaged script.",
            "unprocessed/ contains raw operator captures copied from source/media-originals/.",
            "Generation writes source/media-originals/ and source/reports/media_provenance.json.",
        ],
    }
    (bundle_media_dir / "media_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (bundle_media_dir / "README.md").write_text(render_bundle_media_readme(), encoding="utf-8")
    return manifest


def _referenced_prompt_media_filenames(source_dir: Path) -> set[str]:
    ir_path = source_dir / "protocol.ir.json"
    if not ir_path.is_file():
        return set()
    try:
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(ir, dict):
        return set()
    from .protocol_ir import media_slot_specs, sound_path_specs_from_ir

    names: set[str] = set()
    for spec in [*media_slot_specs(ir), *sound_path_specs_from_ir(ir)]:
        filename = str(spec.get("filename") or "").strip()
        if filename:
            names.add(filename)
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        for key in (
            "image_path",
            "video_path",
            "sound_path",
            "selected_image_path",
            "selected_sound_path",
            "custom_detail_image_file_path",
        ):
            raw = str(params.get(key) or "").strip()
            if raw:
                names.add(PureWindowsPath(raw).name)
    return names


def render_bundle_media_readme() -> str:
    return "\n".join(
        [
            "# TouchTools media",
            "",
            "- `processed/` — deploy-ready files copied by `run_tecan_bundle_setup.bat`.",
            "- `unprocessed/` — exact original operator captures copied from `source/media-originals/`.",
            "- `media_manifest.json` — processed/unprocessed mapping and counts.",
            "",
            "During `generate`, raw captures and normalization backups are staged under `source/media-originals/`.",
            "Packaging moves deploy files into `processed/` and copies raw operator captures into `unprocessed/`.",
            "",
        ]
    )


def assign_step_label_media_to_final_prompts(
    ir: Mapping[str, Any],
    files: list[Path],
) -> list[dict[str, Any]]:
    """Resolve ``stepN.ext`` captures to media slots by final visible prompt number.

    The visible prompt labels in a generated verification script are the operator's
    source of truth after prompt removals/renumbering. Therefore ``step23.HEIC``
    means final prompt ``23/N``, not an older prompt number from a previous bundle.
    Older-number mapping must be requested with a separate explicit policy rather
    than inferred from the same filename shape.
    """
    prompt_steps = [
        step
        for step in ir.get("steps", [])  # type: ignore[union-attr]
        if isinstance(step, dict) and step.get("operation") == "prompt_user"
    ]
    media_steps_by_number: dict[int, dict[str, Any]] = {}
    for number, step in enumerate(prompt_steps, start=1):
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        placeholders = params.get("media_placeholders")
        if isinstance(placeholders, list) and placeholders:
            media_steps_by_number[number] = step

    assignments: list[dict[str, Any]] = []
    for path in sorted(files, key=lambda item: item.name.lower()):
        match = STEP_LABEL_MEDIA_RE.match(path.name)
        if not match:
            assignments.append(
                {
                    "input": path.name,
                    "status": "skipped",
                    "reason": "filename does not match stepN.ext",
                }
            )
            continue
        prompt_number = int(match.group("number"))
        step = media_steps_by_number.get(prompt_number)
        if step is None:
            assignments.append(
                {
                    "input": path.name,
                    "status": "skipped",
                    "prompt_number": prompt_number,
                    "reason": "final prompt number has no media slots",
                }
            )
            continue

        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        media = params.get("media_placeholders") if isinstance(params, dict) else []
        slots = {
            str(item.get("kind") or "").lower(): str(item.get("slot") or "")
            for item in media
            if isinstance(item, dict) and str(item.get("slot") or "")
        }
        rup_kind = str(params.get("rup_kind") or "").strip().lower()
        command_id = str(step.get("command_id") or "").strip()
        worktable_motion_display = (
            path.suffix.lower() in VIDEO_CAPTURE_SUFFIXES | {".gif"}
            and slots.get("video")
            and (rup_kind == "worktable" or command_id == "RUPWorktableStatement")
        )
        if worktable_motion_display:
            for item in media:
                if isinstance(item, dict) and str(item.get("kind") or "").lower() == "video":
                    item["worktable_display"] = True
                    item["requires_worktable_gif_normalization"] = True
        assignment = {
            "input": path.name,
            "status": "mapped",
            "prompt_number": prompt_number,
            "step_id": step.get("id"),
            "image_slot": slots.get("image"),
            "video_slot": slots.get("video"),
            "image_output": f"{slots['image']}.png" if slots.get("image") else None,
            "video_output": f"{slots['video']}.gif" if slots.get("video") else None,
            "mapping_basis": "final_visible_prompt_number",
        }
        if worktable_motion_display:
            assignment["worktable_display_video"] = True
            assignment["worktable_safe_required"] = True
        assignments.append(assignment)
    return assignments


def _copy_unique(destination: Path, source: Path, seen: set[str]) -> None:
    if source.name in seen or not source.is_file():
        return
    seen.add(source.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source, destination)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _processed_file_records(
    processed_names: list[str],
    *,
    provenance: Mapping[str, Any],
    assignment: Mapping[str, Any],
    processed_dir: Path,
) -> list[dict[str, Any]]:
    by_processed = {
        str(entry.get("processed") or ""): entry
        for entry in provenance.get("entries") or []
        if isinstance(entry, dict)
    }
    size_to_slot: dict[int, str] = {}
    for entry in assignment.get("assignments") or []:
        if not isinstance(entry, dict):
            continue
        output = str(entry.get("output") or "")
        slot = str(entry.get("slot") or "")
        if not output or not slot:
            continue
        candidate = Path(output)
        if candidate.is_file():
            size_to_slot[candidate.stat().st_size] = slot

    records: list[dict[str, Any]] = []
    for name in sorted(processed_names):
        size = (processed_dir / name).stat().st_size if (processed_dir / name).is_file() else None
        prov = by_processed.get(name, {})
        slot = size_to_slot.get(size) if size is not None else None
        assignment_entry = next(
            (
                item
                for item in assignment.get("assignments") or []
                if isinstance(item, dict) and str(item.get("slot") or "") == str(slot or "")
            ),
            None,
        )
        records.append(
            {
                "processed": name,
                "bytes": size,
                "matched_slot": slot,
                "unprocessed_source": (
                    prov.get("unprocessed")
                    or (assignment_entry or {}).get("source_file")
                ),
                "provenance_operation": prov.get("operation"),
            }
        )
    return records
