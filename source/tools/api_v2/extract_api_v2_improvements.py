"""Extract and deduplicate API V2 workflow improvements from subagent transcripts."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

SUBAGENTS = Path(os.environ.get("TECAN_API_V2_SUBAGENTS", "agent-transcripts/subagents"))
OUT = (
    Path(__file__).resolve().parents[3]
    / "ready-to-import"
    / "_shared"
    / "temp_files"
    / "build"
    / "api_v2"
    / "api_v2_workflow_improvements.json"
)

PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def norm_method(method: str) -> str:
    m = re.sub(r"\s+", " ", method.strip())
    m = re.sub(r"\(\)\s*\[static\]", "() [static]", m)
    for prefix in (
        ("IRuntimeController.", "RuntimeController."),
        ("IFluentControl.", "FluentControl."),
        ("IFluentControlEvents.", "FluentControlEvents."),
        ("IRuntimeControllerEvents.", "RuntimeControllerEvents."),
        ("IExecutionChannel.", "ExecutionChannel."),
    ):
        m = m.replace(prefix[0], prefix[1])
    m = m.replace("Commands.", "")
    return m


def norm_workflow_part(wp: str) -> str:
    wp = wp.strip().lower()
    wp = re.sub(r"\s+", " ", wp)
    return wp.split("/")[0].split("(")[0].strip()[:48]


def _parse_json_arrays(text: str) -> list[list[dict]]:
    arrays: list[list[dict]] = []
    for block in re.finditer(r"```json\s*([\s\S]*?)```", text):
        try:
            payload = json.loads(block.group(1).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            if "method" in payload[0]:
                arrays.append(payload)
    if not arrays:
        for match in re.finditer(r"\[\s*\{[\s\S]*?\}\s*\]", text):
            try:
                payload = json.loads(match.group())
            except json.JSONDecodeError:
                continue
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                if "method" in payload[0]:
                    arrays.append(payload)
    return arrays


def extract_items() -> list[dict]:
    items: list[dict] = []
    for path in sorted(SUBAGENTS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("role") != "assistant":
                continue
            message = record.get("message") or {}
            for part in message.get("content") or []:
                if part.get("type") != "text":
                    continue
                text = part.get("text") or ""
                for arr in _parse_json_arrays(text):
                    for entry in arr:
                        entry = dict(entry)
                        entry["_source_batch"] = path.stem
                        items.append(entry)
    return items


def dedup_key(method: str) -> str:
    m = norm_method(method)
    m = re.sub(r"\(.*\)$", "", m)
    m = re.sub(r"\(\)$", "", m)
    return m


def dedupe(items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for entry in items:
        method = norm_method(entry.get("method", ""))
        key = dedup_key(entry.get("method", ""))
        pr = entry.get("priority", "medium")
        batch = entry.get("_source_batch", "")
        if key not in seen:
            seen[key] = {
                "method": method,
                "workflow_parts": [entry.get("workflow_part", "").strip()],
                "improvement": entry.get("improvement", "").strip(),
                "priority": pr,
                "source_batches": [batch] if batch else [],
            }
            continue
        cur = seen[key]
        wp = entry.get("workflow_part", "").strip()
        if wp and wp not in cur["workflow_parts"]:
            cur["workflow_parts"].append(wp)
        if PRIORITY_RANK.get(pr, 9) < PRIORITY_RANK.get(cur.get("priority", "medium"), 9):
            cur["priority"] = pr
        imp = entry.get("improvement", "").strip()
        if len(imp) > len(cur["improvement"]):
            cur["improvement"] = imp
        if batch and batch not in cur["source_batches"]:
            cur["source_batches"].append(batch)

    deduped = sorted(
        seen.values(),
        key=lambda x: (PRIORITY_RANK.get(x.get("priority", "medium"), 9), x["method"]),
    )
    for i, entry in enumerate(deduped, 1):
        parts = entry.pop("workflow_parts")
        entry["id"] = f"api-v2-{i:03d}"
        entry["workflow_part"] = "; ".join(parts[:3]) + ("; ..." if len(parts) > 3 else "")
    return deduped


def main() -> None:
    items = extract_items()
    deduped = dedupe(items)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(deduped, indent=2), encoding="utf-8")
    print(f"raw={len(items)} deduped={len(deduped)} -> {OUT}")
    for entry in deduped:
        wp = entry["workflow_part"][:55].encode("ascii", "replace").decode("ascii")
        print(f"{entry['id']}|{entry['priority']}|{entry['method']}|{wp}")


if __name__ == "__main__":
    main()
