from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# These files intentionally contain path samples used to verify sanitization logic.
ALLOWLIST = {
    Path("scripts/test/check-repo-hygiene.py"),
    Path("source/03-protocol-builder/tests/test_determinism.py"),
}

PATTERNS = (
    ("macOS user home", re.compile(r"/Users/(?!<)([A-Za-z0-9._-]+)/")),
    ("Linux user home", re.compile(r"/home/(?!<)([A-Za-z0-9._-]+)/")),
    (
        "Windows user home",
        re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?!<)([^\\/\s\"']+)[\\/]+"),
    ),
    (
        "developer-specific Obsidian vault",
        re.compile(r"(?i)\bObsidianNotes(?:[\\/]|$)"),
    ),
)


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value]


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def main() -> int:
    violations: list[str] = []

    for relative_path in tracked_paths():
        if relative_path in ALLOWLIST:
            continue

        path = ROOT / relative_path
        if not path.is_file():
            continue

        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue

        text = data.decode("utf-8", errors="replace")
        # Match both literal Windows paths and source-code escaped backslashes.
        normalized = text.replace("\\\\", "\\")

        for label, pattern in PATTERNS:
            for match in pattern.finditer(normalized):
                violations.append(
                    f"{relative_path}:{line_number(normalized, match.start())}: {label}: {match.group(0)!r}"
                )

    if not violations:
        print("Repository hygiene check passed.")
        return 0

    print("Repository hygiene check failed. Tracked machine-specific paths were found:", file=sys.stderr)
    for violation in violations:
        print(f"  {violation}", file=sys.stderr)
    print(
        "Remove generated state, use repository-relative/deterministic paths, or add a narrowly scoped allowlist entry for an intentional sanitization fixture.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
