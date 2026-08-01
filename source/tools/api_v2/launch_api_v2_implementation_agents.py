"""Launch implementation subagents for all API V2 issues (prints Task prompts)."""
from __future__ import annotations

import json
from pathlib import Path

ASSIGNMENTS = (
    Path(__file__).resolve().parents[3]
    / "ready-to-import"
    / "_shared"
    / "temp_files"
    / "build"
    / "api_v2"
    / "api_v2_agent_assignments.json"
)
WORKSPACE = Path(__file__).resolve().parents[2] / "03-protocol-builder"
PROMPT_TEMPLATE = """Implement API V2 issue {id} in Tecan protocol-builder.

Workspace: {workspace}
Method: {method}
Priority: {priority}
Workflow: {workflow_part}
Improvement: {improvement}

Read runtime_bridge.py and fluent_runtime_provider.py first.
Implement minimal focused code OR scaffold stubs/interfaces (pythonnet/COM optional when FC absent).
Add tests in tests/ when feasible without FluentControl installed.
Write deliverable: ready-to-import/_shared/temp_files/build/api_v2/api_v2_issues/{id}.md with status (implemented|scaffolded|design-only), files_changed, summary, follow-up.
Do NOT edit ready-to-import/ by hand.
Run `scripts/test/test-fast.ps1` from the repo root if you changed code.
For low-priority delegate-only issues (BeginInvoke/EndInvoke): document skip + helper pattern unless trivial hook.
"""


def main() -> None:
    items = json.loads(ASSIGNMENTS.read_text(encoding="utf-8"))
    for item in items:
        print(f"=== {item['id']} ===")
        print(PROMPT_TEMPLATE.format(workspace=WORKSPACE, **item))
        print()


if __name__ == "__main__":
    main()
