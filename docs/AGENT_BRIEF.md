# Agent brief (short start)

Prefer this over pasting full AGENTS.md into a chat.

## Preferred: MCP or CLI bootstrap

When the Fluent MCP server is connected:

1. `fluent_bootstrap_status`: doctor + list-projects, returns `next_step`
   (`allowed_tools` / `blocked_tools` / `unlock_generate_after`)
2. After inspect: `fluent_bootstrap_status(inspected=true)` to unlock generate
3. Mode pick: `fluent_resolve_brief_mode(intent=...)` or
   `fluent_agent_brief(intent=...)` (repair/new-script/simulator/install keywords);
   resource mirror: `fluent://brief/{mode}`
4. Or `fluent_agent_brief(mode=...)` when you already know the mode

Bootstrap resource mirror (read-only, no doctor.md write): `fluent://bootstrap`

Same payload without MCP (from `source/03-protocol-builder`):

```bash
python -m fluent_pipeline.cli bootstrap-status
python -m fluent_pipeline.cli bootstrap-status --install-missing --confirm-install
python -m fluent_pipeline.cli bootstrap-status --inspected
```

Checklist / intent map from repo root:

```bash
python3 scripts/agent/agent-brief.py --intent "Script won't open; check diagnosis.md"
python3 scripts/agent/agent-brief.py --intent "use this ZEIA" --resolve-only
python3 scripts/agent/agent-brief.py --mode new-script
```

Follow `next_step.tool` (MCP) or `next_step.cli` (shell). Open
`source/03-protocol-builder/AGENTS.md` only for `##` headings the brief lists
under `NEED MORE` / `CONTRACT`. Cursor enforces the start gate via
`.cursor/rules/hard-start-gate.mdc`.

**Token rule:** `fluent_inspect_project` is summary-only. Mine names with
`fluent_project_query` / `project-find --limit`. Never dump full `manifest.json`
or `labware_catalog.json` into the chat.
## CLI fallback

```text
From the Fluent-AI-Assistance repo root, run `python3 scripts/agent/agent-brief.py --mode <MODE>` and follow that output. Only open AGENTS.md sections the brief points to.
```

Replace `<MODE>`:

| Mode | When |
|------|------|
| `install` | Clone/install/wire MCP |
| `status` | Check doctor + projects before work |
| `new-script` | ZEIA → generate → ready-to-import |
| `repair` | FluentControl dialog / runtime failure |
| `simulator` | Simulator UI/assets |

Examples:

```bash
python3 scripts/agent/agent-brief.py --mode status
python3 scripts/agent/agent-brief.py --mode new-script
./scripts/agent/agent-brief.sh --mode repair
```

Shared implementation: `fluent_pipeline/agent_brief.py` (CLI script is a thin wrapper).

The brief is a checklist. Deep rules stay in `AGENTS.md` / `source/03-protocol-builder/AGENTS.md` and are read on demand.
