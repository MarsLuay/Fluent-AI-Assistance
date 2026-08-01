# Scripts

Repo-level install / agent / MCP / test helpers.

| Subfolder | What |
|-----------|------|
| `install/` | `install.ps1` — venv + MCP self-test + `.mcp/server-config.json` |
| `agent/` | `agent-brief.py` / `.sh` — token-cheap mode checklists |
| `mcp/` | `smoke_mcp.py` — stdio MCP smoke (`fluent_status` + `fluent_bootstrap_status`) |
| `test/` | `test-suite.ps1` and suite wrappers (`test-all`, `test-fast`, …) |

Examples (from repo root):

```bash
powershell -ExecutionPolicy Bypass -File .\scripts\install\install.ps1
python3 scripts/agent/agent-brief.py --mode status
powershell -ExecutionPolicy Bypass -File .\scripts\test\test-fast.ps1
```
