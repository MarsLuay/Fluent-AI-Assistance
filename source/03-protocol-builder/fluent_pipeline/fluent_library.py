"""Read-only access to scripts saved in the local FluentControl datastore."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from .runner import PipelineError, ensure_parent


GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def local_fluent_database_root(database: str | Path | None = None) -> Path:
    """Return the local FluentControl database root."""
    if database is not None:
        return Path(database).expanduser().resolve()
    configured = os.environ.get("TECAN_FLUENT_DATABASE")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Tecan" / "VisionX" / "DataBase"


def list_local_fluent_scripts(database: str | Path | None = None) -> list[dict[str, Any]]:
    """List script records stored in the local FluentControl database.

    This scans only files on disk and never opens FluentControl or modifies the
    datastore.
    """
    root = local_fluent_database_root(database)
    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for base in (root / "UserSpecific", root / "SystemSpecific"):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.xscr")):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                stat = path.stat()
            except OSError:
                continue
            object_name = _first_xml_text(text, "ObjectName")
            if not object_name:
                continue
            records.append(
                {
                    "object_name": object_name,
                    "object_path": _first_xml_text(text, "ObjectSubfolderPath"),
                    "guid": path.stem if GUID_RE.fullmatch(path.stem) else "",
                    "path": str(resolved),
                    "database": str(root),
                    "scope": base.name,
                    "modified": stat.st_mtime,
                    "size": stat.st_size,
                }
            )
    return sorted(
        records,
        key=lambda item: (
            str(item.get("object_name") or "").casefold(),
            str(item.get("object_path") or "").casefold(),
            str(item.get("scope") or "").casefold(),
            str(item.get("path") or "").casefold(),
        ),
    )


def resolve_local_fluent_script(
    script: str,
    *,
    folder: str | None = None,
    database: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve one saved FluentControl script by object name/path."""
    query = str(script or "").strip()
    if not query:
        raise PipelineError("--fluent-script requires a script name or path")
    direct = Path(query)
    if direct.exists() and direct.is_file():
        return _record_for_direct_path(direct, database=database)

    records = list_local_fluent_scripts(database)
    query_key = _name_key(query)
    matches = [
        item
        for item in records
        if _name_key(item.get("object_name")) == query_key
        or _name_key(Path(str(item.get("path") or "")).stem) == query_key
    ]
    if folder:
        folder_key = _name_key(folder)
        matches = [item for item in matches if _name_key(item.get("object_path")) == folder_key]
    if not matches:
        hint = _script_match_hint(records, query)
        raise PipelineError(
            f"no saved FluentControl script matched `{query}` in `{local_fluent_database_root(database)}`"
            + (f". {hint}" if hint else "")
        )
    if len(matches) > 1:
        choices = "; ".join(
            f"{item.get('object_path') or '<root>'}\\{item.get('object_name')} ({item.get('scope')}, {item.get('guid') or Path(str(item.get('path'))).name})"
            for item in matches[:8]
        )
        raise PipelineError(
            f"`{query}` matched {len(matches)} saved FluentControl scripts. "
            f"Pass --fluent-folder to disambiguate. Matches: {choices}"
        )
    return dict(matches[0])


def stage_local_fluent_script(record: dict[str, Any], out_dir: Path) -> Path:
    """Copy a resolved local FluentControl script into an analysis folder."""
    source = Path(str(record.get("path") or ""))
    if not source.exists():
        raise PipelineError(f"resolved FluentControl script file is missing: {source}")
    folder = out_dir / "local-fluent-script"
    filename = _safe_filename(
        "_".join(
            part
            for part in (
                str(record.get("object_path") or ""),
                str(record.get("object_name") or source.stem),
                str(record.get("guid") or ""),
            )
            if part
        )
    )
    destination = folder / f"{filename or source.stem}.xscr"
    ensure_parent(destination)
    shutil.copy2(source, destination)
    return destination.resolve()


def _record_for_direct_path(path: Path, *, database: str | Path | None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return {
        "object_name": _first_xml_text(text, "ObjectName") or path.stem,
        "object_path": _first_xml_text(text, "ObjectSubfolderPath"),
        "guid": path.stem if GUID_RE.fullmatch(path.stem) else "",
        "path": str(path.resolve()),
        "database": str(local_fluent_database_root(database)),
        "scope": "direct",
        "modified": path.stat().st_mtime,
        "size": path.stat().st_size,
    }


def _first_xml_text(text: str, tag: str) -> str:
    match = re.search(
        rf"<(?:[A-Za-z0-9_.-]+:)?{re.escape(tag)}(?:\s[^>]*)?>(.*?)</(?:[A-Za-z0-9_.-]+:)?{re.escape(tag)}>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _name_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned[:120]


def _script_match_hint(records: list[dict[str, Any]], query: str) -> str:
    query_key = _name_key(query)
    if not query_key:
        return ""
    partial = [
        item
        for item in records
        if query_key in _name_key(item.get("object_name"))
        or _name_key(item.get("object_name")) in query_key
    ][:8]
    if not partial:
        return ""
    choices = "; ".join(
        f"{item.get('object_path') or '<root>'}\\{item.get('object_name')}" for item in partial
    )
    return f"Closest matches: {choices}"
