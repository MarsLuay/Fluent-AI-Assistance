# Architecture

Fluent AI-Assistance is local tooling for Tecan FluentControl workflows:
project inspection, protocol generation, validation, ready-to-import packaging,
and a simulator/tooling surface.

```text
AI client and CLI
       |
MCP adapter and direct CLI
       |
fluent_pipeline Python API
       |
FluentCoder + integration + media + validation + packaging
```

The CLI, MCP adapter, and direct Python API share `fluent_pipeline` services.
The primary protocol-builder entrypoints are the CLI runtime,
`mcp_server.py`, bootstrap, application services, generation workflow, and
exports. The sibling packages provide shared Tecan helpers, project reading,
worklist building, simulator assets, and repository tools.

Canonical repository layers:

- `source/00-shared/`: shared helpers and command registry.
- `source/01-project-reader/`: ZEIA, XSCR, GWL, and pattern inspection.
- `source/02-worklist-builder/`: structured worklist generation.
- `source/03-protocol-builder/`: protocol pipeline, CLI, MCP, validation, and packaging.
- `source/04-protocol-simulator/`: TypeScript/Vite simulator.
- `source/tools/`: simulator, API, connector, registry, prompt, and common tooling.
- `ready-to-import/`: generated local handoff bundles and scratch outputs.

The generation flow is evidence-first: imported project data and shared
registries supply contracts, then request specs and protocol IR feed rendering,
validation, and packaging. Generated outputs and host-derived assets are not
canonical source files for hand edits.
