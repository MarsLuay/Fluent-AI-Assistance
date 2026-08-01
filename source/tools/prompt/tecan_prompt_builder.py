"""Compatibility entrypoint for the interactive Tecan prompt builder."""

from __future__ import annotations

from tecan_tools.prompt.tecan_prompt_builder_app import CancelRequested, main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, CancelRequested):
        print("\nCancelled.")
        raise SystemExit(130)
