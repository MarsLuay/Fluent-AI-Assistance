# MCP Interface

MCP exposes selected `fluent_pipeline` operations to compatible AI clients over
local stdio. The same services power the CLI and Python API.

## Tools

| Tool | Purpose | Writes files |
|---|---|---|
| `fluent_status` | Check local configuration and checksum support | No |
| `fluent_agent_brief` | Mode-scoped checklist; pass `intent` to auto-pick mode | No |
| `fluent_resolve_brief_mode` | One-liner intent → mode (`repair`/`new-script`/…) | No |
| `fluent_bootstrap_status` | Doctor + list-projects; returns `next_step` with `allowed_tools` / `blocked_tools` (+ optional doctor report). Pass `inspected=true` after inspect to unlock generate. | Optional report under `ready-to-import/_shared/temp_files/logs/doctor.md`; `install_missing` needs `confirm_install=true` |
| `fluent_list_projects` | List imported project contexts | No |
| `fluent_import_project` | Import ZEIA and Snapshot archives | Yes |
| `fluent_inspect_project` | Compact project summary + path pointers (`manifest_path`, `report_path`). Never returns full manifest arrays. | No |
| `fluent_project_query` | Search imported context (`pattern`, optional `context`/`kind`, `limit` default 20 max 50). Compact matches only. | No |
| `fluent_inspect_script` | Explain a source script and dependencies | Reports only |
| `fluent_find_external_command` | Mine a real vendor-command usage contract | Reports only |
| `fluent_diagnose` | Diagnose ZEIA, XSCR, GWL, IR, or Python inputs | Reports only |
| `fluent_parse_fluent_log` | Parse FluentControl/VisionX logs | Reports only |
| `fluent_create_request_spec` | Create `request.spec.yaml` | Yes |
| `fluent_validate_request_spec` | Lint a request specification | No |
| `fluent_generate_protocol` | Run scaffold or final generation | Yes |
| `fluent_plan_repair` | Build a project-aware repair plan for a generated draft | Reports only |
| `fluent_apply_repair` | Apply a reviewed repair plan and write a repaired draft | Yes |
| `fluent_verify_bundle` | Run shared ready-validation over a generated compiled bundle | Reports only |
| `fluent_process_media` | Process replacement prompt media | Yes, confirmation required |
| `fluent_verify_archive` | Audit a generated ZEIA | No |
| `fluent_list_capabilities` | Audit CLI-to-MCP coverage and intentional exclusions | No |
| `fluent_run_safe_cli` | Run an audited offline CLI command without a shell | Depends on command; confirmation required for mutations |
| `fluent_run_opt_in_cli` | Run server-enabled draft execution or setup commands | Yes, server opt-in and execution confirmation required |
| `fluent_worktable_diff` | Compare protocol worktable requirements without executing a draft | Reports only, or controlled artifacts |
| `fluent_summarize_simulation` | Summarize existing simulation JSON without executing Python | No |

## Migration

`fluent_project_info` was replaced by `fluent_inspect_project`. Update existing
MCP clients to call `fluent_inspect_project`; the new tool is the single project
inspection entry point. Treat ZEIA context as a database: use
`fluent_project_query` / CLI `project-find` to mine names. Do **not** load
`manifest.json` or `labware_catalog.json` into chat.

Final generation requires `confirm_final=true`. Replacing an imported project
requires `confirm_replace=true`. In-place media processing requires
`confirm_in_place=true`.

Call `fluent_list_capabilities` before using `fluent_run_safe_cli`. It classifies
every registered CLI command as a dedicated MCP tool, an audited offline bridge
operation, or an intentional safety exclusion. The bridge directly invokes the
CLI parser and handler in-process; it never invokes a shell.

`fluent_run_opt_in_cli` keeps `setup`, `simulate`, `compile`, `roundtrip`, and
`fluent-prepare-check` in the MCP interface. Before the MCP server starts, set
the per-operation flag returned by `fluent_list_capabilities` to `1`:

- `TECAN_MCP_ENABLE_SETUP=1` for `setup`
- `TECAN_MCP_ENABLE_DRAFT_EXECUTION=1` for `simulate`, `compile`, and `roundtrip`
- `TECAN_MCP_ENABLE_PREPARE_CHECK=1` for the current offline `fluent-prepare-check` shim

Each call additionally requires `confirm_execution=true`. The prepare check is
currently an offline compatibility report; it does not drive FluentControl.

## Resources

- `fluent://status`
- `fluent://bootstrap` — read-only mirror of `fluent_bootstrap_status` (`write_report=false`)
- `fluent://brief/{mode}` — mirror of `fluent_agent_brief` (`install`/`status`/`new-script`/`repair`/`simulator`)
- `fluent://projects`
- `fluent://projects/{name}`
- `fluent://capabilities`

## Session start

Call `fluent_bootstrap_status` first (or read `fluent://bootstrap`; CLI
`python -m fluent_pipeline.cli bootstrap-status`). Follow `next_step.tool` /
`next_step.cli`. Honor `allowed_tools` / `blocked_tools`: refuse
`fluent_generate_protocol` (and other blocked tools) until import + inspect
are done. After `fluent_inspect_project`, re-call bootstrap with
`inspected=true` (CLI: `--inspected`) so `next_step.action` becomes
`choose_workflow` and generate is allowed. Doctor auto-fix requires both
install flags (`install_missing` + `confirm_install`, or
`--install-missing --confirm-install`). Local deps only; not full `install.ps1`.
Cursor enforces the gate via `.cursor/rules/hard-start-gate.mdc`.

Prefer tools when you need `inspected=true` / install flags; use resources for
read-only mirrors (`fluent://bootstrap`, `fluent://brief/{mode}`).

## Prompt

`create_fluent_protocol` starts with bootstrap/brief, then inspect, contract,
scaffold, review, final-generation, and handoff.

## Scope

The MCP server does not expose:

- arbitrary shell execution or unreviewed CLI commands
- FluentControl driver installation
- direct writes into Tecan `ProgramData`
- FluentControl UI automation
- instrument initialization or hardware motion
- automatic Python-draft simulation, compilation, or roundtrips without a server opt-in
- automatic claims of Script Editor or hardware readiness
