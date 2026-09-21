"""Thin adapter for the canonical one-shot workflow."""

from __future__ import annotations

import argparse
import json
import sys

from ...application_services import OneShotRunRequest, run_one_shot
from ...config import resolve_user_path
from ..rendering import progress_callback_from_mode


def _cmd_run(args: argparse.Namespace) -> int:
    callback = progress_callback_from_mode(getattr(args, "progress", "auto"))
    result = run_one_shot(
        OneShotRunRequest(
            input_path=resolve_user_path(args.input),
            request=args.request,
            request_file=resolve_user_path(args.request_file) if args.request_file else None,
            output_directory=resolve_user_path(args.out_dir) if args.out_dir else None,
            protocol_name=args.protocol_name,
            project_name=args.project_name,
            approve_partial_zeia=args.approve_partial_zeia,
            force_import=args.force_import,
            progress_callback=callback,
        )
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str), file=sys.stdout)
    return result.exit_code
