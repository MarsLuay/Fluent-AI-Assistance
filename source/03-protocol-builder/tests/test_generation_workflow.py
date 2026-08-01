from dataclasses import replace as dataclass_replace
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import fluent_pipeline.project_context as pc
from fluent_pipeline.compiled_xscr_finalizer import FinalizationReport
from fluent_pipeline.generation_options import GenerationOptions, normalize_generation_options
from fluent_pipeline.generation_workflow import (
    GENERATION_STAGES,
    ApprovalSet,
    GenerationRequest,
    _attach_regeneration_baseline_context,
    _augment_ir_generation_metadata,
    _approval_records,
    _build_readiness_profile,
    _context_source_projects,
    _generation_ir_source_mode,
    _generation_published_zeia_success,
    _matching_regeneration_baseline_script,
    _normalize_ir_labware_labels_against_manifest,
    _refresh_request_spec_context,
    run_generation_workflow as _run_generation_workflow,
)
from fluent_pipeline.readiness import readiness_status_from_readiness
from fluent_pipeline.readiness_gates import readiness_gate_approval_context_keys
from fluent_pipeline.protocol_ir import write_protocol_ir
from fluent_pipeline.protocol_ir_schema import ProtocolIRValidationError
from fluent_pipeline.repair import RepairAction, RepairPlan
from fluent_pipeline.request_spec import normalize_request_spec, write_request_spec
from fluent_pipeline.runner import CommandResult
from fluent_pipeline.runner import PipelineError
from fluent_pipeline.validation import scaffold_validation_report


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

CLEANUP_XSCR = """<?xml version="1.0"?>
<Root>
  <ObjectName>CleanupScript</ObjectName>
  <Reference>
    <Guid>cleanup-workspace-guid</Guid>
    <TypeId>WorktableWorkspace</TypeId>
    <ObjectName>Cleanup Worktable</ObjectName>
  </Reference>
  <Script version="1.0" />
  <Object Type="Tecan.Fluent.Commands.MCA96.DispenseDataV1">
    <LabwareName>DestinationPlate</LabwareName>
    <LiquidClassName>Water Free Single</LiquidClassName>
  </Object>
</Root>
"""


def test_matching_regeneration_baseline_prefers_primary_context(tmp_path: Path):
    primary_root = tmp_path / "primary"
    reference_root = tmp_path / "reference"
    primary_script = primary_root / "extracted" / "main.xscr"
    reference_script = reference_root / "extracted" / "main.xscr"
    primary_script.parent.mkdir(parents=True)
    reference_script.parent.mkdir(parents=True)
    primary_script.write_text("<Root />", encoding="utf-8")
    reference_script.write_text("<Root />", encoding="utf-8")
    context = pc.ProjectCollection(
        "combined",
        tmp_path / "combined",
        {
            "source_projects": [
                {"name": "primary", "root": str(primary_root)},
                {"name": "reference", "root": str(reference_root)},
            ],
            "scripts": [
                {
                    "object_name": "DemoProtocol",
                    "resolved_path": str(reference_script),
                },
                {
                    "object_name": "DemoProtocol",
                    "resolved_path": str(primary_script),
                },
            ],
        },
    )

    assert _matching_regeneration_baseline_script(context, "DemoProtocol") == primary_script.resolve()


def test_generation_ir_source_precedence_is_strict() -> None:
    ir_path = Path("explicit.ir.json")
    baseline = Path("matching.xscr")
    recipe = {"groups": [{"name": "Changed recipe", "steps": [{"prompt": "New"}]}]}

    assert _generation_ir_source_mode(
        ir_source=ir_path,
        recipe=recipe,
        preserve_regeneration_baseline=True,
        regeneration_baseline=baseline,
    ) == "explicit_ir"
    assert _generation_ir_source_mode(
        ir_source=None,
        recipe=recipe,
        preserve_regeneration_baseline=True,
        regeneration_baseline=baseline,
    ) == "explicit_recipe"
    assert _generation_ir_source_mode(
        ir_source=None,
        recipe=None,
        preserve_regeneration_baseline=True,
        regeneration_baseline=baseline,
    ) == "preserve_regeneration_baseline"
    assert _generation_ir_source_mode(
        ir_source=None,
        recipe=None,
        preserve_regeneration_baseline=False,
        regeneration_baseline=baseline,
    ) == "automatic_synthesis"
    assert _generation_ir_source_mode(
        ir_source=None,
        recipe=None,
        preserve_regeneration_baseline=True,
        regeneration_baseline=None,
    ) == "missing_regeneration_baseline"


def test_regeneration_baseline_context_does_not_replace_recipe_steps(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "DemoProtocol.xscr"
    baseline.write_text(XSCR, encoding="utf-8")
    recipe_steps = [
        {"type": "comment", "text": "Recipe-authored step"},
        {"type": "prompt_user", "message": "Confirm the changed recipe"},
    ]
    ir = {
        "protocol": {"name": "DemoProtocol"},
        "steps": list(recipe_steps),
        "source": {
            "selected_source_scripts": [
                {
                    "object_name": "SupportingScript",
                    "resolved_path": str(tmp_path / "support.xscr"),
                }
            ]
        },
    }

    _attach_regeneration_baseline_context(
        ir,
        regeneration_baseline=baseline,
        context=None,
        protocol_name="DemoProtocol",
        project_archive=tmp_path / "source.zeia",
        supplies_steps=False,
    )

    assert ir["steps"] == recipe_steps
    assert ir["source"]["regeneration_baseline"]["role"] == "context_only"
    assert ir["source"]["selected_source_scripts"][0]["resolved_path"] == str(
        baseline
    )
    assert (
        ir["source"]["selected_source_scripts"][1]["object_name"]
        == "SupportingScript"
    )


def _ok_command_result(command: str):
    return mock.Mock(
        ok=True,
        returncode=0,
        stdout="",
        stderr="",
        command_line=mock.Mock(return_value=command),
    )


def run_generation_workflow(**kwargs):
    progress = kwargs.pop("progress", None)
    progress_callback = kwargs.pop("progress_callback", None)
    event_sink = kwargs.pop("event_sink", None)
    event_log_path = kwargs.pop("event_log_path", None)
    request_spec = kwargs.pop("request_spec", None)
    request_spec_source = kwargs.pop("request_spec_source", None)
    base_options = normalize_generation_options(
        kwargs.pop("options", GenerationOptions()),
        simulate=kwargs.pop("simulate", None),
        compile_xscr=kwargs.pop("compile_xscr", None),
        max_repair_iterations=kwargs.pop("max_repair_iterations", None),
        strict_readiness=kwargs.pop("strict_readiness", None),
        apply_modeling=kwargs.pop("apply_modeling", None),
        approve_partial_zeia=kwargs.get("approve_partial_zeia"),
        approve_deck_layout=kwargs.get("approve_deck_layout"),
        approve_command_inventory=kwargs.get("approve_command_inventory"),
        approve_unsupported_raw_xml=kwargs.get("approve_unsupported_raw_xml"),
        waive_checksum_recompute=kwargs.get("waive_checksum_recompute"),
        fluent_context_check=kwargs.pop("fluent_context_check", None),
        fluent_provider=kwargs.pop("fluent_provider", None),
        fluent_timeout=kwargs.pop("fluent_timeout", None),
    )
    options = dataclass_replace(
        base_options,
        project_archive=kwargs.pop("project_archive", None),
        project_name=kwargs.pop("project_name", None),
        force_import=kwargs.pop("force_import", False),
        pattern_refs=tuple(kwargs.pop("pattern_refs", []) or []),
        index_db=kwargs.pop("index_db", None),
        pattern_ids=tuple(kwargs.pop("pattern_ids", []) or []),
        pattern_queries=tuple(kwargs.pop("pattern_queries", []) or []),
        source_script_rank=kwargs.pop("source_script_rank", 1),
        protocol_name=kwargs.pop("protocol_name", None),
        subroutine_dirs=tuple(kwargs.pop("subroutine_dirs", []) or []),
        record_snapshots=kwargs.pop("record_snapshots", None),
        deterministic_compile=kwargs.pop("deterministic_compile", False),
        fluent_method=kwargs.pop("fluent_method", None),
        fluent_command=kwargs.pop("fluent_command", None),
        fluent_host=kwargs.pop("fluent_host", "127.0.0.1"),
        fluent_port=kwargs.pop("fluent_port", 50052),
        fluent_insecure=kwargs.pop("fluent_insecure", False),
    )
    if request_spec is not None:
        if request_spec_source is None:
            request_spec_source = Path(kwargs["out_dir"]) / "request.spec.yaml"
        request_spec_source.parent.mkdir(parents=True, exist_ok=True)
        write_request_spec(request_spec, request_spec_source)
    context = kwargs.pop("context", None)
    request = GenerationRequest(
        intent=kwargs.pop("intent"),
        output_directory=kwargs.pop("out_dir"),
        context_name=context.name if context is not None else None,
        source_scripts=tuple(kwargs.pop("source_scripts", []) or []),
        protocol_ir=kwargs.pop("ir_source", None),
        options=options,
        approvals=ApprovalSet(
            approve_partial_zeia=kwargs.pop("approve_partial_zeia", options.approve_partial_zeia),
            waive_checksum_recompute=kwargs.pop("waive_checksum_recompute", options.waive_checksum_recompute),
            approve_deck_layout=kwargs.pop("approve_deck_layout", options.approve_deck_layout),
            approve_command_inventory=kwargs.pop(
                "approve_command_inventory",
                options.approve_command_inventory,
            ),
            approve_unsupported_raw_xml=kwargs.pop(
                "approve_unsupported_raw_xml",
                options.approve_unsupported_raw_xml,
            ),
        ),
        request_spec_path=request_spec_source,
    )
    if kwargs:
        raise AssertionError(f"Unhandled workflow test kwargs: {sorted(kwargs)}")
    return _run_generation_workflow(
        request,
        progress_callback=progress_callback,
        progress=progress,
        event_sink=event_sink,
        event_log_path=event_log_path,
    )


class GenerationWorkflowTests(unittest.TestCase):
    def test_progress_callback_emits_public_stage_order_for_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            with mock.patch(
                "fluent_pipeline.generation_workflow._verify_full_zeia_export",
                return_value={"accepted": True, "status": "accepted"},
            ):
                run_generation_workflow(
                    intent="Generate a simple scaffold protocol",
                    out_dir=Path(tmp),
                    simulate=False,
                    compile_xscr=False,
                    progress_callback=events.append,
                    request_spec={
                        "schema_version": "tecan.request_spec.v1",
                        "request": {"intent": "Generate a simple scaffold protocol"},
                        "worktable": {"name": "StubWorkspace", "auto_place": False},
                        "review": {"state": "approved"},
                    },
                )

        terminal = [
            (event.stage_id, event.status)
            for event in events
            if event.status in {"completed", "skipped", "warning", "failed"}
        ]
        self.assertEqual(
            terminal,
            [
                ("load_context", "completed"),
                ("validate_request", "completed"),
                ("build_protocol_ir", "completed"),
                ("render_script", "completed"),
                ("simulate", "skipped"),
                ("repair", "completed"),
                ("compile_xscr", "skipped"),
                ("finalize_xscr", "skipped"),
                ("validate_bundle", "skipped"),
                ("publish_bundle", "skipped"),
            ],
        )
        self.assertTrue(all(event.total_stages == 10 for event in events))

    def test_progress_callback_emits_failed_event_for_request_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            with mock.patch(
                "fluent_pipeline.generation_workflow._prepare_request_spec",
                side_effect=PipelineError("bad request"),
            ):
                with self.assertRaises(PipelineError):
                    run_generation_workflow(
                        intent="Generate a broken request",
                        out_dir=Path(tmp),
                        simulate=False,
                        compile_xscr=False,
                        progress_callback=events.append,
                    )

        self.assertEqual(events[-1].stage_id, "validate_request")
        self.assertEqual(events[-1].status, "failed")
        self.assertIn("bad request", events[-1].message or "")

    def test_approval_records_prefer_request_spec_generation_sources(self):
        spec = normalize_request_spec(
            {
                "request": {"intent": "Provenance approval source test"},
                "generation": {
                    "approve_partial_zeia": True,
                    "waive_checksum_recompute": True,
                    "approve_deck_layout": True,
                    "approve_command_inventory": True,
                    "approve_unsupported_raw_xml": True,
                },
                "source": {
                    "full_zeia_export": {
                        "approved_partial_zeia": True,
                    }
                },
            }
        )

        approvals = _approval_records(
            spec,
            approve_partial_zeia=True,
            waive_checksum_recompute=True,
            approve_deck_layout=True,
            approve_command_inventory=True,
            approve_unsupported_raw_xml=True,
        )

        deck_layout_key = readiness_gate_approval_context_keys("deck_layout_consistent")[0]
        self.assertEqual(approvals["partial_zeia_export_approved"]["source"], "request_spec")
        self.assertEqual(
            approvals["checksums_recompute_waived"]["source"],
            "request_spec.generation.waive_checksum_recompute",
        )
        self.assertEqual(
            approvals[deck_layout_key]["source"],
            "request_spec.generation.approve_deck_layout",
        )
        self.assertEqual(
            approvals["command_inventory_approved"]["source"],
            "request_spec.generation.approve_command_inventory",
        )
        self.assertEqual(
            approvals["unsupported_raw_xml_approved"]["source"],
            "request_spec.generation.approve_unsupported_raw_xml",
        )

    def test_refresh_request_spec_context_replaces_stale_contexts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = {
                "source": {
                    "contexts": [
                        {
                            "name": "old-full-export",
                            "root": "C:/stale/project",
                            "manifest": "C:/stale/project/manifest.json",
                        }
                    ]
                }
            }
            context = pc.ProjectCollection(
                "script1-source-request-contexts",
                root / "collection",
                {
                    "kind": "project_collection",
                    "source_projects": [
                        {
                            "name": "script1-source",
                            "root": str(root / "script1-source"),
                            "manifest": str(root / "script1-source" / "manifest.json"),
                            "source_archive": str(root / "script1.zeia"),
                            "copied_archive": str(root / "script1-source" / "source" / "script1.zeia"),
                        },
                        {
                            "name": "full-export",
                            "root": str(root / "full-export"),
                            "manifest": str(root / "full-export" / "manifest.json"),
                            "source_archive": str(root / "FullExport.zeia"),
                            "copied_archive": str(root / "full-export" / "source" / "FullExport.zeia"),
                        },
                    ],
                },
            )

            _refresh_request_spec_context(spec, context)

        self.assertEqual(
            [item["name"] for item in spec["source"]["contexts"]],
            ["script1-source", "full-export"],
        )

    def test_primary_context_source_projects_uses_first_collection_source_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_source = root / "projects" / "script1-source" / "source"
            full_source = root / "projects" / "full-export" / "source"
            script_source.mkdir(parents=True)
            full_source.mkdir(parents=True)
            script_archive = script_source / "script1.zeia"
            full_archive = full_source / "FullExport.zeia"
            script_archive.write_bytes(b"script source archive")
            full_archive.write_bytes(b"full export archive")
            context = pc.ProjectCollection(
                "script1-source-request-contexts",
                root / "collections" / "script1-source-request-contexts",
                {
                    "kind": "project_collection",
                    "source_projects": [
                        {
                            "name": "script1-source",
                            "root": str(root / "projects" / "script1-source"),
                            "manifest": str(root / "projects" / "script1-source" / "manifest.json"),
                            "copied_archive": str(script_archive),
                            "source_archive": str(root / "script1.zeia"),
                        },
                        {
                            "name": "full-export",
                            "root": str(root / "projects" / "full-export"),
                            "manifest": str(root / "projects" / "full-export" / "manifest.json"),
                            "copied_archive": str(full_archive),
                            "source_archive": str(root / "FullExport.zeia"),
                        },
                    ],
                    "copied_archives": [str(script_archive), str(full_archive)],
                    "source_archives": [str(root / "script1.zeia"), str(root / "FullExport.zeia")],
                },
            )

            primary = _context_source_projects(context, primary_only=True)
            all_sources = _context_source_projects(context)

        self.assertEqual(primary, [script_archive.resolve()])
        self.assertEqual(
            all_sources,
            [script_archive.resolve(), full_archive.resolve()],
        )

    def test_normalizes_dynamic_labware_labels_against_source_manifest(self):
        ir = {
            "source": {},
            "labware": [
                {
                    "id": "sourcetube15_numsourcetubes_main",
                    "label": "SourceTube15[NumSourceTubes_Main]",
                    "source": "selected_script",
                }
            ],
            "steps": [
                {
                    "id": "prompt_source_tubes",
                    "operation": "prompt_user",
                    "target_labware": "SourceTube15[NumSourceTubes_Main]",
                    "parameters": {
                        "labware": "SourceTube15[NumSourceTubes_Main]",
                        "worktable_labware": {
                            "labware": "SourceTube15[NumSourceTubes_Main]",
                            "selected_labware_name": "SourceTube15[NumSourceTubes_Main]",
                        },
                        "raw_xml": "<LabwareName>SourceTube15[NumSourceTubes_Main]</LabwareName>",
                    },
                }
            ],
        }
        manifest = {"labware_names": ["SourceTube15[NumSourceTubes]"]}

        records = _normalize_ir_labware_labels_against_manifest(ir, manifest)

        self.assertEqual(
            records,
            [
                {
                    "from": "SourceTube15[NumSourceTubes_Main]",
                    "to": "SourceTube15[NumSourceTubes]",
                    "reason": "source_manifest_dynamic_labware_label",
                }
            ],
        )
        self.assertEqual(ir["labware"][0]["id"], "sourcetube15_numsourcetubes")
        self.assertEqual(ir["labware"][0]["label"], "SourceTube15[NumSourceTubes]")
        self.assertEqual(ir["steps"][0]["target_labware"], "SourceTube15[NumSourceTubes]")
        self.assertEqual(ir["steps"][0]["parameters"]["labware"], "SourceTube15[NumSourceTubes]")
        self.assertEqual(
            ir["steps"][0]["parameters"]["worktable_labware"]["selected_labware_name"],
            "SourceTube15[NumSourceTubes]",
        )
        self.assertIn("SourceTube15[NumSourceTubes_Main]", ir["steps"][0]["parameters"]["raw_xml"])
        self.assertEqual(ir["source"]["labware_label_normalization"], records)

    def test_generation_fails_instead_of_inventing_set_variable_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ir_path = tmp_path / "input.protocol-ir.json"
            ir_path.write_text(
                json.dumps(
                    {
                        "ir_version": "tecan.protocol_ir.v2",
                        "expression_schema_version": "fluent_control.expression.v1",
                        "id": "missing_variable_demo",
                        "protocol": {"name": "Missing Variable Demo"},
                        "worktable": {"name": "780_Empty", "auto_place": False},
                        "labware": [],
                        "reagents": [],
                        "variables": [],
                        "steps": [
                            {
                                "id": "step_set_tube_name",
                                "index": 1,
                                "operation": "set_variable",
                                "command_id": "SetVariableStatement",
                                "name": "Set Variable",
                                "parameters": {
                                    "variable": "TubeName",
                                    "value_expression": {
                                        "kind": "string_literal",
                                        "value": "Falcon",
                                    },
                                },
                            }
                        ],
                        "dependencies": [],
                        "source": {},
                    }
                ),
                encoding="utf-8",
            )
            out_dir = tmp_path / "build"
            with mock.patch(
                "fluent_pipeline.generation_workflow._verify_full_zeia_export",
                return_value={"accepted": True, "status": "accepted"},
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_python_draft",
            ) as render_mock:
                with self.assertRaises(ProtocolIRValidationError) as raised:
                    run_generation_workflow(
                        intent="Compile the generated script",
                        out_dir=out_dir,
                        ir_source=ir_path,
                        simulate=False,
                        compile_xscr=False,
                    )

            render_mock.assert_not_called()
            self.assertIn("TubeName", str(raised.exception))
            self.assertFalse(list(out_dir.glob("*.variable-reconciliation.json")))
            self.assertFalse(list(out_dir.glob("*.protocol-ir.json")))
            self.assertEqual(json.loads(ir_path.read_text(encoding="utf-8")).get("variables") or [], [])

    def test_generation_blocks_zeia_label_catalog_mismatch_before_project_work(self):
        from fluent_pipeline.spec_lint import lint_request_spec

        request_spec = {
            "schema_version": "tecan.request_spec.v1",
            "request": {
                "intent": "Build A200 verification",
                "protocol_name": "Demo_A200_Verification",
            },
            "source": {"context": "demo-context"},
            "verification_recipe": {
                "groups": [
                    {
                        "name": "Setup",
                        "description": "Set up A200 verification.",
                        "steps": [{"prompt": "Confirm setup."}],
                    }
                ],
                "labware": [
                    {
                        "label": "AdapterA200",
                        "catalog": "Adapter A200",
                        "location": "Demo_Nest_Pos",
                        "site": 1,
                    }
                ],
            },
        }
        # Preferred catalogs come from the imported ZEIA, not a hardcoded product rule.
        result = lint_request_spec(
            request_spec,
            preferred_label_catalogs={"adaptera200": "Adapter A200_ElutionRack"},
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("Adapter A200_ElutionRack" in f.message for f in result.errors))

    def test_generation_metadata_annotation_is_idempotent(self):
        ir = {
            "source": {},
            "dependencies": [],
            "safety_assumptions": [
                {
                    "id": "manual_validation_required",
                    "text": "Review before use.",
                },
                {
                    "id": "generated_from_official_workflow",
                    "text": "This IR entered the official inspect-plan-draft-simulate-repair-compile generation workflow.",
                },
            ],
        }

        selection = {
            "resolved_source_scripts": [],
            "pattern_refs": [],
            "indexed_pattern_windows": [],
        }
        _augment_ir_generation_metadata(ir, "Generate a script", None, selection)
        _augment_ir_generation_metadata(ir, "Generate a script", None, selection)

        workflow_assumptions = [
            assumption
            for assumption in ir["safety_assumptions"]
            if isinstance(assumption, dict) and assumption.get("id") == "generated_from_official_workflow"
        ]
        self.assertEqual(len(workflow_assumptions), 1)

    def test_generation_workflow_compile_stage_runs_compiled_xscr_finalizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ir_path = tmp_path / "input.protocol-ir.json"
            out_dir = tmp_path / "build"
            write_protocol_ir(
                {
                    "protocol": {"name": "Workflow Finalizer Demo"},
                    "worktable": {"name": "780_Empty", "auto_place": False},
                    "labware": [],
                    "reagents": [],
                    "steps": [],
                    "dependencies": [],
                    "source": {},
                },
                ir_path,
            )
            finalization_report = FinalizationReport(
                ok=True,
                checksum_before="valid",
                checksum_after="valid",
                roundtrip={"matched": True},
                command_validation={"failure_count": 0},
                generic_command_validation={"failure_count": 0},
            )

            def fake_compile(command, catalog_db=None):
                Path(command[-1]).write_text("<VxData><Payload /></VxData>", encoding="utf-8")
                return _ok_command_result("compile")

            with mock.patch(
                "fluent_pipeline.generation_workflow.run_fluentcoder",
                side_effect=fake_compile,
            ), mock.patch(
                "fluent_pipeline.generation_workflow.finalize_compiled_xscr",
                return_value=finalization_report,
            ) as finalize_mock, mock.patch(
                "fluent_pipeline.generation_workflow.validate_ready_to_import",
                return_value=scaffold_validation_report("synthetic validation stop"),
            ):
                manifest = run_generation_workflow(
                    intent="Compile the generated script",
                    out_dir=out_dir,
                    ir_source=ir_path,
                    simulate=False,
                    compile_xscr=True,
                    approve_partial_zeia=True,
                )

            finalize_mock.assert_called_once()
            self.assertIsNone(manifest["compiled_xscr"])
            self.assertFalse(any(out_dir.glob("*.xscr")))
            self.assertIsNone(manifest["failed_artifacts"])
            self.assertEqual(manifest["internal_artifacts"][0]["kind"], "compiled_xscr_intermediate")
            self.assertFalse(manifest["internal_artifacts"][0]["deliverable"])
            self.assertIsNone(manifest["internal_artifacts"][0]["path"])
            self.assertEqual(manifest["compiled_xscr_finalization"]["ok"], True)
            self.assertIn(
                "Compiled XSCR Finalization",
                Path(manifest["compile_report"]).read_text(encoding="utf-8"),
            )

    def test_generation_workflow_preserves_failed_xscr_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ir_path = tmp_path / "input.protocol-ir.json"
            out_dir = tmp_path / "build"
            write_protocol_ir(
                {
                    "protocol": {"name": "Preserve Failed Demo"},
                    "worktable": {"name": "780_Empty", "auto_place": False},
                    "labware": [],
                    "reagents": [],
                    "steps": [],
                    "dependencies": [],
                    "source": {},
                },
                ir_path,
            )
            finalization_report = FinalizationReport(
                ok=True,
                checksum_before="valid",
                checksum_after="valid",
                roundtrip={"matched": True},
                command_validation={"failure_count": 0},
                generic_command_validation={"failure_count": 0},
            )

            def fake_compile(command, catalog_db=None):
                Path(command[-1]).write_text("<VxData><Payload /></VxData>", encoding="utf-8")
                return _ok_command_result("compile")

            with mock.patch(
                "fluent_pipeline.generation_workflow.run_fluentcoder",
                side_effect=fake_compile,
            ), mock.patch(
                "fluent_pipeline.generation_workflow.finalize_compiled_xscr",
                return_value=finalization_report,
            ), mock.patch(
                "fluent_pipeline.generation_workflow.validate_ready_to_import",
                return_value=scaffold_validation_report("synthetic validation stop"),
            ):
                manifest = run_generation_workflow(
                    intent="Compile the generated script",
                    out_dir=out_dir,
                    ir_source=ir_path,
                    options=GenerationOptions(
                        simulate=False,
                        compile_xscr=True,
                        preserve_failed_artifacts=True,
                    ),
                    approve_partial_zeia=True,
                )

            failed_artifacts = Path(manifest["failed_artifacts"])
            self.assertTrue((failed_artifacts / "protocol.xscr").exists())
            self.assertTrue((failed_artifacts / "reports" / "preserve_failed_demo.compile.md").exists())
            self.assertFalse(any(out_dir.glob("*.xscr")))
            self.assertEqual(manifest["internal_artifacts"][0]["path"], str(failed_artifacts / "protocol.xscr"))
            self.assertFalse(manifest["internal_artifacts"][0]["deliverable"])

    def test_generation_workflow_scaffolds_official_stages_without_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "demo.zeia"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr("Scripts/demo.xscr", XSCR)
                    zf.writestr(
                        "Worktables/base.xwsp",
                        """<?xml version="1.0"?>
<Workspace>
  <ObjectName>Base Worktable</ObjectName>
  <Guid>workspace-guid</Guid>
</Workspace>
""",
                    )
                    _write_liquid_class(zf)

                out_dir = tmp_path / "generation"
                manifest = run_generation_workflow(
                    intent="Use the source assay setup to make a new transfer script",
                    out_dir=out_dir,
                    project_archive=archive,
                    project_name="demo-generation",
                    source_scripts=["DemoScript"],
                    pattern_refs=["pick_up_tips from DemoScript"],
                    simulate=False,
                    compile_xscr=False,
                )

                self.assertEqual(
                    [stage["id"] for stage in manifest["stages"]],
                    [stage_id for stage_id, _ in GENERATION_STAGES],
                )
                stages_by_id = {stage["id"]: stage for stage in manifest["stages"]}
                self.assertEqual(stages_by_id["import_project_context"]["status"], "passed")
                self.assertIn(
                    stages_by_id["inspect_host_instrument_config"]["status"],
                    {"matched", "needs_review", "failed"},
                )
                self.assertEqual(stages_by_id["build_protocol_ir"]["status"], "needs_user")
                self.assertEqual(stages_by_id["validate_liquid_logic"]["status"], "passed")
                self.assertTrue(Path(manifest["ir_synthesis"]).exists())
                self.assertEqual(stages_by_id["simulate"]["status"], "skipped")
                self.assertEqual(stages_by_id["compile_xscr"]["status"], "skipped")
                self.assertEqual(stages_by_id["generate_recreate_script"]["status"], "passed")
                self.assertEqual(stages_by_id["generate_worktable_changes"]["status"], "passed")
                self.assertEqual(stages_by_id["validate_ready_gates"]["status"], "skipped")
                self.assertEqual(stages_by_id["generate_validation_diff"]["status"], "needs_user")
                self.assertEqual(stages_by_id["package_ready_to_import"]["status"], "skipped")
                # A scaffold must be unmistakably marked as NOT validated / NOT ready.
                self.assertEqual(manifest["workflow_status"], "scaffold_not_validated")
                self.assertEqual(manifest["readiness_status"], "scaffold_not_validated")
                self.assertEqual(manifest["bundle_role"], "debug")
                self.assertEqual(manifest["source_export_kind"], "full")
                self.assertEqual(manifest["verification_state"], "not_validated")
                self.assertEqual(manifest["lifecycle"]["bundle_role"], "debug")
                self.assertEqual(manifest["readiness"]["script_editor_load"]["status"], "not_run")
                self.assertFalse(manifest["ready_to_import"])
                self.assertTrue(manifest["ready_validation"])
                self.assertTrue(manifest["validation_report_json"])
                ready_validation = Path(manifest["ready_validation"]).read_text(encoding="utf-8")
                self.assertIn("scaffold only", ready_validation)
                self.assertIn("Not Ready To Import", ready_validation)
                summary = Path(manifest["workflow_report"]).read_text(encoding="utf-8")
                self.assertIn("scaffold_not_validated", summary)
                self.assertIn("Readiness Boundaries", summary)
                self.assertIn("Script Editor Load", summary)
                self.assertIn("NOT READY TO IMPORT", summary)
                self.assertTrue(Path(manifest["request_spec"]).exists())
                self.assertTrue(Path(manifest["host_instrument_config_report"]).exists())
                self.assertTrue(Path(manifest["host_instrument_config_json"]).exists())
                self.assertTrue(Path(manifest["protocol_ir"]).exists())
                self.assertTrue(Path(manifest["liquid_state_validation"]).exists())
                self.assertTrue(Path(manifest["python_draft"]).exists())
                self.assertTrue(Path(manifest["repaired_draft"]).exists())
                self.assertTrue(Path(manifest["recreate_script"]).exists())
                self.assertTrue(Path(manifest["worktable_changes"]).exists())
                self.assertTrue(Path(manifest["worktable_patch"]).exists())
                self.assertTrue(Path(manifest["validation_diff"]).exists())
                self.assertTrue(Path(manifest["validation_diff_json"]).exists())
                self.assertTrue(Path(manifest["generation_manifest"]).exists())
                self.assertTrue(Path(manifest["workflow_report"]).exists())
                environment = manifest["environment"]
                self.assertIn("repository_commit", environment)
                self.assertEqual(environment["protocol_builder_version"], "0.1.0")
                self.assertEqual(environment["fluentcoder_version"], "0.1.0")
                self.assertEqual(environment["tecan_common_version"], "0.1.0")
                self.assertEqual(environment["reader_version"], "0.1.0")
                self.assertEqual(environment["readiness_registry_version"], "tecan.readiness_gate_registry.v2")
                self.assertEqual(environment["simulation_backend"], "not_run")
                self.assertTrue(environment["command_registry_sha256"])
                self.assertIn(
                    "fluent_pipeline/policies/prompt_text.py",
                    environment["policy_profile_sha256s"],
                )
                source_hashes = manifest["source_archive_hashes"]
                self.assertEqual(len(source_hashes), 1)
                self.assertEqual(source_hashes[0]["context"], "demo-generation")
                self.assertTrue(source_hashes[0]["sha256"])
                self.assertTrue(source_hashes[0]["source_import_identity"])
                self.assertTrue(source_hashes[0]["source_import_identity"]["source_archive_sha256"])
                self.assertEqual(manifest["snapshot_hashes"], [])
                artifact_hashes = manifest["artifact_hashes"]
                self.assertTrue(artifact_hashes["request_spec"]["sha256"])
                self.assertTrue(artifact_hashes["input_ir"]["sha256"])
                self.assertTrue(artifact_hashes["python_draft"]["sha256"])
                self.assertTrue(artifact_hashes["repaired_python"]["sha256"])
                self.assertIsNone(artifact_hashes["finalized_xscr"]["sha256"])
                self.assertEqual(artifact_hashes["generated_zeia"], [])
                self.assertEqual(manifest["generation_options"], GenerationOptions(simulate=False, compile_xscr=False).as_dict())
                self.assertFalse(manifest["approval_records"]["partial_zeia_export_approved"]["approved"])
                self.assertEqual(
                    manifest["repair_iterations"]["iterations"][0]["simulation_status"],
                    "skipped",
                )
                self.assertTrue(manifest["repair_iterations"]["selected_repaired_sha256"])
                self.assertEqual(manifest["finalization_changes"]["changes"], [])
                request_spec = (out_dir / "request.spec.yaml").read_text(encoding="utf-8")
                self.assertIn("schema_version: tecan.request_spec.v1", request_spec)
                self.assertIn("source_scripts:", request_spec)
                synthesis = json.loads(Path(manifest["ir_synthesis"]).read_text(encoding="utf-8"))
                self.assertFalse(synthesis["planned"])
                self.assertEqual(synthesis["planned_step_count"], 0)
                worktable_patch = json.loads(Path(manifest["worktable_patch"]).read_text(encoding="utf-8"))
                self.assertEqual(worktable_patch["kind"], "worktable_patch")
                self.assertIn(worktable_patch["summary"]["overall_severity"], {"safe", "needs_review", "blocking"})
                validation_diff = json.loads(Path(manifest["validation_diff_json"]).read_text(encoding="utf-8"))
                self.assertEqual(validation_diff["kind"], "request_validation_diff")
                self.assertEqual(validation_diff["request_spec"]["review_state"], "needs_review")
                plan = (out_dir / "GENERATION_PLAN.md").read_text(encoding="utf-8")
                self.assertIn("1. Capture request.spec.yaml", plan)
                self.assertIn("7. Build protocol.ir.json", plan)
                self.assertIn("DemoScript", plan)
                ir = json.loads(Path(manifest["protocol_ir"]).read_text(encoding="utf-8"))
                self.assertEqual(ir["source"]["request_spec"]["schema_version"], "tecan.request_spec.v1")
                self.assertEqual(
                    ir["source"]["host_instrument_configuration"]["schema_version"],
                    "tecan.host_instrument_config.v1",
                )
                recreate = Path(manifest["recreate_script"]).read_text(encoding="utf-8")
                self.assertIn("Host Instrument Configuration", recreate)
                self.assertIn("configuration dropdown", recreate)
                self.assertIn("## Worktable Used", recreate)
                self.assertIn("Base Worktable", recreate)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_build_readiness_profile_separates_optional_load_failure(self):
        profile = _build_readiness_profile(
            workflow_status="ready_to_import",
            ready_to_import=True,
            validation_report={
                "gates": [
                    {"id": "simulation_passes", "status": "passed"},
                    {"id": "fluent_context_check", "status": "failed"},
                ],
                "offline_validation": {
                    "status": "ready_to_import",
                    "summary": "All required offline readiness gates passed.",
                    "required_gate_count": 26,
                    "passed_count": 26,
                    "failed_count": 0,
                },
                "review_state": {
                    "status": "hardware_review_required",
                    "summary": "Offline validation passed; hardware review is still required.",
                    "needs_review_count": 0,
                    "gates": [],
                },
                "fluentcontrol_load_diagnostic": {
                    "status": "load_failed",
                    "summary": "FluentControl import/load diagnostic reported a load failure.",
                    "requested": True,
                    "gate": "Gate 27",
                },
            },
            package_outputs=["/tmp/generated_project.zeia", "/tmp/generated_script.xscr"],
        )

        self.assertEqual(
            readiness_status_from_readiness(profile, workflow_status="ready_to_import"),
            "load_failed",
        )
        self.assertEqual(profile["generated_zeia_import"]["status"], "ready_to_import")
        self.assertEqual(profile["script_editor_load"]["status"], "load_failed")
        self.assertEqual(profile["offline_validation"]["status"], "ready_to_import")
        self.assertEqual(profile["review_state"]["status"], "hardware_review_required")

    def test_generation_success_requires_protocol_folder_zeia(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready_root = Path(tmp) / "ready-to-import"
            ready_root.mkdir()
            loose_zeia = ready_root / "demo.zeia"
            protocol_folder = ready_root / "demo"
            folder_zeia = protocol_folder / "demo.zeia"
            protocol_folder.mkdir()
            loose_zeia.write_bytes(b"loose")
            folder_zeia.write_bytes(b"folder")
            (protocol_folder / "RECREATE_SCRIPT.md").write_text("# Recreate\n", encoding="utf-8")
            (protocol_folder / "request.spec.yaml").write_text("request: {}\n", encoding="utf-8")
            (protocol_folder / "protocol.ir.json").write_text("{}", encoding="utf-8")
            (protocol_folder / "run_tecan_bundle_setup.bat").write_text("@echo off\n", encoding="utf-8")
            (protocol_folder / "generation_manifest.json").write_text("{}", encoding="utf-8")
            (protocol_folder / "GENERATION_WORKFLOW.md").write_text("# Workflow\n", encoding="utf-8")
            generated_dir = protocol_folder / "generated"
            generated_dir.mkdir()
            (generated_dir / "protocol.py").write_text("def build_worktable():\n    pass\n", encoding="utf-8")
            (protocol_folder / "reports").mkdir()
            (protocol_folder / "source").mkdir()
            (protocol_folder / "media").mkdir()
            (protocol_folder / "delivery_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "tecan.protocol_delivery.v2",
                        "bundle_schema_version": "tecan.ready_to_import.bundle.v2",
                        "protocol_name": "demo",
                        "external_file_deployments": [],
                        "deliverables": [{"kind": "fluent_project_archive", "path": "demo.zeia"}],
                    }
                ),
                encoding="utf-8",
            )

            validation_report = {"ready": True}
            with mock.patch("fluent_pipeline.generation_workflow.READY_TO_IMPORT_DIR", ready_root):
                self.assertFalse(
                    _generation_published_zeia_success(
                        [str(loose_zeia)],
                        validation_report=validation_report,
                    )
                )
                self.assertTrue(
                    _generation_published_zeia_success(
                        [str(folder_zeia)],
                        validation_report=validation_report,
                    )
                )
                self.assertFalse(
                    _generation_published_zeia_success(
                        [str(folder_zeia)],
                        validation_report={"ready": False},
                    )
                )

    def test_generation_workflow_uses_project_collection_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_collections_dir = pc.COLLECTIONS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                first = tmp_path / "first.zeia"
                second = tmp_path / "second.zeia"
                with zipfile.ZipFile(first, "w") as zf:
                    zf.writestr("Scripts/demo.xscr", XSCR)
                    zf.writestr(
                        "Worktables/base.xwsp",
                        """<?xml version="1.0"?>
<Workspace>
  <ObjectName>Base Worktable</ObjectName>
  <Guid>workspace-guid</Guid>
</Workspace>
""",
                    )
                    _write_liquid_class(zf)
                with zipfile.ZipFile(second, "w") as zf:
                    zf.writestr("Scripts/cleanup.xscr", CLEANUP_XSCR)
                    zf.writestr(
                        "Worktables/cleanup.xwsp",
                        """<?xml version="1.0"?>
<Workspace>
  <ObjectName>Cleanup Worktable</ObjectName>
  <Guid>cleanup-workspace-guid</Guid>
</Workspace>
""",
                    )
                    _write_liquid_class(zf)

                pc.import_project(first, name="source-assay")
                pc.import_project(second, name="cleanup-assay")
                collection = pc.create_project_collection(
                    "multi-zeia",
                    ["source-assay", "cleanup-assay"],
                )
                manifest = run_generation_workflow(
                    intent="Use both ZEIA contexts to make a combined protocol",
                    out_dir=tmp_path / "generation",
                    context=collection,
                    source_scripts=["source-assay:DemoScript", "cleanup-assay:CleanupScript"],
                    request_spec={
                        "schema_version": "tecan.request_spec.v1",
                        "request": {"intent": "Use both ZEIA contexts to make a combined protocol"},
                        "worktable": {"name": "Base Worktable", "guid": "workspace-guid"},
                        "review": {"state": "approved"},
                    },
                    simulate=False,
                    compile_xscr=False,
                )

                self.assertEqual(manifest["context"], "multi-zeia")
                self.assertEqual(manifest["context_kind"], "project_collection")
                self.assertEqual(
                    [item["name"] for item in manifest["source_contexts"]],
                    ["source-assay", "cleanup-assay"],
                )

                inspection = json.loads((tmp_path / "generation" / "01_context_inspection.json").read_text())
                self.assertEqual(inspection["context_kind"], "project_collection")
                self.assertEqual(len(inspection["selected_source_scripts"]), 2)
                self.assertEqual(
                    [script["source_context"] for script in inspection["selected_source_scripts"]],
                    ["source-assay", "cleanup-assay"],
                )

                ir = json.loads(Path(manifest["protocol_ir"]).read_text(encoding="utf-8"))
                self.assertEqual(ir["source"]["context_kind"], "project_collection")
                self.assertEqual(
                    [item["name"] for item in ir["source"]["contexts"]],
                    ["source-assay", "cleanup-assay"],
                )
                self.assertEqual(
                    [script["source_context"] for script in ir["source"]["selected_source_scripts"]],
                    ["source-assay", "cleanup-assay"],
                )

                plan = (tmp_path / "generation" / "GENERATION_PLAN.md").read_text(encoding="utf-8")
                self.assertIn("source-assay:DemoScript", plan)
                self.assertIn("cleanup-assay:CleanupScript", plan)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.COLLECTIONS_DIR = old_collections_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_generation_workflow_blocks_likely_partial_zeia(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "partial.zeia"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr("Scripts/demo.xscr", XSCR)

                manifest = run_generation_workflow(
                    intent="Use the source assay setup to make a new transfer script",
                    out_dir=tmp_path / "generation",
                    project_archive=archive,
                    project_name="partial-generation",
                    source_scripts=["DemoScript"],
                    simulate=False,
                    compile_xscr=False,
                )

                self.assertEqual(manifest["workflow_status"], "needs_full_zeia_export")
                self.assertFalse(manifest["ready_to_import"])
                stages_by_id = {stage["id"]: stage for stage in manifest["stages"]}
                self.assertEqual(stages_by_id["verify_full_zeia_export"]["status"], "needs_user")
                self.assertIn("Full ZEIA", stages_by_id["verify_full_zeia_export"]["summary"])
                self.assertIsNone(manifest["protocol_ir"])
                self.assertEqual(manifest["environment"]["simulation_backend"], "not_run")
                self.assertEqual(len(manifest["source_archive_hashes"]), 1)
                self.assertTrue(manifest["source_archive_hashes"][0]["sha256"])
                self.assertTrue(manifest["source_archive_hashes"][0]["source_import_identity"])
                self.assertEqual(manifest["snapshot_hashes"], [])
                self.assertIsNone(manifest["artifact_hashes"]["input_ir"]["sha256"])
                self.assertEqual(manifest["repair_iterations"]["iterations"], [])
                self.assertEqual(manifest["finalization_changes"]["changes"], [])
                self.assertFalse(manifest["approval_records"]["partial_zeia_export_approved"]["approved"])
                report = Path(manifest["full_zeia_export_report"]).read_text(encoding="utf-8")
                self.assertIn("Required User Action", report)
                self.assertIn("full FluentControl ZEIA export", report)
                request_spec = (tmp_path / "generation" / "request.spec.yaml").read_text(encoding="utf-8")
                self.assertIn("full_zeia_export:", request_spec)
                self.assertIn("approved_partial_zeia: false", request_spec)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_generation_workflow_allows_partial_zeia_with_explicit_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_projects_dir = pc.PROJECTS_DIR
            old_active_file = pc.ACTIVE_CONTEXT_FILE
            pc.PROJECTS_DIR = tmp_path / "projects"
            pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
            try:
                archive = tmp_path / "partial.zeia"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr("Scripts/demo.xscr", XSCR)

                manifest = run_generation_workflow(
                    intent="Use the source assay setup to make a new transfer script",
                    out_dir=tmp_path / "generation",
                    project_archive=archive,
                    project_name="approved-partial-generation",
                    source_scripts=["DemoScript"],
                    simulate=False,
                    compile_xscr=False,
                    approve_partial_zeia=True,
                )

                self.assertEqual(manifest["workflow_status"], "scaffold_not_validated")
                self.assertTrue(manifest["partial_zeia_export_approved"])
                self.assertEqual(manifest["full_zeia_export"]["status"], "approved_partial_zeia")
                self.assertTrue(manifest["approval_records"]["partial_zeia_export_approved"]["approved"])
                self.assertEqual(
                    manifest["approval_records"]["partial_zeia_export_approved"]["source"],
                    "request_spec",
                )
                stages_by_id = {stage["id"]: stage for stage in manifest["stages"]}
                self.assertEqual(stages_by_id["verify_full_zeia_export"]["status"], "passed")
                self.assertTrue(Path(manifest["protocol_ir"]).exists())
                request_spec = (tmp_path / "generation" / "request.spec.yaml").read_text(encoding="utf-8")
                self.assertIn("approved_partial_zeia: true", request_spec)
                validation_diff = (tmp_path / "generation" / "validation_diff.md").read_text(encoding="utf-8")
                self.assertIn("full_zeia_export_required", validation_diff)
            finally:
                pc.PROJECTS_DIR = old_projects_dir
                pc.ACTIVE_CONTEXT_FILE = old_active_file

    def test_generation_workflow_pulls_indexed_pattern_windows_into_ir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_db = tmp_path / "tecan_project_index.sqlite"
            _write_pattern_index(index_db)

            out_dir = tmp_path / "generation"
            manifest = run_generation_workflow(
                intent="Reuse mined aspirate and dispense windows for a new transfer",
                out_dir=out_dir,
                index_db=index_db,
                pattern_ids=[1],
                pattern_queries=["dispense"],
                source_script_rank=1,
                simulate=False,
                compile_xscr=False,
                approve_partial_zeia=True,
                request_spec={
                    "schema_version": "tecan.request_spec.v1",
                    "request": {"intent": "Reuse mined aspirate and dispense windows for a new transfer"},
                    "worktable": {"name": "StubWorkspace", "auto_place": False},
                    "review": {"state": "approved"},
                },
            )

            ir = json.loads(Path(manifest["protocol_ir"]).read_text(encoding="utf-8"))
            stages_by_id = {stage["id"]: stage for stage in manifest["stages"]}
            self.assertEqual(stages_by_id["build_protocol_ir"]["status"], "passed")
            self.assertEqual(stages_by_id["validate_liquid_logic"]["status"], "needs_user")
            self.assertTrue(Path(manifest["ir_synthesis"]).exists())
            self.assertTrue(Path(manifest["liquid_state_validation"]).exists())
            windows = ir["source"]["selected_pattern_windows"]
            self.assertEqual([window["id"] for window in windows], [1, 2])
            self.assertEqual(windows[0]["steps"][0]["command_index"], 4)
            self.assertEqual(windows[0]["steps"][0]["fields"]["LabwareName"], "SourcePlate")
            self.assertEqual(windows[1]["source_script"], "BusyScript")
            self.assertEqual(windows[1]["retrieval"]["method"], "pattern_query")
            self.assertEqual(windows[1]["retrieval"]["source_script_rank"], 1)
            self.assertEqual(manifest["indexed_pattern_count"], 2)

            synthesis = json.loads(Path(manifest["ir_synthesis"]).read_text(encoding="utf-8"))
            self.assertTrue(synthesis["planned"])
            self.assertEqual(synthesis["planned_step_count"], 4)
            self.assertEqual(synthesis["synthesized_runtime_step_count"], 2)
            self.assertEqual(synthesis["setup_step_count"], 2)
            self.assertEqual(synthesis["warnings"], [])
            self.assertEqual(ir["source"]["ir_synthesis"], synthesis)

            self.assertEqual(
                [item["label"] for item in ir["labware"]],
                ["SourcePlate", "DestinationPlate"],
            )
            self.assertIn("Water Free Single", [item["name"] for item in ir["liquid_classes"]])
            self.assertEqual(
                [step["operation"] for step in ir["steps"]],
                ["add_labware", "add_labware", "aspirate", "dispense"],
            )
            self.assertEqual(ir["steps"][2]["target_labware"], "SourcePlate")
            self.assertEqual(ir["steps"][2]["volume_ul"], 20)
            self.assertEqual(ir["steps"][3]["target_labware"], "DestinationPlate")
            self.assertEqual(ir["steps"][3]["liquid_class"], "Water Free Single")
            self.assertTrue(Path(stages_by_id["generate_python_draft"]["outputs"]["gwl"]).exists())

            pattern_dependencies = [
                item
                for item in ir["dependencies"]
                if item.get("kind") == "source_pattern" and item.get("pattern_id")
            ]
            self.assertEqual([item["pattern_id"] for item in pattern_dependencies], [1, 2])

            ranked_manifest = run_generation_workflow(
                intent="Reuse the second ranked dispense source",
                out_dir=tmp_path / "generation_ranked",
                index_db=index_db,
                pattern_queries=["dispense"],
                source_script_rank=2,
                simulate=False,
                compile_xscr=False,
                approve_partial_zeia=True,
                request_spec={
                    "schema_version": "tecan.request_spec.v1",
                    "request": {"intent": "Reuse the second ranked dispense source"},
                    "worktable": {"name": "StubWorkspace", "auto_place": False},
                    "review": {"state": "approved"},
                },
            )
            ranked_ir = json.loads(Path(ranked_manifest["protocol_ir"]).read_text(encoding="utf-8"))
            ranked_windows = ranked_ir["source"]["selected_pattern_windows"]
            ranked_stages_by_id = {stage["id"]: stage for stage in ranked_manifest["stages"]}
            self.assertEqual(ranked_stages_by_id["build_protocol_ir"]["status"], "passed")
            self.assertEqual(ranked_stages_by_id["validate_liquid_logic"]["status"], "needs_user")
            self.assertEqual([window["id"] for window in ranked_windows], [4])
            self.assertEqual(ranked_windows[0]["source_script"], "AQuietScript")
            self.assertEqual(
                [step["operation"] for step in ranked_ir["steps"]],
                ["add_labware", "dispense"],
            )
            self.assertEqual(ranked_ir["steps"][1]["target_labware"], "AlternateDestination")

    def test_generation_workflow_reports_pattern_backed_rga_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_db = tmp_path / "tecan_project_index.sqlite"
            _write_pattern_index(index_db)

            manifest = run_generation_workflow(
                intent="Reuse a known-good RGA move window for a plate transfer",
                out_dir=tmp_path / "generation",
                index_db=index_db,
                pattern_ids=[5],
                simulate=False,
                compile_xscr=False,
                approve_partial_zeia=True,
                request_spec={
                    "schema_version": "tecan.request_spec.v1",
                    "request": {"intent": "Reuse a known-good RGA move window for a plate transfer"},
                    "worktable": {"name": "StubWorkspace", "auto_place": False},
                    "review": {"state": "approved"},
                },
            )

            ir = json.loads(Path(manifest["protocol_ir"]).read_text(encoding="utf-8"))
            policy = json.loads(Path(manifest["rga_move_policy_json"]).read_text(encoding="utf-8"))
            policy_report = Path(manifest["rga_move_policy"]).read_text(encoding="utf-8")

            self.assertEqual([step["operation"] for step in ir["steps"]], ["add_labware", "move_plate"])
            self.assertEqual(ir["steps"][1]["parameters"]["source_pattern_id"], 5)
            self.assertEqual(policy["pattern_backed"][0]["source_pattern"]["source_pattern_id"], 5)
            self.assertEqual(policy["manual_fallback"], [])
            self.assertIn("Pattern-backed moves: `1`", policy_report)
            self.assertIn("Pattern ID: `5`", policy_report)

    def test_generation_workflow_does_not_synthesize_low_level_gripper_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index_db = tmp_path / "tecan_project_index.sqlite"
            _write_pattern_index(index_db)

            manifest = run_generation_workflow(
                intent="Review a low-level CGA finger window without synthesizing new RGA motion",
                out_dir=tmp_path / "generation",
                request_spec={
                    "schema_version": "tecan.request_spec.v1",
                    "request": {"intent": "Review a low-level CGA finger window without synthesizing new RGA motion"},
                    "worktable": {"name": "StubWorkspace", "auto_place": False},
                    "review": {"state": "approved"},
                },
                index_db=index_db,
                pattern_ids=[6],
                simulate=False,
                compile_xscr=False,
                approve_partial_zeia=True,
            )

            ir = json.loads(Path(manifest["protocol_ir"]).read_text(encoding="utf-8"))
            synthesis = json.loads(Path(manifest["ir_synthesis"]).read_text(encoding="utf-8"))
            policy = json.loads(Path(manifest["rga_move_policy_json"]).read_text(encoding="utf-8"))
            stages_by_id = {stage["id"]: stage for stage in manifest["stages"]}

            self.assertEqual(stages_by_id["build_protocol_ir"]["status"], "needs_user")
            self.assertEqual(ir["steps"], [])
            self.assertEqual(policy["pattern_backed"], [])
            self.assertTrue(any(warning["kind"] == "unsupported_command" for warning in synthesis["warnings"]))
            self.assertEqual(ir["source"]["selected_pattern_windows"][0]["pattern_type"], "gripper")

    def test_generation_workflow_normalizes_aliases_before_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ir_path = tmp_path / "aliased.protocol-ir.json"
            ir_path.write_text(
                json.dumps(
                    {
                        "ir_version": "tecan.protocol_ir.v1",
                        "protocol": {"name": "aliased_labware"},
                        "worktable": {"name": "780_Empty", "auto_place": False},
                        "labware": [{"label": "EVA[001]", "catalog": "EVA[001]"}],
                        "liquid_classes": [],
                        "steps": [
                            {
                                "id": "step_001",
                                "index": 1,
                                "operation": "add_labware",
                                "target_labware": "EVA[001]",
                                "parameters": {
                                    "label": "EVA[001]",
                                    "catalog": "EVA[001]",
                                    "labware_type": "EVA[001]",
                                    "location": "Site",
                                    "position": 1,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            manifest = run_generation_workflow(
                intent="Normalize project aliases before rendering",
                out_dir=tmp_path / "generation",
                ir_source=ir_path,
                simulate=False,
                compile_xscr=False,
                approve_partial_zeia=True,
            )

            ir = json.loads(Path(manifest["protocol_ir"]).read_text(encoding="utf-8"))
            draft = Path(manifest["python_draft"]).read_text(encoding="utf-8")
            self.assertEqual(ir["labware"][0]["label"], "EVA")
            self.assertEqual(ir["labware"][0]["catalog"], "EVA")
            self.assertEqual(ir["steps"][0]["target_labware"], "EVA")
            self.assertEqual(ir["steps"][0]["parameters"]["label"], "EVA")
            self.assertEqual(ir["steps"][0]["parameters"]["catalog"], "EVA")
            self.assertEqual(ir["steps"][0]["parameters"]["labware_type"], "EVA")
            self.assertIn("Plate96('EVA', catalog='EVA')", draft)
            self.assertNotIn("EVA[001]", draft)

    def test_generation_workflow_resimulates_after_repairs_and_compiles_last_passing_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "generation"
            capture: dict[str, object] = {}
            request_spec = {
                "schema_version": "tecan.request_spec.v1",
                "request": {
                    "intent": "Repair loop demo",
                    "protocol_name": "repair_loop_demo",
                    "verbatim_prompt": "Repair loop demo",
                },
                "worktable": {"name": "StubWorkspace", "auto_place": False},
                "source": {"contexts": []},
                "generation": {"simulate": True, "compile_xscr": True},
                "review": {"state": "approved"},
            }

            def fake_run_fluentcoder(command, catalog_db=None, timeout=600):
                argv = [str(part) for part in command]
                action = argv[0]
                if action == "simulate":
                    protocol_path = Path(argv[1])
                    passed = "repaired" in protocol_path.name
                    payload = {
                        "status": "passed" if passed else "failed",
                        "total_executed_steps": 1,
                        "fully_simulated_steps": 1,
                        "validation_only_steps": 0,
                        "opaque_noop_steps": 0,
                        "raw_xml_generic_steps": 0,
                        "modeled_coverage": 1.0,
                        "warnings": [],
                        "failure": None if passed else {"message": "original candidate failed"},
                        "unsupported_command_ids": {},
                        "effect_counts": {},
                        "final_labware": [],
                        "state_summary": {},
                    }
                    return CommandResult(tuple(argv), tmp_path, 0 if passed else 1, json.dumps(payload), "")
                if action == "compile":
                    protocol_path = Path(argv[1])
                    output_path = Path(argv[argv.index("-o") + 1])
                    capture["compile_input"] = protocol_path
                    output_path.write_text("<Root><ObjectName>RepairLoopDemo</ObjectName></Root>", encoding="utf-8")
                    return CommandResult(tuple(argv), tmp_path, 0, "compiled\n", "")
                raise AssertionError(f"unexpected command: {argv}")

            def fake_build_repair_plan(draft_path, *, context=None, simulation_json_path=None):
                return RepairPlan(
                    draft_path=Path(draft_path),
                    context_name=getattr(context, "name", None),
                    simulation_json_path=simulation_json_path,
                    actions=[],
                )

            def fake_apply_repair_plan(plan, output_path, apply_modeling=False):
                output_path.write_text(plan.draft_path.read_text(encoding="utf-8"), encoding="utf-8")
                return []

            def fake_finalize_compiled_xscr(*args, **kwargs):
                return SimpleNamespace(ok=True, as_dict=lambda: {"ok": True, "summary": "finalized"})

            def fake_validate_ready_to_import(**kwargs):
                capture["validation_context"] = kwargs["validation_context"]
                return {
                    "ready": False,
                    "gate_count": 1,
                    "passed_count": 0,
                    "failed_count": 1,
                    "required_gate_count": 1,
                    "required_passed_count": 0,
                    "required_failed_count": 1,
                    "optional_gate_count": 0,
                    "optional_passed_count": 0,
                    "optional_failed_count": 0,
                    "needs_review_count": 0,
                    "trivial_pass_count": 0,
                    "trivial_pass_gates": [],
                    "offline_validation": {
                        "status": "validated_not_ready",
                        "summary": "stub",
                        "required_gate_count": 1,
                        "passed_count": 0,
                        "failed_count": 1,
                        "failing_gates": ["simulation_passes"],
                        "host_instrument_config_blocking": False,
                    },
                    "review_state": {
                        "status": "validated_not_ready",
                        "summary": "stub",
                        "needs_review_count": 0,
                        "gates": [],
                    },
                    "fluentcontrol_load_diagnostic": {
                        "status": "not_run",
                        "summary": "stub",
                        "requested": False,
                        "gate": "fluent_context_check",
                        "gate_present": False,
                    },
                    "gates": [
                        {
                            "gate": "simulation_passes",
                            "id": "simulation_passes",
                            "name": "Simulation passes",
                            "status": "failed",
                            "summary": "stub",
                            "details": {},
                        }
                    ],
                }

            def fake_build_request_validation_diff(**kwargs):
                capture["generated_files"] = dict(kwargs["generated_files"])
                return {
                    "kind": "request_validation_diff",
                    "status": "needs_user",
                    "request_spec": {
                        "path": str(kwargs["request_spec_path"]),
                        "intent": "Repair loop demo",
                        "intent_summary": "Repair loop demo",
                        "verbatim_prompt": "Repair loop demo",
                        "protocol_name": "repair_loop_demo",
                        "status": "reviewed",
                        "review_state": "approved",
                    },
                    "protocol_ir": {
                        "path": str(kwargs["protocol_ir_path"]),
                        "id": "repair_loop_demo",
                        "name": "Repair loop demo",
                        "step_count": 0,
                        "labware_count": 0,
                        "liquid_class_count": 0,
                    },
                    "generated_files": dict(kwargs["generated_files"]),
                    "checks": [],
                }

            def fake_build_traceability_map(**kwargs):
                return {"kind": "traceability_map", "schema_version": "test", "entries": []}

            with mock.patch("fluent_pipeline.generation_workflow.run_fluentcoder", side_effect=fake_run_fluentcoder), mock.patch(
                "fluent_pipeline.generation_workflow.build_repair_plan", side_effect=fake_build_repair_plan
            ), mock.patch(
                "fluent_pipeline.generation_workflow.apply_repair_plan", side_effect=fake_apply_repair_plan
            ), mock.patch(
                "fluent_pipeline.generation_workflow.finalize_compiled_xscr", side_effect=fake_finalize_compiled_xscr
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_compiled_xscr_finalization_markdown",
                return_value="# Finalization\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.validate_ready_to_import", side_effect=fake_validate_ready_to_import
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_validation_markdown",
                return_value="# Validation\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.build_request_validation_diff",
                side_effect=fake_build_request_validation_diff,
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_request_validation_diff_markdown",
                return_value="# Request Validation Diff\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.build_traceability_map", side_effect=fake_build_traceability_map
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_traceability_markdown",
                return_value="# Traceability\n",
            ):
                manifest = run_generation_workflow(
                    intent="Repair loop demo",
                    out_dir=out_dir,
                    request_spec=request_spec,
                    simulate=True,
                    compile_xscr=True,
                    approve_partial_zeia=True,
                )

            history_path = out_dir / "repair_history.json"
            self.assertTrue(history_path.exists())
            history = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertIn("repair_iterations", history)
            self.assertEqual([item["candidate"] for item in history["repair_iterations"]], [0, 1])
            self.assertEqual(history["repair_iterations"][0]["simulation_status"], "failed")
            self.assertEqual(history["repair_iterations"][1]["simulation_status"], "passed")
            self.assertEqual(history["repair_iterations"][0]["findings"], [])
            self.assertEqual(history["repair_iterations"][1]["findings"], [])
            self.assertEqual(history["repair_iterations"][0]["repairs_applied"], [])
            self.assertEqual(history["repair_iterations"][1]["repairs_applied"], [])

            self.assertEqual(capture["compile_input"].name, "repair_loop_demo.repaired.py")
            self.assertEqual(capture["validation_context"]["simulation_passed"], True)
            self.assertEqual(capture["validation_context"]["repair_plan"]["actions"], [])
            self.assertEqual(capture["validation_context"]["compiled_candidate"], 1)

            self.assertEqual(manifest["compiled_candidate"], 1)
            self.assertTrue(Path(manifest["repair_history"]).exists())
            self.assertTrue(Path(manifest["simulation_report"]).exists())
            self.assertEqual(Path(manifest["simulation_report"]).name, "repair_loop_demo.repair-1.simulation.md")
            self.assertEqual(Path(manifest["repaired_draft"]).name, "repair_loop_demo.repaired.py")
            self.assertEqual(capture["generated_files"]["repair_history"], "repair_history.json")

    def test_generation_workflow_stops_after_passing_candidate_with_no_applicable_repairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "generation"
            capture: dict[str, object] = {"simulate_inputs": []}
            request_spec = {
                "schema_version": "tecan.request_spec.v1",
                "request": {
                    "intent": "Repair loop demo",
                    "protocol_name": "repair_loop_demo",
                    "verbatim_prompt": "Repair loop demo",
                },
                "worktable": {"name": "StubWorkspace", "auto_place": False},
                "source": {"contexts": []},
                "generation": {"simulate": True, "compile_xscr": True},
                "review": {"state": "approved"},
            }

            def fake_run_fluentcoder(command, catalog_db=None, timeout=600):
                argv = [str(part) for part in command]
                action = argv[0]
                if action == "simulate":
                    protocol_path = Path(argv[1])
                    capture["simulate_inputs"].append(protocol_path.name)
                    payload = {
                        "status": "passed",
                        "total_executed_steps": 1,
                        "fully_simulated_steps": 1,
                        "validation_only_steps": 0,
                        "opaque_noop_steps": 0,
                        "raw_xml_generic_steps": 0,
                        "modeled_coverage": 1.0,
                        "warnings": [],
                        "failure": None,
                        "unsupported_command_ids": {},
                        "effect_counts": {},
                        "final_labware": [],
                        "state_summary": {},
                    }
                    return CommandResult(tuple(argv), tmp_path, 0, json.dumps(payload), "")
                if action == "compile":
                    protocol_path = Path(argv[1])
                    output_path = Path(argv[argv.index("-o") + 1])
                    capture["compile_input"] = protocol_path
                    output_path.write_text("<Root><ObjectName>RepairLoopDemo</ObjectName></Root>", encoding="utf-8")
                    return CommandResult(tuple(argv), tmp_path, 0, "compiled\n", "")
                raise AssertionError(f"unexpected command: {argv}")

            def fake_build_repair_plan(draft_path, *, context=None, simulation_json_path=None):
                return RepairPlan(
                    draft_path=Path(draft_path),
                    context_name=getattr(context, "name", None),
                    simulation_json_path=simulation_json_path,
                    actions=[],
                )

            def fake_apply_repair_plan(plan, output_path, apply_modeling=False):
                raise AssertionError("repair application should not run when no applicable repairs remain")

            def fake_finalize_compiled_xscr(*args, **kwargs):
                return SimpleNamespace(ok=True, as_dict=lambda: {"ok": True, "summary": "finalized"})

            def fake_validate_ready_to_import(**kwargs):
                capture["validation_context"] = kwargs["validation_context"]
                return {
                    "ready": False,
                    "gate_count": 1,
                    "passed_count": 0,
                    "failed_count": 1,
                    "required_gate_count": 1,
                    "required_passed_count": 0,
                    "required_failed_count": 1,
                    "optional_gate_count": 0,
                    "optional_passed_count": 0,
                    "optional_failed_count": 0,
                    "needs_review_count": 0,
                    "trivial_pass_count": 0,
                    "trivial_pass_gates": [],
                    "offline_validation": {
                        "status": "validated_not_ready",
                        "summary": "stub",
                        "required_gate_count": 1,
                        "passed_count": 0,
                        "failed_count": 1,
                        "failing_gates": ["simulation_passes"],
                        "host_instrument_config_blocking": False,
                    },
                    "review_state": {
                        "status": "validated_not_ready",
                        "summary": "stub",
                        "needs_review_count": 0,
                        "gates": [],
                    },
                    "fluentcontrol_load_diagnostic": {
                        "status": "not_run",
                        "summary": "stub",
                        "requested": False,
                        "gate": "fluent_context_check",
                        "gate_present": False,
                    },
                    "gates": [
                        {
                            "gate": "simulation_passes",
                            "id": "simulation_passes",
                            "name": "Simulation passes",
                            "status": "failed",
                            "summary": "stub",
                            "details": {},
                        }
                    ],
                }

            def fake_build_request_validation_diff(**kwargs):
                capture["generated_files"] = dict(kwargs["generated_files"])
                return {
                    "kind": "request_validation_diff",
                    "status": "needs_user",
                    "request_spec": {
                        "path": str(kwargs["request_spec_path"]),
                        "intent": "Repair loop demo",
                        "intent_summary": "Repair loop demo",
                        "verbatim_prompt": "Repair loop demo",
                        "protocol_name": "repair_loop_demo",
                        "status": "reviewed",
                        "review_state": "approved",
                    },
                    "protocol_ir": {
                        "path": str(kwargs["protocol_ir_path"]),
                        "id": "repair_loop_demo",
                        "name": "Repair loop demo",
                        "step_count": 0,
                        "labware_count": 0,
                        "liquid_class_count": 0,
                    },
                    "generated_files": dict(kwargs["generated_files"]),
                    "checks": [],
                }

            def fake_build_traceability_map(**kwargs):
                return {"kind": "traceability_map", "schema_version": "test", "entries": []}

            with mock.patch("fluent_pipeline.generation_workflow.run_fluentcoder", side_effect=fake_run_fluentcoder), mock.patch(
                "fluent_pipeline.generation_workflow.build_repair_plan", side_effect=fake_build_repair_plan
            ), mock.patch(
                "fluent_pipeline.generation_workflow.apply_repair_plan", side_effect=fake_apply_repair_plan
            ), mock.patch(
                "fluent_pipeline.generation_workflow.finalize_compiled_xscr", side_effect=fake_finalize_compiled_xscr
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_compiled_xscr_finalization_markdown",
                return_value="# Finalization\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.validate_ready_to_import", side_effect=fake_validate_ready_to_import
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_validation_markdown",
                return_value="# Validation\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.build_request_validation_diff",
                side_effect=fake_build_request_validation_diff,
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_request_validation_diff_markdown",
                return_value="# Request Validation Diff\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.build_traceability_map", side_effect=fake_build_traceability_map
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_traceability_markdown",
                return_value="# Traceability\n",
            ):
                manifest = run_generation_workflow(
                    intent="Repair loop demo",
                    out_dir=out_dir,
                    request_spec=request_spec,
                    simulate=True,
                    compile_xscr=True,
                    max_repair_iterations=3,
                    approve_partial_zeia=True,
                )

            history_path = out_dir / "repair_history.json"
            self.assertTrue(history_path.exists())
            history = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(capture["simulate_inputs"], ["repair_loop_demo.py"])
            self.assertEqual(history["termination_reason"], "simulation_passed_no_repairs")
            self.assertEqual(history["selected_candidate"], 0)
            self.assertEqual([item["candidate"] for item in history["repair_iterations"]], [0])
            self.assertEqual(history["repair_iterations"][0]["simulation_status"], "passed")
            self.assertEqual(history["repair_iterations"][0]["repairs_applied"], [])

            self.assertEqual(capture["compile_input"].name, "repair_loop_demo.py")
            self.assertEqual(capture["validation_context"]["repair_history"]["termination_reason"], "simulation_passed_no_repairs")
            self.assertEqual(capture["validation_context"]["repair_history"]["selected_candidate"], 0)
            self.assertEqual(capture["validation_context"]["compiled_candidate"], 0)

            self.assertEqual(manifest["compiled_candidate"], 0)
            self.assertTrue(Path(manifest["repair_history"]).exists())
            self.assertTrue(Path(manifest["simulation_report"]).exists())
            self.assertEqual(Path(manifest["simulation_report"]).name, "repair_loop_demo.simulation.md")
            self.assertEqual(Path(manifest["repaired_draft"]).name, "repair_loop_demo.py")
            self.assertEqual(manifest["repair_iterations"]["termination_reason"], "simulation_passed_no_repairs")
            self.assertEqual(manifest["repair_iterations"]["selected_candidate"], 0)
            self.assertEqual(capture["generated_files"]["repair_history"], "repair_history.json")

    def test_generation_workflow_continues_after_passing_candidate_for_approved_modeling_repairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "generation"
            capture: dict[str, object] = {"simulate_inputs": [], "applied_outputs": []}
            request_spec = {
                "schema_version": "tecan.request_spec.v1",
                "request": {
                    "intent": "Repair loop demo",
                    "protocol_name": "repair_loop_demo",
                    "verbatim_prompt": "Repair loop demo",
                },
                "worktable": {"name": "StubWorkspace", "auto_place": False},
                "source": {"contexts": []},
                "generation": {"simulate": True, "compile_xscr": True, "apply_modeling": True},
                "review": {"state": "approved"},
            }

            def fake_run_fluentcoder(command, catalog_db=None, timeout=600):
                argv = [str(part) for part in command]
                action = argv[0]
                if action == "simulate":
                    protocol_path = Path(argv[1])
                    capture["simulate_inputs"].append(protocol_path.name)
                    payload = {
                        "status": "passed",
                        "total_executed_steps": 1,
                        "fully_simulated_steps": 1,
                        "validation_only_steps": 0,
                        "opaque_noop_steps": 0,
                        "raw_xml_generic_steps": 0,
                        "modeled_coverage": 1.0,
                        "warnings": [],
                        "failure": None,
                        "unsupported_command_ids": {},
                        "effect_counts": {},
                        "final_labware": [],
                        "state_summary": {},
                    }
                    return CommandResult(tuple(argv), tmp_path, 0, json.dumps(payload), "")
                if action == "compile":
                    protocol_path = Path(argv[1])
                    output_path = Path(argv[argv.index("-o") + 1])
                    capture["compile_input"] = protocol_path
                    output_path.write_text("<Root><ObjectName>RepairLoopDemo</ObjectName></Root>", encoding="utf-8")
                    return CommandResult(tuple(argv), tmp_path, 0, "compiled\n", "")
                raise AssertionError(f"unexpected command: {argv}")

            def fake_build_repair_plan(draft_path, *, context=None, simulation_json_path=None):
                if Path(draft_path).name == "repair_loop_demo.py":
                    return RepairPlan(
                        draft_path=Path(draft_path),
                        context_name=getattr(context, "name", None),
                        simulation_json_path=simulation_json_path,
                        actions=[
                            RepairAction(
                                kind="model_raw_xml_step",
                                status="suggested",
                                summary="Model opaque raw XML as fluentcoder DSL.",
                                line=12,
                                old="wt.raw_xml_step('Mca384Aspirate', '<Object />')",
                                new="head.aspirate(sourceplate, 20, liquid_class='Water Free Single')",
                                command_id="Mca384Aspirate",
                                details={"normalized": True},
                            )
                        ],
                    )
                return RepairPlan(
                    draft_path=Path(draft_path),
                    context_name=getattr(context, "name", None),
                    simulation_json_path=simulation_json_path,
                    actions=[],
                )

            def fake_apply_repair_plan(plan, output_path, apply_modeling=False):
                capture["apply_modeling"] = apply_modeling
                capture["applied_outputs"].append(Path(output_path).name)
                output_path.write_text(plan.draft_path.read_text(encoding="utf-8"), encoding="utf-8")
                return plan.actions

            def fake_finalize_compiled_xscr(*args, **kwargs):
                return SimpleNamespace(ok=True, as_dict=lambda: {"ok": True, "summary": "finalized"})

            def fake_validate_ready_to_import(**kwargs):
                capture["validation_context"] = kwargs["validation_context"]
                return {
                    "ready": False,
                    "gate_count": 1,
                    "passed_count": 0,
                    "failed_count": 1,
                    "required_gate_count": 1,
                    "required_passed_count": 0,
                    "required_failed_count": 1,
                    "optional_gate_count": 0,
                    "optional_passed_count": 0,
                    "optional_failed_count": 0,
                    "needs_review_count": 0,
                    "trivial_pass_count": 0,
                    "trivial_pass_gates": [],
                    "offline_validation": {
                        "status": "validated_not_ready",
                        "summary": "stub",
                        "required_gate_count": 1,
                        "passed_count": 0,
                        "failed_count": 1,
                        "failing_gates": ["simulation_passes"],
                        "host_instrument_config_blocking": False,
                    },
                    "review_state": {
                        "status": "validated_not_ready",
                        "summary": "stub",
                        "needs_review_count": 0,
                        "gates": [],
                    },
                    "fluentcontrol_load_diagnostic": {
                        "status": "not_run",
                        "summary": "stub",
                        "requested": False,
                        "gate": "fluent_context_check",
                        "gate_present": False,
                    },
                    "gates": [
                        {
                            "gate": "simulation_passes",
                            "id": "simulation_passes",
                            "name": "Simulation passes",
                            "status": "failed",
                            "summary": "stub",
                            "details": {},
                        }
                    ],
                }

            def fake_build_request_validation_diff(**kwargs):
                capture["generated_files"] = dict(kwargs["generated_files"])
                return {
                    "kind": "request_validation_diff",
                    "status": "needs_user",
                    "request_spec": {
                        "path": str(kwargs["request_spec_path"]),
                        "intent": "Repair loop demo",
                        "intent_summary": "Repair loop demo",
                        "verbatim_prompt": "Repair loop demo",
                        "protocol_name": "repair_loop_demo",
                        "status": "reviewed",
                        "review_state": "approved",
                    },
                    "protocol_ir": {
                        "path": str(kwargs["protocol_ir_path"]),
                        "id": "repair_loop_demo",
                        "name": "Repair loop demo",
                        "step_count": 0,
                        "labware_count": 0,
                        "liquid_class_count": 0,
                    },
                    "generated_files": dict(kwargs["generated_files"]),
                    "checks": [],
                }

            def fake_build_traceability_map(**kwargs):
                return {"kind": "traceability_map", "schema_version": "test", "entries": []}

            with mock.patch("fluent_pipeline.generation_workflow.run_fluentcoder", side_effect=fake_run_fluentcoder), mock.patch(
                "fluent_pipeline.generation_workflow.build_repair_plan", side_effect=fake_build_repair_plan
            ), mock.patch(
                "fluent_pipeline.generation_workflow.apply_repair_plan", side_effect=fake_apply_repair_plan
            ), mock.patch(
                "fluent_pipeline.generation_workflow.finalize_compiled_xscr", side_effect=fake_finalize_compiled_xscr
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_compiled_xscr_finalization_markdown",
                return_value="# Finalization\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.validate_ready_to_import", side_effect=fake_validate_ready_to_import
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_validation_markdown",
                return_value="# Validation\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.build_request_validation_diff",
                side_effect=fake_build_request_validation_diff,
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_request_validation_diff_markdown",
                return_value="# Request Validation Diff\n",
            ), mock.patch(
                "fluent_pipeline.generation_workflow.build_traceability_map", side_effect=fake_build_traceability_map
            ), mock.patch(
                "fluent_pipeline.generation_workflow.render_traceability_markdown",
                return_value="# Traceability\n",
            ):
                manifest = run_generation_workflow(
                    intent="Repair loop demo",
                    out_dir=out_dir,
                    request_spec=request_spec,
                    simulate=True,
                    compile_xscr=True,
                    apply_modeling=True,
                    max_repair_iterations=1,
                    approve_partial_zeia=True,
                )

            history_path = out_dir / "repair_history.json"
            self.assertTrue(history_path.exists())
            history = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(capture["simulate_inputs"], ["repair_loop_demo.py", "repair_loop_demo.repaired.py"])
            self.assertTrue(capture["apply_modeling"])
            self.assertEqual(capture["applied_outputs"], ["repair_loop_demo.repaired.py"])
            expected_modeling_action = RepairAction(
                kind="model_raw_xml_step",
                status="suggested",
                summary="Model opaque raw XML as fluentcoder DSL.",
                line=12,
                old="wt.raw_xml_step('Mca384Aspirate', '<Object />')",
                new="head.aspirate(sourceplate, 20, liquid_class='Water Free Single')",
                command_id="Mca384Aspirate",
                details={"normalized": True},
            ).to_dict()
            self.assertEqual(history["termination_reason"], "simulation_passed_no_repairs")
            self.assertEqual(history["selected_candidate"], 1)
            self.assertEqual([item["candidate"] for item in history["repair_iterations"]], [0, 1])
            self.assertEqual(history["repair_iterations"][0]["simulation_status"], "passed")
            self.assertEqual(history["repair_iterations"][0]["repairs_applied"], [expected_modeling_action])
            self.assertEqual(history["repair_iterations"][1]["simulation_status"], "passed")
            self.assertEqual(history["repair_iterations"][1]["repairs_applied"], [])

            self.assertEqual(capture["compile_input"].name, "repair_loop_demo.repaired.py")
            self.assertEqual(capture["validation_context"]["repair_history"]["termination_reason"], "simulation_passed_no_repairs")
            self.assertEqual(capture["validation_context"]["repair_history"]["selected_candidate"], 1)
            self.assertEqual(capture["validation_context"]["compiled_candidate"], 1)

            self.assertEqual(manifest["compiled_candidate"], 1)
            self.assertTrue(Path(manifest["repair_history"]).exists())
            self.assertTrue(Path(manifest["simulation_report"]).exists())
            self.assertEqual(Path(manifest["simulation_report"]).name, "repair_loop_demo.repair-1.simulation.md")
            self.assertEqual(Path(manifest["repaired_draft"]).name, "repair_loop_demo.repaired.py")
            self.assertEqual(manifest["repair_iterations"]["termination_reason"], "simulation_passed_no_repairs")
            self.assertEqual(manifest["repair_iterations"]["selected_candidate"], 1)
            self.assertEqual(capture["generated_files"]["repair_history"], "repair_history.json")


def _write_liquid_class(zf: zipfile.ZipFile, name: str = "Water Free Single") -> None:
    zf.writestr(
        f"LiquidClasses/{name}.xlqc",
        f"""<?xml version="1.0"?>
<LiquidClass>
  <ObjectName>{name}</ObjectName>
  <Guid>{name.lower().replace(' ', '-')}-guid</Guid>
</LiquidClass>
""",
    )


def _write_pattern_index(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE zeia_files (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL
            );
            CREATE TABLE script_patterns (
                id INTEGER PRIMARY KEY,
                zeia_file_id INTEGER NOT NULL,
                script_id INTEGER NOT NULL,
                pattern_type TEXT NOT NULL,
                name TEXT NOT NULL,
                source_script TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                start_command_index INTEGER NOT NULL,
                end_command_index INTEGER NOT NULL,
                step_count INTEGER NOT NULL,
                command_signature TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                specifications_json TEXT NOT NULL DEFAULT '{}',
                safety_notes_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE script_pattern_steps (
                id INTEGER PRIMARY KEY,
                pattern_id INTEGER NOT NULL,
                step_number INTEGER NOT NULL,
                command_index INTEGER NOT NULL,
                command_name TEXT NOT NULL DEFAULT '',
                command_family TEXT NOT NULL DEFAULT '',
                line TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                fields_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute("INSERT INTO zeia_files(id, path) VALUES (?, ?)", (1, "/fixtures/source.zeia"))
        _insert_pattern(
            conn,
            pattern_id=1,
            source_script="OldScript",
            pattern_type="aspirate",
            start=4,
            command_name="MCA96Aspirate",
            fields={
                "LabwareName": "SourcePlate",
                "Volume": "20",
                "LiquidClassName": "Water Free Single",
            },
        )
        _insert_pattern(
            conn,
            pattern_id=2,
            source_script="BusyScript",
            pattern_type="dispense",
            start=8,
            command_name="MCA96Dispense",
            fields={
                "LabwareName": "DestinationPlate",
                "Volume": "20",
                "LiquidClassName": "Water Free Single",
            },
        )
        _insert_pattern(
            conn,
            pattern_id=3,
            source_script="BusyScript",
            pattern_type="dispense",
            start=18,
            command_name="MCA96Dispense",
            fields={
                "LabwareName": "DestinationPlate",
                "Volume": "10",
                "LiquidClassName": "Water Free Single",
            },
        )
        _insert_pattern(
            conn,
            pattern_id=4,
            source_script="AQuietScript",
            pattern_type="dispense",
            start=3,
            command_name="MCA96Dispense",
            fields={
                "LabwareName": "AlternateDestination",
                "Volume": "20",
                "LiquidClassName": "Water Free Single",
            },
        )
        _insert_pattern(
            conn,
            pattern_id=5,
            source_script="KnownGoodRgaMove",
            pattern_type="move_plate",
            start=17,
            command_name="MovePlateScriptCommandDataV1",
            fields={
                "LabwareName": "SourcePlate",
                "DestinationLocation": "FixedNest",
                "DestinationSite": "2",
            },
            command_family="RGA/CGA",
        )
        _insert_pattern(
            conn,
            pattern_id=6,
            source_script="KnownGoodFingerSequence",
            pattern_type="gripper",
            start=21,
            command_name="CgaGetFingersScriptCommand",
            fields={
                "LabwareName": "AdapterA200",
                "DeviceAlias": "Instrument=1/Device=CGA:1",
                "AvailableID": "USB:TECAN,FLUENT,1/CGA:1",
            },
            command_family="RGA/CGA",
        )
        conn.commit()
    finally:
        conn.close()


def _insert_pattern(
    conn: sqlite3.Connection,
    *,
    pattern_id: int,
    source_script: str,
    pattern_type: str,
    start: int,
    command_name: str,
    fields: dict[str, str],
    command_family: str = "LiquidHandling",
) -> None:
    conn.execute(
        """
        INSERT INTO script_patterns(
            id, zeia_file_id, script_id, pattern_type, name, source_script,
            source_path, start_command_index, end_command_index, step_count,
            command_signature, confidence, specifications_json,
            safety_notes_json, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pattern_id,
            1,
            pattern_id,
            pattern_type,
            f"{pattern_type} from {source_script}",
            source_script,
            f"Scripts/{source_script}.xscr",
            start,
            start,
            1,
            command_name,
            1.0,
            json.dumps({"labware": [fields["LabwareName"]]}),
            json.dumps(["Verify deck positions before reuse."]),
            json.dumps({"fixture": True}),
        ),
    )
    conn.execute(
        """
        INSERT INTO script_pattern_steps(
            pattern_id, step_number, command_index, command_name,
            command_family, line, summary, fields_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pattern_id,
            1,
            start,
            command_name,
            command_family,
            f"<Object Type=\"{command_name}\" />",
            _pattern_step_summary(pattern_type, fields),
            json.dumps(fields),
        ),
    )


def _pattern_step_summary(pattern_type: str, fields: dict[str, str]) -> str:
    if "Volume" in fields:
        return f"{pattern_type.title()} {fields['Volume']} at {fields['LabwareName']}"
    destination = fields.get("DestinationLocation") or fields.get("ToLocation") or ""
    suffix = f" to {destination}" if destination else ""
    return f"{pattern_type.title()} {fields['LabwareName']}{suffix}"


if __name__ == "__main__":
    unittest.main()
