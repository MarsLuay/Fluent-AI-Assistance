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
- `fluent_pipeline/application_services.py` + `authoring_status.py`: shared Python/CLI/MCP results and authoring/recovery state.
- `fluent_pipeline/workflows/generation/`: canonical protocol generation orchestration; `generation_workflow.py` is the compatibility facade.
- `fluent_pipeline/gates/`: canonical readiness evaluators; `validation.py` orchestrates reports and preserves compatibility wrappers.
- `fluent_pipeline/exports.py`: ready-to-import publish.

## Workspace layout

Generated work lives under `ready-to-import/`:

- `<context>/temp_files/` , per-project scratch
- `<protocol>_vN/` , published bundles
- `_shared/temp_files/build/` , shared indexes, setuptools staging, api_v2 reports
- `_shared/temp_files/logs/` , event logs

Published bundle roots stay operator-focused: `<protocol>_vN.zeia`,
`run_tecan_bundle_setup.bat`, `RECREATE_SCRIPT.md`, `media/`, and `source/`
(plus optional `RECIPE_GROUP_NOTES.md`). PowerShell helpers, manifests,
`request.spec.yaml`, `protocol.ir.json`, `metadata.json`, generated Python, and
reports live under `source/`. Runtime diagnostics may create `temp_files/`.

## Transport

MCP uses local stdio. ZEIA files, logs, generated scripts, and instrument data
stay on the same computer as the client. The server does not open a network
listener.

## RGA evidence boundaries

RGA route and storage assessments are source-backed logical metadata carried
alongside the protocol IR. They classify available vectors, grip modes, regrip
paths, storage order, occupancy, and ambiguity from imported evidence; they do
not add those assessments to `RGA1_TransferLabware` command XML.

The protocol-builder and simulator must keep these boundaries visible:

- A route classification is not PathFinder geometry, contouring, collision
  avoidance, or proof that a taught vector is accurate on the target arm.
- Simulator metadata reports logical occupancy and physical limitations; it
  cannot prove gripper clearance, force, retention, deck state, or instrument
  readiness.
- Physical readiness remains a separate source-backed gate. Missing or
  ambiguous route/storage evidence stays reviewable and fail-closed rather than
  being inferred from labware names, coordinates, or a successful simulation.

Durable agent architecture: [project memory](project-memory/architecture.md). Current-code detail belongs in Serena.
