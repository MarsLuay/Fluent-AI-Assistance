"""Thin CLI adapter for target-aware deployment planning."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ...application_services import DeploymentPlanRequest, plan_deployment
from ...config import resolve_user_path
from ...deployment_plan import render_deployment_plan_markdown
from ...runner import PipelineError
from ...target_datastore import build_target_datastore_profile


def _load_external_files(path: Path | None) -> tuple[dict[str, Any], ...]:
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise PipelineError("--external-files must contain a JSON list of objects")
    return tuple(dict(row) for row in rows)


def _explicit_target(args: argparse.Namespace) -> dict[str, Any] | Path | None:
    if args.target_profile:
        return resolve_user_path(args.target_profile)
    target_fields = (
        args.target_userspecific_dir,
        args.target_systemspecific_dir,
        args.target_software_family,
        args.target_fluentcontrol_version,
        args.target_fluentcontrol_build,
        args.target_profile_id,
    )
    if not any(value not in (None, "") for value in target_fields):
        return None
    return build_target_datastore_profile(
        userspecific_dir=resolve_user_path(args.target_userspecific_dir) if args.target_userspecific_dir else None,
        systemspecific_dir=resolve_user_path(args.target_systemspecific_dir) if args.target_systemspecific_dir else None,
        software_family=args.target_software_family,
        fluentcontrol_version=args.target_fluentcontrol_version,
        fluentcontrol_build=args.target_fluentcontrol_build,
        target_profile_id=args.target_profile_id,
        provenance={"source": "explicit_cli_target_input"},
    )


def _cmd_plan_deployment(args: argparse.Namespace) -> int:
    """Render a plan; never writes to a target datastore or invokes FluentControl."""
    explicit_target_fields = (
        args.target_userspecific_dir,
        args.target_systemspecific_dir,
        args.target_software_family,
        args.target_fluentcontrol_version,
        args.target_fluentcontrol_build,
        args.target_profile_id,
    )
    if args.target_profile and any(value not in (None, "") for value in explicit_target_fields):
        raise PipelineError("--target-profile cannot be combined with target directory or identity flags")
    if args.no_target and any(
        value not in (None, "")
        for value in (
            args.target_profile,
            args.target_userspecific_dir,
            args.target_systemspecific_dir,
            args.target_software_family,
            args.target_fluentcontrol_version,
            args.target_fluentcontrol_build,
            args.target_profile_id,
        )
    ):
        raise PipelineError("--no-target cannot be combined with explicit target evidence")

    try:
        result = plan_deployment(
            DeploymentPlanRequest(
                source_profile=resolve_user_path(args.source_profile),
                target_profile=None if args.no_target else _explicit_target(args),
                mode=args.mode,
                external_files=_load_external_files(resolve_user_path(args.external_files) if args.external_files else None),
                current_target_profile=(
                    resolve_user_path(args.current_target_profile)
                    if args.current_target_profile
                    else None
                ),
                report_path=resolve_user_path(args.report) if args.report else None,
                json_path=resolve_user_path(args.json_out) if args.json_out else None,
            )
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError(str(exc)) from exc

    payload = result.to_dict()
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_deployment_plan_markdown(result.plan), end="")
        if result.report_path:
            print(f"Report: {result.report_path}")
        if result.json_path:
            print(f"JSON: {result.json_path}")
    return result.exit_code


__all__ = ["_cmd_plan_deployment"]
