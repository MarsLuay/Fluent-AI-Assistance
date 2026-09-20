# Fluent AI Assistance compatibility policy

Policy version: `fluent.compatibility.v1`

This file is the only source of truth for what “backward compatible” means in
this repository. Issues in the #137–#145 migration series must consult it
instead of guessing which historical behavior has to survive.

The machine-checkable inventory lives beside this file in
[`compatibility-inventory.json`](compatibility-inventory.json). Tests compare
that inventory to live installed commands, CLI subcommands, MCP tools, and
public Python exports.

## Meaning

**Supported** means a caller-facing contract: an installed command, CLI
subcommand, MCP tool, documented Python export, or persisted artifact that a
previous Fluent AI Assistance user may still invoke. Supported surfaces stay
behavior-compatible across the current protocol-builder line, or they fail with
a stable diagnostic that tells the caller how to migrate.

**Deprecated** means still callable but scheduled for removal; emit a warning
only at that caller-facing boundary.

**Internal** means implementation. Names, module layout, private helpers,
parser limits, and duplicated workflow steps are not contracts.

**Removed** means the behavior must not return. Regression tests guard it.

Compatibility protects callers and persisted artifacts. It does not protect
accidental parser limits, implicit filesystem scans, duplicated business logic,
or already-deleted residue.

## Migration window

- Installed commands and CLI/MCP tool names on the current `main` line are
  supported until a future policy revision reclassifies them.
- Persisted protocol-builder project manifests with
  `schema_version` 1 or 2 migrate in place to version 3.
- Unknown or newer project-manifest schema versions are rejected; re-import the
  ZEIA with current `protocol-builder`.
- Project-index SQLite databases are rebuildable caches, not migrated
  artifacts. A schema mismatch requires `index.build` / `build_project_index`.

## Supported surfaces

| Surface | Owner path | Why it is a contract |
| --- | --- | --- |
| `protocol-builder` | `source/03-protocol-builder/pyproject.toml` | Installed console entry for generation/import/validate |
| `tecan-ai-mcp` | `source/03-protocol-builder/pyproject.toml` | Installed local MCP stdio adapter |
| `project-reader`, `tecan-reader` | `source/01-project-reader/pyproject.toml` | Installed ZEIA/XSCR/GWL inspection commands |
| `worklist-builder`, `tecan-worklist` | `source/02-worklist-builder/pyproject.toml` | Installed worklist conversion commands |
| Protocol-builder CLI subcommands | `source/03-protocol-builder/fluent_pipeline/cli/parser.py` | Command names/options and observable output |
| MCP tools named `fluent_*` | `source/03-protocol-builder/fluent_pipeline/mcp_server.py` | Tool names and request/result shapes |
| `fluent_pipeline.application_services` | `source/03-protocol-builder/fluent_pipeline/application_services.py` | Canonical CLI/MCP/Python implementation |
| `import-project` CLI/Python/MCP | application services + adapters | Callers depend on isolated project-context import |
| Compact `project-info` / `fluent_project_query` | `project_context.inspection_payload` / `query_project` | Token-capped inspect/query output |
| Project collections | `create-collection` / `list-collections` | Persisted multi-context capability |
| Public `tecan_reader` exports | `source/01-project-reader/tecan_reader/__init__.py` | Documented Python inspection/index API |
| Project manifest schema 3 | `PROJECT_MANIFEST_SCHEMA_VERSION` | Persisted import context |

CLI, MCP, and Python adapters for the same operation must call the same
application-service function. Parser handlers and MCP gateway helpers are not
the compatibility surface; the command/tool names and result shapes are.

## Not contracts

These remain implementation behavior. Do not keep them alive “for compatibility”:

- Default semantic truncation (50-script / 200-object preview limits).
- Silent XML parser failure skipping.
- Size-dependent loss of semantic fidelity.
- Manual multi-step orchestration that a one-shot `protocol-builder run` (#141)
  can wrap once that command exists.
- Internal dataclass/module layout and private CLI helper names.
- Parallel ZIP/XML parsers that compete with the canonical importer (#137).

## Removed (do not reintroduce)

| Behavior | Issue | Guard |
| --- | --- | --- |
| Production Obsidian-vault / newest-ZEIA auto-discovery | #144 | `tests/test_explicit_zeia_input.py` |
| Tracked `AGENTS.md.orig` / shadow instruction copies | #146 | no tracked `.orig` residue |
| Developer-home absolute paths in committed artifacts | #145 | `tests/test_repository_hygiene.py` |

#145 cleaned tracked path residue. Those paths were never a compatibility
contract.

## Persisted artifacts

### Project manifests

Current version: `PROJECT_MANIFEST_SCHEMA_VERSION = 3`.

`load_project` migrates supported older versions (1–2, or a missing version
treated as 1) by stamping `schema_version` 3 when the document already has the
v3 field set. It rejects any other version with a stable
`unsupported project manifest schema_version` error that tells the caller to
re-import.

### Project index

Current version: `tecan_reader.project_index.SCHEMA_VERSION = "2"`.

Indexes are derived caches. `build_project_index` always writes the current
schema. Search/summary refuse a mismatched schema and tell the caller to
rebuild. There is no row-level migration.

### Project collections

Collection manifests currently use `schema_version` 1 (`kind` =
`project_collection`). That document is supported as the collection contract;
it is not a project-context manifest.
