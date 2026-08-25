# Quirks

- `fluent_bootstrap_status` is the documented session-start gate. After
  inspection, bootstrap must be checked again with `inspected=true` before
  generation is unlocked.
- `fluent_inspect_project` returns a compact summary. Use project-query tools
  for names and patterns; do not load full manifest or catalog arrays into
  agent context.
- `fluent_project_info` is obsolete; the current inspection entrypoint is
  `fluent_inspect_project`.
- The protocol-builder CLI is available as `python -m fluent_pipeline.cli` and
  the installed `protocol-builder` script; the MCP server is `tecan-ai-mcp`.
- The worklist and project-reader packages expose separate CLI entrypoints and
  depend on the shared package rather than duplicating its helpers.
