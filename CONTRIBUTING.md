# Contributing

## Development

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install\install.ps1
.\scripts\test\test-fast.ps1      # reader/worklist + protocol-builder + fluentcoder
.\scripts\test\test-mcp.ps1       # MCP gateway, self-test, smoke
.\scripts\test\test-simulator.ps1 # FluentControl-gated simulator suite
.\scripts\test\test-all.ps1       # everything above
```

Run `test-fast` before opening a pull request. Use the MCP, simulator, or all
suites when your change touches those areas.

Layout and interfaces: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
