"""Project-local fluentcoder catalog helpers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .config import CATALOG_CACHE_DIR
from .project_context import ProjectLike
from .runner import PipelineError, run_python


CATALOG_BUILD_DIR = ".fluentcoder_catalog"
CATALOG_SIDECAR_NAME = "catalog_inputs.sidecar.json"
CATALOG_SIDECAR_SCHEMA = "tecan.catalog_inputs_sidecar.v1"
_HASH_CHUNK = 1024 * 1024


def project_datastore_dir(context: ProjectLike | None) -> Path | None:
    """Return the normalized DataStore root for a project context, if present."""
    if context is None or getattr(context, "manifest", {}).get("kind") == "project_collection":
        return None
    candidate = context.extracted_dir / "DataStore"
    required = [
        candidate / "SystemSpecific" / "Worktable" / "Components",
        candidate / "SystemSpecific" / "Worktable" / "Workspaces",
    ]
    if all(path.exists() and path.is_dir() for path in required):
        return candidate
    return None


def project_catalog_db_path(context: ProjectLike) -> Path:
    return context.build_dir / CATALOG_BUILD_DIR / "install_index.db"


def ensure_project_catalog(
    context: ProjectLike | None,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path | None:
    """Build and return a project-local fluentcoder catalog DB when possible.

    The build is content-addressed: the catalog source files are hashed and a
    matching DB is reused from a shared cache (a fast copy) instead of being
    rebuilt from scratch. Only a genuinely new set of catalog inputs triggers the
    multi-minute fluentcoder rebuild, after which the result is cached for reuse by
    re-imports and other contexts with identical worktable inputs.
    """
    emit = progress or (lambda _message: None)
    datastore = project_datastore_dir(context)
    if datastore is None or context is None:
        return None

    db_path = project_catalog_db_path(context)
    if _catalog_is_fresh(datastore, db_path):
        emit("Catalog index already fresh for this context; reusing it.")
        return db_path

    db_path.parent.mkdir(parents=True, exist_ok=True)

    catalog_hash = _resolve_catalog_hash(datastore, db_path.parent)
    cached_db = _shared_cache_db_path(catalog_hash) if catalog_hash else None
    if cached_db is not None and cached_db.exists():
        emit(f"Reusing cached catalog index (key {catalog_hash[:12]}).")
        _copy_db(cached_db, db_path)
        _mark_fresh(db_path)
        return db_path

    emit("Building fluentcoder catalog index (no cached match); this can take minutes.")
    result = run_python(
        [
            "-m",
            "fluentcoder.cli",
            "catalog",
            "refresh",
            "--install",
            datastore,
            "--db",
            db_path,
        ],
        timeout=600,
    )
    if not result.ok:
        raise PipelineError(
            "failed to build project-local fluentcoder catalog index:\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}".strip()
        )
    _mark_fresh(db_path)
    if cached_db is not None:
        _store_in_shared_cache(db_path, cached_db)
        emit(f"Cached catalog index for reuse (key {catalog_hash[:12]}).")
    if catalog_hash is not None:
        fingerprint = _catalog_source_fingerprint(datastore)
        if fingerprint is not None:
            _write_catalog_sidecar(db_path.parent, catalog_hash=catalog_hash, source_fingerprint=fingerprint)
    return db_path


def _catalog_sidecar_path(db_dir: Path) -> Path:
    return db_dir / CATALOG_SIDECAR_NAME


def _catalog_source_fingerprint(datastore: Path) -> str | None:
    """Return a cheap stat-only fingerprint for catalog source files.

    Re-importing the same ZEIA resets mtimes, which makes the per-context DB
    look stale even though the catalog inputs are byte-identical. This fingerprint
    uses relative path + size only (no content reads) so we can detect unchanged
    inputs in milliseconds instead of re-hashing ~280 MB of catalog XML.
    """
    files = sorted(_catalog_source_files(datastore), key=lambda p: p.as_posix())
    if not files:
        return None
    digest = hashlib.sha256()
    digest.update(b"tecan.catalog_fingerprint.v1")
    for path in files:
        try:
            rel = path.relative_to(datastore).as_posix()
        except ValueError:
            rel = path.name
        try:
            size = path.stat().st_size
        except OSError:
            return None
        digest.update(rel.encode("utf-8"))
        digest.update(str(size).encode("utf-8"))
    return digest.hexdigest()


def _load_catalog_sidecar(db_dir: Path) -> dict[str, Any] | None:
    path = _catalog_sidecar_path(db_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != CATALOG_SIDECAR_SCHEMA:
        return None
    return payload


def _write_catalog_sidecar(db_dir: Path, *, catalog_hash: str, source_fingerprint: str) -> None:
    """Persist the last resolved catalog hash + cheap source fingerprint."""
    path = _catalog_sidecar_path(db_dir)
    tmp: Path | None = None
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(
            json.dumps(
                {
                    "schema": CATALOG_SIDECAR_SCHEMA,
                    "catalog_hash": catalog_hash,
                    "source_fingerprint": source_fingerprint,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        try:
            if tmp is not None and tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _resolve_catalog_hash(datastore: Path, db_dir: Path) -> str | None:
    """Resolve the catalog content hash, skipping full reads when the sidecar matches."""
    fingerprint = _catalog_source_fingerprint(datastore)
    if fingerprint is None:
        return None

    sidecar = _load_catalog_sidecar(db_dir)
    if sidecar is not None and sidecar.get("source_fingerprint") == fingerprint:
        cached_hash = str(sidecar.get("catalog_hash") or "")
        if cached_hash and _shared_cache_db_path(cached_hash).exists():
            return cached_hash

    catalog_hash = _catalog_content_hash(datastore)
    if catalog_hash is not None:
        _write_catalog_sidecar(db_dir, catalog_hash=catalog_hash, source_fingerprint=fingerprint)
    return catalog_hash


def _catalog_is_fresh(datastore: Path, db_path: Path) -> bool:
    if not db_path.exists():
        return False
    db_mtime = db_path.stat().st_mtime
    for path in _catalog_source_files(datastore):
        try:
            if path.stat().st_mtime > db_mtime:
                return False
        except FileNotFoundError:
            return False
    return True


def _catalog_source_files(datastore: Path) -> list[Path]:
    patterns = [
        "SystemSpecific/Worktable/Components/*.xcmp",
        "SystemSpecific/Worktable/Workspaces/*.xwsp",
        "SystemSpecific/Worktable/Sites/*.xsit",
        "SystemSpecific/LiquidClasses/*.xlqc",
    ]
    out: list[Path] = []
    for pattern in patterns:
        out.extend(datastore.glob(pattern))
    return out


def _catalog_content_hash(datastore: Path) -> str | None:
    """Hash the catalog inputs by content so identical inputs share a cache key.

    Uses relative path + size + file content. This is independent of mtimes, so
    re-extracting the same ZEIA (which resets mtimes) produces the same key.
    """
    files = sorted(_catalog_source_files(datastore), key=lambda p: p.as_posix())
    if not files:
        return None
    digest = hashlib.sha256()
    digest.update(b"tecan.catalog.v1")
    for path in files:
        try:
            rel = path.relative_to(datastore).as_posix()
        except ValueError:
            rel = path.name
        try:
            size = path.stat().st_size
        except OSError:
            return None
        digest.update(rel.encode("utf-8"))
        digest.update(str(size).encode("utf-8"))
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
                    digest.update(chunk)
        except OSError:
            return None
    return digest.hexdigest()


def _shared_cache_db_path(catalog_hash: str) -> Path:
    return CATALOG_CACHE_DIR / catalog_hash / "install_index.db"


def _copy_db(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _store_in_shared_cache(db_path: Path, cached_db: Path) -> None:
    """Copy a freshly built DB into the shared cache atomically. Best effort."""
    try:
        cached_db.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached_db.with_suffix(cached_db.suffix + f".tmp-{os.getpid()}")
        shutil.copy2(db_path, tmp)
        os.replace(tmp, cached_db)
    except OSError:
        # A cache write failure must never break generation; the per-context DB
        # already exists and is valid.
        try:
            if tmp.exists():
                tmp.unlink()
        except (OSError, NameError, UnboundLocalError):
            pass


def _mark_fresh(db_path: Path) -> None:
    """Bump the DB mtime above the (freshly extracted) source mtimes.

    A cached DB copied in via copy2 keeps the original build time, which is older
    than the just-extracted catalog sources and would otherwise read as stale.
    """
    try:
        now = time.time()
        os.utime(db_path, (now, now))
    except OSError:
        pass
