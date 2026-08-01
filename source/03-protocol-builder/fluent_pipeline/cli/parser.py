"""Argument parsing for the protocol-builder CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from .commands.diagnostics import _cmd_analyze, _cmd_diagnose, _cmd_map_media, _cmd_parse_fluent_log, _cmd_process_media, _cmd_template_info, _cmd_template_list
from .commands.doctor import _cmd_bootstrap_status, _cmd_compatibility_matrix, _cmd_doctor, _cmd_setup
from .commands.generation import _cmd_bundle_lifecycle, _cmd_compile, _cmd_decompile, _cmd_determinism_check, _cmd_generate, _cmd_ir_build, _cmd_ir_export, _cmd_ir_schema, _cmd_repair_draft, _cmd_repair_plan, _cmd_roundtrip
from .commands.projects import _cmd_alias_list, _cmd_alias_normalize_ir, _cmd_alias_resolve, _cmd_catalog_find, _cmd_catalog_info, _cmd_clear_project, _cmd_collection_info, _cmd_create_collection, _cmd_current_project, _cmd_import_project, _cmd_inspect_external_command, _cmd_list_collections, _cmd_list_projects, _cmd_project_find, _cmd_project_info, _cmd_script_report, _cmd_use_project
from .commands.simulator import _cmd_launch_simulator, _cmd_simulate
from .commands.validation import _cmd_fluent_prepare_check, _cmd_ir_validate, _cmd_request_spec, _cmd_resolve_spec, _cmd_validate_delivery_bundle, _cmd_validate_spec, _cmd_verify_bundle, _cmd_worktable_diff
from ..protocol_ir import CANONICAL_IR_VERSION

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="protocol-builder",
        description="Build, simulate, validate, and package local FluentControl protocol drafts.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_doctor = sub.add_parser("doctor", help="check the local fluentcoder setup")
    p_doctor.add_argument("--report", type=Path, default=None)
    p_doctor.add_argument(
        "--install-missing",
        action="store_true",
        help="create the local venv and install required packages before checking",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_bootstrap = sub.add_parser(
        "bootstrap-status",
        help="run doctor + list-projects and print the next required step (JSON)",
    )
    p_bootstrap.add_argument(
        "--install-missing",
        action="store_true",
        help="create the local venv and install required packages before checking",
    )
    p_bootstrap.add_argument(
        "--confirm-install",
        action="store_true",
        help="required together with --install-missing (local deps only)",
    )
    p_bootstrap.add_argument(
        "--no-report",
        action="store_true",
        help="skip writing ready-to-import/_shared/temp_files/logs/doctor.md",
    )
    p_bootstrap.add_argument(
        "--inspected",
        action="store_true",
        help="attest that inspect-project (or equivalent) already ran this session; unlocks generate tools",
    )
    p_bootstrap.set_defaults(func=_cmd_bootstrap_status)

    p_compat = sub.add_parser(
        "compatibility-matrix",
        help="report FluentControl/manual/connector version compatibility evidence",
    )
    p_compat.add_argument("--report", type=Path, default=None, help="write the Markdown matrix to this path")
    p_compat.add_argument("--json-out", type=Path, default=None, help="write the JSON matrix to this path")
    p_compat.add_argument("--json", dest="as_json", action="store_true", help="print JSON instead of Markdown")
    p_compat.add_argument(
        "--connector",
        choices=["all", "unitelabs", "tecan-sample"],
        default="all",
        help="limit the matrix to one connector",
    )
    p_compat.add_argument(
        "--fluentcontrol-version",
        default=None,
        help="classify a specific FluentControl version label instead of the built-in matrix",
    )
    p_compat.add_argument("--fluentcontrol-build", default="unknown", help="specific FluentControl build number")
    p_compat.add_argument("--manual-version", default=None, help="manual/resource version for the specific setup")
    p_compat.add_argument("--windows-environment", default=None, help="Windows version/environment for the setup")
    p_compat.set_defaults(func=_cmd_compatibility_matrix)

    p_setup = sub.add_parser("setup", help="create the local venv and install protocol-builder dependencies")
    p_setup.set_defaults(func=_cmd_setup)

    p_import = sub.add_parser("import-project", help="import a .zeia/archive as an isolated project context")
    p_import.add_argument("archive", type=Path)
    p_import.add_argument("--name", default=None)
    p_import.add_argument("--force", action="store_true")
    p_import.add_argument(
        "--snapshot",
        action="append",
        type=Path,
        default=[],
        help="FluentControl Snapshot ZIP to extract into the project context; repeat for multiple snapshots",
    )
    p_import.add_argument("--activate", action="store_true", help="make this the default context")
    p_import.set_defaults(func=_cmd_import_project)

    p_list = sub.add_parser("list-projects", help="list imported project contexts")
    p_list.add_argument("--json", dest="as_json", action="store_true")
    p_list.set_defaults(func=_cmd_list_projects)

    p_use = sub.add_parser("use-project", help="set the active project context")
    p_use.add_argument("name")
    p_use.set_defaults(func=_cmd_use_project)

    p_current = sub.add_parser("current-project", help="show the active project context")
    p_current.set_defaults(func=_cmd_current_project)

    p_clear = sub.add_parser("clear-project", help="clear the active project context")
    p_clear.set_defaults(func=_cmd_clear_project)

    p_project_info = sub.add_parser("project-info", help="show a project manifest summary")
    p_project_info.add_argument("name", nargs="?")
    p_project_info.add_argument("--json", dest="as_json", action="store_true")
    p_project_info.set_defaults(func=_cmd_project_info)

    p_project_find = sub.add_parser("project-find", help="search a project manifest")
    p_project_find.add_argument("pattern")
    p_project_find.add_argument("--context", default=None)
    p_project_find.add_argument("--kind", default=None)
    p_project_find.add_argument(
        "--limit",
        type=int,
        default=20,
        help="max matches to return (default 20, max 50)",
    )
    p_project_find.add_argument("--json", dest="as_json", action="store_true")
    p_project_find.set_defaults(func=_cmd_project_find)

    p_external_command = sub.add_parser(
        "inspect-external-command",
        help="mine existing XSCR usages before generating an external driver command",
    )
    p_external_command.add_argument("command_name", help="exact macro name, such as ResolvexA200_Run")
    p_external_command.add_argument("--module", default=None, help="optional exact module name")
    p_external_command.add_argument("--source-script", default=None, help="optional exact source script name")
    p_external_command.add_argument("--context", default=None, help="project context; defaults to active context")
    p_external_command.add_argument("--json-out", type=Path, default=None)
    p_external_command.add_argument("--markdown-out", type=Path, default=None)
    p_external_command.add_argument("--json", dest="as_json", action="store_true")
    p_external_command.set_defaults(func=_cmd_inspect_external_command)

    p_script_report = sub.add_parser(
        "script-report",
        help="explain one imported script and list manual command/improvement references",
    )
    p_script_report.add_argument("script", nargs="?", help="script object name/path; defaults to --script-index")
    p_script_report.add_argument("--script-index", type=int, default=1, help="1-based script index in the context manifest")
    p_script_report.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_script_report.add_argument("--out-dir", type=Path, default=None)
    p_script_report.add_argument("--max-commands", type=int, default=120)
    p_script_report.add_argument("--json", dest="as_json", action="store_true")
    p_script_report.set_defaults(func=_cmd_script_report)

    p_create_collection = sub.add_parser(
        "create-collection",
        help="combine imported project contexts into one generation collection",
    )
    p_create_collection.add_argument("name")
    p_create_collection.add_argument(
        "--context",
        action="append",
        required=True,
        help="imported project context to include; repeat for multiple ZEIA contexts",
    )
    p_create_collection.add_argument("--force", action="store_true")
    p_create_collection.add_argument(
        "--progress",
        nargs="?",
        const="plain",
        default="auto",
        choices=["auto", "plain", "json", "none"],
        help="progress display mode; auto currently uses flushed plain line-based progress",
    )
    p_create_collection.set_defaults(func=_cmd_create_collection)

    p_list_collections = sub.add_parser("list-collections", help="list project collections")
    p_list_collections.add_argument("--json", dest="as_json", action="store_true")
    p_list_collections.set_defaults(func=_cmd_list_collections)

    p_collection_info = sub.add_parser("collection-info", help="show a project collection manifest summary")
    p_collection_info.add_argument("name")
    p_collection_info.add_argument("--json", dest="as_json", action="store_true")
    p_collection_info.set_defaults(func=_cmd_collection_info)

    p_cat_info = sub.add_parser("catalog-info", help="show fluentcoder catalog index info")
    p_cat_info.set_defaults(func=_cmd_catalog_info)

    p_cat_find = sub.add_parser("catalog-find", help="search fluentcoder catalog components")
    p_cat_find.add_argument("pattern")
    p_cat_find.add_argument("--category", default=None)
    p_cat_find.set_defaults(func=_cmd_catalog_find)

    p_alias_list = sub.add_parser("alias-list", help="list configured catalog/labware/liquid/device aliases")
    p_alias_list.add_argument("--json", dest="as_json", action="store_true")
    p_alias_list.set_defaults(func=_cmd_alias_list)

    p_alias_resolve = sub.add_parser("alias-resolve", help="resolve one configured alias")
    p_alias_resolve.add_argument("kind", choices=["catalog", "labware", "liquid_class", "device_alias"])
    p_alias_resolve.add_argument("name")
    p_alias_resolve.add_argument("--json", dest="as_json", action="store_true")
    p_alias_resolve.set_defaults(func=_cmd_alias_resolve)

    p_alias_normalize = sub.add_parser("alias-normalize-ir", help="write an alias-normalized protocol IR copy")
    p_alias_normalize.add_argument("input", type=Path)
    p_alias_normalize.add_argument("-o", "--output", type=Path, default=None)
    p_alias_normalize.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_alias_normalize.set_defaults(func=_cmd_alias_normalize_ir)

    p_decompile = sub.add_parser("decompile", help="turn an .xscr into a Python protocol draft")
    p_decompile.add_argument("input", type=Path)
    p_decompile.add_argument("-o", "--output", type=Path, default=None)
    p_decompile.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_decompile.add_argument("--strict", action="store_true")
    p_decompile.set_defaults(func=_cmd_decompile)

    p_sim = sub.add_parser("simulate", help="simulate a Python protocol draft")
    p_sim.add_argument("input", type=Path)
    p_sim.add_argument("--json-out", type=Path, default=None)
    p_sim.add_argument("--report", type=Path, default=None)
    p_sim.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_sim.add_argument("--fail-on-opaque", action="store_true")
    p_sim.add_argument("--min-coverage", type=float, default=None)
    p_sim.add_argument("--strict", action="store_true")
    p_sim.add_argument("--watch-log", type=Path, default=None, help="capture lines appended to this log while simulation runs")
    p_sim.add_argument("--log-script", default=None, help="only capture watched log lines containing this script name/text")
    p_sim.add_argument("--log-report", type=Path, default=None, help="write watched log capture markdown")
    p_sim.set_defaults(func=_cmd_simulate)

    p_launch_sim = sub.add_parser(
        "launch-simulator",
        help="start the local 3D protocol simulator and optionally preload a bundle",
    )
    p_launch_sim.add_argument("--bundle", type=Path, default=None, help="ready-to-import bundle directory, ZEIA, ZIP, or artifact to preload")
    p_launch_sim.add_argument("--host", default="127.0.0.1", help="host for the local Vite dev server")
    p_launch_sim.add_argument("--port", type=int, default=5173, help="port for the local Vite dev server")
    p_launch_sim.add_argument("--strict-port", action="store_true", help="fail if the requested port is already in use")
    p_launch_sim.add_argument("--no-open", action="store_true", help="start the server without opening a browser")
    p_launch_sim.add_argument("--skip-install", action="store_true", help="do not run npm install if node_modules is missing")
    p_launch_sim.set_defaults(func=_cmd_launch_simulator)

    p_fluent_prepare = sub.add_parser(
        "fluent-prepare-check",
        help="start/attach FluentControl in simulation mode and prepare a method",
    )
    p_fluent_prepare.add_argument("--method", default=None, help="FluentControl method name to prepare")
    p_fluent_prepare.add_argument("--xscr", type=Path, default=None, help="compiled XSCR path to load/import in simulation mode")
    p_fluent_prepare.add_argument("--zeia", type=Path, default=None, help="generated ZEIA path to import in simulation mode")
    p_fluent_prepare.add_argument("--json-out", type=Path, default=None)
    p_fluent_prepare.add_argument("--report", type=Path, default=None)
    _add_fluent_context_args(p_fluent_prepare, include_enable=False)
    p_fluent_prepare.set_defaults(func=_cmd_fluent_prepare_check)

    p_compile = sub.add_parser("compile", help="compile a Python protocol draft to .xscr")
    p_compile.add_argument("input", type=Path)
    p_compile.add_argument("-o", "--output", type=Path, default=None)
    p_compile.add_argument("--context", default=None, help="project context name; defaults to active context")
    _add_fluent_context_args(p_compile)
    p_compile.set_defaults(func=_cmd_compile)

    p_ir_export = sub.add_parser("ir-export", help="export Python, XSCR, GWL, or ZEIA into canonical protocol IR")
    p_ir_export.add_argument("input", type=Path)
    p_ir_export.add_argument("-o", "--output", type=Path, default=None)
    p_ir_export.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_ir_export.set_defaults(func=_cmd_ir_export)

    p_ir_build = sub.add_parser("ir-build", help="generate artifacts from canonical protocol IR")
    p_ir_build.add_argument("input", type=Path)
    p_ir_build.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="intermediate output directory under ready-to-import/<project>/temp_files/",
    )
    p_ir_build.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_ir_build.add_argument("--no-compile", action="store_true", help="skip XSCR compilation")
    _add_fluent_context_args(p_ir_build)
    p_ir_build.set_defaults(func=_cmd_ir_build)

    p_ir_schema = sub.add_parser("ir-schema", help="print the canonical protocol IR JSON Schema")
    p_ir_schema.add_argument("--version", default=CANONICAL_IR_VERSION)
    p_ir_schema.add_argument("--bundle", action="store_true", help="print the ZEIA-derived IR bundle schema")
    p_ir_schema.add_argument("--versions", action="store_true", help="list registered protocol IR schema versions")
    p_ir_schema.add_argument("--format", choices=["json", "markdown"], default="json")
    p_ir_schema.add_argument("-o", "--output", type=Path, default=None)
    p_ir_schema.set_defaults(func=_cmd_ir_schema)

    p_ir_validate = sub.add_parser("ir-validate", help="validate a protocol.ir.json file or IR bundle")
    p_ir_validate.add_argument("input", type=Path)
    p_ir_validate.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_ir_validate.add_argument("--json", dest="as_json", action="store_true")
    p_ir_validate.add_argument("--normalize", action="store_true", help="validate after applying migrations/defaults")
    p_ir_validate.add_argument("--write-normalized", type=Path, default=None)
    p_ir_validate.set_defaults(func=_cmd_ir_validate)

    p_worktable_diff = sub.add_parser(
        "worktable-diff",
        help="compare source ZEIA worktable/context with protocol IR requirements",
    )
    p_worktable_diff.add_argument("input", type=Path, help="protocol.ir.json to compare")
    p_worktable_diff.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_worktable_diff.add_argument(
        "--source-script",
        action="append",
        default=[],
        help="source script name/path to use for exact existing labware positions",
    )
    p_worktable_diff.add_argument("-o", "--output", type=Path, default=None)
    p_worktable_diff.add_argument("--json", dest="as_json", action="store_true")
    p_worktable_diff.set_defaults(func=_cmd_worktable_diff)

    p_analyze = sub.add_parser(
        "analyze",
        help="combine diagnosis, script analysis, and optional FluentControl logs",
    )
    p_analyze.add_argument("input", nargs="?", type=Path, help=".zeia/.zip archive, .xscr, protocol IR, or other analyzable input")
    p_analyze.add_argument("--script", default=None, help="script object name or extracted path when input is a ZEIA/archive")
    p_analyze.add_argument("--script-index", type=int, default=1, help="1-based script index when analyzing a project context")
    p_analyze.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_analyze.add_argument("--fluent-script", default=None, help="saved FluentControl script name or local path to stage and analyze")
    p_analyze.add_argument("--fluent-folder", default=None, help="saved FluentControl subfolder used to disambiguate --fluent-script")
    p_analyze.add_argument("--fluent-database", type=Path, default=None, help="override local FluentControl DataBase root for --fluent-script")
    p_analyze.add_argument("--name", default=None, help="project context name when importing a ZEIA/archive input")
    p_analyze.add_argument("--force-import", action="store_true", help="replace an existing imported context for ZEIA/archive input")
    p_analyze.add_argument(
        "--snapshot",
        action="append",
        type=Path,
        default=[],
        help="FluentControl Snapshot ZIP to include when importing the diagnostic context",
    )
    p_analyze.add_argument("--error-text", default=None, help="FluentControl error text to correlate with static findings")
    p_analyze.add_argument("--error-file", type=Path, default=None, help="file containing FluentControl error text")
    p_analyze.add_argument("--log", type=Path, default=None, help="FluentControl/VisionX log file to parse alongside the analysis")
    p_analyze.add_argument(
        "--audit-log",
        action="append",
        type=Path,
        default=[],
        help="VisionX AuditTrail CSV used to associate an imported script with later log errors",
    )
    p_analyze.add_argument("--latest-log", action="store_true", help="scan recent FluentControl/VisionX logs from common locations")
    p_analyze.add_argument("--since-hours", type=float, default=48.0)
    p_analyze.add_argument("--max-files", type=int, default=12)
    p_analyze.add_argument("--max-records", type=int, default=80)
    p_analyze.add_argument("--out-dir", type=Path, default=None)
    p_analyze.add_argument("--max-commands", type=int, default=120)
    p_analyze.add_argument("--json", dest="as_json", action="store_true")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_map_media = sub.add_parser(
        "map-media",
        help="map bundle-relative media slots to deployed absolute TouchTools paths",
    )
    p_map_media.add_argument(
        "input",
        type=Path,
        help="generated out-dir, a *.protocol-ir.json, or a media_placeholders.json",
    )
    p_map_media.add_argument(
        "--touchtools-dir",
        required=True,
        help="absolute TouchTools image directory on the FluentControl machine",
    )
    p_map_media.add_argument(
        "--subfolder",
        default=None,
        help="optional per-script subfolder under --touchtools-dir",
    )
    p_map_media.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_map_media.add_argument("-o", "--output", type=Path, default=None)
    p_map_media.add_argument("--json", dest="as_json", action="store_true")
    p_map_media.add_argument(
        "--apply-xscr",
        type=Path,
        default=None,
        help="rewrite prompt media paths in this compiled XSCR using the mapped absolute paths",
    )
    p_map_media.set_defaults(func=_cmd_map_media)

    p_process_media = sub.add_parser(
        "process-media",
        help="convert raw operator captures in media/unprocessed/ into prompt media slots",
    )
    p_process_media.add_argument(
        "target",
        type=Path,
        help="ready-to-import bundle directory, build out-dir, or media/ folder",
    )
    p_process_media.add_argument(
        "--ir",
        type=Path,
        default=None,
        help="protocol IR JSON (required when multiple *.protocol-ir.json candidates exist under target)",
    )
    p_process_media.add_argument(
        "--unprocessed-dir",
        action="append",
        type=Path,
        default=[],
        help="extra folder of raw captures to scan",
    )
    p_process_media.add_argument(
        "--source-dir",
        action="append",
        type=Path,
        default=[],
        help="additional capture folders to scan",
    )
    p_process_media.add_argument(
        "--no-finalize",
        action="store_true",
        help="resolve slots only; skip placeholder creation and video-to-gif normalization",
    )
    p_process_media.add_argument("--json", dest="as_json", action="store_true")
    p_process_media.set_defaults(func=_cmd_process_media)

    p_diagnose = sub.add_parser("diagnose", help="diagnose why a ZEIA/XSCR/GWL script may be failing")
    p_diagnose.add_argument("input", type=Path, help=".zeia/.zip archive, .xscr script, .gwl worklist, Python draft, or protocol IR")
    p_diagnose.add_argument("--script", default=None, help="script object name or extracted path when input is a ZEIA/archive")
    p_diagnose.add_argument("--context", default=None, help="project context name for non-ZEIA inputs; defaults to active context")
    p_diagnose.add_argument("--name", default=None, help="project context name when importing a ZEIA/archive input")
    p_diagnose.add_argument("--force-import", action="store_true", help="replace an existing imported context for ZEIA/archive input")
    p_diagnose.add_argument(
        "--snapshot",
        action="append",
        type=Path,
        default=[],
        help="FluentControl Snapshot ZIP to include when importing the diagnostic context",
    )
    p_diagnose.add_argument("--error-text", default=None, help="FluentControl error text to correlate with static findings")
    p_diagnose.add_argument("--error-file", type=Path, default=None, help="file containing FluentControl error text")
    p_diagnose.add_argument("--out-dir", type=Path, default=None)
    p_diagnose.add_argument("--json", dest="as_json", action="store_true")
    p_diagnose.set_defaults(func=_cmd_diagnose)

    p_parse_log = sub.add_parser(
        "parse-fluent-log",
        help="parse FluentControl/VisionX logs into workflow diagnostics",
    )
    p_parse_log.add_argument("log", type=Path, help="FluentControl/VisionX .log or copied error text file")
    p_parse_log.add_argument(
        "--audit-log",
        action="append",
        type=Path,
        default=[],
        help="VisionX AuditTrail CSV used to associate an imported script with later errors",
    )
    p_parse_log.add_argument("--json-out", type=Path, default=None, help="write structured diagnostics JSON")
    p_parse_log.add_argument("--report", type=Path, default=None, help="write Markdown diagnostics report")
    p_parse_log.add_argument("--json", dest="as_json", action="store_true", help="print JSON instead of a summary")
    p_parse_log.set_defaults(func=_cmd_parse_fluent_log)

    p_template_list = sub.add_parser("template-list", help="list reusable protocol templates")
    p_template_list.add_argument("--json", dest="as_json", action="store_true")
    p_template_list.set_defaults(func=_cmd_template_list)

    p_template_info = sub.add_parser("template-info", help="show details for one reusable protocol template")
    p_template_info.add_argument("name")
    p_template_info.add_argument("--json", dest="as_json", action="store_true")
    p_template_info.set_defaults(func=_cmd_template_info)

    p_request_spec = sub.add_parser("request-spec", help="write a request.spec.yaml scaffold")
    p_request_spec.add_argument("intent", help="what the new script should do")
    p_request_spec.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="defaults to ready-to-import/<project>/temp_files/build/request.spec.yaml",
    )
    p_request_spec.add_argument("--protocol-name", default=None)
    p_request_spec.add_argument("--project-archive", action="append", type=Path, default=[])
    p_request_spec.add_argument("--context", action="append", default=[])
    p_request_spec.add_argument("--collection", default=None)
    p_request_spec.add_argument("--source-script", action="append", default=[])
    p_request_spec.add_argument("--pattern", action="append", default=[])
    p_request_spec.add_argument("--index-db", type=Path, default=None)
    p_request_spec.add_argument("--pattern-id", action="append", default=[])
    p_request_spec.add_argument("--pattern-query", action="append", default=[])
    p_request_spec.add_argument("--source-script-rank", type=int, default=1)
    p_request_spec.add_argument("--no-simulate", action="store_true")
    p_request_spec.add_argument("--no-compile", action="store_true")
    p_request_spec.add_argument("--max-repair-iterations", type=int, default=1)
    p_request_spec.add_argument("--strict-readiness", action="store_true")
    p_request_spec.add_argument("--apply-modeling", action="store_true")
    p_request_spec.add_argument(
        "--target-fluentcontrol-version",
        default=None,
        help="target FluentControl version label; versions below 3.6 force Worktable prompt images",
    )
    p_request_spec.add_argument(
        "--approve-partial-zeia",
        action="store_true",
        help="record explicit user approval to continue with a partial/non-full ZEIA export",
    )
    p_request_spec.add_argument(
        "--approve-deck-layout",
        action="store_true",
        help="record explicit approval for deck-layout changes relative to the source worktable",
    )
    p_request_spec.add_argument(
        "--approve-command-inventory",
        action="store_true",
        help="record explicit approval for unresolved command inventory strings on the target system",
    )
    p_request_spec.add_argument(
        "--approve-unsupported-raw-xml",
        action="store_true",
        help="record explicit approval for unsupported raw XML/pass-through command usage",
    )
    p_request_spec.add_argument(
        "--approve-unsupported-command",
        dest="approved_unsupported_command_ids",
        action="append",
        default=[],
        help="record approval for one unsupported command id; repeat for multiple ids",
    )
    p_request_spec.add_argument(
        "--waive-checksum-recompute",
        action="store_true",
        help="record explicit approval to package edited ZEIA entries without recomputed checksums",
    )
    p_request_spec.add_argument(
        "--preserve-failed-artifacts",
        action="store_true",
        help="record that failed generation runs may keep internal artifacts for debugging",
    )
    p_request_spec.add_argument(
        "--preserve-regeneration-baseline",
        action="store_true",
        help="explicitly preserve matching baseline steps when no IR or recipe is supplied",
    )
    p_request_spec.add_argument(
        "--fluent-context-check",
        action="store_true",
        help="record that final generation should run the optional FluentControl import/load diagnostic before packaging",
    )
    p_request_spec.add_argument("--fluent-method", default=None, help="FluentControl method name to prepare")
    p_request_spec.add_argument(
        "--fluent-provider",
        choices=["auto", "legacy-sila", "external", "local-desktop"],
        default="auto",
        help="runtime provider for the optional FluentControl import/load diagnostic",
    )
    p_request_spec.add_argument(
        "--fluent-timeout",
        type=float,
        default=180.0,
        help="timeout in seconds for the optional FluentControl import/load diagnostic",
    )
    p_request_spec.set_defaults(func=_cmd_request_spec)

    p_validate_spec = sub.add_parser(
        "validate-spec",
        help="lint a request.spec.yaml before generation",
    )
    p_validate_spec.add_argument("spec", help="path to request.spec.yaml")
    p_validate_spec.set_defaults(func=_cmd_validate_spec)

    p_resolve_spec = sub.add_parser(
        "resolve-spec",
        help="find the newest ready-to-import request spec without generating",
    )
    p_resolve_spec.add_argument("spec", nargs="?", default=None, help="latest:<protocol>, a versioned spec path, or omit with --protocol-name")
    p_resolve_spec.add_argument("--protocol-name", default=None, help="protocol name used when the spec is omitted")
    p_resolve_spec.add_argument("--context", default=None, help="context name used when the spec is omitted")
    p_resolve_spec.add_argument("--pin-spec", action="store_true", help="do not upgrade a versioned explicit spec")
    p_resolve_spec.add_argument("--json", dest="as_json", action="store_true", help="print resolver metadata as JSON")
    p_resolve_spec.set_defaults(func=_cmd_resolve_spec)

    p_validate_delivery_bundle = sub.add_parser(
        "validate-delivery-bundle",
        help="validate a published V2 ready-to-import protocol folder",
    )
    p_validate_delivery_bundle.add_argument("bundle_dir", type=Path, help="ready-to-import/<protocol> folder")
    p_validate_delivery_bundle.add_argument("--protocol-name", default=None, help="expected protocol folder/ZEIA stem")
    p_validate_delivery_bundle.add_argument(
        "--allow-missing-final-reports",
        action="store_true",
        help="validate the publication-stage folder before generation_manifest.json and GENERATION_WORKFLOW.md are attached",
    )
    p_validate_delivery_bundle.add_argument("--json", dest="as_json", action="store_true", help="print JSON instead of summary text")
    p_validate_delivery_bundle.add_argument("--json-out", type=Path, default=None, help="write validation JSON")
    p_validate_delivery_bundle.set_defaults(func=_cmd_validate_delivery_bundle)

    p_verify_bundle = sub.add_parser(
        "verify-bundle",
        help="run shared ready-validation over generated bundle artifacts",
    )
    p_verify_bundle.add_argument("compiled_xscr", type=Path, help="compiled XSCR to validate")
    p_verify_bundle.add_argument("--draft-path", type=Path, default=None)
    p_verify_bundle.add_argument("--protocol-ir", type=Path, default=None)
    p_verify_bundle.add_argument("--worklist", type=Path, default=None)
    p_verify_bundle.add_argument("--source-project", action="append", type=Path, default=[])
    p_verify_bundle.add_argument("--source-script", action="append", type=Path, default=[])
    p_verify_bundle.add_argument("--source-xscr", type=Path, default=None)
    p_verify_bundle.add_argument("--recreate-guide", type=Path, default=None)
    p_verify_bundle.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_verify_bundle.add_argument("--report", type=Path, default=None, help="write ready-validation Markdown")
    p_verify_bundle.add_argument("--json-out", type=Path, default=None, help="write ready-validation JSON")
    p_verify_bundle.add_argument("--json", dest="as_json", action="store_true", help="print JSON instead of summary text")
    p_verify_bundle.set_defaults(func=_cmd_verify_bundle)

    p_generate = sub.add_parser(
        "generate",
        help="run the official ZEIA-to-new-script generation workflow",
        description=(
            "Run the official ZEIA-to-new-script workflow. A READY TO IMPORT result is an "
            "offline packaging/import boundary; Script Editor load-clean still requires "
            "the optional --fluent-context-check diagnostic or a manual Script Editor open/load check."
        ),
    )
    p_generate.add_argument("intent", nargs="?", help="what the new script should do")
    p_generate.add_argument("--spec", default=None, help="request.spec.yaml or latest:<protocol> to use as the generation contract")
    p_generate.add_argument(
        "--pin-spec",
        action="store_true",
        help="use the exact --spec path instead of auto-upgrading to the newest matching reviewed spec",
    )
    p_generate.add_argument(
        "--project-archive",
        action="append",
        type=Path,
        default=[],
        help="import this ZEIA before generating; repeat to build a collection",
    )
    p_generate.add_argument(
        "--name",
        default=None,
        help="project context name for one archive, or collection name for multiple sources",
    )
    p_generate.add_argument("--force-import", action="store_true", help="replace an existing imported context")
    p_generate.add_argument(
        "--context",
        action="append",
        default=[],
        help="project context name; repeat to combine contexts; defaults to active context",
    )
    p_generate.add_argument("--collection", default=None, help="saved project collection to use for generation")
    p_generate.add_argument("--source-script", action="append", default=[], help="source script name/path to reuse")
    p_generate.add_argument("--pattern", action="append", default=[], help="pattern reference to reuse")
    p_generate.add_argument("--index-db", type=Path, default=None, help="tecan-reader SQLite project index with mined patterns")
    p_generate.add_argument("--pattern-id", action="append", default=[], help="mined script_patterns.id to pull into the IR")
    p_generate.add_argument("--pattern-query", action="append", default=[], help="search mined patterns and pull the ranked match into the IR")
    p_generate.add_argument(
        "--source-script-rank",
        type=int,
        default=1,
        help="1-based source-script rank to use when resolving --pattern-query",
    )
    p_generate.add_argument("--ir", type=Path, default=None, help="existing protocol IR to run through the generation workflow")
    p_generate.add_argument("--protocol-name", default=None, help="protocol name for a new seed IR")
    p_generate.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="intermediate output directory under ready-to-import/<project>/temp_files/",
    )
    p_generate.add_argument("--no-simulate", action="store_true", help="skip simulation gate")
    p_generate.add_argument("--no-compile", action="store_true", help="skip compile and packaging gate")
    p_generate.add_argument(
        "--apply-modeling",
        action="store_true",
        help="apply suggested raw XML modeling repairs in addition to ready repairs",
    )
    p_generate.add_argument(
        "--target-fluentcontrol-version",
        default=None,
        help="target FluentControl version label; versions below 3.6 force Worktable prompt images",
    )
    p_generate.add_argument(
        "--target-script-folder",
        default=None,
        help="FluentControl script folder/ObjectSubfolderPath for the generated main script",
    )
    p_generate.add_argument(
        "--approve-partial-zeia",
        action="store_true",
        help=(
            "explicitly approve continuing when the source ZEIA is missing full-export "
            "dependency evidence. Without this flag generation blocks and asks for a full export."
        ),
    )
    p_generate.add_argument(
        "--waive-checksum-recompute",
        action="store_true",
        help=(
            "allow packaging when edited ZEIA entries ship with blank checksums "
            "(no FluentControl checksum bridge available); FluentControl will prompt "
            "to recalculate on import. Without this flag the checksum gate blocks packaging."
        ),
    )
    p_generate.add_argument(
        "--approve-deck-layout",
        action="store_true",
        help=(
            "approve deck position changes relative to the source worktable (deck_layout_consistent). "
            "Use only after confirming the labware can be physically relocated; without "
            "this flag a deck move blocks packaging."
        ),
    )
    p_generate.add_argument(
        "--approve-command-inventory",
        action="store_true",
        help=(
            "approve compiled command name strings (catalog/labware/liquid-class/device) "
            "that resolve nowhere in the source context (command_inventory_resolves). Use only after confirming "
            "the target FluentControl system carries them; without this flag an unknown name "
            "blocks packaging."
        ),
    )
    p_generate.add_argument(
        "--approve-unsupported-raw-xml",
        action="store_true",
        help=(
            "approve unsupported raw XML/pass-through command usage after reviewing the command XML. "
            "Prefer --approve-unsupported-command for narrow command-specific approval."
        ),
    )
    p_generate.add_argument(
        "--approve-unsupported-command",
        dest="approved_unsupported_command_ids",
        action="append",
        default=[],
        help="approve one unsupported command id after review; repeat for multiple ids",
    )
    p_generate.add_argument(
        "--preserve-failed-artifacts",
        action="store_true",
        help="keep failed internal artifacts under failed_artifacts/ for debugging",
    )
    p_generate.add_argument(
        "--preserve-regeneration-baseline",
        action="store_true",
        help="explicitly preserve matching baseline steps; explicit IR and recipes still take precedence",
    )
    p_generate.add_argument(
        "--max-repair-iterations",
        type=int,
        default=None,
        help="maximum number of repair rounds to simulate after the original candidate",
    )
    p_generate.add_argument(
        "--strict-readiness",
        action="store_true",
        help="record that generation used the strict readiness profile",
    )
    p_generate.add_argument("--launch-simulator", action="store_true", help="open the local simulator with the generated bundle after a successful run")
    p_generate.add_argument("--simulator-host", default="127.0.0.1", help="host for --launch-simulator")
    p_generate.add_argument("--simulator-port", type=int, default=5173, help="port for --launch-simulator")
    p_generate.add_argument("--simulator-strict-port", action="store_true", help="fail if --simulator-port is already in use")
    p_generate.add_argument("--simulator-no-open", action="store_true", help="start the simulator without opening a browser")
    p_generate.add_argument("--simulator-skip-install", action="store_true", help="do not run npm install before launching the simulator")
    p_generate.add_argument(
        "--progress",
        nargs="?",
        const="plain",
        default="auto",
        choices=["auto", "plain", "json", "none"],
        help="progress display mode; auto currently uses plain line-based progress",
    )
    p_generate.add_argument(
        "--event-log",
        type=Path,
        default=None,
        help="write JSONL progress events under ready-to-import/<project>/temp_files/",
    )
    p_generate.add_argument(
        "--event-log-stderr",
        action="store_true",
        help="also stream JSONL progress events to stderr",
    )
    p_generate.add_argument(
        "--no-event-log",
        action="store_true",
        help="disable the default <out-dir>/logs/generation.events.jsonl event stream",
    )
    _add_fluent_context_args(p_generate)
    p_generate.set_defaults(fluent_provider=None, fluent_timeout=None)
    p_generate.set_defaults(func=_cmd_generate)

    p_lifecycle = sub.add_parser(
        "bundle-lifecycle",
        help="list generated/probe bundles and recommend safe cleanup actions",
        description=(
            "Inventory ready/probe/debug bundles and print safe archive recommendations. "
            "Dry-run is the default; use --archive to move recommended items into an archive folder."
        ),
    )
    p_lifecycle.add_argument("--root", type=Path, default=None, help="ready-to-import root; defaults to the configured ready root")
    p_lifecycle.add_argument(
        "--probe-root",
        action="append",
        type=Path,
        default=[],
        help="probe/debug artifact root to include; repeat for multiple roots",
    )
    p_lifecycle.add_argument(
        "--no-default-probe-root",
        action="store_true",
        help="do not include build/fluent_import_probe when it exists",
    )
    p_lifecycle.add_argument("--keep-latest-ready", type=int, default=1, help="ready bundles to keep per context/script")
    p_lifecycle.add_argument("--json", dest="as_json", action="store_true", help="print JSON instead of Markdown")
    p_lifecycle.add_argument("--report", type=Path, default=None, help="write a Markdown bundle index report")
    p_lifecycle.add_argument(
        "--write-index",
        action="store_true",
        help="write BUNDLE_INDEX.md under the ready-to-import root",
    )
    p_lifecycle.add_argument(
        "--archive",
        action="store_true",
        help="move recommended archive candidates instead of only reporting them",
    )
    p_lifecycle.add_argument("--archive-dir", type=Path, default=None, help="archive destination; defaults to <root>/archive")
    p_lifecycle.set_defaults(func=_cmd_bundle_lifecycle)

    p_round = sub.add_parser("roundtrip", help="decompile, simulate, and compile into an output folder")
    p_round.add_argument("input", type=Path)
    p_round.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="intermediate output directory under ready-to-import/<project>/temp_files/",
    )
    p_round.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_round.add_argument("--strict-decompile", action="store_true")
    p_round.add_argument("--strict-simulate", action="store_true")
    p_round.add_argument("--fail-on-opaque", action="store_true")
    p_round.add_argument("--min-coverage", type=float, default=None)
    _add_fluent_context_args(p_round)
    p_round.set_defaults(func=_cmd_roundtrip)

    p_determinism = sub.add_parser(
        "determinism-check",
        help="compare two generation output folders for byte-identical regenerated artifacts",
    )
    p_determinism.add_argument("first", type=Path, help="first generation output directory")
    p_determinism.add_argument("second", type=Path, help="second generation output directory")
    p_determinism.add_argument(
        "--root",
        action="append",
        default=[],
        help="additional absolute root to normalize out of compared artifacts; repeat for multiple roots",
    )
    p_determinism.add_argument("--json", dest="as_json", action="store_true", help="print the full JSON report")
    p_determinism.set_defaults(func=_cmd_determinism_check)

    p_repair_plan = sub.add_parser("repair-plan", help="analyze a decompiled draft for project-aware repairs")
    p_repair_plan.add_argument("input", type=Path)
    p_repair_plan.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_repair_plan.add_argument("--simulation-json", type=Path, default=None)
    p_repair_plan.add_argument("--report", type=Path, default=None)
    p_repair_plan.add_argument("--json", dest="as_json", action="store_true")
    p_repair_plan.set_defaults(func=_cmd_repair_plan)

    p_repair_draft = sub.add_parser("repair-draft", help="write a non-destructive repaired Python draft")
    p_repair_draft.add_argument("input", type=Path)
    p_repair_draft.add_argument("-o", "--output", type=Path, default=None)
    p_repair_draft.add_argument("--context", default=None, help="project context name; defaults to active context")
    p_repair_draft.add_argument("--simulation-json", type=Path, default=None)
    p_repair_draft.add_argument("--report", type=Path, default=None)
    p_repair_draft.add_argument(
        "--apply-modeling",
        action="store_true",
        help="also apply suggested raw XML modeling replacements",
    )
    p_repair_draft.set_defaults(func=_cmd_repair_draft)

    return parser

def _add_fluent_context_args(parser: argparse.ArgumentParser, *, include_enable: bool = True) -> None:
    if include_enable:
        parser.add_argument(
            "--fluent-context-check",
            action="store_true",
            help="prepare the method in FluentControl simulation mode before ready packaging",
        )
    parser.add_argument("--fluent-method", default=None, help="FluentControl method name to prepare")
    parser.add_argument(
        "--fluent-provider",
        choices=["auto", "legacy-sila", "external", "local-desktop"],
        default="auto",
        help="runtime provider for FluentControl prepare checks",
    )
    parser.add_argument(
        "--fluent-command",
        default=None,
        help=(
            "external command template that loads/imports the artifact in simulation "
            "mode and fails on load errors; placeholders: {method}, {xscr}, {zeia}, "
            "{mode}, {timeout}, {host}, {port}"
        ),
    )
    parser.add_argument("--fluent-host", default="127.0.0.1")
    parser.add_argument("--fluent-port", type=int, default=50052)
    parser.add_argument("--fluent-insecure", action="store_true", help="connect to a legacy SiLA server without TLS")
    parser.add_argument("--fluent-timeout", type=float, default=180.0, help="FluentControl prepare timeout in seconds")
    parser.add_argument("--no-fluent-close-method", action="store_true", help="leave the prepared method open")
