# Safety Model

Generated FluentControl scripts are drafts until reviewed on the target system.
An import-clean archive is not automatically Script Editor load-clean or
hardware-run-ready.

## MCP boundaries

- Local stdio only; no network listener.
- Tools call allowlisted `fluent_pipeline` Python services.
- Output paths stay under project, build, and ready-to-import roots.
- Extra write roots need the explicit `TECAN_MCP_WRITE_ROOTS` environment
  variable.
- Mutating operations are serialized so generation jobs do not overlap.
- Destructive replacements and final packaging need confirmation arguments.
- User input is never passed to a shell.

## Operator responsibilities

Before hardware execution:

1. Review `request.spec.yaml` and `protocol.ir.json`.
2. Read validation and worktable reports.
3. Import and open the script in FluentControl Script Editor.
4. Confirm dependencies, deck state, labware, adapters, fingers, and liquids.
5. Validate movement cautiously on the target instrument.

Never treat an AI response as authorization to bypass laboratory or instrument
safety procedures.
