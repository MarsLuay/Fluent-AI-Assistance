"""Project-wide default catalog names per labware class.

Authors can register defaults so common classes like ``Plate96`` do not need
an explicit ``catalog=`` on every construction when the catalog index is built.
Omitting defaults preserves the strict ``catalog=`` requirement.
"""

from __future__ import annotations

_CATALOG_DEFAULTS: dict[type, str] = {}


def set_catalog_default(labware_class: type, catalog_name: str) -> None:
    """Register one default FluentControl catalog name for a labware class."""
    _CATALOG_DEFAULTS[labware_class] = catalog_name


def set_catalog_defaults(mapping: dict[type, str]) -> None:
    """Register multiple class → catalog defaults at once."""
    _CATALOG_DEFAULTS.update(mapping)


def get_catalog_default(labware_class: type) -> str | None:
    """Return the registered default catalog name for ``labware_class``, if any."""
    for base in labware_class.__mro__:
        if base in _CATALOG_DEFAULTS:
            return _CATALOG_DEFAULTS[base]
    return None


def clear_catalog_defaults() -> None:
    """Remove all registered defaults (mainly for tests)."""
    _CATALOG_DEFAULTS.clear()
