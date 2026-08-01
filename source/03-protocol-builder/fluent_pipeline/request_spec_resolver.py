"""Resolve the newest request.spec.yaml for regeneration workflows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import READY_TO_IMPORT_DIR, TEMP_FILES_DIRNAME, resolve_user_path

REQUEST_SPEC_FILENAME = "request.spec.yaml"
_VERSION_SUFFIX_RE = re.compile(r"^(?P<base>.+)_v(?P<version>\d+)$", re.IGNORECASE)
_LATEST_PREFIX_RE = re.compile(r"^(?:@)?latest(?::(?P<stem>.+))?$", re.IGNORECASE)
_VERSIONED_SPEC_ROOTS = (
    "build/generations",
    "ready-to-import",
)


@dataclass(frozen=True)
class RequestSpecCandidate:
    path: Path
    stem: str
    version: int
    source: str
    mtime: float
    protocol_name: str | None = None


def normalize_protocol_stem(value: str) -> str:
    """Normalize protocol/bundle labels to a comparable lowercase stem."""
    keep: list[str] = []
    for char in str(value or "").strip().lower():
        if char.isalnum():
            keep.append(char)
        elif char in {" ", "_", "-", "."}:
            keep.append("_")
    stem = "".join(keep).strip("_")
    while "__" in stem:
        stem = stem.replace("__", "_")
    return stem


def split_version_suffix(label: str) -> tuple[str, int | None]:
    clean = str(label or "").strip()
    match = _VERSION_SUFFIX_RE.match(clean)
    if match:
        return match.group("base"), int(match.group("version"))
    return clean, None


def is_latest_alias(value: str | Path | None) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(_LATEST_PREFIX_RE.match(text))


def parse_latest_alias(value: str | Path) -> str | None:
    """Return an optional stem from ``latest`` or ``latest:<stem>`` aliases."""
    match = _LATEST_PREFIX_RE.match(str(value).strip())
    if not match:
        return None
    stem = str(match.group("stem") or "").strip()
    return normalize_protocol_stem(stem) if stem else None


def _source_priority(source: str) -> int:
    return {
        "ready-to-import": 3,
        "project-temp-generations": 2,
        "build-generations": 1,
    }.get(source, 0)


def _version_from_labels(*labels: str | None) -> int:
    versions: list[int] = []
    for label in labels:
        if not label:
            continue
        _, version = split_version_suffix(str(label))
        if version is not None:
            versions.append(version)
    return max(versions, default=0)


def _stem_from_labels(*labels: str | None) -> str:
    for label in labels:
        if not label:
            continue
        base, _ = split_version_suffix(str(label))
        stem = normalize_protocol_stem(base)
        if stem:
            return stem
    return ""


def _candidate_from_path(path: Path, *, source: str, bundle_label: str | None = None) -> RequestSpecCandidate | None:
    if not path.is_file() or path.name.lower() not in {REQUEST_SPEC_FILENAME, "request.spec.yml"}:
        return None
    parent = path.parent
    folder_label = bundle_label or parent.name
    if parent.name.lower() == "source" and parent.parent.name:
        folder_label = parent.parent.name
    stem = _stem_from_labels(folder_label, parent.name)
    version = _version_from_labels(folder_label, parent.name)
    protocol_name: str | None = None
    try:
        from .request_spec import load_request_spec

        spec = load_request_spec(path)
        request = spec.get("request") if isinstance(spec.get("request"), dict) else {}
        protocol_name = str(request.get("protocol_name") or "").strip() or None
    except Exception:
        pass
    stem = _stem_from_labels(folder_label, parent.name)
    if protocol_name:
        base_name, _ = split_version_suffix(protocol_name)
        stem = normalize_protocol_stem(base_name)
    elif not stem:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return RequestSpecCandidate(
        path=path.resolve(),
        stem=stem,
        version=version,
        source=source,
        mtime=mtime,
        protocol_name=protocol_name,
    )


def _metadata_script_labels(metadata_path: Path) -> list[str]:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    labels: list[str] = []
    for key in ("bundle_name", "script_name", "base_bundle_name"):
        value = metadata.get(key)
        if value:
            labels.append(str(value))
    naming = metadata.get("script_naming")
    if isinstance(naming, dict):
        for key in ("final_name", "requested_name", "base_name"):
            value = naming.get(key)
            if value:
                labels.append(str(value))
    request = metadata.get("request")
    if isinstance(request, dict):
        for key in ("protocol_name",):
            value = request.get(key)
            if value:
                labels.append(str(value))
    return labels


def enumerate_request_spec_candidates(
    *,
    projects_dir: Path | None = None,
    ready_to_import_dir: Path | None = None,
    build_generations_dir: Path | None = None,
    project_temp_root: Path | None = None,
) -> list[RequestSpecCandidate]:
    """Collect request specs from delivery folders and per-project temp files."""
    ready_root = (ready_to_import_dir or READY_TO_IMPORT_DIR).resolve()
    build_root = (
        build_generations_dir
        or (ready_root / "unscoped" / TEMP_FILES_DIRNAME / "build" / "generations")
    ).resolve()
    temp_root = (project_temp_root or ready_root).resolve()
    candidates: list[RequestSpecCandidate] = []
    seen: set[str] = set()

    def add(candidate: RequestSpecCandidate | None) -> None:
        if candidate is None:
            return
        key = str(candidate.path)
        if key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    if ready_root.is_dir():
        for bundle_dir in sorted(ready_root.iterdir()):
            if not bundle_dir.is_dir() or bundle_dir.name.startswith("."):
                continue
            metadata_path = bundle_dir / "source" / "metadata.json"
            labels = _metadata_script_labels(metadata_path) if metadata_path.is_file() else [bundle_dir.name]
            spec_path = bundle_dir / "source" / REQUEST_SPEC_FILENAME
            if spec_path.is_file():
                candidate = _candidate_from_path(
                    spec_path,
                    source="ready-to-import",
                    bundle_label=labels[0] if labels else bundle_dir.name,
                )
                if candidate is not None:
                    version = _version_from_labels(bundle_dir.name, *labels)
                    add(
                        RequestSpecCandidate(
                            path=candidate.path,
                            stem=candidate.stem or _stem_from_labels(bundle_dir.name, *labels),
                            version=max(candidate.version, version),
                            source=candidate.source,
                            mtime=candidate.mtime,
                            protocol_name=candidate.protocol_name,
                        )
                    )

    if build_root.is_dir():
        for generation_dir in sorted(build_root.iterdir()):
            if not generation_dir.is_dir():
                continue
            add(
                _candidate_from_path(
                    generation_dir / REQUEST_SPEC_FILENAME,
                    source="build-generations",
                    bundle_label=generation_dir.name,
                )
            )

    if temp_root.is_dir():
        for generations_dir in sorted(temp_root.glob(f"*/{TEMP_FILES_DIRNAME}/build/generations")):
            for generation_dir in sorted(generations_dir.iterdir()):
                if not generation_dir.is_dir():
                    continue
                add(
                    _candidate_from_path(
                        generation_dir / REQUEST_SPEC_FILENAME,
                        source="project-temp-generations",
                        bundle_label=generation_dir.name,
                    )
                )

    return candidates


def _candidate_matches(
    candidate: RequestSpecCandidate,
    *,
    stem: str | None,
    protocol_name: str | None,
    context_name: str | None,
) -> bool:
    wanted_stems: set[str] = set()
    if stem:
        base, _ = split_version_suffix(stem)
        wanted_stems.add(normalize_protocol_stem(base or stem))
    if protocol_name:
        base, _ = split_version_suffix(protocol_name)
        wanted_stems.add(normalize_protocol_stem(base or protocol_name))
    if wanted_stems:
        candidate_base, _ = split_version_suffix(candidate.stem)
        return normalize_protocol_stem(candidate_base or candidate.stem) in wanted_stems
    if context_name:
        return normalize_protocol_stem(context_name) in candidate.stem
    return False


def _pick_latest(candidates: list[RequestSpecCandidate]) -> RequestSpecCandidate | None:
    if not candidates:
        return None

    def sort_key(candidate: RequestSpecCandidate) -> tuple[int, int, float]:
        return (candidate.version, _source_priority(candidate.source), candidate.mtime)

    return max(candidates, key=sort_key)


def resolve_latest_request_spec(
    *,
    stem: str | None = None,
    protocol_name: str | None = None,
    context_name: str | None = None,
    candidates: list[RequestSpecCandidate] | None = None,
) -> Path | None:
    """Return the newest matching request.spec.yaml, if any."""
    pool = candidates if candidates is not None else enumerate_request_spec_candidates()
    matched = [
        candidate
        for candidate in pool
        if _candidate_matches(
            candidate,
            stem=stem,
            protocol_name=protocol_name,
            context_name=context_name,
        )
    ]
    latest = _pick_latest(matched)
    return latest.path if latest is not None else None


def bundle_dir_for_request_spec(spec_path: Path) -> Path | None:
    """Return the ready-to-import bundle root when ``spec_path`` lives under one."""
    path = spec_path.resolve()
    parts = [part.casefold() for part in path.parts]
    if "ready-to-import" not in parts:
        return None
    if path.parent.name.casefold() != "source":
        return None
    bundle_dir = path.parent.parent
    return bundle_dir if bundle_dir.is_dir() else None


def _attach_source_bundle_dir(info: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    bundle_dir = bundle_dir_for_request_spec(path)
    if bundle_dir is not None:
        info["source_bundle_dir"] = str(bundle_dir)


def _path_is_versioned_spec_root(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    for index, part in enumerate(parts):
        if part == "generations" and index > 0 and parts[index - 1] == "build":
            return True
        if part == "ready-to-import" and index + 1 < len(parts):
            return True
    return False


def _stem_for_path(path: Path) -> str | None:
    candidate = _candidate_from_path(path, source="explicit")
    if candidate is None:
        return None
    return candidate.stem


def resolve_request_spec_path(
    spec: str | Path | None,
    *,
    protocol_name: str | None = None,
    context_name: str | None = None,
    pin: bool = False,
    candidates: list[RequestSpecCandidate] | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    """Resolve a CLI ``--spec`` value to a concrete request.spec.yaml path.

  Returns ``(path, info)`` where ``info`` describes whether resolution upgraded
  to a newer spec. Explicit paths are pinned when ``pin=True`` or when the path
  is outside known versioned roots. Versioned roots auto-upgrade to the newest
  matching spec unless pinned.
    """
    pool = candidates if candidates is not None else enumerate_request_spec_candidates()
    info: dict[str, Any] = {
        "requested": str(spec) if spec is not None else None,
        "resolved": None,
        "pinned": bool(pin),
        "upgraded": False,
        "reason": "none",
    }

    if spec is None:
        latest = resolve_latest_request_spec(
            protocol_name=protocol_name,
            context_name=context_name,
            candidates=pool,
        )
        if latest is not None:
            info.update({"resolved": str(latest), "reason": "default_latest"})
        _attach_source_bundle_dir(info, latest)
        return latest, info

    if is_latest_alias(spec):
        stem = parse_latest_alias(spec)
        latest = resolve_latest_request_spec(
            stem=stem,
            protocol_name=protocol_name if not stem else None,
            context_name=context_name if not stem else None,
            candidates=pool,
        )
        if latest is None:
            raise FileNotFoundError(
                "could not resolve a latest request.spec.yaml"
                + (f" for stem `{stem}`" if stem else "")
            )
        info.update({"resolved": str(latest), "reason": "latest_alias"})
        _attach_source_bundle_dir(info, latest)
        return latest, info

    resolved = resolve_user_path(spec)
    if pin or not _path_is_versioned_spec_root(resolved):
        if not resolved.is_file():
            raise FileNotFoundError(f"request spec not found: {resolved}")
        info.update({"resolved": str(resolved), "reason": "pinned" if pin else "explicit_path"})
        if not pin:
            _attach_source_bundle_dir(info, resolved)
        return resolved, info

    if not resolved.is_file():
        raise FileNotFoundError(f"request spec not found: {resolved}")

    stem = _stem_for_path(resolved)
    latest = resolve_latest_request_spec(
        stem=stem,
        protocol_name=protocol_name,
        context_name=context_name,
        candidates=pool,
    )
    if latest is None:
        info.update({"resolved": str(resolved), "reason": "versioned_no_match"})
        _attach_source_bundle_dir(info, resolved)
        return resolved, info
    latest_path = latest.resolve()
    if latest_path != resolved.resolve():
        info.update(
            {
                "resolved": str(latest_path),
                "upgraded": True,
                "reason": "versioned_auto_latest",
                "previous": str(resolved),
            }
        )
        _attach_source_bundle_dir(info, latest_path)
        return latest_path, info
    info.update({"resolved": str(resolved), "reason": "versioned_already_latest"})
    _attach_source_bundle_dir(info, resolved)
    return resolved, info


def enumerate_ready_bundle_dirs_for_stem(
    stem: str,
    *,
    ready_to_import_dir: Path | None = None,
) -> list[Path]:
    """Return ready-to-import bundle roots for a protocol stem, highest version first."""
    root = (ready_to_import_dir or READY_TO_IMPORT_DIR).resolve()
    base_stem = normalize_protocol_stem(split_version_suffix(str(stem or ""))[0] or str(stem or ""))
    if not base_stem:
        return []
    matches: list[tuple[int, float, Path]] = []
    if not root.is_dir():
        return []
    for bundle_dir in root.iterdir():
        if not bundle_dir.is_dir():
            continue
        bundle_base = normalize_protocol_stem(split_version_suffix(bundle_dir.name)[0] or bundle_dir.name)
        if bundle_base != base_stem:
            continue
        _, version = split_version_suffix(bundle_dir.name)
        try:
            mtime = bundle_dir.stat().st_mtime
        except OSError:
            mtime = 0.0
        matches.append((version or 0, mtime, bundle_dir.resolve()))
    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [path for _, _, path in matches]


def ready_to_import_script_names(ready_to_import_dir: Path | None = None) -> set[str]:
    """Collect FluentControl script names recorded in ready-to-import bundle metadata."""
    root = (ready_to_import_dir or READY_TO_IMPORT_DIR).resolve()
    names: set[str] = set()
    if not root.is_dir():
        return names
    for bundle_dir in root.iterdir():
        if not bundle_dir.is_dir():
            continue
        metadata_path = bundle_dir / "source" / "metadata.json"
        if not metadata_path.is_file():
            continue
        for label in _metadata_script_labels(metadata_path):
            names.add(label)
            base, version = split_version_suffix(label)
            if base:
                names.add(base)
            if version is not None:
                names.add(f"{base}_v{version}")
    return names
