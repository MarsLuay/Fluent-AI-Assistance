# Fluent AI-Assistance

Local Python tools for reading Tecan ZEIA projects, understanding existing
FluentControl scripts, and generating validated ready-to-import protocol
bundles.

The product is the full repository: Python package, CLI, generation pipeline,
and (for AI clients) an MCP adapter over the same local API.

```text
AI client / CLI
   |
MCP adapter / direct CLI
   |
fluent_pipeline Python API
   |
FluentCoder, FluentControl integration, media tools, validation, packaging
```

## Start an AI on a job (token-cheap)

Prefer MCP when wired: `fluent_bootstrap_status`, then follow `next_step` (or
`fluent_agent_brief(mode=...)`). Same gate without MCP:

```bash
cd source/03-protocol-builder
python -m fluent_pipeline.cli bootstrap-status
```

Checklist-only CLI fallback:

```text
From the Fluent-AI-Assistance repo root, run `python3 scripts/agent/agent-brief.py --mode new-script` and follow that output. Only open AGENTS.md sections the brief points to.
```

Modes: `install`, `status`, `new-script`, `repair`, `simulator`. See [Agent brief](docs/AGENT_BRIEF.md) and [MCP tools](docs/MCP_TOOLS.md). Cursor: `.cursor/rules/hard-start-gate.mdc`.

Inspect returns a summary only; mine ZEIA names with `fluent_project_query` /
`project-find` — do not load full `manifest.json` into chat.
## Install with an AI

Copy this entire box into an AI client that can run local commands and configure
MCP:

```text
Install Fluent AI-Assistance from https://github.com/MarsLuay/Fluent-AI-Assistance on this computer. Clone the repository to a sensible user-owned tools directory as Fluent-AI-Assistance (for example `git clone https://github.com/MarsLuay/Fluent-AI-Assistance.git Fluent-AI-Assistance`), or run `git pull --ff-only` if that clone already exists. From the repository root run `powershell -ExecutionPolicy Bypass -File .\scripts\install\install.ps1`. Read the generated `.mcp/server-config.json`, merge only its `fluent-ai-assistance` entry into this client's MCP configuration, and reload or reconnect MCP. Then call `fluent_status` and `fluent_bootstrap_status` to verify it works. The Python API and protocol-builder CLI stay available in the same install. Do not install Tecan drivers, modify FluentControl, write to `C:\ProgramData\Tecan`, upload ZEIA files or logs, or expose the MCP server over a network. If this client cannot edit its own MCP configuration, give me the exact configuration entry and destination file instead of claiming installation succeeded.
```

See [Installation](docs/INSTALLATION.md) for manual setup details. The same prompt
lives in [AI_INSTALL_PROMPT.md](docs/AI_INSTALL_PROMPT.md).

## Interfaces

Three surfaces, one implementation:

1. **Python API** (`fluent_pipeline`) , the real pipeline.
2. **CLI** , `python -m fluent_pipeline.cli …` for local work and scripts.
3. **MCP** , stdio tools for compatible AI clients (`docs/MCP_TOOLS.md`).

```powershell
.\.venv\Scripts\python.exe -m fluent_pipeline.cli doctor --install-missing --report ready-to-import\_shared\temp_files\logs\doctor.md
.\.venv\Scripts\python.exe -m fluent_pipeline.cli list-projects
```

## What it does

- Imports and inspects `.zeia`, `.xscr`, and `.gwl` files
- Lists scripts and mines reusable command patterns
- Finds real source contracts for external or vendor commands
- Diagnoses FluentControl logs and failed scripts
- Builds reviewed `request.spec.yaml` and `protocol.ir.json` artifacts
- Simulates, compiles, validates, and packages FluentControl scripts
- Hosts repository-level generators for registries and API V2 workflow data
- Processes TouchTools prompt images, GIFs, and audio
- Validates checksums, dependencies, worktables, and ready-to-import archives

## Project structure

- `source/00-shared/` , shared `tecan_common` helpers installed into the repo `.venv`
- `source/01-project-reader/` , ZEIA, XSCR, GWL, and pattern inspection
- `source/02-worklist-builder/` , GWL worklist generation
- `source/03-protocol-builder/` , protocol engine, CLI, and MCP adapter
- `source/04-protocol-simulator/` , TypeScript/Vite simulator for bundles and Fluent asset caches
- `source/tools/` , generators for simulator assets and API V2 workflow data
- `source/00-shared/tecan_common/data/command_registry.json` , shared command metadata
- `scripts/` , install, smoke testing, and other repo automation
- `docs/` , installation, architecture, tools, and safety
- `ready-to-import/` , local generated handoff bundles; generated contents are ignored

## Editing boundaries

Keep hand-edited sources separate from the generated artifacts they produce:

- Hand-edited simulator sources live in `source/04-protocol-simulator/src/**`, `scripts/**`, and app config in that folder.
- Generated simulator assets live under `source/04-protocol-simulator/public/models/fluent/**` and should be regenerated, not edited by hand.
- Hand-edited repository tooling lives under `source/tools/{simulator,api_v2,connectors,registry,prompt,common}/`, plus `source/tools/README.md` and `api_v2/API_V2_AGENT_ROSTER.md` / `api_v2/api_v2_methods.json`.
- Generated tooling reports live under `ready-to-import/_shared/temp_files/build/api_v2/`.
- If you change a generator, rerun the generator instead of patching its output.

## Development

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install\install.ps1
.\scripts\test\test-fast.ps1      # reader/worklist + protocol-builder + fluentcoder
.\scripts\test\test-mcp.ps1       # MCP gateway, self-test, smoke
.\scripts\test\test-simulator.ps1 # FluentControl-gated simulator suite
.\scripts\test\test-all.ps1       # everything above
```

## Safety

Generated scripts must be reviewed, opened in FluentControl Script Editor, and
validated on the target instrument before hardware execution. See
[Safety](docs/SAFETY.md).

## License

**PolyForm Noncommercial License 1.0.0** — see [LICENSE](LICENSE).

- Free for personal, hobby, education, research, and other noncommercial use
  (source is public; you may use, modify, and share under those terms).
- **Not** free for company / commercial use. Contact
  [marwanluay2005@gmail.com](mailto:marwanluay2005@gmail.com) for a commercial
  license.
- This is **source-available noncommercial**, not an OSI “Open Source” license
  (OSI requires commercial use be allowed).
- Third-party material retains its original license.

Required Notice: Copyright (c) 2026 Marwan Luay
(https://github.com/MarsLuay/Fluent-AI-Assistance)

Commercial / company use: contact [marwanluay2005@gmail.com](mailto:marwanluay2005@gmail.com)
