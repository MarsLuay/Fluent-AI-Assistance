import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

import fluent_pipeline.project_context as pc
from fluent_pipeline.protocol_ir import protocol_ir_from_gwl, protocol_ir_from_python, protocol_ir_from_xscr
from fluent_pipeline.protocol_ir import render_python_draft, render_recreate_markdown


LIQUID_HANDLING = {"aspirate", "dispense", "liha_aspirate", "liha_dispense", "mca384_mix", "liha_mix"}


class ProtocolCorpusTests(unittest.TestCase):
    def test_protocol_corpus_regressions(self):
        corpus_root = Path(__file__).parent / "protocols"
        cases = sorted(path for path in corpus_root.iterdir() if (path / "input_zeia.json").exists())
        self.assertEqual([case.name for case in cases], [
            "mca384_transfer",
            "plate_copy",
            "serial_dilution",
            "simple_transfer",
            "tip_pickup_cleanup",
            "worklist_import",
        ])

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                for case_dir in cases:
                    with self.subTest(case=case_dir.name):
                        self._run_case(case_dir, tmp_path)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def _run_case(self, case_dir: Path, tmp_path: Path) -> None:
        input_zeia = _read_json(case_dir / "input_zeia.json")
        expected_manifest = _read_json(case_dir / "expected_extracted_manifest.json")
        expected_ir = _read_json(case_dir / "expected_protocol.ir.json")
        expected_guide = _read_json(case_dir / "expected_guide_steps.json")
        expected_worklists = _read_json(case_dir / "expected_worklist_records.json")
        expected_simulation = _read_json(case_dir / "expected_simulation_result.json")
        allowed_operations = set(_read_json(case_dir / "allowed_source_operations.json"))

        archive = _write_archive(tmp_path / f"{case_dir.name}.zeia", input_zeia)
        ctx = pc.import_project(archive, name=f"{case_dir.name}-ctx", force=True)

        self.assertEqual(len(ctx.manifest["scripts"]), expected_manifest["script_count"])
        self.assertEqual(len(ctx.manifest["workspaces"]), expected_manifest["workspace_count"])
        for name in expected_manifest.get("labware_names", []):
            self.assertIn(name, ctx.manifest["labware_names"])
        for name in expected_manifest.get("liquid_classes", []):
            self.assertIn(name, ctx.manifest["liquid_classes"])
        for path in expected_manifest.get("worklist_paths", []):
            self.assertIn(path, ctx.manifest["worklist_paths"])

        script_path = ctx.root / ctx.manifest["scripts"][0]["extracted_path"]
        ir = protocol_ir_from_xscr(script_path)
        self.assertEqual(
            [{"operation": step["operation"]} for step in ir["steps"]],
            expected_ir["steps"],
        )
        self.assertEqual(
            [{"label": item["label"]} for item in ir["labware"]],
            expected_ir["labware"],
        )
        self.assertEqual(
            [{"name": item["name"]} for item in ir["liquid_classes"]],
            expected_ir["liquid_classes"],
        )

        guide = render_recreate_markdown(ir)
        manual_lines = _section_lines(guide, "## Manual FluentControl Steps")
        guide_lh_count = sum(
            1 for line in manual_lines
            if re.match(r"\d+\. (Aspirate|Dispense|Mix|LiHa Aspirate|LiHa Dispense)", line)
        )
        ir_lh_count = sum(1 for step in ir["steps"] if step["operation"] in LIQUID_HANDLING)
        self.assertEqual(guide_lh_count, ir_lh_count)
        self.assertEqual(guide_lh_count, expected_guide["liquid_handling_steps"])

        for label in [item["label"] for item in ir["labware"]]:
            self.assertIn(label, ctx.manifest["labware_names"])
        for liquid_class in [item["name"] for item in ir["liquid_classes"]]:
            self.assertIn(liquid_class, ctx.manifest["liquid_classes"])

        rendered = tmp_path / f"{case_dir.name}_roundtrip.py"
        rendered.write_text(render_python_draft(ir), encoding="utf-8")
        roundtrip_ir = protocol_ir_from_python(rendered)
        # Compiled XSCR fixtures are represented by the source ZEIA XSCR. This
        # roundtrip catches regressions in XSCR -> IR -> Python -> IR behavior
        # without requiring a FluentControl runtime in unit tests.
        self.assertEqual(
            [step["operation"] for step in roundtrip_ir["steps"] if step["operation"] in LIQUID_HANDLING],
            [step["operation"] for step in ir["steps"] if step["operation"] in LIQUID_HANDLING],
        )

        self.assertFalse({step["operation"] for step in expected_ir["steps"]} - allowed_operations)

        if expected_worklists:
            gwl_path = ctx.root / "extracted" / expected_manifest["worklist_paths"][0]
            gwl_ir = protocol_ir_from_gwl(gwl_path)
            actual = [
                {"operation": step["operation"], "rack_label": step["target_labware"]}
                for step in gwl_ir["steps"]
            ]
            self.assertEqual(actual, expected_worklists)

        self.assertEqual(expected_simulation["status"], "fixture_expected")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_archive(path: Path, input_zeia: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Scripts/protocol.xscr", _script_xml(input_zeia))
        zf.writestr(
            "Worktables/base.xwsp",
            (
                "<?xml version=\"1.0\"?><Workspace>"
                f"<ObjectName>{input_zeia['worktable']}</ObjectName>"
                f"<Guid>{input_zeia.get('worktable_guid', 'workspace-guid')}</Guid>"
                "</Workspace>"
            ),
        )
        for name, lines in input_zeia.get("worklists", {}).items():
            zf.writestr(f"Worklists/{name}", "\n".join(lines))
    return path


def _section_lines(markdown: str, header: str) -> list[str]:
    lines = markdown.splitlines()
    try:
        start = lines.index(header) + 1
    except ValueError:
        return []
    out = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        out.append(line)
    return out


def _script_xml(input_zeia: dict) -> str:
    parts = [
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>",
        "<VxData><Payload>",
        f"<ObjectName>{input_zeia['script_name']}</ObjectName>",
        "<Reference>",
        f"<Guid>{input_zeia.get('worktable_guid', 'workspace-guid')}</Guid>",
        "<TypeId>WorktableWorkspace</TypeId>",
        f"<ObjectName>{input_zeia['worktable']}</ObjectName>",
        "</Reference>",
        "<PayloadData><Script><Commands><ScriptGroup><Objects>",
        "<Object Type=\"Tecan.Core.Scripting.ScriptGroupDataV1\"><ScriptGroupDataV1><Name>Protocol</Name><Data><Statements>",
    ]
    line = 1
    for labware in input_zeia.get("labware", []):
        parts.append(
            "<Object Type=\"Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1\">"
            "<AddLabwareDataV1>"
            f"<LabwareType>{labware['type']}</LabwareType>"
            f"<LabwareLable>{labware['label']}</LabwareLable>"
            f"<Location>{labware.get('location', 'Site')}</Location>"
            f"<Position>{labware.get('position', 1)}</Position>"
            "<Rotation>0</Rotation><HasLid>False</HasLid>"
            f"<Data><LineNumber>{line}</LineNumber></Data>"
            "</AddLabwareDataV1></Object>"
        )
        line += 1
    for command in input_zeia.get("commands", []):
        parts.append(_command_xml(command, line))
        line += 1
    parts.append("</Statements></Data></ScriptGroupDataV1></Object>")
    parts.append("</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>")
    return "".join(parts)


def _command_xml(command: dict, line: int) -> str:
    command_id = command["id"]
    body = [f"<{command_id}>"]
    if command.get("liquid_class"):
        body.append(f"<LiquidClassName>{command['liquid_class']}</LiquidClassName>")
    if command.get("volume") is not None:
        body.append(f"<Volume>{command['volume']}</Volume>")
    body.append("<ScriptCommandCommonDataV2>")
    if command.get("labware"):
        body.append(f"<LabwareName>{command['labware']}</LabwareName>")
    if command.get("device_alias"):
        body.append(f"<DeviceAlias>{command['device_alias']}</DeviceAlias>")
    body.append(f"<LineNumber>{line}</LineNumber>")
    body.append("</ScriptCommandCommonDataV2>")
    body.append(f"</{command_id}>")
    return f"<Object Type=\"Tecan.Core.Scripting.Commands.{command_id}\">{''.join(body)}</Object>"


if __name__ == "__main__":
    unittest.main()
