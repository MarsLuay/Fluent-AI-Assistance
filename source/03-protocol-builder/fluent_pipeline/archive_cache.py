"""Content-addressed cache for per-source-ZEIA reference-resolution records.

The base (source) ZEIA is immutable for a given import, but deriving its
datastore reference records during packaging is expensive: building the
node-description map regex-parses the whole ``nodedescription.xml`` and, for
every node, scans all archive entries to find the backing datastore file
(roughly O(entries^2) on a ~15k-entry full export). Packaging redoes this on
every ``generate`` against the same base, which dominates the "minutes in
reference resolution" cost noted in ``AGENTS.md``.

These records are a pure function of the immutable base ZEIA, so this module
caches them on disk keyed by the archive's (size + content) fingerprint. A
repeat generate against the same base loads the JSON records and skips the scan
entirely. The cache is path-independent (byte-identical bases share a key,
mirroring the catalog cache), and every read/write is best-effort: a cache miss,
corrupt entry, or write failure falls back to direct computation and never
blocks packaging.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .config import ZEIA_REFERENCE_CACHE_DIR

# Bump when the shape of a cached record changes so stale entries are ignored.
CACHE_SCHEMA_VERSION = "tecan.zeia_reference_cache.v1"
_HASH_CHUNK = 1024 * 1024


def archive_reference_fingerprint(source_project: Path) -> str | None:
    """Return a stable ``(size + content)`` fingerprint for a source ZEIA.

    Returns ``None`` if the file cannot be read, which callers treat as "do not
    cache" and fall back to direct computation.
    """
    try:
        size = source_project.stat().st_size
    except OSError:
        return None
    digest = hashlib.sha256()
    digest.update(CACHE_SCHEMA_VERSION.encode("utf-8"))
    digest.update(str(size).encode("utf-8"))
    try:
        with source_project.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _cache_file(fingerprint: str, kind: str) -> Path:
    return ZEIA_REFERENCE_CACHE_DIR / fingerprint / f"{kind}.json"


def load_records(fingerprint: str | None, kind: str) -> Any | None:
    """Return cached records of ``kind`` for ``fingerprint``, or ``None``."""
    if not fingerprint:
        return None
    try:
        raw = _cache_file(fingerprint, kind).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA_VERSION:
        return None
    return payload.get("records")


def store_records(fingerprint: str | None, kind: str, records: Any) -> None:
    """Persist records of ``kind`` for ``fingerprint``. Best effort; never raises."""
    if not fingerprint:
        return
    path = _cache_file(fingerprint, kind)
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(
            json.dumps({"schema": CACHE_SCHEMA_VERSION, "kind": kind, "records": records}),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        # A cache write failure must never break packaging; the records were
        # already computed and returned to the caller.
        try:
            if tmp is not None and tmp.exists():
                tmp.unlink()
        except OSError:
            pass
