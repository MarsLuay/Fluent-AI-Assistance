from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_module(path: Path, alias: str | None = None):
    """Load a Python module from disk without creating __pycache__ artifacts."""
    spec = importlib.util.spec_from_file_location(alias or path.stem, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = original
    return module
