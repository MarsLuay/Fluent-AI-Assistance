"""Add agent assignment metadata to deduplicated API V2 improvements."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
_API_V2 = REPO_ROOT / "ready-to-import" / "_shared" / "temp_files" / "build" / "api_v2"
IMPROVEMENTS = _API_V2 / "api_v2_workflow_improvements.json"
ASSIGNMENTS = _API_V2 / "api_v2_agent_assignments.json"
OUT_DIR = _API_V2 / "api_v2_issues"

def main() -> None:
    items = json.loads(IMPROVEMENTS.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    assignments = []
    for item in items:
        assignments.append(
            {
                **item,
                "agent_status": "pending",
                "output_path": str(OUT_DIR / f"{item['id']}.md").replace("\\", "/"),
                "codebase_roots": [
                    "source/03-protocol-builder/fluent_pipeline/runtime_bridge.py",
                    "source/03-protocol-builder/fluent_pipeline/fluent_runtime_provider.py",
                    "source/03-protocol-builder/fluent_pipeline/generation_workflow.py",
                    "source/tools/api_v2/api_v2_methods.json",
                ],
            }
        )
    ASSIGNMENTS.parent.mkdir(parents=True, exist_ok=True)
    ASSIGNMENTS.write_text(json.dumps(assignments, indent=2), encoding="utf-8")
    print(f"assignments={len(assignments)} -> {ASSIGNMENTS}")


if __name__ == "__main__":
    main()
