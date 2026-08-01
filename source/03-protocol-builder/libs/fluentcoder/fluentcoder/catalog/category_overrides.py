"""Optional user category overrides for catalog name → category mapping.

Loads ``category_overrides.toml`` from, in order:

1. ``FLUENTCODER_CATEGORY_OVERRIDES`` (explicit file path)
2. ``fluentcoder/catalog/category_overrides.toml`` (next to this module)
3. ``<install>/category_overrides.toml``
4. ``fluentcoder/_assets/config/category_overrides.toml``

File shape — flat map of catalog ``ObjectName`` → category string:

```toml
"My Misclassified Plate" = "plate"
```

Keys are matched case-insensitively against component ``ObjectName`` values.
Overrides apply after built-in inference rules in ``infer_category()``.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from .inference import CATEGORIES

_CATALOG_DIR = Path(__file__).resolve().parent
_PACKAGE_CONFIG_DIR = _CATALOG_DIR.parent / "_assets" / "config"
_VALID_CATEGORIES = frozenset(CATEGORIES)
_OVERRIDE_FILENAMES = ("category_overrides.toml",)


def category_overrides_path_default(install_path: Path | str | None = None) -> Path | None:
    """Return the first existing overrides file path, or None."""
    env = os.environ.get("FLUENTCODER_CATEGORY_OVERRIDES")
    if env:
        path = Path(env)
        return path if path.is_file() else None

    for name in _OVERRIDE_FILENAMES:
        candidate = _CATALOG_DIR / name
        if candidate.is_file():
            return candidate

    if install_path is not None:
        install = Path(install_path)
    else:
        from .indexer import install_path_default

        install = install_path_default()

    for name in _OVERRIDE_FILENAMES:
        candidate = install / name
        if candidate.is_file():
            return candidate

    for name in _OVERRIDE_FILENAMES:
        candidate = _PACKAGE_CONFIG_DIR / name
        if candidate.is_file():
            return candidate

    return None


def load_category_overrides(path: Path | str | None = None) -> dict[str, str]:
    """Load name → category overrides from *path* or the default search path."""
    resolved = Path(path) if path else category_overrides_path_default()
    if resolved is None or not resolved.is_file():
        return {}

    raw = _read_overrides_file(resolved)
    normalized = _normalize_overrides(raw, source=str(resolved))
    return normalized


def get_category_override(name: str, *, install_path: Path | str | None = None) -> str | None:
    """Return the override category for *name*, if any."""
    overrides = _cached_category_overrides(
        str(install_path) if install_path else None,
        os.environ.get("FLUENTCODER_CATEGORY_OVERRIDES", ""),
    )
    return overrides.get(name.strip().lower())


def clear_category_overrides_cache() -> None:
    """Drop cached overrides (for tests)."""
    _cached_category_overrides.cache_clear()


@lru_cache(maxsize=8)
def _cached_category_overrides(install_path: str | None, overrides_env: str) -> dict[str, str]:
    if overrides_env:
        return load_category_overrides(Path(overrides_env))
    if install_path:
        path = category_overrides_path_default(install_path)
    else:
        path = category_overrides_path_default()
    if path is None:
        return {}
    return load_category_overrides(path)


def _read_overrides_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".toml":
        raise ValueError(f"Unsupported category overrides format: {path.suffix}")
    return tomllib.loads(text)


def _normalize_overrides(raw: Any, *, source: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError(f"Category overrides in {source} must be a mapping")

    items = raw.get("overrides", raw) if isinstance(raw.get("overrides"), dict) else raw
    if not isinstance(items, dict):
        raise ValueError(f"Category overrides in {source} must be a mapping")

    normalized: dict[str, str] = {}
    for key, value in items.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(
                f"Category overrides in {source} must map catalog name strings to category strings"
            )
        category = value.strip()
        if category not in _VALID_CATEGORIES:
            raise ValueError(
                f"Unknown category {category!r} for {key!r} in {source}; "
                f"expected one of: {', '.join(CATEGORIES)}"
            )
        normalized[key.strip().lower()] = category
    return normalized
