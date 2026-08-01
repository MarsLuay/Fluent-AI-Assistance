# Installation

## AI-assisted installation

Paste the minimal prompt from [`AI_INSTALL_PROMPT.md`](AI_INSTALL_PROMPT.md)
(also shown in [`README.md`](../README.md)) into an AI client that can run local
commands and configure MCP. The AI should open that file in the clone and follow
its steps.

That installs the **full repository**. MCP is the adapter the installer wires
into the client; the CLI and Python API come with the same checkout.

## What the installer does

`scripts/install/install.ps1`:

1. Creates a repository-local `.venv`.
2. Installs the protocol-builder package with its `mcp` dependency group.
3. Runs the server self-test (required tools + in-process bootstrap `next_step`).
4. Starts a real stdio MCP session: `fluent_status` then `fluent_bootstrap_status`
   (fails if required tools are missing or `next_step` is absent).
5. Runs CLI `bootstrap-status --no-report` (exit `>=2` fails install).
6. Writes `.mcp/server-config.json` with absolute local paths.

It does not install Tecan drivers, modify FluentControl, write into
`C:\ProgramData\Tecan`, or configure an AI client without permission.

## Manual installation

```powershell
git clone https://github.com/MarsLuay/Fluent-AI-Assistance.git Fluent-AI-Assistance
cd Fluent-AI-Assistance
powershell -ExecutionPolicy Bypass -File .\scripts\install\install.ps1
```

Merge the generated `.mcp/server-config.json` entry into the MCP configuration
used by the client, then reload that client. The installer already ran
`fluent_status` / `fluent_bootstrap_status` during setup; call them again only
if you want to confirm MCP wiring after the reload.

## Credentials

Do not place GitHub tokens in an MCP configuration or commit them to this
repository. Clone and pull with your normal Git credentials when needed.
