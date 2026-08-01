# AI Installation Prompt

## Minimal paste (for README / chat)

```text
Install Fluent AI-Assistance from https://github.com/MarsLuay/Fluent-AI-Assistance. Clone or pull, then follow docs/AI_INSTALL_PROMPT.md in the repo.
```

## After clone : do this

1. From the repository root run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\install\install.ps1
   ```
2. Read `.mcp/server-config.json`.
3. Merge **only** the `fluent-ai-assistance` entry into this client's MCP config.
4. Reload or reconnect MCP.
5. Call `fluent_status` and `fluent_bootstrap_status` to verify.

If the client cannot edit its own MCP config, give the user the exact entry and
destination file : do not claim install succeeded.

The Python API and protocol-builder CLI come with the same install.

## Do not

- Install Tecan drivers
- Modify FluentControl
- Write to `C:\ProgramData\Tecan`
- Upload ZEIA files or logs
- Expose the MCP server over a network

## Manual install

See [INSTALLATION.md](INSTALLATION.md).
