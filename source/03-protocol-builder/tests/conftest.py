"""Register sibling ``source/tools`` as the ``tecan_tools`` package for pytest.

Installed editable layout maps ``tecan_tools`` → ``source/tools`` via setuptools
``package-dir``. Offline/analyzer runs need the same mapping without mutating
``sys.path`` (forbidden by architecture import-path tests).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[2] / "tools"
if _TOOLS.is_dir() and "tecan_tools" not in sys.modules:
    init_path = _TOOLS / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "tecan_tools",
        init_path,
        submodule_search_locations=[str(_TOOLS)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load tecan_tools from {init_path}")
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["tecan_tools"] = pkg
    spec.loader.exec_module(pkg)
