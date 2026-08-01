# AI Installation Prompt

Paste the block below into an AI client that can run local commands and install
MCP:

```text
Install Fluent AI-Assistance from https://github.com/MarsLuay/Fluent-AI-Assistance on this computer. Clone the repository to a sensible user-owned tools directory as Fluent-AI-Assistance (for example `git clone https://github.com/MarsLuay/Fluent-AI-Assistance.git Fluent-AI-Assistance`), or run `git pull --ff-only` if that clone already exists. From the repository root run `powershell -ExecutionPolicy Bypass -File .\scripts\install\install.ps1`. Read the generated `.mcp/server-config.json`, merge only its `fluent-ai-assistance` entry into this client's MCP configuration, and reload or reconnect MCP. Then call `fluent_status` and `fluent_bootstrap_status` to verify it works. The Python API and protocol-builder CLI stay available in the same install. Do not install Tecan drivers, modify FluentControl, write to `C:\ProgramData\Tecan`, upload ZEIA files or logs, or expose the MCP server over a network. If this client cannot edit its own MCP configuration, give me the exact configuration entry and destination file instead of claiming installation succeeded.
```
