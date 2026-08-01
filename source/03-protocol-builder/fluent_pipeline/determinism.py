"""Deterministic-regeneration check.

Regenerating from the same reviewed ``request.spec.yaml`` + IR should yield
byte-identical generated artifacts. This module compares two generation output
directories (or two explicit file maps) after normalizing the few legitimately
volatile values -- wall-clock timestamps and absolute output/source paths -- so
genuine nondeterminism (dict ordering, set iteration, GUID churn, etc.) is
surfaced instead of being masked by timestamps and run-specific paths.

The comparison is intentionally strict: after normalization the artifacts must
match byte-for-byte. Pure telemetry streams whose content is inherently
per-run (e.g. the JSONL event log, whose ``elapsed_ms`` is monotonic timing)
are excluded by name; everything else -- including the protocol IR, Python
draft, GWL, recreate guide, worktable patch, validation diff, and the
generation manifest -- is expected to be reproducible.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

# ISO-8601 timestamps emitted across artifacts (request_spec ``created_at``,
# manifest ``generated_at``, event ``ts``). Matches second- and microsecond
# precision with optional ``Z``/``+00:00`` offset.
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)
_TIMESTAMP_TOKEN = "<TIMESTAMP>"
_ROOT_TOKEN = "<ROOT>"

# Files whose content is inherently per-run telemetry (not generated protocol
# content) and therefore excluded from the strict byte-identity comparison.
DEFAULT_VOLATILE_GLOBS: tuple[str, ...] = ("*.events.jsonl",)


def _root_variants(root: str) -> tuple[str, ...]:
    """All on-disk spellings a path root can take across JSON/YAML/Markdown."""
    root = root.rstrip("\\/")
    if not root:
        return ()
    return (
        root,  # native (Windows backslash or POSIX slash)
        root.replace("\\", "/"),  # POSIX / as_posix()
        root.replace("\\", "\\\\"),  # JSON-escaped Windows backslashes
    )


def normalize_artifact_text(text: str, roots: Iterable[str]) -> str:
    """Blank volatile values so two reproducible artifacts compare equal.

    Replaces every known absolute path root with ``<ROOT>`` (in native, POSIX,
    and JSON-escaped spellings) and every ISO-8601 timestamp with
    ``<TIMESTAMP>``. Longer roots are substituted first so nested paths are
    collapsed before their parents.
    """
    out = text
    variants: list[str] = []
    for root in roots:
        variants.extend(_root_variants(str(root)))
    for variant in sorted(set(variants), key=len, reverse=True):
        out = out.replace(variant, _ROOT_TOKEN)
    return _ISO_TIMESTAMP.sub(_TIMESTAMP_TOKEN, out)


def artifact_digest(path: Path, roots: Iterable[str]) -> str:
    """SHA-256 of an artifact's normalized text content."""
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8", errors="surrogateescape")
    normalized = normalize_artifact_text(text, roots)
    return hashlib.sha256(normalized.encode("utf-8", errors="surrogateescape")).hexdigest()


def compare_artifact_maps(
    files_a: Mapping[str, Path],
    files_b: Mapping[str, Path],
    *,
    roots: Iterable[str] = (),
) -> dict[str, Any]:
    """Compare two logical-name -> path maps after normalization.

    Returns a report dict with ``deterministic`` (bool), the list of
    ``mismatches`` (present in both but differing), and the keys present in only
    one side. Each compared artifact records its normalized digest from both
    runs for actionable diffing.
    """
    roots = list(roots)
    shared = sorted(set(files_a) & set(files_b))
    artifacts: dict[str, Any] = {}
    mismatches: list[str] = []
    for key in shared:
        digest_a = artifact_digest(files_a[key], roots)
        digest_b = artifact_digest(files_b[key], roots)
        match = digest_a == digest_b
        artifacts[key] = {
            "match": match,
            "digest_a": digest_a,
            "digest_b": digest_b,
        }
        if not match:
            mismatches.append(key)
    only_in_first = sorted(set(files_a) - set(files_b))
    only_in_second = sorted(set(files_b) - set(files_a))
    return {
        "deterministic": not mismatches and not only_in_first and not only_in_second,
        "compared_count": len(shared),
        "mismatches": mismatches,
        "only_in_first": only_in_first,
        "only_in_second": only_in_second,
        "artifacts": artifacts,
    }


def _is_volatile(relative: str, volatile_globs: Iterable[str]) -> bool:
    name = Path(relative).name
    return any(Path(name).match(pattern) or Path(relative).match(pattern) for pattern in volatile_globs)


def _collect_files(directory: Path, volatile_globs: Iterable[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        if _is_volatile(relative, volatile_globs):
            continue
        files[relative] = path
    return files


def compare_run_dirs(
    dir_a: Path,
    dir_b: Path,
    *,
    extra_roots: Iterable[str] = (),
    volatile_globs: Iterable[str] = DEFAULT_VOLATILE_GLOBS,
) -> dict[str, Any]:
    """Compare two generation output directories for byte-identical artifacts.

    Every regular file present in both directories (recursively, excluding
    ``volatile_globs``) is compared after normalizing timestamps and the two
    directory roots (plus any ``extra_roots`` such as a shared temp/projects
    root). Files present in only one directory are reported as a determinism
    failure.
    """
    dir_a = Path(dir_a)
    dir_b = Path(dir_b)
    volatile_globs = tuple(volatile_globs)
    files_a = _collect_files(dir_a, volatile_globs)
    files_b = _collect_files(dir_b, volatile_globs)
    roots = [str(dir_a), str(dir_b), *[str(root) for root in extra_roots]]
    report = compare_artifact_maps(files_a, files_b, roots=roots)
    report["dir_a"] = str(dir_a)
    report["dir_b"] = str(dir_b)
    return report


def render_determinism_report(report: Mapping[str, Any]) -> str:
    """Human-readable summary of a determinism comparison."""
    lines: list[str] = []
    status = "DETERMINISTIC" if report.get("deterministic") else "NON-DETERMINISTIC"
    lines.append(f"Determinism check: {status}")
    lines.append(f"Artifacts compared: {report.get('compared_count', 0)}")
    mismatches = report.get("mismatches") or []
    only_a = report.get("only_in_first") or []
    only_b = report.get("only_in_second") or []
    if mismatches:
        lines.append(f"Differing artifacts ({len(mismatches)}):")
        for key in mismatches:
            lines.append(f"  - {key}")
    if only_a:
        lines.append(f"Only in first run ({len(only_a)}):")
        for key in only_a:
            lines.append(f"  - {key}")
    if only_b:
        lines.append(f"Only in second run ({len(only_b)}):")
        for key in only_b:
            lines.append(f"  - {key}")
    if report.get("deterministic"):
        lines.append("All compared artifacts are byte-identical after normalizing timestamps and paths.")
    return "\n".join(lines)
