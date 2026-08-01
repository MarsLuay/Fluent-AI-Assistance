# Source of truth (derived)

**Code is authority.** This tree is regenerated with `/update-docs`. Do not invent APIs here.

- **Target:** `.Projects/Fluent-AI-Assistance`
- **Docs root:** `docs/source-of-truth/`
- **Last updated:** 2026-08-01
- **Mode:** full `fluent_pipeline` inventory (clustered) + path layout sync (`scripts/` / `source/tools/` / simulator `scripts/` subfolders) + prior fluentcoder/simulator pages

## Coverage counts

| Layer | Documented | Notes |
| --- | --- | --- |
| Modules | 17 | 11 fluent_pipeline clusters + overview + prior fluentcoder/simulator pages |
| Function pages | 15 | All `fluent_pipeline` public (+ significant private) symbols clustered |
| Type pages | 8+ | Classes per cluster; LC schema deep page retained |
| `fluent_pipeline` files | 156/156 | Every `.py` file assigned to a cluster |
| Entrypoints | updated | CLI (49), MCP (25 tools), bootstrap, sibling packages |
| Conflicts | see `conflicts.md` | |

## How to read

1. [architecture.md](architecture.md) , system map
2. [entrypoints.md](entrypoints.md) , CLI / MCP / package surfaces
3. [cross-cutting.md](cross-cutting.md) , config, paths, fail-closed ZEIA policy
4. [modules/fluent-pipeline-overview.md](modules/fluent-pipeline-overview.md) , cluster index
5. [modules/](modules/) → [functions/](functions/) → [types/](types/)
6. [conflicts.md](conflicts.md) , open logic conflicts / dupes

## fluent_pipeline clusters

| Module id | Concern |
| --- | --- |
| [fluent-pipeline-api-v2](modules/fluent-pipeline-api-v2.md) | API v2 commands / runtime / validators |
| [fluent-pipeline-cli](modules/fluent-pipeline-cli.md) | CLI |
| [fluent-pipeline-mcp](modules/fluent-pipeline-mcp.md) | MCP |
| [fluent-pipeline-generation](modules/fluent-pipeline-generation.md) | Generation workflow / request specs |
| [fluent-pipeline-protocol-ir](modules/fluent-pipeline-protocol-ir.md) | Protocol IR |
| [fluent-pipeline-exports-mining](modules/fluent-pipeline-exports-mining.md) | ZEIA catalogs / package export |
| [fluent-pipeline-liquid-classes](modules/fluent-pipeline-liquid-classes.md) | xlqc → liquid_classes.json (deep) |
| [fluent-pipeline-project](modules/fluent-pipeline-project.md) | Project context / store / bootstrap |
| [fluent-pipeline-gates-validation](modules/fluent-pipeline-gates-validation.md) | Gates / validation |
| [fluent-pipeline-ops-media](modules/fluent-pipeline-ops-media.md) | Media / logs / diagnostics |
| [fluent-pipeline-support](modules/fluent-pipeline-support.md) | Aliases / subroutines / glue |
| [fluent-pipeline-misc](modules/fluent-pipeline-misc.md) | Tiny remainder |

## Skipped trees

| Path | Reason |
| --- | --- |
| `ready-to-import/` (except README) | gitignored runtime artifacts |
| `node_modules/`, `build/` / `dist/` caches | vendored / generated |
| `inspirations/`, `static-analysis/` clones | read-only externals |
| Retired `Inspiration/` | no longer a checkout root; optional manuals under `_shared/temp_files/manuals/` |
| `source/01-project-reader`, `02-worklist-builder` | sibling packages; summarized in entrypoints / cross-cutting |
| Simulator UI chrome beyond `labwareCatalog` / parsers hooks | partial , see simulator module pages |
| fluentcoder full tree beyond catalog/renderer LC+device | expand later; deep pages exist for xlqc/device |

## Related non-SOT docs

Product / operator docs (README points here; not symbol inventory):

- [../CAPABILITIES.md](../CAPABILITIES.md): file types, artifacts, inspect rules behind README bullets
- [../ARCHITECTURE.md](../ARCHITECTURE.md): stack, interfaces, repo layout
- [../AI_INSTALL_PROMPT.md](../AI_INSTALL_PROMPT.md), [../INSTALLATION.md](../INSTALLATION.md)
- [../AGENT_BRIEF.md](../AGENT_BRIEF.md), [../MCP_TOOLS.md](../MCP_TOOLS.md), [../SAFETY.md](../SAFETY.md)
- `AGENTS.md`, nested `source/*/AGENTS.md`, [../../CONTRIBUTING.md](../../CONTRIBUTING.md)

Domain canvas (debt tracker): vault `zeia-lab-agnostic-debt.canvas.tsx`, not a
substitute for this tree.
