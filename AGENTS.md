# AGENTS.md

Fluent AI-Assistance , local Python tooling for Tecan FluentControl ZEIA inspect,
script generation, validation, and ready-to-import packaging (CLI, Python API,
and MCP).

This file is the **project entrypoint** for agents. Deep protocol-generation
rules live in the nested contract below. Do not duplicate them here.

## Nested contracts (read when relevant)

| Path | When |
|------|------|
| [`source/03-protocol-builder/AGENTS.md`](source/03-protocol-builder/AGENTS.md) | New script / ZEIA / `request.spec` / IR / generate / validate / ready-to-import |
| [`source/04-protocol-simulator/AGENTS.md`](source/04-protocol-simulator/AGENTS.md) | Simulator UI/assets (if present) |

Default ZEIA → new script work: open **`source/03-protocol-builder/AGENTS.md`** and work from that folder.

Lab-agnostic rule: script/worktable/subroutine/labware names come from the
user-provided full ZEIA (see **Lab / ZEIA name provenance** in the nested
protocol-builder contract). Do not commit lab-confidential names into docs or
examples. `tests/fixtures/**` are synthetic stubs only. Never treat them as a
lab template (see nested **Test fixtures vs ZEIA**).

## Layout

| Path | What |
|------|------|
| `source/00-shared/` | Shared Tecan common data (e.g. command registry) |
| `source/01-project-reader/` | ZEIA/XSCR/GWL inspect, pattern mining |
| `source/02-worklist-builder/` | Structured transfer → GWL |
| `source/03-protocol-builder/` | Generation pipeline (`fluent_pipeline`), packaging, validation |
| `source/04-protocol-simulator/` | TypeScript/Vite protocol simulator |
| `source/tools/` | Repo generators in subfolders (`simulator/`, `api_v2/`, `connectors/`, `registry/`, `prompt/`, `common/`) |
| `scripts/` | Install / agent-brief / MCP smoke / test suites (`install/`, `agent/`, `mcp/`, `test/`); see `scripts/README.md` |
| `ready-to-import/` | Published bundles + `_shared/temp_files` scratch; do not hand-edit generated artifacts as “fixes” |
| `docs/`, `.mcp/` | Docs and MCP config outputs |
| `.venv/` | Local Python env (prefer for CLI/tests) |

## Layer order

1. `01-project-reader` , inspect / mine source patterns
2. `02-worklist-builder` , only if CSV→GWL needed
3. `03-protocol-builder` , all new FluentControl script generation and packaging
4. `04-protocol-simulator` , simulator only when that surface is in scope

Command registry (before new string heuristics):
`source/00-shared/tecan_common/data/command_registry.json`
(via `source_command_registry_path()` / `command_registry_resource()` in protocol-builder).

## Project-wide rules

- Preserve exact FluentControl names, command IDs, aliases, modules, macros, labware, carriers, variables, timing, disabled state, execution settings.
- Do not invent command parameters, deck positions, labware, liquid classes, hardware config, or vendor behavior.
- Prefer mined source patterns and shared pipeline utilities over command-name string heuristics.
- Fix generator/shared architecture, not generated ZEIA/XSCR by hand.
- Keep scope tight; summarize errors. Do not paste full logs/XML.
- Do not run fluentcoder `author` / `chat` / `deploy` unless the nested contract and user explicitly allow.
- Do not write to `C:\ProgramData\Tecan` or install Tecan drivers unless the user explicitly asks for instrument-side setup.

## Quick pointers

- Token-cheap start (hard gate) → MCP `fluent_bootstrap_status` or CLI `python -m fluent_pipeline.cli bootstrap-status`; then `fluent_agent_brief` / `scripts/agent/agent-brief.py` ([docs/AGENT_BRIEF.md](docs/AGENT_BRIEF.md), [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md), `.cursor/rules/hard-start-gate.mdc`)
- Protocol CLI / generate / ready gates → `source/03-protocol-builder/AGENTS.md`
- Install / MCP → root `README.md`, `scripts/install/install.ps1`, `.mcp/`
- Method-source incorporation map (instrument dump) → under a bundle's
  `temp_files/tecan_method_source/INCORPORATION_MAP.md` when that collect exists
- Derived inventory → `docs/source-of-truth/`
