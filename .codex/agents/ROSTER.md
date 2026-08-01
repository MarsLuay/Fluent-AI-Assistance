# Tecan diagnosis agents

Project Cursor agents under `.cursor/agents/`. Mirror copies under `.codex/agents/` for Codex. Project defs win over user globals.

| Agent | Owns | Edit? |
|-------|------|-------|
| `diagnosis-section-router` | `Get-DiagnosisSectionId`; runtime deps stay in script-errors MD | yes |
| `diagnosis-md-deduper` | Other Error / Evidence dump dedupe in MD render | yes |
| `diagnosis-attribution-auditor` | Weak script_line / command_hint / stale dump review | read-only |

## Bug assignment map

| Symptom | Assign |
|---------|--------|
| JSON has Failed-to-open / missing file; MD silent on script-errors profile | `diagnosis-section-router` |
| Mixed `VX_IMP_*` + Failed-to-open still hidden from script-errors MD | `diagnosis-section-router` (runtime file-open wins) |
| Unattributed + Other Error repeat same dump titles | `diagnosis-md-deduper` |
| Evidence listed twice for same dump text | `diagnosis-md-deduper` |
| script_line 0 / IScriptStatement / audit_import_timeline doubt | `diagnosis-attribution-auditor` |

## Invoke

```text
Use the diagnosis-section-router subagent to ...
Use the diagnosis-md-deduper subagent to ...
Use the diagnosis-attribution-auditor subagent to ...
```
