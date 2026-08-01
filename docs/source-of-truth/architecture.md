# Architecture

Fluent-AI-Assistance packages ZEIA/FluentControl import → protocol generation → validation → ready-to-import handoff (and a local simulator).

```text
AI client / CLI / MCP
        │
 fluent_pipeline (03-protocol-builder)
        │
   ┌────┴────┬──────────────────┬─────────────────┐
   │         │                  │                 │
 ZEIA mine   Generation         API v2 / runtime  Package
 catalogs    + Protocol IR      + gates           ready-to-import
   │         │                  │
   │    FluentCoder render      Simulator
   │    (libs/fluentcoder)      (04-protocol-simulator)
```

Three operator surfaces share one Python API: **CLI**, **MCP**, and direct **Python** imports. Sibling packages (`01-project-reader`, `02-worklist-builder`, `00-shared`, `source/tools`) feed inspect/index/GWL/simulator assets into that pipeline.

## fluent_pipeline ownership (clusters)

| Cluster | Owns |
| --- | --- |
| exports-mining + liquid-classes | ZEIA → JSON catalogs / connectors / drivers / bindings / package export |
| generation | Request specs, workflow stages, repair, application_services facades |
| protocol-ir | IR load/normalize/schema |
| api-v2 | Live/offline FluentControl command + runtime surfaces |
| gates-validation | Readiness + validate gates |
| cli / mcp | Operator entrypoints over the same services |
| project | Context paths, store, bootstrap |
| ops-media | Logs, media, diagnostics, progress |
| support | Aliases, subroutines, worktable helpers, fluentcoder glue |

## Workspace paths

| Path | Role |
| --- | --- |
| `ready-to-import/<context>/temp_files/` | Per-context workflow scratch (extract, specs, generations, error_logs) |
| `ready-to-import/<protocol>_vN/` | Published import bundles |
| `ready-to-import/_shared/temp_files/build/` | Shared tooling scratch (indexes, setuptools staging, api_v2 reports) |
| `ready-to-import/_shared/temp_files/logs/` | Workflow event JSONL and tool logs |
| `ready-to-import/_shared/temp_files/cache/` | Catalog + ZEIA reference caches |

Do not recreate package-local `source/*/build/` for tooling output. Setuptools `build-base` is redirected via each package `setup.cfg`.

## Major data flows

1. **Import ZEIA** → `labware_catalog.json`, `liquid_classes.json` (`tecan.liquid_classes.v2`), `driver_macros.json`, `script_folder_bindings.json`, meshes/textures under context.
2. **Generate protocol** → request spec → IR → FluentCoder DSL → renderer → `.xscr` / worktable; DeviceAlias/AvailableID fail-closed.
3. **Resolve liquid class GUID** → SQL index → portable JSON → site `generation.yaml` guid when configured (never invent product GUID).
4. **Validate / gates** → readiness registry + IR/worktable/archive gates before package.
5. **Simulate** → register ZEIA catalog; `hardwareProfileFromZeia` from FunctionalGroup (+ exact tube phrases only).

## Policy invariants (lab-agnostic)

- No baked host/PNNL product law in shipped defaults.
- Fail closed when ZEIA device bindings / catalog evidence missing.
- xlqc params never invented , mine tags only.
