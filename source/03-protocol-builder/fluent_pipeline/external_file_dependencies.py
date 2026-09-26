"""Audit external file paths referenced by generated scripts and subroutines.

FluentControl methods declare ``FileReference`` / ``FileRef`` paths and
``ExecuteApplication`` targets (for example ``C:\\TubeEye\\bin\\TEyeClient.exe``).
On import, FluentControl prompts when those paths are missing or not writable.
This module collects required paths from the generated artifact tree, checks
whether each path exists locally, and searches common user folders plus the
imported ZEIA ``fs/`` payload for installable copies.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

from .exports import _script_file_references
from .subroutine_dependencies import clean_subroutine_reference, resolve_subroutine_dependencies

_FILE_TAG_RE = re.compile(r"<File>(.*?)</File>", re.DOTALL | re.IGNORECASE)
_APPLICATION_TAG_RE = re.compile(r"<Application>(.*?)</Application>", re.DOTALL | re.IGNORECASE)
_FILE_REF_TAG_RE = re.compile(r"<FileRef>(.*?)</FileRef>", re.DOTALL | re.IGNORECASE)
# ExecuteVbScriptStatement stores its compiled-at-runtime .vb path in <VbScript>
# (typically a quoted literal). Audit/stage these like other external files so the
# barcode/helper VB scripts ship in the bundle and the operator install checklist
# lists them.
_VBSCRIPT_TAG_RE = re.compile(r"<VbScript>(.*?)</VbScript>", re.DOTALL | re.IGNORECASE)

# Executable/script extensions worth staging for operator install.
_EXTERNAL_FILE_SUFFIXES = frozenset(
    {".exe", ".bat", ".cmd", ".vb", ".vbs", ".py", ".ps1", ".com", ".dll", ".prf", ".csv", ".jpg", ".png", ".gif"}
)


def _normalize_path_text(value: str) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    text = text.replace("/", "\\")
    return os.path.expandvars(text)


def _looks_like_windows_path(value: str) -> bool:
    text = _normalize_path_text(value)
    if not text or text.startswith("$"):
        return False
    if re.match(r"^[A-Za-z]:\\", text):
        return True
    return text.startswith("\\\\")


def _extract_paths_from_xscr_text(text: str) -> list[str]:
    paths: list[str] = []
    for pattern in (_FILE_TAG_RE, _APPLICATION_TAG_RE, _FILE_REF_TAG_RE, _VBSCRIPT_TAG_RE):
        for match in pattern.finditer(text):
            candidate = _normalize_path_text(match.group(1))
            if _looks_like_windows_path(candidate):
                paths.append(candidate)
    return paths


def _extract_paths_from_xscr(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        return _extract_paths_from_xscr_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in paths:
        text = _normalize_path_text(raw)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def default_external_file_search_roots(*, extra_roots: list[Path] | None = None) -> list[Path]:
    """Common writable locations where operators stash FluentControl externals."""
    roots: list[Path] = []
    home = Path.home()
    for name in ("Documents", "Downloads", "Desktop"):
        candidate = home / name
        if candidate.is_dir():
            roots.append(candidate.resolve())
    for candidate in sorted(home.glob("OneDrive*")):
        docs = candidate / "Documents"
        if docs.is_dir():
            roots.append(docs.resolve())
    if extra_roots:
        for item in extra_roots:
            if item.is_dir():
                roots.append(item.resolve())
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _zeia_fs_search_roots(context_root: Path | None) -> list[Path]:
    if context_root is None:
        return []
    extracted = context_root / "extracted" / "fs"
    if not extracted.is_dir():
        return []
    return [extracted.resolve()]


def _search_by_basename(name: str, roots: list[Path], *, limit: int = 8) -> list[str]:
    if not name:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for root in roots:
        try:
            for path in root.rglob(name):
                if not path.is_file():
                    continue
                resolved = str(path.resolve())
                key = resolved.casefold()
                if key in seen:
                    continue
                seen.add(key)
                hits.append(resolved)
                if len(hits) >= limit:
                    return hits
        except OSError:
            continue
    return hits


def _expected_basename(expected: str) -> str:
    if _looks_like_windows_path(expected):
        return PureWindowsPath(expected).name
    return Path(expected).name


def _expected_parent(expected: str) -> str:
    if _looks_like_windows_path(expected):
        return str(PureWindowsPath(expected).parent)
    return str(Path(expected).parent)


def _expected_exists(expected: str) -> bool:
    if _looks_like_windows_path(expected) and os.name != "nt":
        return False
    return Path(expected).is_file()


def _permission_sensitive_path(expected: str) -> bool:
    """True when the expected path is outside the user profile (often needs elevation)."""
    text = _normalize_path_text(expected)
    if not re.match(r"^[A-Za-z]:\\", text):
        return False
    home = os.environ.get("USERPROFILE") or str(Path.home().resolve())
    home = _normalize_path_text(home).casefold()
    return not text.casefold().startswith(home)


def _subroutine_xscr_paths(
    protocol_ir: Mapping[str, Any] | None,
    source_manifest: Mapping[str, Any] | None,
) -> list[Path]:
    if not isinstance(protocol_ir, dict) or not isinstance(source_manifest, dict):
        return []
    resolution = resolve_subroutine_dependencies(protocol_ir, source_manifest)
    paths: list[Path] = []
    for record in resolution.get("resolved") or []:
        if not isinstance(record, dict):
            continue
        raw = record.get("resolved_path") or record.get("path") or record.get("extracted_path")
        if not raw:
            continue
        path = Path(str(raw))
        if path.is_file():
            paths.append(path.resolve())
    return paths


def _collect_required_paths(
    *,
    compiled_xscr: Path | None,
    protocol_ir: Mapping[str, Any] | None,
    source_manifest: Mapping[str, Any] | None,
    extra_script_paths: list[Path] | None = None,
) -> list[str]:
    script_paths: list[Path] = []
    if compiled_xscr is not None and compiled_xscr.is_file():
        script_paths.append(compiled_xscr.resolve())
    script_paths.extend(_subroutine_xscr_paths(protocol_ir, source_manifest))
    if extra_script_paths:
        script_paths.extend(path.resolve() for path in extra_script_paths if path.is_file())

    collected: list[str] = []
    seen_scripts: set[str] = set()
    for path in script_paths:
        key = str(path).casefold()
        if key in seen_scripts:
            continue
        seen_scripts.add(key)
        collected.extend(_extract_paths_from_xscr(path))
        collected.extend(_script_file_references(path))

    return _dedupe_paths(collected)


def _entry_for_path(
    expected: str,
    *,
    search_roots: list[Path],
    referenced_by: list[str],
) -> dict[str, Any]:
    exists = _expected_exists(expected)
    basename = _expected_basename(expected)
    candidates = _search_by_basename(basename, search_roots) if not exists else []
    if exists:
        resolved = str(Path(expected).resolve())
        if resolved not in candidates:
            candidates = [resolved, *candidates]

    permission_sensitive = _permission_sensitive_path(expected)
    remediation: list[str] = []
    if not exists:
        if candidates:
            best = candidates[0]
            remediation.append(
                f"Copy `{best}` to `{expected}` (create `{_expected_parent(expected)}` first)."
            )
            if permission_sensitive:
                remediation.append(
                    "The script hard-codes a path under `C:\\` or Program Files; creating "
                    "that folder usually requires **Run as administrator** or IT help."
                )
                remediation.append(
                    "FluentControl import may let you browse to a file once, but runtime "
                    f"`ExecuteApplication` steps still call `{expected}` — install there or "
                    "use a directory junction/symlink from the expected path to your copy."
                )
        else:
            remediation.append(
                f"Install or copy `{basename}` to `{expected}` before running TubeEye/subroutine steps."
            )
            if permission_sensitive:
                remediation.append(
                    "If you lack permission to create the expected folder, ask IT to provision "
                    f"`{_expected_parent(expected)}` or relocate TubeEye per vendor guidance."
                )

    status = "present" if exists else ("found_elsewhere" if candidates else "missing")
    return {
        "expected_path": expected,
        "basename": basename,
        "status": status,
        "exists": exists,
        "permission_sensitive": permission_sensitive,
        "referenced_by": referenced_by,
        "candidate_paths": candidates,
        "remediation": remediation,
    }


def audit_external_file_dependencies(
    *,
    compiled_xscr: Path | None = None,
    protocol_ir: Mapping[str, Any] | None = None,
    source_manifest: Mapping[str, Any] | None = None,
    context_root: Path | str | None = None,
    extra_script_paths: list[Path] | None = None,
    search_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """Return a structured audit of external file dependencies for a generated script."""
    ctx = Path(str(context_root)).expanduser() if context_root else None
    roots = list(search_roots or [])
    roots.extend(default_external_file_search_roots())
    roots.extend(_zeia_fs_search_roots(ctx))
    # De-dupe search roots
    deduped_roots: list[Path] = []
    seen_roots: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        deduped_roots.append(root)

    required = _collect_required_paths(
        compiled_xscr=compiled_xscr,
        protocol_ir=protocol_ir,
        source_manifest=source_manifest,
        extra_script_paths=extra_script_paths,
    )

    # Map path -> script labels that reference it (for operator context).
    ref_map: dict[str, list[str]] = {path: [] for path in required}
    script_paths: list[tuple[str, Path]] = []
    if compiled_xscr is not None and compiled_xscr.is_file():
        script_paths.append(("generated script", compiled_xscr.resolve()))
    if isinstance(protocol_ir, dict) and isinstance(source_manifest, dict):
        resolution = resolve_subroutine_dependencies(protocol_ir, source_manifest)
        for record in resolution.get("resolved") or []:
            if not isinstance(record, dict):
                continue
            raw = record.get("resolved_path") or record.get("path") or record.get("extracted_path")
            ref = clean_subroutine_reference(record.get("ref"))
            if raw and ref:
                script_paths.append((ref, Path(str(raw)).resolve()))

    for label, path in script_paths:
        if not path.is_file():
            continue
        for item in _dedupe_paths(_extract_paths_from_xscr(path) + _script_file_references(path)):
            ref_map.setdefault(item, [])
            if label not in ref_map[item]:
                ref_map[item].append(label)

    entries = [
        _entry_for_path(path, search_roots=deduped_roots, referenced_by=ref_map.get(path) or [])
        for path in required
    ]
    missing = [item for item in entries if item["status"] == "missing"]
    found_elsewhere = [item for item in entries if item["status"] == "found_elsewhere"]
    declared_path_missing = [item for item in entries if not item.get("exists")]

    return {
        "schema_version": "tecan.external_file_dependencies.v1",
        "entry_count": len(entries),
        "declared_path_missing_count": len(declared_path_missing),
        "missing_count": len(missing),
        "found_elsewhere_count": len(found_elsewhere),
        "search_roots": [str(root) for root in deduped_roots],
        "entries": entries,
    }


def render_external_file_dependencies_markdown(report: Mapping[str, Any]) -> str:
    """Operator-facing checklist for external file installs."""
    lines = [
        "# External file dependencies",
        "",
        "Generated scripts and called subroutines reference files outside the ZEIA "
        "(executables, VB helpers, images, TubeEye client, etc.). FluentControl may "
        "block import or show a **missing referenced files** dialog when these paths "
        "do not exist on the instrument PC.",
        "",
        f"- Required paths scanned: `{report.get('entry_count', 0)}`",
        f"- Missing at script-declared path: `{report.get('declared_path_missing_count', report.get('missing_count', 0))}`",
        f"- Missing with no candidate found: `{report.get('missing_count', 0)}`",
        f"- Found under Documents/Downloads/OneDrive/ZEIA fs: `{report.get('found_elsewhere_count', 0)}`",
        "",
    ]
    search_roots = report.get("search_roots") or []
    if search_roots:
        lines.append("## Search locations")
        lines.append("")
        for root in search_roots[:12]:
            lines.append(f"- `{root}`")
        if len(search_roots) > 12:
            lines.append(f"- `{len(search_roots) - 12}` additional roots recorded in JSON.")
        lines.append("")

    entries = report.get("entries") or []
    actionable = [item for item in entries if item.get("status") != "present"]
    if not actionable:
        lines.append("All scanned external file paths exist at their script-declared locations.")
        return "\n".join(lines).rstrip() + "\n"

    lines.append("## Action required")
    lines.append("")
    for item in actionable:
        status = item.get("status")
        expected = item.get("expected_path")
        lines.append(f"### `{expected}` ({status})")
        refs = item.get("referenced_by") or []
        if refs:
            lines.append(f"- Referenced by: {', '.join(f'`{ref}`' for ref in refs)}")
        if item.get("permission_sensitive"):
            lines.append("- Expected path is outside your user profile (may require Administrator/IT).")
        for candidate in item.get("candidate_paths") or []:
            lines.append(f"- Found copy: `{candidate}`")
        for step in item.get("remediation") or []:
            lines.append(f"- {step}")
        lines.append("")

    lines.append(
        "After copying files, re-import the generated ZEIA or open the script in Script Editor "
        "and confirm the missing-file prompt clears."
    )
    return "\n".join(lines).rstrip() + "\n"


def write_external_file_dependency_artifacts(
    report: Mapping[str, Any],
    *,
    out_dir: Path,
) -> dict[str, Path]:
    """Write markdown + JSON reports under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "external_file_dependencies.json"
    md_path = out_dir / "external_file_dependencies.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_external_file_dependencies_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def stage_found_external_files(
    report: Mapping[str, Any],
    *,
    bundle_source_dir: Path,
) -> list[dict[str, str]]:
    """Copy installable external files into ``source/external-files/`` for handoff.

    Stage both files found away from their declared path and files already
    present at the declared path. The latter keeps bundles portable to the
    instrument PC: the setup BAT can provision the same hard-coded path there.
    """
    staged: list[dict[str, str]] = []
    dest_root = bundle_source_dir / "external-files"
    for item in report.get("entries") or []:
        if item.get("status") not in {"found_elsewhere", "present"}:
            continue
        candidates = item.get("candidate_paths") or []
        expected = str(item.get("expected_path") or "")
        if not candidates or not expected:
            continue
        source = Path(candidates[0])
        if not source.is_file():
            continue
        # Preserve bin/data leaf folders when expected path uses them (TubeEye layout).
        expected_path = PureWindowsPath(expected) if _looks_like_windows_path(expected) else Path(expected)
        parts = expected_path.parts
        if len(parts) >= 2 and parts[1].casefold() in {"tubeye", "tecaninfomation", "programdata"}:
            relative = Path(*parts[2:]) if len(parts) > 2 else Path(source.name)
        else:
            relative = Path(source.name)
        dest = dest_root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            continue
        try:
            dest.write_bytes(source.read_bytes())
        except OSError:
            continue
        staged.append(
            {
                "expected_path": expected,
                "staged_path": str(dest),
                "source_path": str(source),
            }
        )
    return staged


def write_external_file_install_bat(
    staged: list[dict[str, str]],
    *,
    bundle_source_dir: Path,
    bat_name: str = "install_external_files.bat",
) -> Path | None:
    """Write a root BAT that installs staged external files to script paths."""
    if not staged:
        return None
    bundle_root = bundle_source_dir.parent
    bat_path = bundle_root / bat_name
    lines = [
        "@echo off",
        "setlocal",
        "set \"ROOT=%~dp0\"",
        "echo Installing external files required by this FluentControl script.",
        "echo If copy fails under C:\\ or Program Files, right-click this BAT and Run as administrator.",
        "",
    ]
    for item in staged:
        expected = str(item.get("expected_path") or "")
        staged_path = Path(str(item.get("staged_path") or ""))
        if not expected or not staged_path:
            continue
        try:
            source = str(staged_path.relative_to(bundle_root)).replace("/", "\\")
            source_expr = f"%ROOT%{source}"
        except ValueError:
            source_expr = str(staged_path)
        expected_parent = _expected_parent(expected)
        lines.extend(
            [
                f"echo.",
                f"echo {expected}",
                f"if not exist \"{expected_parent}\" mkdir \"{expected_parent}\"",
                f"copy /Y \"{source_expr}\" \"{expected}\"",
                "if errorlevel 1 (",
                f"  echo FAILED to copy {expected}",
                "  exit /b 1",
                ")",
            ]
        )
    lines.extend(
        [
            "",
            "echo.",
            "echo External file install complete.",
            "exit /b 0",
            "",
        ]
    )
    bat_path.write_text("\r\n".join(lines), encoding="utf-8")
    return bat_path
