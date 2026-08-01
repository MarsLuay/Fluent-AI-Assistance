# Module: fluent-pipeline-overview

**Paths:** `source/03-protocol-builder/fluent_pipeline/`
**Purpose:** Protocol import, generation, validation, packaging. CLI and MCP share the same Python API.
**Public surface:** clustered in module pages below
**Depends on:** fluentcoder (`libs/fluentcoder`), shared tecan libs
**Invariants:** ZEIA-mined catalogs over baked product law; `ready-to-import/` gitignored.

## Clusters

| Module id | Purpose | Files |
| --- | --- | --- |
| [fluent-pipeline-api-v2](fluent-pipeline-api-v2.md) | FluentControl API v2 command models, runtime controllers, stepped execution, and command validators. | 35 |
| [fluent-pipeline-cli](fluent-pipeline-cli.md) | CLI parser, request builders, command modules, and result rendering. | 13 |
| [fluent-pipeline-exports-mining](fluent-pipeline-exports-mining.md) | ZEIA/import mining and packaging: catalogs, liquid classes, connectors, drivers, bindings, ready-to-import exports. | 12 |
| [fluent-pipeline-gates-validation](fluent-pipeline-gates-validation.md) | Readiness gates, IR/worktable/archive gates, validation and spec lint. | 10 |
| [fluent-pipeline-generation](fluent-pipeline-generation.md) | Generation workflow, request specs, IR planning, repair, application service facades. | 14 |
| [fluent-pipeline-liquid-classes](fluent-pipeline-liquid-classes.md) | Portable liquid_classes.json mining (existing deep page). | 1 |
| [fluent-pipeline-mcp](fluent-pipeline-mcp.md) | MCP tool surface and gateway into the same Python API as CLI. | 3 |
| [fluent-pipeline-misc](fluent-pipeline-misc.md) | Remaining root modules not clustered above (keep small). | 4 |
| [fluent-pipeline-ops-media](fluent-pipeline-ops-media.md) | Media conversion, prompt media, simulator scene hooks, logs, diagnostics, checksums, progress. | 13 |
| [fluent-pipeline-project](fluent-pipeline-project.md) | Project context paths, store/catalog, bootstrap, config, runner I/O. | 10 |
| [fluent-pipeline-protocol-ir](fluent-pipeline-protocol-ir.md) | Protocol IR load/normalize/schema and related IR helpers. | 8 |
| [fluent-pipeline-support](fluent-pipeline-support.md) | Aliases, labware contracts, subroutines, worktable helpers, variables, fluentcoder glue, policies. | 33 |
