# Architecture

The product is the full repository: Python package, CLI, generation pipeline,
and (for AI clients) an MCP adapter over the same local API. Use it for ZEIA
inspect, protocol generation, validation, and ready-to-import packaging. AI
clients talk through MCP or the CLI; both call `fluent_pipeline`.

```text
AI client / CLI
   |
MCP adapter / direct CLI
   |
fluent_pipeline Python API
   |
FluentCoder, FluentControl integration, media tools, validation, packaging
```

## Interfaces

One pipeline, three doors:

1. **Python API** (`fluent_pipeline`) : implementation and reusable service boundary
2. **CLI** : `python -m fluent_pipeline.cli …` for development, automation, offline work
3. **MCP** : structured tools for AI clients (`mcp_server.py` / `mcp_gateway.py`);
   see [MCP_TOOLS.md](MCP_TOOLS.md)

The MCP adapter imports the same services the CLI uses. It does not duplicate
generation, validation, media, or packaging logic, and it does not run
arbitrary shell commands.

## Repository layout

- `source/00-shared/` : shared `tecan_common` helpers installed into the repo `.venv`
- `source/01-project-reader/` : ZEIA, XSCR, GWL, and pattern inspection
- `source/02-worklist-builder/` : GWL worklist generation
- `source/03-protocol-builder/` : protocol engine, CLI, and MCP adapter
- `source/04-protocol-simulator/` : TypeScript/Vite simulator for bundles and Fluent asset caches
- `source/tools/` : generators for simulator assets and API V2 workflow data
- `scripts/` : install, smoke testing, and other repo automation
- `docs/` : installation, architecture, tools, and safety
- `ready-to-import/` : local generated handoff bundles; generated contents are ignored

### Editing boundaries

- Hand-edit simulator sources in `source/04-protocol-simulator/src/**` and related scripts/config.
- Generated simulator assets under `source/04-protocol-simulator/public/models/fluent/**` should be regenerated, not hand-edited.
- Hand-edit tooling under `source/tools/{simulator,api_v2,connectors,registry,prompt,common}/`.
- Generated tooling reports live under `ready-to-import/_shared/temp_files/build/api_v2/`.
- If you change a generator, rerun the generator instead of patching its output.

## Main modules

- `fluent_pipeline/mcp_server.py`: MCP tools, resources, and reusable prompt.
- `fluent_pipeline/mcp_gateway.py`: safety checks and calls into the Python API.
- `fluent_pipeline/cli/`: command-line interface.
- `fluent_pipeline/generation_workflow.py`: protocol generation orchestration.
- `fluent_pipeline/validation.py`: ready-to-import validation gates.
- `fluent_pipeline/exports.py`: ready-to-import publish.

## Workspace layout

Generated work lives under `ready-to-import/`:

- `<context>/temp_files/` , per-project scratch
- `<protocol>_vN/` , published bundles
- `_shared/temp_files/build/` , shared indexes, setuptools staging, api_v2 reports
- `_shared/temp_files/logs/` , event logs

## Transport

MCP uses local stdio. ZEIA files, logs, generated scripts, and instrument data
stay on the same computer as the client. The server does not open a network
listener.

Deeper derived inventory: [source-of-truth/](source-of-truth/README.md).
