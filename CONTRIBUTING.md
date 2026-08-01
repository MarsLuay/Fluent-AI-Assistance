# Contributing

## Development setup

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install\install.ps1
```

Run `.\scripts\test-fast.ps1` before opening a pull request. Use
`.\scripts\test-mcp.ps1`, `.\scripts\test-simulator.ps1`, or
`.\scripts\test-all.ps1` when your change touches those areas or needs a full
sweep.

## Design rules

- Keep `fluent_pipeline` as the implementation and reusable Python API.
- Keep the CLI usable without MCP.
- Keep MCP thin: call the existing API instead of duplicating workflow logic.
- Do not expose arbitrary shell commands, driver installation, direct
  FluentControl database writes, or hardware movement through MCP.
- Add tests for new tool schemas, confirmation boundaries, and API delegation.
- Update README and `docs/MCP_TOOLS.md` when the interface changes.

## Pull requests

Keep changes focused, describe instrument or operator impact, and include the
commands used for verification.
