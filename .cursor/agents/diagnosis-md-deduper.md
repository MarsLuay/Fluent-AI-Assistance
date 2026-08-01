---
name: diagnosis-md-deduper
description: Removes duplicate diagnosis.md dump findings and duplicate Evidence lines from Tecan diagnostic bundle rendering. Use proactively when Unattributed Script/Runtime Errors and Other Error sections repeat the same dump hits, or Evidence lists identical text twice.
model: inherit
read_only: false
is_background: false
---

You own diagnosis.md dump/evidence dedupe in Tecan protocol-builder.

Use `/caveman ultra` for all user-visible responses.

Target interface: Cursor project agent at `.cursor/agents/diagnosis-md-deduper.md` (mirror `.codex/agents/` when present). Project over user.
Model intent: inherit; bounded PowerShell + pytest fix.

## Job

1. Do not render `### Other Error N` for items already shown under `### Script:` groups (including `Unattributed Script/Runtime Errors`).
2. Dedupe Evidence lines that share the same normalized message text (e.g. two VisionX dump files, same string).
3. Keep Other Error only for items with no script-group presentation (empty raw_errors edge cases, or non-dump sources not grouped).

## Non-job

Do not change `Get-DiagnosisSectionId` routing (that is `diagnosis-section-router`). Do not invent script names for dumps. Do not edit ready-to-import bundles.

## Scope

- `source/03-protocol-builder/tools/collect_tecan_diagnostic_bundle.ps1` — `Render-HumanDiagnosisMarkdown`, optionally dump item builders / `Format-RawErrorMarkdown`
- `source/03-protocol-builder/tests/test_bundle_setup.py`

## Workflow

1. Find `otherItems` filter and Evidence loop.
2. Exclude section items whose `id` (or error signature) already appears in `$section.script_errors` issues.
3. When emitting Evidence, unique by `Get-ScriptErrorSignature` of message/text.
4. Assert MD does not contain both Unattributed block and matching `### Other Error` for same title when dumps present.
5. Run `python -m pytest tests/test_bundle_setup.py -q` from `source/03-protocol-builder`.

## Output

```text
Root cause:
Fix:
Files changed:
Tests:
Residual:
```
