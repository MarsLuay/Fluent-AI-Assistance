# API V2 implementation agent roster

Generated from analysis subagents → deduplicated issues (method-signature normalized).

## Artifacts

| File | Purpose |
|------|---------|
| `source/tools/api_v2/api_v2_methods.json` | Checked-in input catalog for API V2 mining |
| `ready-to-import/_shared/temp_files/build/api_v2/api_v2_workflow_improvements.json` | Deduplicated issue list (`api-v2-001` …) |
| `ready-to-import/_shared/temp_files/build/api_v2/api_v2_agent_assignments.json` | Same issues + `output_path` per deliverable |
| `ready-to-import/_shared/temp_files/build/api_v2/api_v2_issues/{id}.md` | Per-issue implementation report (written by agents) |

## Priority breakdown

Counts come from `api_v2_workflow_improvements.json` (regenerate when that file changes).

## Agent dispatch model

- Early issues: one dedicated background subagent each.
- Later issues: multi-issue subagents; deliverables still land as separate `{id}.md` files.

Re-run dedupe: `python3 source/tools/api_v2/extract_api_v2_improvements.py`

Build assignment metadata: `python3 source/tools/api_v2/assign_api_v2_agents.py`

Print launch prompts: `python3 source/tools/api_v2/launch_api_v2_implementation_agents.py`

## Critical issues (implement first)

| ID | Method |
|----|--------|
| api-v2-001 | ExecutionChannel.ExecuteCommand |
| api-v2-002 | ExecutionChannel.FinishExecution |
| api-v2-003 | FluentControl.GetRuntime |
| api-v2-004 | GenericCommand.ToXML |
| api-v2-005 | ICommand.ToXML |
| api-v2-006 | ICommand.Validate |

See `api_v2_workflow_improvements.json` for the full method → improvement mapping.

Do not commit past agent-session UUIDs into this roster; dispatch IDs are ephemeral.
