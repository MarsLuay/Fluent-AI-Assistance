"""Optional compiled acceleration kernels for tecan-common."""

try:
    from . import _native
except ImportError:  # pragma: no cover - pure-Python installations
    _native = None

__all__ = ["_native"]
