# Rust helper CLI

The former Python, shell, and PowerShell helper scripts have been consolidated
into the Rust CLI at `scripts/rust/`. Install Rust/Cargo and Python 3.11 or newer,
then run commands from the repository root:

| Command | Replaces | Purpose |
|---------|----------|---------|
| `cargo run --manifest-path scripts/rust/Cargo.toml -- install` | `install/install.ps1` | Create `.venv`, bootstrap Python packages, run install checks, write `.mcp/server-config.json` |
| `cargo run --manifest-path scripts/rust/Cargo.toml -- agent-brief --mode status` | `agent/agent-brief.py`, `agent/agent-brief.sh` | Print or resolve a short agent checklist |
| `cargo run --manifest-path scripts/rust/Cargo.toml -- mcp-smoke` | `mcp/smoke_mcp.py` | Verify the stdio MCP tools and resources |
| `cargo run --manifest-path scripts/rust/Cargo.toml -- check-shadow-instructions` | `test/check-shadow-instructions.py` | Detect tracked backup copies of instruction files |
| `cargo run --manifest-path scripts/rust/Cargo.toml -- test --suite fast` | `test/test-fast.ps1` | Run reader/worklist, protocol-builder, and fluentcoder tests |
| `cargo run --manifest-path scripts/rust/Cargo.toml -- test --suite mcp` | `test/test-mcp.ps1` | Run MCP gateway, self-test, and smoke checks |
| `cargo run --manifest-path scripts/rust/Cargo.toml -- test --suite simulator` | `test/test-simulator.ps1` | Run the FluentControl-gated simulator suite |
| `cargo run --manifest-path scripts/rust/Cargo.toml -- test --suite all` | `test/test-all.ps1`, `test/test-suite.ps1` | Run the full suite or select a suite |

Agent brief options include `--intent "..."` and `--resolve-only`. Run
`cargo run --manifest-path scripts/rust/Cargo.toml -- help` for the command list.
The MCP server and domain-specific Python APIs remain Python components; the
Rust helpers call them where that is the existing product interface.
