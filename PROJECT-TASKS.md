# Fluent AI-Assistance task ledger

Project ID/key: `fluent-ai-assistance`  
Schema version: `1`

The JSON ledger is canonical; this Markdown view mirrors the same four task
records and their verified routing metadata.

## Tasks

### `bootstrap-and-inspect` — Bootstrap and inspect an imported project context

Summary: Start with bootstrap status, import a project context when needed, and
inspect it before choosing a workflow.

Task paths:

- `source/03-protocol-builder/fluent_pipeline/bootstrap.py`
- `source/03-protocol-builder/fluent_pipeline/application_services.py`
- `docs/AGENT_BRIEF.md`
- `docs/MCP_TOOLS.md`

Required reads:

- `AGENTS.md`
- `docs/AGENT_BRIEF.md`
- `docs/MCP_TOOLS.md`

Invariants:

- Bootstrap status is the session-start gate.
- Generation remains blocked until import and inspection are done.
- Use compact project queries for names and patterns; do not load full manifests into chat.

Tests/checks: `python -m fluent_pipeline.cli bootstrap-status`,
`fluent_bootstrap_status`, `fluent_inspect_project`.

### `generate-and-package` — Generate and package a reviewed protocol

Summary: Use the shared `fluent_pipeline` services through the CLI, Python API,
or MCP to create a reviewed protocol package and ready-to-import handoff.

Task paths:

- `source/03-protocol-builder/fluent_pipeline/workflows/generation/workflow.py`
- `source/03-protocol-builder/fluent_pipeline/generation_workflow.py`
- `source/03-protocol-builder/fluent_pipeline/exports.py`
- `source/03-protocol-builder/fluent_pipeline/cli/runtime.py`
- `source/03-protocol-builder/fluent_pipeline/mcp_server.py`

Required reads:

- `AGENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/SAFETY.md`
- `docs/project-memory/architecture.md`

Invariants:

- CLI, MCP, and direct Python access the same pipeline services.
- Use mined project evidence and shared registries; do not invent vendor parameters or hardware configuration.
- Generated ready-to-import contents are handoff artifacts, not the source of truth for fixes.

Tests/checks: `fluent_create_request_spec`, `fluent_validate_request_spec`,
`fluent_generate_protocol`.

### `validate-handoff` — Validate a ready-to-import handoff

Summary: Run package, dependency, worktable, checksum, and archive checks before
handoff, while keeping hardware readiness as a separate review step.

Task paths:

- `source/03-protocol-builder/fluent_pipeline/validation.py`
- `source/03-protocol-builder/fluent_pipeline/exports.py`
- `docs/SAFETY.md`
- `docs/project-memory/architecture.md`

Required reads:

- `docs/SAFETY.md`
- `docs/MCP_TOOLS.md`
- `docs/project-memory/architecture.md`

Invariants:

- Readiness evaluators run through the shared validation path.
- Missing ZEIA bindings or catalog evidence fail closed.
- Offline package validation does not claim Script Editor or hardware readiness.

Tests/checks: `fluent_verify_bundle`, `fluent_verify_archive`, and review in
FluentControl Script Editor before hardware execution.

### `maintain-reader-worklist-simulator-tools` — Maintain reader, worklist, simulator, and repository tooling boundaries

Summary: Keep the sibling reader and worklist packages, simulator, shared
command registry, and repository tools aligned with the pipeline without
hand-editing generated assets.

Task paths:

- `source/01-project-reader/tecan_reader/cli.py`
- `source/02-worklist-builder/tecan_worklist/cli.py`
- `source/00-shared/tecan_common/data/command_registry.json`
- `source/04-protocol-simulator/package.json`
- `source/tools/README.md`

Required reads:

- `AGENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/project-memory/architecture.md`

Invariants:

- Keep shared command definitions and mined source patterns ahead of string heuristics.
- Regenerate simulator and tooling outputs through their generators.
- Keep generated handoff and host-derived assets outside canonical source edits.

Tests/checks: `project-reader`, `worklist-builder`, `launch-simulator`.
