"""Compatibility entrypoint for the interactive Tecan prompt builder."""

from __future__ import annotations

try:
    from tecan_tools.prompt.tecan_prompt_builder_app import CancelRequested, main
except ImportError:
    import site
    from pathlib import Path

    # We are run without site-packages via -S.
    repo_root = Path(__file__).resolve().parents[3]
    tools_path = repo_root / "source" / "tools"

    site.addsitedir(str(tools_path))

    from prompt.tecan_prompt_builder_app import CancelRequested, main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, CancelRequested):
        print("\nCancelled.")
        raise SystemExit(130)
