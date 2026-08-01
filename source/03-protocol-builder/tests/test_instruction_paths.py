from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
INSTRUCTION_FILES = (
    REPO_ROOT / ".cursor/rules/full-zeia-export-required.mdc",
    REPO_ROOT / ".cursor/rules/interactive-script-query-variable.mdc",
    REPO_ROOT / "source/03-protocol-builder/AGENTS.md",
    REPO_ROOT / "source/03-protocol-builder/README.md",
    REPO_ROOT / "source/03-protocol-builder/docs/CODEX_WORKFLOW.md",
    REPO_ROOT / "source/03-protocol-builder/docs/PROTOCOL_BUILDER_GUIDE.md",
)


class InstructionPathTests(unittest.TestCase):
    def test_protocol_builder_instruction_paths_are_repo_relative(self) -> None:
        offenders: list[str] = []
        for path in INSTRUCTION_FILES:
            text = path.read_text(encoding="utf-8")
            if "Projects/Fluent-AI-Assistance" in text or "Fluent-AI-Assistance\\source\\03-protocol-builder" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))

        self.assertFalse(offenders, "Machine-specific protocol-builder paths found in:\n" + "\n".join(offenders))

    def test_cursor_rule_globs_use_repo_relative_protocol_builder_scope(self) -> None:
        for path in (
            REPO_ROOT / ".cursor/rules/full-zeia-export-required.mdc",
            REPO_ROOT / ".cursor/rules/interactive-script-query-variable.mdc",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("globs: source/03-protocol-builder/**", text)


if __name__ == "__main__":
    unittest.main()
