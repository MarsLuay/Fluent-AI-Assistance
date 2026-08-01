# Welcome to Fluent AI-Assistance!

This is a tool for the Tecan Fluent liquid handler. It lets an LLM understand
existing FluentControl scripts, debug errors, and generate fresh scripts.

## What this thing does

- Opens your Fluent project export and shows what scripts and patterns are in it
- Looks up how real commands work before inventing new ones
- Helps read FluentControl error logs and figure out why a script failed
- Turns a clear request into a script package you can review and import
- Checks the package (worktable, dependencies, checksums) before handoff
- Handles TouchTools prompts (images, GIFs, audio) when a script needs them
- Includes a web simulator and helpers for Fluent asset / workflow data

File formats, artifact names, and agent rules are in
[Capabilities](docs/CAPABILITIES.md).

## Setup

**AI:** paste this, then let it follow the linked file:

```text
Install Fluent AI-Assistance from https://github.com/MarsLuay/Fluent-AI-Assistance. Clone or pull, then follow docs/AI_INSTALL_PROMPT.md in the repo.
```

**Manual:** clone the repo, run `scripts/install/install.ps1`, then wire MCP from
`.mcp/server-config.json`. Details: [Installation](docs/INSTALLATION.md).

## Use

Setup already checks that the tools work. After that, talk to your AI client like
you would a coworker:

- "Open this ZEIA and explain what the scripts do"
- "This script failed; here is the FluentControl log"
- "Write a new script for ..."

Point it at your **full ZEIA export** on disk (and any error log). Review the
package it builds in FluentControl before you run anything on the instrument.

How the AI should start a job: [Agent brief](docs/AGENT_BRIEF.md).
Tool list: [MCP tools](docs/MCP_TOOLS.md).

## Safety

Review every generated script, open it in FluentControl Script Editor, and
validate it on the target instrument before hardware execution. Offline
`ready_to_import` does **not** mean Script Editor load-clean or hardware-ready.
See [Safety](docs/SAFETY.md).

## License

This repository is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE). It is **not** OSI "Open Source"
(OSI requires commercial use be allowed).

That means:

- Free for personal, hobby, education, research, and other noncommercial use
  (you may use, modify, and share under those terms).
- **Not** free for company / commercial use. Contact
  [marwanluay2005@gmail.com](mailto:marwanluay2005@gmail.com) for a commercial
  license.
- Third-party material retains its original license.

Required Notice: Copyright (c) 2026 Marwan Luay
(https://github.com/MarsLuay/Fluent-AI-Assistance)

Commercial / company use: contact [marwanluay2005@gmail.com](mailto:marwanluay2005@gmail.com)
