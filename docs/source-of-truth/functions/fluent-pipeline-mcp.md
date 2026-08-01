# Functions: fluent-pipeline-mcp

Source roots: `fluent_pipeline/` (3 files)

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `_extra_write_roots (priv)` | `mcp_gateway.py` | `()` | see source | see source |
| `resolve_process_media_ir_path` | `mcp_gateway.py` | `(target, ir_path)` | Resolve the protocol IR used for prompt media processing.  If the caller supplies ``ir_path``, use i | see source |
| `ProtocolBuilderGateway` | `mcp_gateway.py` | class | Selected fluent_pipeline services exposed to MCP clients. | , |
| `ProtocolBuilderGateway.status` | `mcp_gateway.py` | `()` | see source | see source |
| `ProtocolBuilderGateway.agent_brief` | `mcp_gateway.py` | `(mode)` | Mode-scoped checklist for connected agents. | see source |
| `ProtocolBuilderGateway.bootstrap_status` | `mcp_gateway.py` | `(install_missing, confirm_install, write_report, inspected)` | Doctor + list-projects; returns `next_step` with tool gates. | optional doctor.md; install_missing needs confirm |
| `ProtocolBuilderGateway.projects` | `mcp_gateway.py` | `()` | see source | see source |
| `ProtocolBuilderGateway.project` | `mcp_gateway.py` | `(name)` | see source | see source |
| `ProtocolBuilderGateway.import_archive` | `mcp_gateway.py` | `(archive)` | see source | see source |
| `ProtocolBuilderGateway.inspect_script` | `mcp_gateway.py` | `()` | see source | see source |
| `ProtocolBuilderGateway.find_external_command` | `mcp_gateway.py` | `(command_name)` | see source | see source |
| `ProtocolBuilderGateway.diagnose` | `mcp_gateway.py` | `(input_path)` | see source | see source |
| `ProtocolBuilderGateway.parse_log` | `mcp_gateway.py` | `(log_path)` | see source | see source |
| `ProtocolBuilderGateway.create_request_spec` | `mcp_gateway.py` | `(intent)` | see source | see source |
| `ProtocolBuilderGateway.validate_request_spec` | `mcp_gateway.py` | `(spec_path)` | see source | see source |
| `ProtocolBuilderGateway.generate` | `mcp_gateway.py` | `(spec_path)` | see source | see source |
| `ProtocolBuilderGateway.plan_repair` | `mcp_gateway.py` | `(draft_path)` | see source | see source |
| `ProtocolBuilderGateway.apply_repair` | `mcp_gateway.py` | `(draft_path)` | see source | see source |
| `ProtocolBuilderGateway.verify_bundle` | `mcp_gateway.py` | `(compiled_xscr)` | see source | see source |
| `ProtocolBuilderGateway.process_media` | `mcp_gateway.py` | `(target)` | see source | see source |
| `ProtocolBuilderGateway.verify_archive` | `mcp_gateway.py` | `(archive)` | see source | see source |
| `ProtocolBuilderGateway.cli_capabilities` | `mcp_gateway.py` | `()` | Return the audited MCP coverage for every registered CLI command. | see source |
| `ProtocolBuilderGateway.run_safe_cli` | `mcp_gateway.py` | `(operation, arguments)` | Run one audited offline CLI command without invoking a shell.  The parser and its command handler ar | see source |
| `ProtocolBuilderGateway.run_opt_in_cli` | `mcp_gateway.py` | `(operation, arguments)` | Run an explicitly enabled local-execution CLI operation.  The server process must have the operation | see source |
| `ProtocolBuilderGateway.diff_worktable` | `mcp_gateway.py` | `(protocol_ir_path)` | Compare a protocol IR's worktable requirements without running code. | see source |
| `ProtocolBuilderGateway.summarize_simulation` | `mcp_gateway.py` | `(simulation_json_path)` | Summarize existing simulation JSON without executing a protocol draft. | see source |
| `ProtocolBuilderGateway._cli_parser_and_commands (priv)` | `mcp_gateway.py` | `()` | see source | see source |
| `ProtocolBuilderGateway._validate_safe_cli_request (priv)` | `mcp_gateway.py` | `(operation, parsed)` | see source | see source |
| `json_text` | `mcp_gateway.py` | `(value)` | see source | see source |
| `generation_request_from_mcp` | `mcp_requests.py` | `(spec_source)` | see source | see source |
| `project_import_request_from_mcp` | `mcp_requests.py` | `(archive)` | see source | see source |
| `project_inspection_request_from_mcp` | `mcp_requests.py` | `(name)` | see source | see source |
| `log_analysis_request_from_mcp` | `mcp_requests.py` | `(log_path)` | see source | see source |
| `request_spec_create_request_from_mcp` | `mcp_requests.py` | `(intent)` | see source | see source |
| `request_spec_validation_request_from_mcp` | `mcp_requests.py` | `(spec_path)` | see source | see source |
| `fluent_status` | `mcp_server.py` | `()` | Check the local MCP, Python, workspace, and checksum configuration. | see source |
| `fluent_agent_brief` | `mcp_server.py` | `(mode, intent)` | Return a short mode-scoped checklist; `intent` auto-picks mode. | see source |
| `fluent_resolve_brief_mode` | `mcp_server.py` | `(intent, default)` | Map user intent text → brief mode (no checklist body). | see source |
| `fluent_bootstrap_status` | `mcp_server.py` | `(install_missing, confirm_install, write_report, inspected)` | Run doctor + list-projects and return the next required tool/step (with allowed/blocked tools). | optional doctor.md; confirm_install for install_missing |
| `fluent_list_projects` | `mcp_server.py` | `()` | List locally imported ZEIA project contexts. | see source |
| `fluent_import_project` | `mcp_server.py` | `(archive, name, activate, snapshots, force, confirm_replace)` | Import a ZEIA and optional Snapshot ZIPs into an isolated local context. | see source |
| `fluent_inspect_project` | `mcp_server.py` | `(name)` | Compact project summary + path pointers (no full manifest). | see source |
| `fluent_project_query` | `mcp_server.py` | `(pattern, context, kind, limit)` | Capped compact search over an imported ZEIA context. | see source |
| `fluent_inspect_script` | `mcp_server.py` | `(context, script, output_directory)` | Create a structured report for an imported FluentControl script. | see source |
| `fluent_find_external_command` | `mcp_server.py` | `(command_name, context, module, source_script, output_directory)` | Mine a real source usage before generating a vendor/external command. | see source |
| `fluent_diagnose` | `mcp_server.py` | `(input_path, context, script, error_file, output_directory)` | Diagnose an XSCR, ZEIA, GWL, protocol IR, or Python protocol draft. | see source |
| `fluent_parse_fluent_log` | `mcp_server.py` | `(log_path, output_directory)` | Parse a FluentControl/VisionX log into structured findings and Markdown. | see source |
| `fluent_create_request_spec` | `mcp_server.py` | `(intent, context, source_scripts, protocol_name, generation_options, output_path)` | Create the reviewable request.spec.yaml contract for a new protocol. | see source |
| `fluent_validate_request_spec` | `mcp_server.py` | `(spec_path)` | Lint request.spec.yaml before any protocol generation. | see source |
| `fluent_generate_protocol` | `mcp_server.py` | `(spec_path, context, ir_path, output_directory, mode, confirm_final, generation_options)` | Generate scaffold or final protocol artifacts from a reviewed spec and IR.  Final mode forces simula | see source |
| `fluent_plan_repair` | `mcp_server.py` | `(draft_path, context, simulation_json_path, output_directory)` | Build a project-aware repair plan for a generated Python draft. | see source |
| `fluent_apply_repair` | `mcp_server.py` | `(draft_path, output_path, context, simulation_json_path, apply_modeling, output_directory)` | Apply a reviewed repair plan and write a repaired Python draft. | see source |
| `fluent_verify_bundle` | `mcp_server.py` | `(compiled_xscr, draft_path, protocol_ir, worklist, source_projects, source_scripts, source_xscr, recreate_guide, output_directory)` | Run shared ready-validation over a generated compiled bundle. | see source |
| `fluent_process_media` | `mcp_server.py` | `(target, ir_path, confirm_in_place)` | Process replacement media in a bundle; requires explicit in-place confirmation.  When the bundle con | see source |
| `fluent_verify_archive` | `mcp_server.py` | `(archive)` | Audit a generated ZEIA archive, datastore metadata, and checksums. | see source |
| `fluent_list_capabilities` | `mcp_server.py` | `()` | List audited MCP coverage for every Fluent CLI command and safety exclusion. | see source |
| `fluent_run_safe_cli` | `mcp_server.py` | `(operation, arguments, confirm_mutation)` | Run one audited, offline-only CLI operation without using a shell.  Use ``fluent_list_capabilities`` | see source |
| `fluent_run_opt_in_cli` | `mcp_server.py` | `(operation, arguments, confirm_execution)` | Run a server-enabled Python-execution/setup CLI operation.  The MCP server process must have the env | see source |
| `fluent_worktable_diff` | `mcp_server.py` | `(protocol_ir_path, context, source_scripts, output_directory)` | Compare protocol worktable requirements without executing a draft or UI. | see source |
| `fluent_summarize_simulation` | `mcp_server.py` | `(simulation_json_path, protocol_ir_path)` | Summarize an existing simulation result without executing Python. | see source |
| `status_resource` | `mcp_server.py` | `()` | Current local server and protocol-builder status. | see source |
| `bootstrap_resource` | `mcp_server.py` | `()` | Read-only bootstrap status (`next_step` included). | no doctor.md write |
| `brief_resource` | `mcp_server.py` | `(mode)` | Mode-scoped agent checklist JSON. | invalid mode → ok:false payload |
| `projects_resource` | `mcp_server.py` | `()` | Imported project context inventory. | see source |
| `project_resource` | `mcp_server.py` | `(name)` | Manifest for an imported project context. | see source |
| `capabilities_resource` | `mcp_server.py` | `()` | Audited CLI-to-MCP coverage and intentional safety exclusions. | see source |
| `create_fluent_protocol` | `mcp_server.py` | `(request, context, source_script)` | Reusable safe workflow prompt for creating a FluentControl protocol. | see source |
| `main` | `mcp_server.py` | `(argv)` | see source | see source |
