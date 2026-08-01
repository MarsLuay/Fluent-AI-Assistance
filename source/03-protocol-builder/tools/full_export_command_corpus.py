"""Build and verify a one-of-each-command corpus from an imported full export."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape as xml_escape

from fluent_pipeline import xml_compat as ET
from fluent_pipeline.api_v2.commands import command_from_xscr_object, command_to_xml
from fluent_pipeline.api_v2.xml_compare import compare_command_xml
from fluent_pipeline.command_registry import (
    registry_command_operation,
    registry_command_support_status,
)
from fluent_pipeline.config import READY_TO_IMPORT_DIR
from fluent_pipeline.delivery_bundle import (
    DELIVERY_MANIFEST_SCHEMA_VERSION,
    READY_BUNDLE_SCHEMA_VERSION,
    validate_v2_delivery_bundle,
)
from fluent_pipeline.external_file_dependencies import (
    audit_external_file_dependencies,
    stage_found_external_files,
    write_external_file_dependency_artifacts,
)
from fluent_pipeline.exports import (
    _copy_v2_setup_script,
    _publish_protocol_folder_replacement,
    _write_generated_project_archive,
    plan_ready_to_import_publish,
)
from fluent_pipeline.runner import PipelineError

ROOT = Path(__file__).resolve().parents[1]
READY_BUNDLE_NAME = "full_export_command_corpus"
DEFAULT_TARGET_SCRIPT_FOLDER = "Demo scripts"
SAMPLE_EXECUTION_MODES = ("inspection-only", "source-behavior")
DEFAULT_SAMPLE_EXECUTION = "inspection-only"


@dataclass(frozen=True)
class CommandSample:
    command_id: str
    object_type: str
    xml: str
    source_path: str
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "object_type": self.object_type,
            "source_path": self.source_path,
            "count": self.count,
            "xml_bytes": len(self.xml.encode("utf-8")),
        }


@dataclass(frozen=True)
class VariableDeclaration:
    name: str
    xml: str


@dataclass(frozen=True)
class WorkspaceData:
    source_path: str
    base_workspace_name: str
    xml: str


def default_project_root() -> Path:
    """Prefer an existing imported context under ``ready-to-import/*/temp_files``; else placeholder.

    Callers should pass ``--project`` to an imported full ZEIA context. No lab-
    specific folder names are hard-coded here.
    """
    ready_root = READY_TO_IMPORT_DIR
    if ready_root.is_dir():
        candidates = sorted(
            path
            for path in ready_root.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and not path.name.startswith("_")
            and (path / "temp_files").is_dir()
        )
        if candidates:
            return candidates[0] / "temp_files"
        temp_candidates = sorted(
            path
            for path in ready_root.glob("*/temp_files")
            if path.is_dir()
        )
        if temp_candidates:
            return temp_candidates[0]
    return ready_root / "imported-full-export" / "temp_files"


def default_ready_to_import_root() -> Path:
    return READY_TO_IMPORT_DIR


def collect_command_samples(project_root: Path) -> tuple[list[CommandSample], list[dict[str, str]]]:
    """Return one source XML sample per command ID, plus parse errors."""
    extracted = project_root / "extracted"
    first_by_id: OrderedDict[str, tuple[ET.Element, Path]] = OrderedDict()
    counts: Counter[str] = Counter()
    parse_errors: list[dict[str, str]] = []

    for path in sorted(extracted.rglob("*.xscr")):
        try:
            root = ET.parse(path, max_bytes=-1).getroot()
        except Exception as exc:
            parse_errors.append({"path": str(path), "error": str(exc)})
            continue
        for group_object in _script_group_objects(root):
            group_data = _direct_child(group_object, "ScriptGroupDataV1")
            statements = _first_descendant(group_data, "Statements")
            for command_object in _statement_objects(statements):
                command_id = _command_id(command_object)
                if command_id == "ScriptGroupDataV1":
                    continue
                counts[command_id] += 1
                first_by_id.setdefault(command_id, (command_object, path))

    samples = [
        CommandSample(
            command_id=command_id,
            object_type=str(element.attrib.get("Type") or ""),
            xml=ET.tostring(element, encoding="unicode"),
            source_path=str(path),
            count=counts[command_id],
        )
        for command_id, (element, path) in first_by_id.items()
    ]
    samples.sort(key=lambda item: item.command_id)
    return samples, parse_errors


def verify_samples(samples: Iterable[CommandSample]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for sample in samples:
        row: dict[str, Any] = {
            **sample.as_dict(),
            "registry_support_status": registry_command_support_status(sample.command_id) or "",
            "registry_operation": registry_command_operation(sample.command_id) or "",
            "ok": False,
            "api_v2_type": "",
            "diff_hint": "",
            "error": "",
        }
        try:
            element = ET.fromstring(sample.xml, max_bytes=-1)
            command = command_from_xscr_object(element, command_id=sample.command_id)
            if command is None:
                row["error"] = "command_from_xscr_object returned None"
            else:
                row["api_v2_type"] = type(command).__name__
                rendered = command_to_xml(command, validate=False)
                compare = compare_command_xml(sample.xml, rendered)
                row["ok"] = compare.equal
                row["diff_hint"] = compare.diff_hint
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        if not row["registry_support_status"] and not row["registry_operation"]:
            row["ok"] = False
            row["error"] = (row["error"] + "; " if row["error"] else "") + "missing registry support"
        results.append(row)
    return results


def collect_variable_declarations(samples: Iterable[CommandSample]) -> list[VariableDeclaration]:
    """Collect source variable declarations needed by sampled command expressions."""
    declarations: OrderedDict[str, VariableDeclaration] = OrderedDict()
    source_paths = OrderedDict.fromkeys(sample.source_path for sample in samples if sample.source_path)
    for source_path in source_paths:
        path = Path(source_path)
        try:
            root = ET.parse(path, max_bytes=-1).getroot()
        except Exception:
            continue
        for element in root.iter():
            if not _is_variable_declaration_element(element):
                continue
            name = _first_descendant_text(element, "Name")
            if not name or name in declarations:
                continue
            declarations[name] = VariableDeclaration(
                name=name,
                xml=ET.tostring(element, encoding="unicode"),
            )
    return list(declarations.values())


def collect_workspace_data(samples: Iterable[CommandSample]) -> WorkspaceData | None:
    """Collect a source script VxWorkspaceData block for Fluent script property deserialization."""
    candidates: list[WorkspaceData] = []
    source_paths = OrderedDict.fromkeys(sample.source_path for sample in samples if sample.source_path)
    for source_path in source_paths:
        path = Path(source_path)
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        workspace_xml = _first_workspace_data_xml(text)
        if not workspace_xml:
            continue
        candidates.append(
            WorkspaceData(
                source_path=str(path),
                base_workspace_name=_first_xml_tag_text(workspace_xml, "BaseWorkspaceName"),
                xml=workspace_xml,
            )
        )
    if not candidates:
        return None
    return min(candidates, key=lambda item: (len(item.xml.encode("utf-8")), item.source_path))


def build_synthetic_xscr(
    samples: Iterable[CommandSample],
    *,
    variable_declarations: Iterable[VariableDeclaration] | None = None,
    workspace_data: WorkspaceData | None = None,
    target_script_folder: str = DEFAULT_TARGET_SCRIPT_FOLDER,
    sample_execution: str = DEFAULT_SAMPLE_EXECUTION,
) -> str:
    if sample_execution not in SAMPLE_EXECUTION_MODES:
        raise PipelineError(
            f"Unknown command corpus sample execution mode {sample_execution!r}; "
            f"expected one of {', '.join(SAMPLE_EXECUTION_MODES)}."
        )
    samples = list(samples)
    declarations = (
        list(variable_declarations)
        if variable_declarations is not None
        else collect_variable_declarations(samples)
    )
    workspace = workspace_data if workspace_data is not None else collect_workspace_data(samples)
    if workspace is None:
        raise PipelineError(
            "Cannot build FluentControl command corpus XSCR without source VxWorkspaceData. "
            "The Script/Properties block must contain VxWorkspaceData before VariableDeclarations."
        )
    declaration_xml = "\n".join(
        _indent(declaration.xml.strip(), "                ") for declaration in declarations
    )
    workspace_xml = _indent(_corpus_workspace_data_xml(workspace).strip(), "          ")
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="4">',
        "  <Payload>",
        "    <ObjectName>Full Export One Of Each Command Corpus</ObjectName>",
        f"    <ObjectSubfolderPath>{xml_escape(target_script_folder)}</ObjectSubfolderPath>",
        (
            "    <Comment>One sampled source-shaped instance of each command ID from the full export. "
            f"Sample execution mode: {sample_execution}.</Comment>"
        ),
        "    <Tags></Tags>",
        "    <ObjectAttributes>",
        "      <SingleAttribute>",
        "        <ObjAttrName>ParentProcessId</ObjAttrName>",
        "        <ObjAttrValue>00000000-0000-0000-0000-000000000000</ObjAttrValue>",
        "      </SingleAttribute>",
        "    </ObjectAttributes>",
        "    <PayloadData>",
        '      <Script version="2.0" contentIsDeepSave="False" contentIsApproved="False" dataVersion="152">',
        "        <Properties>",
        "          <IsProcessScript>False</IsProcessScript>",
        "          <ExpectedDuration>12000000000</ExpectedDuration>",
        workspace_xml,
        "          <VariableDeclarations>",
        '            <VariableDeclarations xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine">',
        '              <VariableDeclarations xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays">',
    ]
    if declaration_xml:
        lines.append(declaration_xml)
    lines.extend(
        [
            "              </VariableDeclarations>",
            "            </VariableDeclarations>",
            "          </VariableDeclarations>",
            "        </Properties>",
            "        <Commands>",
            "          <ScriptGroup>",
            "            <Objects>",
        ]
    )
    for sample in samples:
        lines.append(
            _indent(_sample_xml_for_execution(sample, sample_execution).strip(), "              ")
        )
    lines.extend(
        [
            "            </Objects>",
            "          </ScriptGroup>",
            "        </Commands>",
            "      </Script>",
            "    </PayloadData>",
            "  </Payload>",
            "  <Checksum></Checksum>",
            "</sd:VxData>",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    project_root: Path,
    out_dir: Path,
    *,
    sample_execution: str = DEFAULT_SAMPLE_EXECUTION,
) -> dict[str, Any]:
    samples, parse_errors = collect_command_samples(project_root)
    results = verify_samples(samples)
    failures = [item for item in results if not item.get("ok")]
    workspace_data = collect_workspace_data(samples)
    return {
        "schema_version": "tecan.full_export_command_corpus.v1",
        "project_root": str(project_root),
        "out_dir": str(out_dir),
        "command_type_count": len(samples),
        "command_instance_count": sum(item.count for item in samples),
        "workspace_data_present": workspace_data is not None,
        "workspace_data_source": workspace_data.source_path if workspace_data else "",
        "workspace_base_name": workspace_data.base_workspace_name if workspace_data else "",
        "workspace_data_bytes": len(workspace_data.xml.encode("utf-8")) if workspace_data else 0,
        "workspace_delta_entries_removed": True,
        "sample_execution": sample_execution,
        "parse_errors": parse_errors,
        "ok": not failures and not parse_errors and workspace_data is not None,
        "failure_count": len(failures),
        "failures": failures,
        "commands": results,
    }


def write_outputs(
    project_root: Path,
    out_dir: Path | None = None,
    *,
    ready_root: Path | None = None,
    publish_ready_bundle: bool = True,
    target_script_folder: str = DEFAULT_TARGET_SCRIPT_FOLDER,
    sample_execution: str = DEFAULT_SAMPLE_EXECUTION,
) -> dict[str, Any]:
    samples, parse_errors = collect_command_samples(project_root)
    results = verify_samples(samples)
    failures = [item for item in results if not item.get("ok")]
    variable_declarations = collect_variable_declarations(samples)
    workspace_data = collect_workspace_data(samples)
    xscr_text = build_synthetic_xscr(
        samples,
        variable_declarations=variable_declarations,
        workspace_data=workspace_data,
        target_script_folder=target_script_folder,
        sample_execution=sample_execution,
    )
    report = {
        "schema_version": "tecan.full_export_command_corpus.v1",
        "project_root": str(project_root),
        "temporary_artifact_dir": str(out_dir) if out_dir is not None else "",
        "command_type_count": len(samples),
        "command_instance_count": sum(item.count for item in samples),
        "variable_declaration_count": len(variable_declarations),
        "workspace_data_present": workspace_data is not None,
        "workspace_data_source": workspace_data.source_path if workspace_data else "",
        "workspace_base_name": workspace_data.base_workspace_name if workspace_data else "",
        "workspace_data_bytes": len(workspace_data.xml.encode("utf-8")) if workspace_data else 0,
        "workspace_delta_entries_removed": True,
        "sample_execution": sample_execution,
        "parse_errors": parse_errors,
        "ok": not failures and not parse_errors and workspace_data is not None,
        "failure_count": len(failures),
        "failures": failures,
        "commands": results,
        "ready_to_import": False,
        "target_script_folder": target_script_folder,
        "ready_to_import_bundle": "",
        "ready_to_import_artifacts": [],
    }
    if publish_ready_bundle and report["ok"]:
        bundle = publish_ready_to_import_bundle(
            report,
            xscr_text,
            ready_root=ready_root or default_ready_to_import_root(),
            target_script_folder=target_script_folder,
        )
        zeia_path = bundle / f"{bundle.name}.zeia"
        published_report_path = bundle / "source" / "reports" / "command_corpus_report.json"
        if published_report_path.exists():
            report.update(json.loads(published_report_path.read_text(encoding="utf-8")))
        report["ready_to_import"] = True
        report["ready_to_import_bundle"] = str(bundle)
        report["ready_to_import_deliverable"] = f"{bundle.name}.zeia"
        report["ready_to_import_artifacts"] = [
            str(zeia_path),
        ]

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "one_of_each_command.xscr").write_text(xscr_text, encoding="utf-8")
        (out_dir / "command_corpus_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (out_dir / "command_corpus_report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def publish_ready_to_import_bundle(
    report: dict[str, Any],
    xscr_text: str,
    *,
    ready_root: Path,
    bundle_name: str = READY_BUNDLE_NAME,
    target_script_folder: str = DEFAULT_TARGET_SCRIPT_FOLDER,
) -> Path:
    """Publish the verified corpus as a ZEIA-only V2 ready-to-import folder."""
    ready_root = ready_root.resolve()
    publish_plan = plan_ready_to_import_publish(ready_root, bundle_name)
    bundle_name = publish_plan.bundle_name
    bundle_dir = publish_plan.bundle_dir
    staging_dir = publish_plan.staging_dir
    backup_dir = publish_plan.backup_dir
    _assert_under_root(ready_root, bundle_dir)
    _assert_under_root(ready_root, staging_dir)

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    archive_path = staging_dir / f"{bundle_name}.zeia"
    reports_dir = staging_dir / "source" / "reports"
    root_reports_dir = staging_dir / "reports"
    source_dir = staging_dir / "source"
    generated_dir = staging_dir / "generated"
    media_dir = staging_dir / "media"
    support_dir = staging_dir / "support"
    reports_dir.mkdir(parents=True, exist_ok=True)
    root_reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)
    support_dir.mkdir(parents=True, exist_ok=True)

    source_zeia = _source_zeia_for_project(Path(str(report.get("project_root") or "")))
    external_file_report: dict[str, Any] = {}
    external_file_deployments: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="command_corpus_") as tmp:
        compiled_xscr = Path(tmp) / "full_export_command_corpus.xscr"
        compiled_xscr.write_text(xscr_text, encoding="utf-8")
        archive_record = _write_generated_project_archive(
            source_zeia,
            archive_path,
            compiled_xscr=compiled_xscr,
            bundle_root=staging_dir,
            source_manifest=None,
            source_xscr=None,
            source_scripts=[],
            subroutine_artifacts=[],
            target_script_folder=target_script_folder,
            filesystem_source_archives=[source_zeia],
        )
        _assert_archive_import_clean(archive_record, staging_dir=staging_dir)
        external_file_report = audit_external_file_dependencies(
            compiled_xscr=compiled_xscr,
            context_root=Path(str(report.get("project_root") or "")),
        )
        staged_external_files = stage_found_external_files(
            external_file_report,
            bundle_source_dir=source_dir,
        )
        external_file_deployments = _external_deployment_manifest_records(
            staged_external_files,
            bundle_root=staging_dir,
        )
        write_external_file_dependency_artifacts(external_file_report, out_dir=reports_dir)

    bundle_report = {
        **report,
        "ready_to_import": True,
        "ready_to_import_bundle": str(bundle_dir),
        "ready_to_import_bundle_base": READY_BUNDLE_NAME,
        "ready_to_import_bundle_name": bundle_name,
        "ready_to_import_deliverable": f"{bundle_name}.zeia",
        "ready_to_import_artifacts": [
            f"{bundle_name}.zeia",
        ],
        "ready_to_import_companions": [
            "source/reports/command_corpus_report.json",
            "source/reports/command_corpus_report.md",
        ],
        "project_import": archive_record,
        "external_file_dependencies": external_file_report,
        "external_file_deployment_count": len(external_file_deployments),
        "target_script_folder": target_script_folder,
    }
    (reports_dir / "command_corpus_report.json").write_text(
        json.dumps(bundle_report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (reports_dir / "command_corpus_report.md").write_text(render_markdown(bundle_report), encoding="utf-8")
    (root_reports_dir / "README.md").write_text(
        "Command corpus reports live under `source/reports/`. The only import deliverable is the root ZEIA.\n",
        encoding="utf-8",
    )
    (staging_dir / "source" / "metadata.json").write_text(
        json.dumps(_ready_bundle_metadata(bundle_report, bundle_name=bundle_name), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (staging_dir / "RECREATE_SCRIPT.md").write_text(_recreate_script(bundle_report), encoding="utf-8")
    (support_dir / "request.spec.yaml").write_text(_request_spec(bundle_report), encoding="utf-8")
    (support_dir / "protocol.ir.json").write_text(
        json.dumps(_protocol_ir_stub(bundle_report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (support_dir / "generation_manifest.json").write_text(
        json.dumps(_generation_manifest(bundle_report, bundle_name=bundle_name), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (support_dir / "GENERATION_WORKFLOW.md").write_text(
        _generation_workflow_markdown(bundle_report, bundle_name=bundle_name),
        encoding="utf-8",
    )
    (generated_dir / "protocol.py").write_text(_generated_protocol_stub(), encoding="utf-8")
    _copy_v2_setup_script(support_dir / "run_tecan_bundle_setup.bat")
    shutil.move(str(support_dir / "run_tecan_bundle_setup.bat"), staging_dir / "run_tecan_bundle_setup.bat")
    _write_delivery_manifest(
        staging_dir,
        bundle_name,
        external_file_deployments=external_file_deployments,
    )

    result = validate_v2_delivery_bundle(staging_dir, protocol_name=bundle_name)
    if not result.ok:
        raise PipelineError(
            "Command corpus ready-to-import bundle is not ZEIA-only V2: "
            + "; ".join(f"{issue.code}: {issue.message}" for issue in result.issues)
        )

    _publish_protocol_folder_replacement(staging_dir, bundle_dir, backup_dir=backup_dir)
    return bundle_dir


def _ready_bundle_metadata(report: dict[str, Any], *, bundle_name: str) -> dict[str, Any]:
    return {
        "schema_version": "tecan.full_export_command_corpus.ready_bundle.v2",
        "bundle_schema_version": READY_BUNDLE_SCHEMA_VERSION,
        "bundle_name": bundle_name,
        "bundle_family": READY_BUNDLE_NAME,
        "ready_to_import": bool(report.get("ok")),
        "workflow_status": "ready_to_import" if report.get("ok") else "validated_not_ready",
        "source_project": report.get("project_root"),
        "target_script_folder": report.get("target_script_folder") or DEFAULT_TARGET_SCRIPT_FOLDER,
        "sample_execution": report.get("sample_execution") or DEFAULT_SAMPLE_EXECUTION,
        "workspace_delta_entries_removed": bool(report.get("workspace_delta_entries_removed")),
        "command_type_count": report.get("command_type_count"),
        "command_instance_count": report.get("command_instance_count"),
        "source_of_truth": {
            "kind": "fluent_project_archive",
            "relative_path": f"{bundle_name}.zeia",
            "description": "Only FluentControl import deliverable for this corpus.",
        },
        "reports": {
            "json": "source/reports/command_corpus_report.json",
            "markdown": "source/reports/command_corpus_report.md",
        },
    }


def _generation_manifest(report: dict[str, Any], *, bundle_name: str) -> dict[str, Any]:
    return {
        "schema_version": "tecan.generation_manifest.v1",
        "workflow_status": "ready_to_import",
        "protocol_name": bundle_name,
        "bundle_family": READY_BUNDLE_NAME,
        "source_project": report.get("project_root"),
        "target_script_folder": report.get("target_script_folder") or DEFAULT_TARGET_SCRIPT_FOLDER,
        "sample_execution": report.get("sample_execution") or DEFAULT_SAMPLE_EXECUTION,
        "ready_to_import_deliverable": report.get("ready_to_import_deliverable"),
    }


def _generation_workflow_markdown(report: dict[str, Any], *, bundle_name: str) -> str:
    return "\n".join(
        [
            "# Generation Workflow",
            "",
            f"- Bundle: `{bundle_name}`",
            f"- Source project: `{report.get('project_root') or ''}`",
            f"- Target script folder: `{report.get('target_script_folder') or DEFAULT_TARGET_SCRIPT_FOLDER}`",
            f"- Sample execution: `{report.get('sample_execution') or DEFAULT_SAMPLE_EXECUTION}`",
            "- Delivery: root ZEIA only; support artifacts remain under `support/`.",
            "",
        ]
    )


def _recreate_script(report: dict[str, Any]) -> str:
    project = report.get("project_root") or "ready-to-import/imported-full-export/temp_files"
    deliverable = report.get("ready_to_import_deliverable") or f"{READY_BUNDLE_NAME}.zeia"
    return "\n".join(
        [
            "# Recreate Full Export Command Corpus",
            "",
            "```powershell",
            "Set-Location source\\03-protocol-builder",
            "python -m tools.full_export_command_corpus `",
            f"  --project \"{project}\"",
            f"  --target-script-folder \"{report.get('target_script_folder') or DEFAULT_TARGET_SCRIPT_FOLDER}\"",
            f"  --sample-execution \"{report.get('sample_execution') or DEFAULT_SAMPLE_EXECUTION}\"",
            "```",
            "",
            f"Import `{deliverable}`. This root ZEIA is the only FluentControl import deliverable.",
            "",
            "Use `--out-dir` only for temporary debug artifacts such as the loose comparison XSCR.",
            "",
        ]
    )


def _write_delivery_manifest(
    staging_dir: Path,
    bundle_name: str,
    *,
    external_file_deployments: list[dict[str, str]],
) -> None:
    support_dir = staging_dir / "support"
    (support_dir / "delivery_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": DELIVERY_MANIFEST_SCHEMA_VERSION,
                "bundle_schema_version": READY_BUNDLE_SCHEMA_VERSION,
                "protocol_name": bundle_name,
                "external_file_deployments": external_file_deployments,
                "deliverables": [
                    {
                        "kind": "fluent_project_archive",
                        "path": f"{bundle_name}.zeia",
                        "description": "Only FluentControl import deliverable in this folder.",
                    }
                ],
                "companion_artifacts": [
                    {"kind": "recreation_instructions", "path": "RECREATE_SCRIPT.md"},
                    {"kind": "request_specification", "path": "support/request.spec.yaml"},
                    {"kind": "protocol_ir", "path": "support/protocol.ir.json"},
                    {"kind": "delivery_manifest", "path": "support/delivery_manifest.json"},
                    {"kind": "generated_python", "path": "generated/protocol.py"},
                    {"kind": "reports", "path": "reports/"},
                    {"kind": "source_tree", "path": "source/"},
                    {"kind": "bundle_setup", "path": "run_tecan_bundle_setup.bat"},
                ],
                "internal_artifacts": [
                    {
                        "kind": "command_corpus_xscr_intermediate",
                        "published": False,
                        "description": "The loose XSCR is only written when --out-dir is explicitly passed.",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _external_deployment_manifest_records(
    staged_external_files: Iterable[dict[str, str]],
    *,
    bundle_root: Path,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for item in staged_external_files:
        target_path = str(item.get("expected_path") or "").strip()
        staged_path = Path(str(item.get("staged_path") or ""))
        if not target_path or not staged_path.is_file():
            continue
        try:
            bundle_path = staged_path.relative_to(bundle_root).as_posix()
        except ValueError:
            continue
        records.append(
            {
                "bundle_path": bundle_path,
                "target_path": target_path,
                "sha256": hashlib.sha256(staged_path.read_bytes()).hexdigest(),
            }
        )
    return records


def _assert_archive_import_clean(record: dict[str, Any], *, staging_dir: Path | None = None) -> None:
    archive_audit = record.get("archive_audit") or {}
    blocking = list(archive_audit.get("blocking") or [])
    needs_review = list(archive_audit.get("needs_review") or [])
    critical_warnings = [
        str(warning)
        for warning in record.get("warnings") or []
        if str(warning).startswith(("BROKEN IMPORT ARTIFACT:", "NOT IMPORT-CLEAN:"))
    ]
    zip_ok = bool(archive_audit.get("zip_ok", record.get("zip_valid", True)))
    if zip_ok and not blocking and not critical_warnings:
        return
    if staging_dir is not None and staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    detail = []
    if not zip_ok:
        detail.append("zip audit failed")
    if blocking:
        detail.append(f"{len(blocking)} blocking finding(s)")
    if needs_review:
        detail.append(f"{len(needs_review)} needs-review finding(s)")
    if critical_warnings:
        detail.append(f"{len(critical_warnings)} critical warning(s)")
    raise PipelineError(
        "Command corpus ZEIA failed archive import audit; refusing to publish ready-to-import: "
        + ", ".join(detail)
    )


def _source_zeia_for_project(project_root: Path) -> Path:
    source_dir = project_root / "source"
    candidates = sorted(path for path in source_dir.glob("*.zeia") if zipfile.is_zipfile(path))
    if not candidates:
        raise PipelineError(f"No source ZEIA found for command corpus project: {source_dir}")
    return candidates[0]


def _request_spec(report: dict[str, Any]) -> str:
    project = str(report.get("project_root") or "")
    return "\n".join(
        [
            "request:",
            "  kind: full_export_command_corpus",
            "  description: Build one corpus script containing one source-shaped sample of each command ID.",
            f"  source_project: {json.dumps(project)}",
            "generation:",
            "  ready_to_import_deliverable: zeia_only",
            f"  target_script_folder: {json.dumps(report.get('target_script_folder') or DEFAULT_TARGET_SCRIPT_FOLDER)}",
            f"  sample_execution: {json.dumps(report.get('sample_execution') or DEFAULT_SAMPLE_EXECUTION)}",
            "",
        ]
    )


def _protocol_ir_stub(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "tecan.protocol_ir.v1",
        "name": READY_BUNDLE_NAME,
        "metadata": {
            "kind": "full_export_command_corpus",
            "command_type_count": report.get("command_type_count"),
            "command_instance_count": report.get("command_instance_count"),
            "source_of_truth": f"{READY_BUNDLE_NAME}.zeia",
        },
        "steps": [],
    }


def _generated_protocol_stub() -> str:
    return (
        '"""Generated command-corpus companion stub.\n\n'
        "The importable artifact is full_export_command_corpus.zeia.\n"
        '"""\n\n'
        "def build_worktable():\n"
        "    return None\n"
    )


def _assert_under_root(root: Path, path: Path) -> None:
    root = root.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Refusing to publish outside ready-to-import root: {path}")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Full Export Command Corpus",
        "",
        f"- Project: `{report.get('project_root')}`",
        f"- Command IDs: `{report.get('command_type_count')}`",
        f"- Command instances scanned: `{report.get('command_instance_count')}`",
        f"- Source variable declarations copied: `{report.get('variable_declaration_count', 0)}`",
        f"- Source workspace metadata copied: `{'yes' if report.get('workspace_data_present') else 'no'}`",
        f"- Source workspace deltas removed: `{'yes' if report.get('workspace_delta_entries_removed') else 'no'}`",
        f"- Sample execution: `{report.get('sample_execution') or DEFAULT_SAMPLE_EXECUTION}`",
        f"- Status: `{'pass' if report.get('ok') else 'fail'}`",
    ]
    if report.get("workspace_data_source"):
        lines.append(f"- Workspace metadata source: `{report.get('workspace_data_source')}`")
    if report.get("workspace_base_name"):
        lines.append(f"- Workspace base name: `{report.get('workspace_base_name')}`")
    if report.get("ready_to_import_bundle"):
        lines.append(f"- Ready-to-import bundle: `{report.get('ready_to_import_bundle')}`")
    if report.get("ready_to_import_deliverable"):
        lines.append(f"- Ready-to-import deliverable: `{report.get('ready_to_import_deliverable')}`")
    project_import = report.get("project_import") or {}
    archive_audit = project_import.get("archive_audit") or {}
    if archive_audit:
        lines.append(f"- ZEIA archive blockers: `{len(archive_audit.get('blocking') or [])}`")
        lines.append(f"- ZEIA archive needs-review: `{len(archive_audit.get('needs_review') or [])}`")
    if report.get("temporary_artifact_dir"):
        lines.append(f"- Temporary debug artifacts: `{report.get('temporary_artifact_dir')}`")
    lines.extend(
        [
            "",
            "| Command ID | Count | API V2 type | Registry | Compare | Source |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in report.get("commands") or []:
        registry = row.get("registry_operation") or row.get("registry_support_status") or "missing"
        status = "pass" if row.get("ok") else row.get("error") or row.get("diff_hint") or "fail"
        lines.append(
            "| {command_id} | {count} | {api_v2_type} | {registry} | {status} | {source} |".format(
                command_id=_cell(row.get("command_id")),
                count=row.get("count") or 0,
                api_v2_type=_cell(row.get("api_v2_type")),
                registry=_cell(registry),
                status=_cell(status),
                source=_cell(row.get("source_path")),
            )
        )
    if report.get("parse_errors"):
        lines.extend(["", "## Parse Errors", ""])
        for item in report["parse_errors"]:
            lines.append(f"- `{item.get('path')}`: {item.get('error')}")
    if report.get("failures"):
        lines.extend(["", "## Failures", ""])
        for item in report["failures"]:
            lines.append(
                f"- `{item.get('command_id')}`: {item.get('error') or item.get('diff_hint') or 'failed'}"
            )
    lines.append("")
    return "\n".join(lines)


def _script_group_objects(root: ET.Element) -> Iterable[ET.Element]:
    for element in root.iter():
        if _local_name(element.tag) != "Object":
            continue
        if str(element.attrib.get("Type") or "").endswith("ScriptGroupDataV1"):
            yield element


def _statement_objects(container: ET.Element | None) -> Iterable[ET.Element]:
    for command_object in _direct_children(container, "Object"):
        command_id = _command_id(command_object)
        yield command_object
        payload = _direct_child(command_object, command_id)
        for nested_name in ("Objects", "Statements"):
            nested = _direct_child(payload, nested_name)
            if nested is not None:
                yield from _statement_objects(nested)


def _command_id(command_object: ET.Element) -> str:
    for child in list(command_object):
        return _local_name(child.tag)
    return str(command_object.attrib.get("Type") or "").rsplit(".", 1)[-1]


def _direct_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_children(parent: ET.Element | None, name: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [child for child in list(parent) if _local_name(child.tag) == name]


def _first_descendant(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in parent.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _is_variable_declaration_element(element: ET.Element) -> bool:
    local = _local_name(element.tag)
    if local == "anyType":
        return "VariableDefinitionHelper" in " ".join(str(value) for value in element.attrib.values())
    return local == "VariableDefinitionHelper"


def _first_descendant_text(parent: ET.Element | None, name: str) -> str:
    child = _first_descendant(parent, name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _first_workspace_data_xml(text: str) -> str:
    match = re.search(
        r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?VxWorkspaceData)\b[^>]*>.*?</(?P=tag)>",
        text,
        re.DOTALL,
    )
    return match.group(0).strip() if match else ""


def _first_xml_tag_text(text: str, tag_name: str) -> str:
    match = re.search(
        rf"<(?:[A-Za-z_][\w.-]*:)?{re.escape(tag_name)}\b[^>]*>(?P<value>.*?)</(?:[A-Za-z_][\w.-]*:)?{re.escape(tag_name)}>",
        text,
        re.DOTALL,
    )
    return match.group("value").strip() if match else ""


def _corpus_workspace_data_xml(workspace: WorkspaceData) -> str:
    """Keep script-level workspace metadata but exclude source-PC delta state."""
    try:
        root = ET.fromstring(workspace.xml, max_bytes=-1)
    except Exception as exc:
        raise PipelineError(f"Cannot parse source VxWorkspaceData for command corpus: {exc}") from exc
    for element in root.iter():
        if _local_name(element.tag) == "WorkspaceDeltas":
            element.clear()
    return ET.tostring(root, encoding="unicode")


def _disable_sample_for_execution(sample: CommandSample) -> str:
    """Keep the corpus safe to open by disabling every copied source command."""
    try:
        root = ET.fromstring(sample.xml, max_bytes=-1)
    except Exception as exc:
        raise PipelineError(
            f"Cannot make command corpus sample {sample.command_id!r} inspection-only: {exc}"
        ) from exc

    disabled_flags = 0
    for element in root.iter():
        if _local_name(element.tag) == "IsDisabledForExecution":
            element.text = "True"
            disabled_flags += 1
        for attribute, value in element.attrib.items():
            if _local_name(attribute) != "IsDisabledForExecution":
                continue
            element.attrib[attribute] = "true" if value == value.lower() else "True"
            disabled_flags += 1
    if not disabled_flags:
        raise PipelineError(
            f"Cannot make command corpus sample {sample.command_id!r} inspection-only: "
            "source XML has no IsDisabledForExecution field or attribute."
        )
    return ET.tostring(root, encoding="unicode")


def _sample_xml_for_execution(sample: CommandSample, sample_execution: str) -> str:
    if sample_execution == "source-behavior":
        return sample.xml
    return _disable_sample_for_execution(sample)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" if line else prefix.rstrip() for line in text.splitlines())


def _cell(value: Any) -> str:
    text = str(value or "").replace("|", "\\|").strip()
    return text or " "


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project",
        type=Path,
        default=default_project_root(),
        help="Imported project context root (pass an activated full ZEIA import under ready-to-import/<ctx>/temp_files).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional temporary debug artifact directory. Default workflow writes only ready-to-import.",
    )
    parser.add_argument(
        "--ready-root",
        type=Path,
        default=default_ready_to_import_root(),
        help="ready-to-import root for the generated corpus bundle.",
    )
    parser.add_argument(
        "--target-script-folder",
        default=DEFAULT_TARGET_SCRIPT_FOLDER,
        help="FluentControl script folder/ObjectSubfolderPath for the command corpus script.",
    )
    parser.add_argument(
        "--sample-execution",
        choices=SAMPLE_EXECUTION_MODES,
        default=DEFAULT_SAMPLE_EXECUTION,
        help=(
            "inspection-only disables every sample; source-behavior preserves source enabled/disabled "
            "flags for controlled debugging."
        ),
    )
    parser.add_argument(
        "--no-ready-to-import",
        action="store_true",
        help="Only write temporary artifacts; do not publish ready-to-import/full_export_command_corpus.",
    )
    args = parser.parse_args()
    if args.no_ready_to_import and args.out_dir is None:
        parser.error("--no-ready-to-import requires --out-dir for temporary debug artifacts")
    report = write_outputs(
        args.project,
        args.out_dir,
        ready_root=args.ready_root,
        publish_ready_bundle=not args.no_ready_to_import,
        target_script_folder=args.target_script_folder,
        sample_execution=args.sample_execution,
    )
    print(render_markdown(report))
    if not report.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
