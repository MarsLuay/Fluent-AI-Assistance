"""Compatibility entrypoint for the interactive Tecan prompt builder."""

from __future__ import annotations

import sys
from pathlib import Path

# Add source/tools to path via tecan_tools package structure
repo_root = Path(__file__).resolve().parents[3]
source_path = repo_root / "source"
if str(source_path) not in sys.path:
    sys.path.insert(0, str(source_path))

from tools.prompt.tecan_prompt_builder_app import CancelRequested, main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, CancelRequested):
        print("\nCancelled.")
        raise SystemExit(130)
