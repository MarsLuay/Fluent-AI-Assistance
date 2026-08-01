# Entrypoints

| Surface | Path | Notes |
| --- | --- | --- |
| Protocol-builder CLI | `fluent_pipeline/cli/` (`parser.py`, `runtime.py`, `commands/*`) | Primary offline CLI; console script `protocol-builder` |
| CLI `__main__` | `fluent_pipeline/cli/__main__.py` | `python -m fluent_pipeline.cli` |
| MCP server | `fluent_pipeline/mcp_server.py` | stdio tools (`fluent_*`); console script `tecan-ai-mcp` |
| MCP gateway | `fluent_pipeline/mcp_gateway.py` | safety + service calls |
| Application services | `fluent_pipeline/application_services.py` | Facade DTOs for generate/import/inspect/repair |
| Generation workflow | `fluent_pipeline/generation_workflow.py` + `workflows/generation/` | orchestration (stages partially extracted) |
| Export / package | `fluent_pipeline/exports.py` | ready-to-import publish |
| Bootstrap | `fluent_pipeline/bootstrap.py` | editable workspace install |
| FluentCoder render | `libs/fluentcoder/.../compiler/renderer.py` | `.xscr` emit |
| Catalog indexer | `libs/fluentcoder/.../catalog/indexer.py` | SQL index from install/ZEIA |
| Project reader CLI | `source/01-project-reader/tecan_reader/cli.py` | `project-reader` / `tecan-reader` |
| Worklist CLI | `source/02-worklist-builder/tecan_worklist/cli.py` | `worklist-builder` / `tecan-worklist` |
| Repo tools | `source/tools/{simulator,api_v2,connectors,registry,prompt,common}/` | simulator assets, API V2 mining, prompt builder |
| Simulator | `source/04-protocol-simulator/` | Vite/React; `launch-simulator` / `tools/simulator/launch_simulator.py` |
| Install script | `scripts/install/install.ps1` | venv + MCP self-test/smoke (bootstrap `next_step`) + `.mcp/server-config.json` |
| Agent brief CLI | `scripts/agent/agent-brief.py` | token-cheap mode checklist |
| Test suites | `scripts/test/test-suite.ps1` | fast/mcp/simulator/all wrappers |

## CLI command set (parser)

49 top-level commands in `fluent_pipeline/cli/parser.py`, including: `doctor`, `bootstrap-status`, `setup`, `import-project`, `list-projects`, `generate`, `request-spec`, `validate-spec`, `verify-bundle`, `process-media`, `worktable-diff`, `launch-simulator`, and related inspect/catalog/IR helpers.

Media conversion helpers (`convert_video_to_gif`, `normalize_worktable_gif`, `compare_xscr_minimal_edit`) are **library APIs** under `media_convert.py` / `minimal_edit.py`. They are not separate CLI verbs. Prefer `process-media` or call the Python helpers directly.

## MCP tools (25)

`fluent_status`, `fluent_agent_brief`, `fluent_resolve_brief_mode`, `fluent_bootstrap_status`, `fluent_list_projects`, `fluent_import_project`, `fluent_inspect_project`, `fluent_project_query`, `fluent_inspect_script`, `fluent_find_external_command`, `fluent_diagnose`, `fluent_parse_fluent_log`, `fluent_create_request_spec`, `fluent_validate_request_spec`, `fluent_generate_protocol`, `fluent_plan_repair`, `fluent_apply_repair`, `fluent_verify_bundle`, `fluent_process_media`, `fluent_verify_archive`, `fluent_list_capabilities`, `fluent_run_safe_cli`, `fluent_run_opt_in_cli`, `fluent_worktable_diff`, `fluent_summarize_simulation`

Session start: `fluent_bootstrap_status` or CLI `bootstrap-status`; mode via `fluent_resolve_brief_mode(intent=...)` / `fluent_agent_brief(intent=...)`.

Resources: `fluent://status`, `fluent://bootstrap`, `fluent://brief/{mode}`, `fluent://projects`, `fluent://projects/{name}`, `fluent://capabilities`. Prompt: `create_fluent_protocol`.

Cluster function tables: [modules/fluent-pipeline-overview.md](modules/fluent-pipeline-overview.md).
