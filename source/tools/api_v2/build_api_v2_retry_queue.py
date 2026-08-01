"""List API V2 issues missing deliverable markdown files."""
from __future__ import annotations

import json
from pathlib import Path

_API_V2 = (
    Path(__file__).resolve().parents[3]
    / "ready-to-import"
    / "_shared"
    / "temp_files"
    / "build"
    / "api_v2"
)
ISSUES = _API_V2 / "api_v2_workflow_improvements.json"
OUT = _API_V2 / "api_v2_issues"
RETRY = _API_V2 / "api_v2_retry_queue.json"


def main() -> None:
    items = json.loads(ISSUES.read_text(encoding="utf-8"))
    missing = []
    done = []
    for item in items:
        md = OUT / f"{item['id']}.md"
        if md.is_file():
            done.append(item["id"])
        else:
            missing.append(item)
    RETRY.write_text(json.dumps(missing, indent=2), encoding="utf-8")
    print(f"done={len(done)} missing={len(missing)} -> {RETRY}")


if __name__ == "__main__":
    main()
