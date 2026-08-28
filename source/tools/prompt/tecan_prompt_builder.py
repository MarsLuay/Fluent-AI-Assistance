"""Compatibility entrypoint for the interactive Tecan prompt builder."""

from __future__ import annotations

try:
    from tecan_tools.prompt.tecan_prompt_builder_app import CancelRequested, main
except ImportError:
    import importlib.util
    import sys
    import os
    from pathlib import Path

    # We are run without site-packages via -S.
    repo_root = Path(__file__).resolve().parents[3]
    app_path = repo_root / "source" / "tools" / "prompt" / "tecan_prompt_builder_app.py"

    spec = importlib.util.spec_from_file_location("tecan_tools.prompt.tecan_prompt_builder_app", app_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load tecan_prompt_builder_app from {app_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["tecan_tools.prompt.tecan_prompt_builder_app"] = module
    spec.loader.exec_module(module)

    CancelRequested = module.CancelRequested
    main = module.main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, CancelRequested):
        print("\nCancelled.")
        raise SystemExit(130)
