# Capabilities (technical)

Plain-language overview lives in the [README](../README.md). This page lists the
concrete file types, artifacts, and pipelines behind those bullets.

## Import and inspect

- Imports and inspects `.zeia`, `.xscr`, and `.gwl` files
- Lists scripts and mines reusable command patterns from your own full ZEIA export
- Finds real source contracts for external or vendor commands before generation

`fluent_inspect_project` returns a **summary only**. Mine ZEIA names with
`fluent_project_query` / `project-find`. Do not load full `manifest.json` into
chat. See [AGENT_BRIEF.md](AGENT_BRIEF.md).

## Diagnose

- Diagnoses FluentControl logs and failed scripts

## Spec, generate, package

- Builds reviewed `request.spec.yaml` and `protocol.ir.json` artifacts
- Simulates, compiles, validates, and packages FluentControl scripts
- Validates checksums, dependencies, worktables, and ready-to-import archives

## Media and tooling

- Processes TouchTools prompt images, GIFs, and audio
- Hosts generators for simulator assets and API V2 workflow data

Related: [MCP_TOOLS.md](MCP_TOOLS.md), [ARCHITECTURE.md](ARCHITECTURE.md),
[SAFETY.md](SAFETY.md).
