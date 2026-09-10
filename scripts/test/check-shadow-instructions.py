from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHADOW_SUFFIXES = (".orig", ".bak", ".old", ".backup", "~")


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value]


def is_instruction_surface(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()
    return (
        name.startswith("agents")
        or "agents.details" in parts
        or (len(parts) >= 2 and parts[0] in {".cursor", ".codex"} and parts[1] == "rules")
    )


def is_shadow_instruction(path: Path) -> bool:
    return is_instruction_surface(path) and path.name.casefold().endswith(SHADOW_SUFFIXES)


def main() -> int:
    violations = [path for path in tracked_paths() if is_shadow_instruction(path)]
    if not violations:
        print("Shadow instruction check passed.")
        return 0

    print("Shadow instruction check failed. Tracked backup instruction files were found:", file=sys.stderr)
    for path in violations:
        print(f"  {path}", file=sys.stderr)
    print(
        "Remove the shadow copy and use Git history for previous instruction versions.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
