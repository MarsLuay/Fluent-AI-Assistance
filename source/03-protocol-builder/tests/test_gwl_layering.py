from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _project_dependencies(pyproject_path: Path) -> set[str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for item in data.get("project", {}).get("dependencies", []):
        text = str(item).strip()
        # Accept bare names, version constraints, extras, and direct references.
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", text)
        if match:
            names.add(match.group(0))
    return names


class GwlLayeringTests(unittest.TestCase):
    def test_gwl_parser_and_model_live_in_shared_layer(self) -> None:
        reader_gwl = (REPO_ROOT / "source/01-project-reader/tecan_reader/gwl.py").read_text(encoding="utf-8")
        worklist_gwl = (REPO_ROOT / "source/02-worklist-builder/tecan_worklist/gwl.py").read_text(encoding="utf-8")
        protocol_ir = (REPO_ROOT / "source/03-protocol-builder/fluent_pipeline/protocol_ir.py").read_text(encoding="utf-8")

        self.assertIn("from tecan_common.gwl import", reader_gwl)
        self.assertIn("from tecan_common.gwl import", worklist_gwl)
        self.assertIn("from tecan_common.gwl import", protocol_ir)
        self.assertNotIn("from tecan_worklist.gwl import", reader_gwl)
        self.assertNotIn("from tecan_worklist.gwl import", protocol_ir)

    def test_package_manifests_match_the_shared_gwl_boundary(self) -> None:
        reader_deps = _project_dependencies(REPO_ROOT / "source/01-project-reader/pyproject.toml")
        worklist_deps = _project_dependencies(REPO_ROOT / "source/02-worklist-builder/pyproject.toml")
        protocol_deps = _project_dependencies(REPO_ROOT / "source/03-protocol-builder/pyproject.toml")

        self.assertIn("tecan-common", reader_deps)
        self.assertIn("tecan-common", worklist_deps)
        self.assertIn("tecan-common", protocol_deps)
        self.assertNotIn("tecan-worklist-builder", reader_deps)
        self.assertNotIn("tecan-worklist-builder", protocol_deps)


if __name__ == "__main__":
    unittest.main()
