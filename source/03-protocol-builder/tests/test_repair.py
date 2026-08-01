import tempfile
import unittest
import sqlite3
from pathlib import Path

from fluent_pipeline.project_context import ProjectContext
import fluent_pipeline.repair as repair


class RepairTests(unittest.TestCase):
    def test_catalog_alias_plan_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            output = Path(tmp) / "draft_repaired.py"
            draft.write_text("catalog = 'Plexiglas Pane[002]'\n", encoding="utf-8")
            ctx = ProjectContext(
                name="demo",
                root=Path(tmp),
                manifest={
                    "catalog_alias_candidates": [
                        {"project_name": "Plexiglas Pane[002]", "base_name": "Plexiglas Pane"}
                    ]
                },
            )
            old = repair._catalog_name_exists
            repair._catalog_name_exists = lambda name: name == "Plexiglas Pane"
            try:
                plan = repair.build_repair_plan(draft, context=ctx)
                applied = repair.apply_repair_plan(plan, output)
            finally:
                repair._catalog_name_exists = old

            self.assertEqual(len(plan.actions), 1)
            self.assertEqual(plan.actions[0].status, "ready")
            self.assertEqual(plan.actions[0].edits[0].target_type, "python_ast_assignment")
            self.assertEqual(plan.actions[0].edits[0].target_node, "Assign.value[catalog]")
            self.assertEqual(plan.actions[0].edits[0].expected_source_text, "'Plexiglas Pane[002]'")
            self.assertEqual(len(applied), 1)
            self.assertIn("Plexiglas Pane'", output.read_text(encoding="utf-8"))
            self.assertNotIn("[002]", output.read_text(encoding="utf-8"))

    def test_catalog_alias_edit_does_not_touch_prompt_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            output = Path(tmp) / "draft_repaired.py"
            draft.write_text(
                "prompt = 'Plexiglas Pane[002]'\n"
                "catalog = 'Plexiglas Pane[002]'\n",
                encoding="utf-8",
            )
            ctx = ProjectContext(
                name="demo",
                root=Path(tmp),
                manifest={
                    "catalog_alias_candidates": [
                        {"project_name": "Plexiglas Pane[002]", "base_name": "Plexiglas Pane"}
                    ]
                },
            )
            old = repair._catalog_name_exists
            repair._catalog_name_exists = lambda name: name == "Plexiglas Pane"
            try:
                plan = repair.build_repair_plan(draft, context=ctx)
                applied = repair.apply_repair_plan(plan, output)
            finally:
                repair._catalog_name_exists = old

            self.assertEqual(len(plan.actions), 1)
            self.assertEqual(len(plan.actions[0].edits), 1)
            self.assertEqual(plan.actions[0].edits[0].target_node, "Assign.value[catalog]")
            self.assertEqual(len(applied), 1)
            text = output.read_text(encoding="utf-8")
            self.assertIn("prompt = 'Plexiglas Pane[002]'", text)
            self.assertIn("catalog = 'Plexiglas Pane'", text)

    def test_configured_alias_plan_without_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            output = Path(tmp) / "draft_repaired.py"
            draft.write_text("liquid_class = 'Water Free Single[001]'\n", encoding="utf-8")

            plan = repair.build_repair_plan(draft)
            applied = repair.apply_repair_plan(plan, output)

            self.assertEqual(len(plan.actions), 1)
            self.assertEqual(plan.actions[0].kind, "liquid_class_alias")
            self.assertEqual(plan.actions[0].status, "ready")
            self.assertEqual(plan.actions[0].edits[0].target_type, "python_ast_assignment")
            self.assertEqual(plan.actions[0].edits[0].target_node, "Assign.value[liquid_class]")
            self.assertEqual(len(applied), 1)
            self.assertIn("Water Free Single'", output.read_text(encoding="utf-8"))
            self.assertNotIn("[001]", output.read_text(encoding="utf-8"))

    def test_configured_catalog_alias_uses_vendored_catalog_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_dir = root / "fluentcoder" / "catalog"
            catalog_dir.mkdir(parents=True)
            conn = sqlite3.connect(str(catalog_dir / "install_index.db"))
            try:
                conn.execute("CREATE TABLE components (name TEXT NOT NULL)")
                conn.execute("INSERT INTO components (name) VALUES (?)", ("EVA",))
                conn.commit()
            finally:
                conn.close()

            draft = root / "draft.py"
            output = root / "draft_repaired.py"
            draft.write_text("plate = Plate96('EVA[001]', catalog='EVA[001]')\n", encoding="utf-8")

            old = repair.fluentcoder_root
            repair.fluentcoder_root = lambda: root
            try:
                plan = repair.build_repair_plan(draft)
                applied = repair.apply_repair_plan(plan, output)
            finally:
                repair.fluentcoder_root = old

            catalog_actions = [action for action in plan.actions if action.kind == "catalog_alias"]
            self.assertEqual(len(catalog_actions), 1)
            self.assertEqual(catalog_actions[0].status, "ready")
            self.assertEqual(len(catalog_actions[0].edits), 1)
            self.assertIn("Call.keyword[catalog]", {edit.target_node for edit in catalog_actions[0].edits})
            self.assertNotIn("Call.args[0]", {edit.target_node for edit in catalog_actions[0].edits})
            self.assertEqual(len(applied), 1)
            text = output.read_text(encoding="utf-8")
            self.assertIn("Plate96('EVA[001]', catalog='EVA')", text)

    def test_raw_xml_modeling_suggestion_can_be_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            output = Path(tmp) / "draft_repaired.py"
            simulation = Path(tmp) / "simulation.json"
            xml = (
                "<Object>"
                "<LabwareName>SourcePlate</LabwareName>"
                "<LiquidClassName>Water Free Single</LiquidClassName>"
                "<Volume>20</Volume>"
                "</Object>"
            )
            draft.write_text(
                "\n".join(
                    [
                        "from fluentcoder import Plate96, Worktable",
                        "def build_worktable():",
                        "    wt = Worktable.from_workspace('Deck')",
                        "    sourceplate = wt.place(Plate96('SourcePlate'), 'Site', 1)",
                        "    head = wt.mca96",
                        f"    wt.raw_xml_step('Mca384Aspirate', {xml!r})",
                        "    return wt",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            simulation.write_text(
                '{"unsupported_command_ids": {"Mca384Aspirate": 1}}',
                encoding="utf-8",
            )

            plan = repair.build_repair_plan(draft, simulation_json_path=simulation)
            applied = repair.apply_repair_plan(plan, output, apply_modeling=True)

            self.assertEqual(len(plan.actions), 1)
            self.assertEqual(plan.actions[0].status, "suggested")
            self.assertEqual(plan.actions[0].edits[0].target_type, "python_ast_call")
            self.assertEqual(plan.actions[0].edits[0].target_node, "Call.raw_xml_step[Mca384Aspirate]")
            self.assertEqual(len(applied), 1)
            text = output.read_text(encoding="utf-8")
            self.assertIn("head.aspirate(sourceplate, 20, liquid_class='Water Free Single')", text)
            self.assertNotIn("raw_xml_step", text)

    def test_registry_supported_raw_xml_does_not_create_repair_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            simulation = Path(tmp) / "simulation.json"
            draft.write_text(
                "def build_worktable():\n"
                "    wt.raw_xml_step('LoopGroup', '<Object />')\n",
                encoding="utf-8",
            )
            simulation.write_text(
                '{"unsupported_command_ids": {"LoopGroup": 1}}',
                encoding="utf-8",
            )

            plan = repair.build_repair_plan(draft, simulation_json_path=simulation)

            self.assertEqual(plan.actions, [])

    def test_tipbox_capacity_failure_repairs_generic_tipbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            output = Path(tmp) / "draft_repaired.py"
            simulation = Path(tmp) / "simulation.json"
            draft.write_text(
                "\n".join(
                    [
                        "from fluentcoder import TipBox, Worktable",
                        "def build_worktable():",
                        "    wt = Worktable.from_workspace('Deck')",
                        "    tips = wt.place(TipBox('Tips', catalog='MCA96, 100ul, Box'), 'Site', 1)",
                        "    return wt",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            simulation.write_text(
                '{"failure": {"category": "tip_capacity", "details": {"capacity_ul": 0.0}}}',
                encoding="utf-8",
            )

            plan = repair.build_repair_plan(draft, simulation_json_path=simulation)
            applied = repair.apply_repair_plan(plan, output)

            self.assertEqual(len(plan.actions), 1)
            self.assertEqual(plan.actions[0].kind, "tipbox_class_repair")
            self.assertEqual(plan.actions[0].status, "ready")
            self.assertEqual(plan.actions[0].edits[0].target_type, "python_ast_name")
            self.assertEqual(plan.actions[0].edits[0].target_node, "Call.func[TipBox]")
            self.assertEqual(len(applied), 1)
            text = output.read_text(encoding="utf-8")
            self.assertIn("MCA100Box", text)
            self.assertIn("tips = wt.place(MCA100Box('Tips'", text)

    def test_worktable_instance_labels_are_not_catalog_alias_repairs(self):
        """TransferLabware Labware must keep FilterDWP[001]-style instance labels."""
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            draft.write_text(
                "wt.application_driver_macro('RGA1_TransferLabware', "
                "parameters={'Labware': 'FilterDWP[001]', "
                "'Location': 'GetCoverSiteName(\"CollectionDWP[001]\")', "
                "'Site': 'GetCoverSiteIndex(\"CollectionDWP[001]\")'})\n",
                encoding="utf-8",
            )
            ctx = ProjectContext(
                name="demo",
                root=Path(tmp),
                manifest={
                    "labware_names": ["FilterDWP[001]", "CollectionDWP[001]"],
                    "catalog_alias_candidates": [
                        {"project_name": "FilterDWP[001]", "base_name": "FilterDWP"}
                    ],
                },
            )
            plan = repair.build_repair_plan(draft, context=ctx)
            catalog_actions = [action for action in plan.actions if action.kind == "catalog_alias"]
            self.assertEqual(catalog_actions, [])

    def test_repair_application_fails_when_recorded_span_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.py"
            output = Path(tmp) / "draft_repaired.py"
            draft.write_text("catalog = 'Plexiglas Pane[002]'\n", encoding="utf-8")
            ctx = ProjectContext(
                name="demo",
                root=Path(tmp),
                manifest={
                    "catalog_alias_candidates": [
                        {"project_name": "Plexiglas Pane[002]", "base_name": "Plexiglas Pane"}
                    ]
                },
            )
            old = repair._catalog_name_exists
            repair._catalog_name_exists = lambda name: name == "Plexiglas Pane"
            try:
                plan = repair.build_repair_plan(draft, context=ctx)
                draft.write_text("catalog = 'Plexiglas Pane[999]'\n", encoding="utf-8")
                with self.assertRaises(repair.RepairApplicationError):
                    repair.apply_repair_plan(plan, output)
            finally:
                repair._catalog_name_exists = old


if __name__ == "__main__":
    unittest.main()
