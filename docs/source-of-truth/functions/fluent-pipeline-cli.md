# Functions: fluent-pipeline-cli

Source roots: `fluent_pipeline/` (13 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `_build_analysis_report (priv)` | `cli/commands/diagnostics.py` | `()` | see source | see source |
| `_render_analysis_markdown (priv)` | `cli/commands/diagnostics.py` | `(report)` | see source | see source |
| `_resolve_media_source (priv)` | `cli/commands/diagnostics.py` | `(source)` | Locate the IR or media_placeholders.json for a map-media input. | see source |
| `_cmd_parse_fluent_log (priv)` | `cli/commands/diagnostics.py` | `(args)` | see source | see source |
| `_cmd_ir_export (priv)` | `cli/commands/generation.py` | `(args)` | see source | see source |
| `_cmd_ir_build (priv)` | `cli/commands/generation.py` | `(args)` | see source | see source |
| `_render_bundle_schema_markdown (priv)` | `cli/commands/generation.py` | `(schema)` | see source | see source |
| `_resolve_ir_source (priv)` | `cli/commands/generation.py` | `(ctx, value)` | see source | see source |
| `_resolve_generation_event_log (priv)` | `cli/commands/generation.py` | `(args, out_dir)` | see source | see source |
| `_build_ir_artifacts (priv)` | `cli/commands/generation.py` | `(ir, out_dir)` | see source | see source |
| `_cmd_import_project (priv)` | `cli/commands/projects.py` | `(args)` | see source | see source |
| `_cmd_alias_resolve (priv)` | `cli/commands/projects.py` | `(args)` | see source | see source |
| `_cmd_alias_normalize_ir (priv)` | `cli/commands/projects.py` | `(args)` | see source | see source |
| `_cmd_ir_validate (priv)` | `cli/commands/validation.py` | `(args)` | see source | see source |
| `_cmd_validate_spec (priv)` | `cli/commands/validation.py` | `(args)` | see source | see source |
| `_cmd_resolve_spec (priv)` | `cli/commands/validation.py` | `(args)` | Resolve a regeneration spec without generating or importing anything. | see source |
| `_cmd_validate_delivery_bundle (priv)` | `cli/commands/validation.py` | `(args)` | see source | see source |
| `_build_parser (priv)` | `cli/parser.py` | `()` | see source | see source |
| `normalize_progress_mode` | `cli/rendering.py` | `(value)` | see source | see source |
| `progress_callback_from_mode` | `cli/rendering.py` | `(mode)` | see source | see source |
| `generation_exit_code` | `cli/rendering.py` | `(result)` | see source | see source |
| `generation_simulator_bundle` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_generation_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_project_import_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_project_inspection_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_request_spec_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `request_spec_validation_exit_code` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_request_spec_validation_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_repair_plan_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_repair_apply_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `log_analysis_exit_code` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_log_analysis_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `bundle_verification_exit_code` | `cli/rendering.py` | `(result)` | see source | see source |
| `print_bundle_verification_result` | `cli/rendering.py` | `(result)` | see source | see source |
| `generation_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `project_import_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `project_inspection_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `request_spec_create_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `request_spec_validation_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `repair_plan_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `repair_apply_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `log_analysis_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `bundle_verification_request_from_cli` | `cli/requests.py` | `(args)` | see source | see source |
| `generation_options_from_generate_args` | `cli/requests.py` | `(args, request_spec)` | see source | see source |
| `generation_context_from_args` | `cli/requests.py` | `(args)` | see source | see source |
| `merge_generate_spec_args` | `cli/requests.py` | `(args, request_spec)` | see source | see source |
| `resolve_ir_source` | `cli/requests.py` | `(ctx, value)` | see source | see source |
| `resolve_generation_event_log` | `cli/requests.py` | `(args, out_dir)` | see source | see source |
| `cli_module` | `cli/runtime.py` | `()` | Return the CLI package so command handlers can follow package-level patches. | see source |
| `main` | `cli/runtime.py` | `(argv)` | see source | see source |
| `_resolve_artifact_output_path (priv)` | `cli/runtime.py` | `(value)` | Resolve a user-selected intermediate output inside a project temp_files folder. | see source |
| `_render_log_watch_markdown (priv)` | `cli/runtime.py` | `(protocol, watch)` | see source | see source |
| `_write_roundtrip_report (priv)` | `cli/runtime.py` | `(path, source, stages)` | see source | see source |
| `_generation_return_code (priv)` | `cli/runtime.py` | `(manifest)` | Return success only when the published ZEIA/root guide and nested `source/` final artifacts exist. | Reads filesystem state; returns `0` or `1`. |
