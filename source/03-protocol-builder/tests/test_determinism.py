from dataclasses import replace as dataclass_replace
import tempfile
import unittest
import zipfile
from pathlib import Path

import fluent_pipeline.project_context as pc
from fluent_pipeline.determinism import (
    artifact_digest,
    compare_run_dirs,
    normalize_artifact_text,
    render_determinism_report,
)
from fluent_pipeline.generation_options import GenerationOptions, normalize_generation_options
from fluent_pipeline.generation_workflow import ApprovalSet, GenerationRequest, run_generation_workflow as _run_generation_workflow


def run_generation_workflow(**kwargs):
    options = dataclass_replace(
        normalize_generation_options(
            kwargs.pop("options", GenerationOptions()),
            simulate=kwargs.pop("simulate", None),
            compile_xscr=kwargs.pop("compile_xscr", None),
        ),
        project_archive=kwargs.pop("project_archive", None),
        project_name=kwargs.pop("project_name", None),
        force_import=kwargs.pop("force_import", False),
        pattern_refs=tuple(kwargs.pop("pattern_refs", []) or []),
    )
    request = GenerationRequest(
        intent=kwargs.pop("intent"),
        output_directory=kwargs.pop("out_dir"),
        source_scripts=tuple(kwargs.pop("source_scripts", []) or []),
        options=options,
        approvals=ApprovalSet(
            approve_partial_zeia=options.approve_partial_zeia,
            waive_checksum_recompute=options.waive_checksum_recompute,
            approve_deck_layout=options.approve_deck_layout,
            approve_command_inventory=options.approve_command_inventory,
        ),
    )
    if kwargs:
        raise AssertionError(f"Unhandled workflow test kwargs: {sorted(kwargs)}")
    return _run_generation_workflow(request)


XSCR = """<?xml version="1.0"?>
<Root>
  <ObjectName>DemoScript</ObjectName>
  <Reference>
    <Guid>workspace-guid</Guid>
    <TypeId>WorktableWorkspace</TypeId>
    <ObjectName>Base Worktable</ObjectName>
  </Reference>
  <Script version="1.0" />
  <Object Type="Tecan.Fluent.Commands.MCA96.AspirateDataV1">
    <LabwareName>SourcePlate</LabwareName>
    <LiquidClassName>Water Free Single</LiquidClassName>
  </Object>
</Root>
"""

WORKTABLE = """<?xml version="1.0"?>
<Workspace>
  <ObjectName>Base Worktable</ObjectName>
  <Guid>workspace-guid</Guid>
</Workspace>
"""

LIQUID_CLASS = """<?xml version="1.0"?>
<LiquidClass>
  <ObjectName>Water Free Single</ObjectName>
  <Guid>water-free-single-guid</Guid>
</LiquidClass>
"""


class NormalizationTests(unittest.TestCase):
    def test_timestamps_are_blanked(self):
        a = 'created_at: 2026-06-12T08:30:00+00:00'
        b = 'created_at: 2026-06-12T09:45:13+00:00'
        self.assertEqual(normalize_artifact_text(a, []), normalize_artifact_text(b, []))
        self.assertIn("<TIMESTAMP>", normalize_artifact_text(a, []))

    def test_microsecond_and_z_timestamps_match(self):
        a = '"ts": "2026-06-12T08:30:00.123456+00:00"'
        b = '"ts": "2026-06-12T08:30:00Z"'
        self.assertEqual(normalize_artifact_text(a, []), normalize_artifact_text(b, []))

    def test_path_roots_collapsed_in_all_spellings(self):
        root = r"C:\Users\me\run_a"
        native = f"{root}\\request.spec.yaml"
        posix = f"{root.replace(chr(92), '/')}/request.spec.yaml"
        json_escaped = f"{root.replace(chr(92), chr(92) * 2)}\\\\request.spec.yaml"
        n = normalize_artifact_text(native, [root])
        p = normalize_artifact_text(posix, [root])
        j = normalize_artifact_text(json_escaped, [root])
        self.assertNotIn("run_a", n)
        self.assertNotIn("run_a", p)
        self.assertNotIn("run_a", j)

    def test_two_roots_normalize_to_same(self):
        text_a = r'"request_spec": "C:\\tmp\\run_a\\request.spec.yaml"'
        text_b = r'"request_spec": "C:\\tmp\\run_b\\request.spec.yaml"'
        self.assertEqual(
            normalize_artifact_text(text_a, [r"C:\tmp\run_a"]),
            normalize_artifact_text(text_b, [r"C:\tmp\run_b"]),
        )

    def test_artifact_digest_ignores_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.json"
            b = Path(tmp) / "b.json"
            a.write_text('{"generated_at": "2026-06-12T08:30:00+00:00", "x": 1}', encoding="utf-8")
            b.write_text('{"generated_at": "2026-06-12T10:11:12+00:00", "x": 1}', encoding="utf-8")
            self.assertEqual(artifact_digest(a, []), artifact_digest(b, []))


class RegenerationDeterminismTests(unittest.TestCase):
    """Regenerating from the same inputs yields byte-identical artifacts."""

    def _build_archive(self, tmp_path: Path) -> Path:
        archive = tmp_path / "demo.zeia"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Scripts/demo.xscr", XSCR)
            zf.writestr("Worktables/base.xwsp", WORKTABLE)
            zf.writestr("LiquidClasses/Water Free Single.xlqc", LIQUID_CLASS)
        return archive

    def _generate(self, *, out_dir: Path, archive: Path) -> dict:
        return run_generation_workflow(
            intent="Use the SOURCE assay setup to make a NEW script; keep the prompt text. pipette A1 -> B2",
            out_dir=out_dir,
            project_archive=archive,
            project_name="determinism-demo",
            source_scripts=["DemoScript"],
            pattern_refs=["pick_up_tips from DemoScript"],
            simulate=False,
            compile_xscr=False,
            force_import=True,
        )

    def test_regeneration_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = self._build_archive(tmp_path)
                out_a = tmp_path / "run_a"
                out_b = tmp_path / "run_b"
                self._generate(out_dir=out_a, archive=archive)
                self._generate(out_dir=out_b, archive=archive)

                report = compare_run_dirs(
                    out_a,
                    out_b,
                    extra_roots=[tmp_path, pc.PROJECTS_DIR],
                )
                self.assertTrue(
                    report["deterministic"],
                    msg=render_determinism_report(report),
                )
                # Guard against a vacuous pass: real artifacts must be compared.
                self.assertGreaterEqual(report["compared_count"], 5)

                # The CLI command agrees and exits 0.
                from fluent_pipeline.cli import main as cli_main

                exit_code = cli_main(
                    [
                        "determinism-check",
                        str(out_a),
                        str(out_b),
                        "--root",
                        str(tmp_path),
                    ]
                )
                self.assertEqual(exit_code, 0)

                # A real content change is detected as non-deterministic.
                ir_files = list(out_b.rglob("*.protocol-ir.json"))
                self.assertTrue(ir_files)
                ir_files[0].write_text(
                    ir_files[0].read_text(encoding="utf-8").replace("\"id\"", "\"id_changed\"", 1),
                    encoding="utf-8",
                )
                tampered = compare_run_dirs(out_a, out_b, extra_roots=[tmp_path, pc.PROJECTS_DIR])
                self.assertFalse(tampered["deterministic"])
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file


if __name__ == "__main__":
    unittest.main()
