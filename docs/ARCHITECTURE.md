# Architecture

Fluent AI-Assistance is a local Python workspace for ZEIA inspect, protocol
generation, validation, and ready-to-import packaging. AI clients talk to it
through MCP or the CLI; both call the same `fluent_pipeline` API.

```text
AI client / operator
   |
MCP adapter  or  CLI
   |
fluent_pipeline Python API
   |
FluentCoder, FluentControl integration, media tools, validation, packaging
```

## Interfaces

- **Python API:** implementation and reusable service boundary.
- **CLI:** development, automation, troubleshooting, offline operation.
- **MCP:** structured tools for compatible AI clients (`mcp_server.py` /
  `mcp_gateway.py`).

The MCP adapter imports the same services the CLI uses. It does not duplicate
generation, validation, media, or packaging logic, and it does not run
arbitrary shell commands.

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
