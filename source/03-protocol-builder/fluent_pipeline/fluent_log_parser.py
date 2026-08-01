"""Parse FluentControl/VisionX logs and map errors to workflow diagnostics."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import json
import re
from datetime import datetime, timedelta
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from .audit_import_context import import_for_error, read_audit_import_events


LOG_DIAGNOSTIC_VERSION = "tecan.fluent_log_diagnostics.v1"
CURRENT_RUNTIME_DIAGNOSTIC_WINDOW_SECONDS = 900.0
NEARBY_SCRIPT_WINDOW_SECONDS = 60.0
THREAD_SCOPE_WINDOW_SECONDS = 120.0
CURRENT_RUNTIME_CATEGORIES = {
    "device_alias",
    "device_binding",
    "dependencies",
    "driver_command",
    "import",
    "prompt",
    "runtime",
    "script_editor_load",
    "variables",
    "worktable",
    "worktable_metadata",
    "worktable_prompt",
}
DEFAULT_FLUENT_LOG_LOCATIONS: tuple[tuple[str, str], ...] = (
    (r"C:\ProgramData\Tecan\LoggingServer\LogFiles", "*.ulf"),
    (r"C:\ProgramData\Tecan\VisionX\AuditTrail", "AuditTrail_*.csv"),
    (r"C:\ProgramData\Tecan\VisionX\AuditArch\Log", "auditarch*.log"),
    (r"C:\ProgramData\Tecan\VisionX\TaskHandling", "*.log"),
    (r"C:\ProgramData\Tecan\DataStore\IoT-Client\MAP.Services.Logging.Service\LogFile", "*.ulf"),
    (r"C:\ProgramData\Tecan\VisionX\Logs", "*.log"),
    (r"C:\ProgramData\Tecan\VisionX\LogFiles", "*.log"),
    (r"C:\ProgramData\Tecan\FluentControl", "*.log"),
)
ERROR_ID_RE = re.compile(r"\b(VX_[A-Z0-9_]+)\b")
TIMESTAMP_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)"
)
LEVEL_RE = re.compile(r"\b(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\b", re.IGNORECASE)
MODULE_BRACKET_RE = re.compile(r"\[(?P<value>[^\]]{2,80})\]")
SCRIPT_RE = re.compile(r"\b(?:script|method|object)\s*[:=]\s*['\"]?(?P<value>[^'\";\r\n]+)", re.IGNORECASE)
SCRIPT_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:for|loading|load operation for)\s+Script\s+['\"]?(?P<value>[^'\"\r\n]+)", re.IGNORECASE),
    re.compile(r"\bLoadWorker\((?P<value>[^)\r\n]+)\)", re.IGNORECASE),
    re.compile(r"\bScript\s+['\"](?P<value>[^'\"\r\n]+)['\"]", re.IGNORECASE),
    re.compile(
        r"\b(?:loaded|unloaded)\s+script\s+['\"]?(?P<value>[^'\"\r\n<]+?)(?=\.(?:['\"<\s]|$)|['\"<\s]|$)",
        re.IGNORECASE,
    ),
)
SCOPE_STACK_RE = re.compile(r"\bScope:\s*Method\|(?P<body>[^<\r\n]+)", re.IGNORECASE)
SCRIPT_LINE_RE = re.compile(r"\bLine number:\s*(?P<line>\d+)\b", re.IGNORECASE)
THREAD_ID_RE = re.compile(r"<ThreadId>(?P<value>\d+)</ThreadId>", re.IGNORECASE)
COMMAND_HINT_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*(?:CommandScriptStatement|ScriptStatement))\b|"
    r"\bCommand\s+\"(?P<quoted>[^\"]+)\"|"
    r"\b(?P<token>ResolvexA200_\w+|MoveAxis\w*)\b"
)
XML_ATTR_RE = re.compile(r"\b(?P<name>TimeStamp|Channel|Severity|Message)=\"(?P<value>[^\"]*)\"")


@dataclass(frozen=True)
class FluentLogRecord:
    timestamp: str = ""
    application: str = ""
    module: str = ""
    level: str = ""
    error_id: str = ""
    script: str = ""
    main_script: str = ""
    script_source: str = ""
    script_context: str = ""
    script_line: int = 0
    thread_id: str = ""
    command_hint: str = ""
    object_name: str = ""
    message: str = ""
    detail_lines: tuple[str, ...] = ()
    raw_context: str = ""
    source: str = ""
    line_number: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "application": self.application,
            "module": self.module,
            "level": self.level,
            "error_id": self.error_id,
            "script": self.script,
            "main_script": self.main_script,
            "script_source": self.script_source,
            "script_context": self.script_context,
            "script_line": self.script_line,
            "thread_id": self.thread_id,
            "command_hint": self.command_hint,
            "object": self.object_name,
            "message": self.message,
            "detail_lines": list(self.detail_lines),
            "raw_context": self.raw_context,
            "source": self.source,
            "line_number": self.line_number,
        }


@dataclass(frozen=True)
class FluentLogDiagnostic:
    id: str
    severity: str
    category: str
    title: str
    likely_workflow_defect: str
    suggested_fix: str
    evidence: tuple[str, ...] = ()
    error_ids: tuple[str, ...] = ()
    records: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "likely_workflow_defect": self.likely_workflow_defect,
            "suggested_fix": self.suggested_fix,
            "evidence": list(self.evidence),
            "error_ids": list(self.error_ids),
            "records": list(self.records),
        }


@dataclass(frozen=True)
class DiagnosticRule:
    id: str
    severity: str
    category: str
    title: str
    likely_workflow_defect: str
    suggested_fix: str
    pattern: re.Pattern[str]
    error_ids: tuple[str, ...] = ()

    def matches(self, text: str, error_ids: set[str]) -> bool:
        if self.error_ids and not any(error_id in error_ids for error_id in self.error_ids):
            return False
        return bool(self.pattern.search(text))


DIAGNOSTIC_RULES: tuple[DiagnosticRule, ...] = (
    DiagnosticRule(
        id="fluent_log.invalid_variable_default",
        severity="blocking",
        category="variables",
        title="Script variable default cannot be converted to its declared value type",
        likely_workflow_defect=(
            "Generated XSCR reconciled a variable to Integer or Floating Point while preserving "
            "a missing or incompatible startup default."
        ),
        suggested_fix=(
            "Normalize generated startup defaults to the final declaration type, regenerate the XSCR, "
            "and keep the typed variable-default post-compile gate enabled."
        ),
        error_ids=("VX_ESHRD_001_009",),
        pattern=re.compile(
            r"(Null object cannot be converted to a value type|VariableContainer\.Declare|"
            r"DeclareScriptScopeVariable|System\.Convert\.ChangeType)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.script_workspace_data_missing",
        severity="blocking",
        category="worktable_metadata",
        title="Script properties are missing VxWorkspaceData before variable declarations",
        likely_workflow_defect=(
            "Generated XSCR omitted the script-level VxWorkspaceData block from Script/Properties. "
            "FluentControl expects that worktable workspace metadata before VariableDeclarations when "
            "recreating script-specific worktable values such as workspaceName."
        ),
        suggested_fix=(
            "Regenerate the method with source VxWorkspaceData copied into Script/Properties before "
            "VariableDeclarations. For the command corpus, rebuild full_export_command_corpus.zeia with "
            "the workspace-metadata collector enabled."
        ),
        error_ids=("VX_ESHRD_001_009",),
        pattern=re.compile(
            r"(workspaceName.{0,700}Expecting element ['\"]VxWorkspaceData['\"].{0,700}VariableDeclarations|"
            r"Expecting element ['\"]VxWorkspaceData['\"].{0,700}VariableDeclarations|"
            r"VariableDeclarations.{0,700}Expecting element ['\"]VxWorkspaceData['\"])",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.rup_worktable_variable_labware_index",
        severity="blocking",
        category="worktable_prompt",
        title="RUP Worktable prompt contains a variable labware index",
        likely_workflow_defect=(
            "Generated RUPWorktableStatement ConfigureDataLabwareDataModel preserved a labware name "
            "with a runtime variable inside brackets, for example `SampleSourceTube[NumSourceTubes_Main]`. "
            "FluentControl Script Editor deserializes that index as an Int32 and fails before the script opens."
        ),
        suggested_fix=(
            "Regenerate with unsafe Worktable prompt selected-labware bindings blanked or resolved to concrete "
            "numeric instances such as `[001]`; keep the RUP Worktable variable-index validation gate enabled."
        ),
        error_ids=("VX_SCEDT_001_005",),
        pattern=re.compile(
            r"(FormatException|Input string was not in a correct format|ParseInt32).{0,800}"
            r"(WorktableStatementDataClass|ConfigureDataLabwareDataModel)|"
            r"(WorktableStatementDataClass|ConfigureDataLabwareDataModel).{0,800}"
            r"(FormatException|Input string was not in a correct format|ParseInt32)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.scriptgroup_statement_list_invalid_xml_node",
        severity="blocking",
        category="script_editor_load",
        title="ScriptGroup XML has a child FluentControl cannot deserialize",
        likely_workflow_defect=(
            "Generated XSCR used an invalid ScriptGroup serialization shape. FluentControl expects the "
            "sampled command Object elements directly under ScriptGroup/Objects; a nested "
            "ScriptGroupDataV1 wrapper, XML comment, or other non-command node makes the ScriptGroup "
            "serializer fail before the script opens."
        ),
        suggested_fix=(
            "Regenerate with only sampled command Object elements directly inside ScriptGroup/Objects. "
            "Do not add a ScriptGroupDataV1 wrapper, XML comments, or other non-command nodes there; "
            "keep command provenance in bundle reports instead."
        ),
        error_ids=("VX_SCEDT_001_005",),
        pattern=re.compile(
            r"(ScriptReadStatementsException|Failed to deserialize statements).{0,800}"
            r"invalid XmlNodeType.{0,800}(Read1_ScriptGroup|DeserializeListElements)|"
            r"(Read1_ScriptGroup|DeserializeListElements).{0,800}invalid XmlNodeType",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.variable_definition_namespace",
        severity="blocking",
        category="xml_namespace",
        title="Script Editor cannot deserialize generated variable declarations",
        likely_workflow_defect=(
            "Generated XSCR variable definitions use an invalid VariableDefinitionHelper XML namespace "
            "or xsi/i type alias."
        ),
        suggested_fix=(
            "Regenerate or repair the compiled XSCR with VariableDefinitionHelper namespace normalization, "
            "then keep the static namespace gate enabled and verify Script Editor load manually on the instrument PC."
        ),
        error_ids=("VX_SCEDT_001_005",),
        pattern=re.compile(
            r"(VariableDefinitionHelper|schemas\.datacontract\.org|\bxsi:type\b|\bi:type\b|\bd3p1\b|namespace)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.script_editor_load_failed",
        severity="high",
        category="script_editor_load",
        title="Script Editor failed while processing script commands",
        likely_workflow_defect=(
            "The compiled XSCR contains a command or metadata shape Script Editor cannot load."
        ),
        suggested_fix=(
            "Inspect the surrounding Script Editor log details, re-run post-compile XSCR reinspection, "
            "and add a targeted static gate for the failing command shape."
        ),
        error_ids=("VX_SCEDT_001_005",),
        pattern=re.compile(r"(VX_SCEDT_001_005|processing script commands|load script|Script Editor)", re.IGNORECASE),
    ),
    DiagnosticRule(
        id="fluent_log.undefined_variable",
        severity="blocking",
        category="variables",
        title="Runtime references an undefined FluentControl variable",
        likely_workflow_defect=(
            "Protocol IR or generated XSCR references a variable-indexed labware/object without declaring "
            "the corresponding FluentControl variable."
        ),
        suggested_fix=(
            "Declare the missing IR variable before generating, or replace variable-indexed labware with "
            "explicit generated labware labels."
        ),
        pattern=re.compile(
            r"(undefined|undeclared|unknown|not defined).{0,80}\b(variable|platecount)\b|\bvariable\b.{0,80}(undefined|not defined|platecount)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.prompt_timeout_range",
        severity="blocking",
        category="prompt",
        title="Prompt auto-close timeout is outside FluentControl range",
        likely_workflow_defect="Generated runtime prompt uses a close-after timeout outside FluentControl's 1-7200 second range.",
        suggested_fix="Clamp prompt close-after values to 1-7200 seconds in the IR/generator and regenerate the XSCR.",
        pattern=re.compile(r"(close prompt after|close-after|timeout).{0,80}(lower range|1\s*-\s*7200|7200|range)", re.IGNORECASE | re.DOTALL),
    ),
    DiagnosticRule(
        id="fluent_log.missing_subroutine",
        severity="blocking",
        category="subroutine",
        title="Selected subroutine dependency cannot be loaded",
        likely_workflow_defect="Generated method references a subroutine that is missing, ambiguous, or not packaged/imported with the base context.",
        suggested_fix=(
            "Resolve the subroutine against the source ZEIA manifest, preserve/replace the source subroutine GUID, "
            "and rerun the subroutine metadata gate."
        ),
        pattern=re.compile(
            r"(unable|could not|cannot|failed).{0,120}(load|open|resolve|find).{0,100}(selected )?subroutine|"
            r"subroutine.{0,160}(missing|not found|could not be found|cannot be found|ambiguous|unresolved)|"
            r"missing.{0,80}subroutine|subroutine_reference_missing",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.missing_referenced_files",
        severity="blocking",
        category="dependencies",
        title="FluentControl reports missing referenced files or dependencies",
        likely_workflow_defect=(
            "A script command referenced a file or dependency that is absent on this PC. For normal generated "
            "methods, the ZEIA or target context may be missing a prerequisite. For the inspection-only command "
            "corpus, this means a copied source command remained enabled and FluentControl tried to validate its "
            "original external path."
        ),
        suggested_fix=(
            "Use a full ZEIA export as the base, verify project_import_report missing dependencies, "
            "and import required prerequisites on the target system before opening a normal generated method. "
            "For the command corpus, regenerate with every sample disabled; do not copy an unrelated source file "
            "just to satisfy a context check."
        ),
        pattern=re.compile(
            r"FileNotFoundException|DirectoryNotFoundException|Could not find file|"
            r"Could not find a part of the path|Cannot find path|cannot open|could not open|failed to open|"
            r"(missing|not found|unresolved).{0,180}"
            r"(referenced file|file|path|reference|dependency|worktable|liquid class|labware object|worklist|\.gwl|\.csv|\.png|\.gif|\.jpg|\.vb|\.exe)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.zeia_import_failed",
        severity="blocking",
        category="import",
        title="FluentControl reports a ZEIA import/load failure",
        likely_workflow_defect=(
            "The generated project archive did not import cleanly, or FluentControl rejected one "
            "of its datastore/import payloads during load."
        ),
        suggested_fix=(
            "Check project_import_report, validation_report, and the audit trail for the imported ZEIA. "
            "Then repair the archive packaging/checksum/dependency issue and re-import the generated ZEIA."
        ),
        pattern=re.compile(
            r"(ZEIA|ExportImportArchive|ImportArchive|Import|import|imported from).{0,200}"
            r"(failed|error|exception|cannot|could not|invalid|aborted|rejected)|"
            r"VX_APPFR_016_005|Failed to import",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.checksum_recalculate",
        severity="high",
        category="checksum",
        title="FluentControl requires checksum recalculation",
        likely_workflow_defect="Generated datastore entries were edited without valid FluentControl checksum recomputation.",
        suggested_fix=(
            "Run checksum recomputation on a FluentControl machine or explicitly accept the in-app recalculation prompt "
            "before treating the ZEIA as import-clean. For generated XSCR XML, also check that text values were XML-escaped "
            "before stamping; raw text such as `A -> B` can pass offline XML parsing but fail FluentControl checksum load."
        ),
        pattern=re.compile(
            r"(InvalidChecksumException|ChecksumException|XML checksum error|unauthorized modification|"
            r"(checksum|recalculate|recalculation).{0,160}"
            r"(invalid|blank|required|prompt|object|datastore|error|unauthorized|failed)|"
            r"\bimport-clean\b.{0,80}(checksum|recompute))",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.adapter_rga_command",
        severity="blocking",
        category="rga_adapter",
        title="RGA/adapter labware command is not valid for FluentControl",
        likely_workflow_defect=(
            "The workflow synthesized an RGA/finger/adapter move that FluentControl does not support for the selected labware."
        ),
        suggested_fix=(
            "Reuse a mined source RGA/finger command pattern or convert the step to a manual/operator fallback; "
            "do not synthesize raw RGA command XML without source evidence."
        ),
        pattern=re.compile(
            r"(RGA|finger|adapter).{0,120}(invalid|unsupported|cannot|could not).{0,120}(labware|move|command)|"
            r"invalid adapter labware",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.if_else_branches_mismatched",
        severity="blocking",
        category="script_logic",
        title="Mismatching If-Else branches",
        likely_workflow_defect=(
            "A conditional group in the selected script has an invalid If/Else structure, commonly from "
            "a copied branch with a missing, duplicated, or incorrectly nested Else command."
        ),
        suggested_fix=(
            "Open the named script and compare the affected conditional group with its source pattern. "
            "Make each If/Else pair structurally balanced, regenerate the XSCR, and rerun the "
            "post-compile Script Editor validation."
        ),
        pattern=re.compile(r"Mismatching\s+If-Else\s+branches", re.IGNORECASE),
    ),
    DiagnosticRule(
        id="fluent_log.vb_script_compile_failed",
        severity="blocking",
        category="script_vb",
        title="Unable to load and compile VB script",
        likely_workflow_defect=(
            "An Execute VB Script command points to a missing, unreadable, or non-compiling VB file, "
            "or the VB script requires a reference unavailable on this FluentControl PC."
        ),
        suggested_fix=(
            "Verify the VB file exists at the exact referenced path, then compile or inspect it for syntax and "
            "reference errors. Stage the corrected file through the bundle manifest and rerun the script."
        ),
        pattern=re.compile(r"Unable to load and compile VB script", re.IGNORECASE),
    ),
    DiagnosticRule(
        id="fluent_log.resolvex_a200_command_unknown",
        severity="blocking",
        category="driver_command",
        title="ResolvexA200_Run command is unknown",
        likely_workflow_defect=(
            "The script calls the Resolvex A200 driver macro, but this FluentControl installation does not "
            "currently expose that macro or its driver module is not initialized for the active instrument."
        ),
        suggested_fix=(
            "Confirm the ResolvexA200 driver is installed, connected, and initialized on the target instrument PC. "
            "If it is present, copy the exact macro/module contract from a working source script rather than "
            "renaming or synthesizing the command."
        ),
        pattern=re.compile(r"(?:Command\s+['\"]?ResolvexA200_Run['\"]?\s+is\s+unknown|ResolvexA200_Run.{0,120}\bunknown\b)", re.IGNORECASE),
    ),
    DiagnosticRule(
        id="fluent_log.invalid_labware_selection",
        severity="blocking",
        category="labware_binding",
        title="Script command requires a valid labware selection",
        likely_workflow_defect=(
            "The command's selected labware is empty, absent from the loaded worktable, or incompatible with "
            "the command's expected labware type."
        ),
        suggested_fix=(
            "Select the intended labware in the failing command, verify it exists on the active worktable with "
            "the expected type, and include its referenced datastore object in the ZEIA before regenerating."
        ),
        pattern=re.compile(r"Select\s+a\s+valid\s+labware", re.IGNORECASE),
    ),
    DiagnosticRule(
        id="fluent_log.unknown_driver_command",
        severity="blocking",
        category="driver_command",
        title="FluentControl does not know a hardware driver command",
        likely_workflow_defect=(
            "The script contains a hardware driver command that is absent from the current FluentControl "
            "installation, it was opened while the target hardware was powered off/uninitialized, "
            "or it was opened on a non-instrument computer without that driver configured."
        ),
        suggested_fix=(
            "First confirm the relevant instrument hardware is powered on, connected, and initialized on the "
            "instrument PC. If the command still fails after that, use a source-mined native command or an "
            "explicit operator prompt; otherwise keep the source-authored hardware command unchanged."
        ),
        pattern=re.compile(
            r"Command\s+[\"“][^\"”]*(RGA|CGA|BCR|TransferLabware|ExecuteSingleVector)[^\"”]*[\"”]\s+is unknown|"
            r"corresponding driver is (available|installed|configured)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.gripper_fingers_not_mounted",
        severity="blocking",
        category="driver_command",
        title="An RGA command requires gripper fingers that are not mounted",
        likely_workflow_defect=(
            "An enabled ApplicationDriverMacro or movement command requested a gripper transfer, but the "
            "instrument reports no fingers mounted. This is a physical instrument precondition, not an XSCR "
            "serialization failure."
        ),
        suggested_fix=(
            "Mount and initialize the correct gripper fingers before running that command. For command-corpus "
            "debugging without the hardware configured, keep the RGA sample disabled and inspect it separately; "
            "do not bypass the driver safety check."
        ),
        pattern=re.compile(
            r"Unable to start transfer because no gripper fingers are mounted|"
            r"Please mount fingers to the arm",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.move_axis_drive_unresolved",
        severity="high",
        category="device_binding",
        title="MoveAxis command cannot resolve its AxisDrive device",
        likely_workflow_defect=(
            "A packaged subroutine contains MoveAxisCommandScriptStatement entries whose IdLabel or AvailableID "
            "does not resolve to a live AxisDrive in the current FluentControl session."
        ),
        suggested_fix=(
            "Inspect packaged/imported subroutines that contain MoveAxis commands "
            "(discover names from the active import, XSCR package, or log scope — do not assume lab-specific "
            "script names). First confirm the instrument/controller is powered on and initialized and that the "
            "referenced AxisDrive devices exist on this PC. If it still fails with hardware ready, replace those "
            "subroutines from a source export made on this instrument or convert the affected step to a "
            "manual/operator prompt."
        ),
        pattern=re.compile(
            r"(Error while creating the device|Object reference not set).{0,500}"
            r"MoveAxisCommandScriptStatement\.get_AxisDrive|"
            r"MoveAxisCommandScriptStatement\.get_AxisDrive.{0,500}"
            r"(Error while creating the device|Object reference not set)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.scanner_instance_binding",
        severity="blocking",
        category="device_alias",
        title="Hardware command is bound to a scanner/device instance that is not configured",
        likely_workflow_defect=(
            "A subroutine or generated command preserved a hard-coded USB/CGA/BCR scanner instance that does "
            "not resolve in the current FluentControl runtime, or the target scanner/hardware was not powered "
            "on and initialized."
        ),
        suggested_fix=(
            "First power on and initialize the relevant instrument/scanner on the instrument PC, then re-check. "
            "Only change the script if the binding still fails with hardware ready; otherwise preserve the "
            "existing subroutine/command."
        ),
        pattern=re.compile(
            r"(USB:TECAN|CGA|BCR|barcode|scanner).{0,160}not associated with a scanner instance|"
            r"not associated with a scanner instance.{0,160}(USB:TECAN|CGA|BCR|barcode|scanner)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.missing_worktable_labware",
        severity="blocking",
        category="worktable",
        title="Required labware is missing from the active worktable",
        likely_workflow_defect="Generated method requires labware that is absent from the source or target worktable layout.",
        suggested_fix="Compare protocol IR labware requirements with worktable_changes.md, then use the full source ZEIA worktable or approve a reviewed deck-layout change.",
        pattern=re.compile(r"(missing|required).{0,80}(labware|plate|rack|carrier).{0,80}(worktable|deck)|worktable.{0,80}(missing|required).{0,80}(labware|plate|rack|carrier)", re.IGNORECASE | re.DOTALL),
    ),
    DiagnosticRule(
        id="fluent_log.worktable_workspace_delta_missing",
        severity="blocking",
        category="worktable_metadata",
        title="Worktable prompt cannot load workspace delta metadata",
        likely_workflow_defect=(
            "FluentControl tried to load VxWorkspaceData/WorkspaceDeltas for a worktable prompt or "
            "subroutine workspace context, but the live workspace delta ID was missing or could not be "
            "resolved in the current session."
        ),
        suggested_fix=(
            "First check the generated bundle reports for the worktable workspace metadata transplant and "
            "confirm the referenced WorktableWorkspace exists on the target PC. If the bundle metadata is "
            "missing or empty, regenerate from the selected source script and keep the workspace-metadata "
            "gate enabled. If the metadata is present, rerun after a clean FluentControl restart/worktable "
            "load and treat repeated deltaId warnings as a target-session or source-workspace export issue."
        ),
        pattern=re.compile(
            r"(WorktableVXDataStoreManager|LoadWorkspaceDelta|workspace delta).{0,240}"
            r"(deltaId|ArgumentNullException|Value cannot be null)|"
            r"(deltaId|ArgumentNullException|Value cannot be null).{0,240}"
            r"(WorktableVXDataStoreManager|LoadWorkspaceDelta|workspace delta)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DiagnosticRule(
        id="fluent_log.unsupported_datastore_key_import_ignored",
        severity="medium",
        category="import",
        title="FluentControl ignored unsupported source datastore objects during import",
        likely_workflow_defect=(
            "The generated ZEIA packaged source-system datastore objects whose DataStoreKey is no "
            "longer importable in this FluentControl version. FluentControl ignores those objects, "
            "so shipping them only creates warnings; the referenced object must resolve from the "
            "target PC's installed system/library data instead."
        ),
        suggested_fix=(
            "Regenerate with the import-unsupported datastore filter enabled. The generated project "
            "report should list those objects under dependencies_not_packaged / target dependencies "
            "not packaged. Confirm the named liquid class/worktable/component exists on the target PC; "
            "if not, install/import it through the supported FluentControl mechanism rather than relying "
            "on the generated method ZEIA to import that legacy system object."
        ),
        pattern=re.compile(
            r"DataStoreKey\s+'(?:5|9|12)'\s+is\s+not\s+supported\s+anymore\.\s+"
            r"[0-9a-fA-F-]{36}\s+will\s+be\s+ignored\s+for\s+import",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)


def parse_fluent_log_file(path: Path) -> list[FluentLogRecord]:
    """Parse a FluentControl/VisionX log file into structured records."""
    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="replace")
    return parse_fluent_log_text(text, source=str(source))


def parse_fluent_log_text(text: str, *, source: str = "") -> list[FluentLogRecord]:
    """Parse line-oriented FluentControl logs while preserving nearby detail lines."""
    lines = _logical_log_lines(text)
    records: list[FluentLogRecord] = []
    current: dict[str, Any] | None = None
    for index, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if not line.strip():
            continue
        explicit_record = bool(_starts_log_record(line) or ERROR_ID_RE.search(line))
        if current is not None and not explicit_record and _is_detail_line(line):
            current["detail_lines"].append(line.strip()[:1000])
            current["raw_lines"].append(line[:1000])
            continue
        starts_record = explicit_record or _looks_like_error(line)
        if starts_record:
            if current is not None:
                records.append(_record_from_parts(current))
            current = _parse_record_header(line, source=source, line_number=index)
            continue
    if current is not None:
        records.append(_record_from_parts(current))
    return _records_with_thread_scope(records)


def _records_with_audit_import_context(
    records: Sequence[FluentLogRecord],
    audit_events: Sequence[Any],
) -> list[FluentLogRecord]:
    """Name otherwise-unattributed errors only when a recent audit import is unique."""
    associated: list[FluentLogRecord] = []
    for record in records:
        if record.script or not (record.error_id or _looks_like_error(_record_text(record))):
            associated.append(record)
            continue
        event = import_for_error(record.timestamp, audit_events)
        if event is None:
            associated.append(record)
            continue
        associated.append(
            replace(
                record,
                script=event.script,
                main_script=record.main_script or event.script,
                object_name=record.object_name or event.script,
                script_source="audit_import_timeline",
                script_context=f"{event.timestamp} | {event.archive}",
            )
        )
    return associated


def _records_with_thread_scope(records: Sequence[FluentLogRecord]) -> list[FluentLogRecord]:
    """Carry Scope stack script names onto later errors on the same ThreadId."""
    if not records:
        return []
    ordered = sorted(
        enumerate(records),
        key=lambda item: (_timestamp_to_epoch(item[1].timestamp), item[1].line_number or 0, item[0]),
    )
    carry: dict[str, tuple[float, FluentLogRecord]] = {}
    updates: dict[int, FluentLogRecord] = {}
    for index, record in ordered:
        thread_id = str(record.thread_id or "").strip()
        if record.script and record.script_source == "scope_stack" and thread_id:
            carry[thread_id] = (_timestamp_to_epoch(record.timestamp), record)
            continue
        if record.script or not thread_id:
            continue
        if not (record.error_id or _looks_like_error(_record_text(record))):
            continue
        previous = carry.get(thread_id)
        if previous is None:
            continue
        previous_time, previous_record = previous
        current_time = _timestamp_to_epoch(record.timestamp)
        if previous_time and current_time and abs(current_time - previous_time) > THREAD_SCOPE_WINDOW_SECONDS:
            continue
        updates[index] = replace(
            record,
            script=previous_record.script,
            main_script=previous_record.main_script or previous_record.script,
            script_source="thread_scope",
            script_context=previous_record.script_context or previous_record.script,
            object_name=record.object_name or previous_record.object_name or previous_record.script,
            script_line=record.script_line or previous_record.script_line,
        )
    return [updates.get(index, record) for index, record in enumerate(records)]


def _records_with_script_command_lines(
    records: Sequence[FluentLogRecord],
    command_index: Sequence[dict[str, Any]],
) -> list[FluentLogRecord]:
    """Pin script_line when an error's command hint uniquely matches XSCR LineNumber metadata."""
    if not command_index:
        return list(records)
    out: list[FluentLogRecord] = []
    for record in records:
        if record.script_line:
            out.append(record)
            continue
        hint = record.command_hint or _command_hint_from_text(_record_text(record))
        if not hint:
            out.append(record)
            continue
        matches = [
            entry
            for entry in command_index
            if _command_ids_match(str(entry.get("command_id") or ""), hint)
            and (
                not record.script
                or _script_names_match(str(entry.get("script") or ""), record.script)
                or _script_names_match(str(entry.get("script") or ""), record.main_script)
            )
        ]
        if not matches:
            out.append(replace(record, command_hint=hint) if hint and not record.command_hint else record)
            continue
        scripts = sorted({str(entry.get("script") or "") for entry in matches if entry.get("script")})
        lines = sorted({int(entry.get("line_number") or 0) for entry in matches if int(entry.get("line_number") or 0) > 0})
        updated = record
        if not updated.script and len(scripts) == 1:
            updated = replace(
                updated,
                script=scripts[0],
                main_script=updated.main_script or scripts[0],
                script_source=updated.script_source or "xscr_command_index",
                script_context=updated.script_context or f"command={hint}",
                object_name=updated.object_name or scripts[0],
            )
        if len(lines) == 1:
            updated = replace(
                updated,
                script_line=lines[0],
                command_hint=hint or updated.command_hint,
                script_source=updated.script_source or "xscr_command_index",
                script_context=(
                    updated.script_context
                    or f"command={hint} line={lines[0]}"
                ),
            )
        elif hint and not updated.command_hint:
            updated = replace(updated, command_hint=hint)
        out.append(updated)
    return out


def build_script_command_index(xscr_paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Index XSCR command ids to FluentControl script LineNumber metadata."""
    import defusedxml.ElementTree as ET

    entries: list[dict[str, Any]] = []
    for path in xscr_paths:
        try:
            root = ET.parse(Path(path)).getroot()
        except (OSError, ET.ParseError):
            continue
        script_name = Path(path).stem
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag in {"Object", "Objects", "Statements", "ScriptGroupDataV1", "Script"}:
                continue
            line_text = ""
            for child in list(element):
                child_tag = child.tag.rsplit("}", 1)[-1]
                if child_tag == "LineNumber" and (child.text or "").strip():
                    line_text = (child.text or "").strip()
                    break
            if not line_text:
                continue
            try:
                line_number = int(line_text)
            except ValueError:
                continue
            if line_number <= 0:
                continue
            entries.append(
                {
                    "script": script_name,
                    "command_id": tag,
                    "line_number": line_number,
                    "path": str(path),
                }
            )
    return entries


def diagnose_fluent_log_text(text: str, *, source: str = "") -> list[dict[str, Any]]:
    return [item.as_dict() for item in diagnose_fluent_log_records(parse_fluent_log_text(text, source=source))]


def diagnose_fluent_messages(messages: Iterable[str]) -> list[dict[str, Any]]:
    text = "\n".join(str(message) for message in messages if str(message).strip())
    return diagnose_fluent_log_text(text)


def diagnose_fluent_log_records(
    records: Sequence[FluentLogRecord],
    *,
    command_index: Sequence[dict[str, Any]] | None = None,
) -> list[FluentLogDiagnostic]:
    """Map parsed log records to known protocol-builder workflow defects."""
    if not records:
        return []
    diagnostic_records = _records_with_nearby_error_ids(records)
    text = "\n".join(_record_text(record) for record in diagnostic_records)
    error_ids = {record.error_id for record in diagnostic_records if record.error_id}
    diagnostics: list[FluentLogDiagnostic] = []
    for rule in DIAGNOSTIC_RULES:
        matched_records = [record for record in diagnostic_records if _rule_record_match(rule, record)]
        if not matched_records:
            continue
        display_records = _recent_records(matched_records or records)
        evidence = _evidence_for_rule(rule, display_records)
        matched_error_ids = {record.error_id for record in matched_records if record.error_id}
        suggested_fix = rule.suggested_fix
        if rule.id == "fluent_log.move_axis_drive_unresolved":
            suggested_fix = _enrich_move_axis_suggested_fix(
                suggested_fix,
                matched_records=matched_records,
                command_index=command_index,
            )
        diagnostics.append(
            FluentLogDiagnostic(
                id=rule.id,
                severity=rule.severity,
                category=rule.category,
                title=rule.title,
                likely_workflow_defect=rule.likely_workflow_defect,
                suggested_fix=suggested_fix,
                evidence=tuple(evidence),
                error_ids=tuple(sorted(matched_error_ids.intersection(rule.error_ids) or matched_error_ids)),
                records=tuple(record.as_dict() for record in display_records[:3]),
            )
        )
    if diagnostics:
        return _prune_stale_runtime_diagnostics(
            _sort_diagnostics_recent_first(_prune_generic_diagnostics(diagnostics))
        )
    if text.strip():
        display_records = _recent_records(records)
        return [
            FluentLogDiagnostic(
                id="fluent_log.unclassified",
                severity="medium",
                category="runtime",
                title="FluentControl log contains errors without a known mapping",
                likely_workflow_defect="The runtime failure is not yet covered by protocol-builder diagnostic rules.",
                suggested_fix="Keep the log excerpt with the runtime report and add a parser fixture once the root cause is known.",
                evidence=tuple(_compact([_single_line(_record_text(record)) for record in display_records])[:5]),
                error_ids=tuple(sorted(error_ids)),
                records=tuple(record.as_dict() for record in display_records[:3]),
            )
        ]
    return []


def _enrich_move_axis_suggested_fix(
    base_fix: str,
    *,
    matched_records: Sequence[FluentLogRecord],
    command_index: Sequence[dict[str, Any]] | None,
) -> str:
    """Append MoveAxis-related script names mined from log scope / imported XSCRs."""
    names = _move_axis_script_names(matched_records=matched_records, command_index=command_index)
    if not names:
        return base_fix
    listed = ", ".join(names[:8])
    suffix = "…" if len(names) > 8 else ""
    return (
        f"{base_fix.rstrip()} MoveAxis-related scripts from this import/log: "
        f"{listed}{suffix}."
    )


def _move_axis_script_names(
    *,
    matched_records: Sequence[FluentLogRecord],
    command_index: Sequence[dict[str, Any]] | None,
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        # Prefer bare ObjectName leaf (drop Method|… scope prefixes if present).
        leaf = text.split("|")[-1].strip() or text
        key = leaf.casefold()
        if key in seen:
            return
        seen.add(key)
        names.append(leaf)

    for record in matched_records:
        _add(record.script)
        _add(record.object_name)
        # main_script is useful context but often the top-level method; keep after leaf scripts.
    for record in matched_records:
        _add(record.main_script)
    for entry in command_index or ():
        command_id = str(entry.get("command_id") or "")
        if "moveaxis" not in command_id.casefold():
            continue
        _add(entry.get("script"))
    return names


def build_fluent_log_report(
    path: Path,
    *,
    audit_paths: Sequence[Path] = (),
    xscr_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    records = parse_fluent_log_file(path)
    audit_events = read_audit_import_events(audit_paths)
    records = _records_with_audit_import_context(records, audit_events)
    command_index = build_script_command_index(xscr_paths)
    records = _records_with_script_command_lines(records, command_index)
    diagnostics = diagnose_fluent_log_records(records, command_index=command_index)
    return {
        "diagnostic_version": LOG_DIAGNOSTIC_VERSION,
        "source": str(path),
        "record_count": len(records),
        "diagnostic_count": len(diagnostics),
        "audit_import_event_count": len(audit_events),
        "script_command_index_count": len(command_index),
        "records": [record.as_dict() for record in records],
        "diagnostics": [item.as_dict() for item in diagnostics],
    }


def discover_fluent_log_files(
    *,
    locations: Sequence[tuple[str | Path, str]] | None = None,
    since_hours: float = 48.0,
    max_files: int = 12,
) -> list[Path]:
    """Return recent FluentControl/VisionX logs from the common install paths."""
    cutoff = datetime.now() - timedelta(hours=since_hours) if since_hours > 0 else None
    candidates: list[tuple[float, Path]] = []
    for folder_raw, pattern in locations or DEFAULT_FLUENT_LOG_LOCATIONS:
        folder = Path(folder_raw)
        if not folder.exists():
            continue
        try:
            paths = folder.glob(pattern)
        except OSError:
            continue
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file():
                continue
            modified = datetime.fromtimestamp(stat.st_mtime)
            if cutoff is not None and modified < cutoff:
                continue
            candidates.append((stat.st_mtime, path.resolve()))
    candidates.sort(key=lambda item: item[0], reverse=True)
    limit = max(1, max_files)
    return [path for _, path in candidates[:limit]]


def build_latest_fluent_log_report(
    *,
    locations: Sequence[tuple[str | Path, str]] | None = None,
    since_hours: float = 48.0,
    max_files: int = 12,
    max_records: int = 80,
) -> dict[str, Any]:
    """Build diagnostics from the newest common FluentControl/VisionX log files."""
    files = discover_fluent_log_files(
        locations=locations,
        since_hours=since_hours,
        max_files=max_files,
    )
    records: list[FluentLogRecord] = []
    file_items: list[dict[str, Any]] = []
    for path in files:
        try:
            stat = path.stat()
            file_items.append(
                {
                    "path": str(path),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "size": stat.st_size,
                }
            )
            records.extend(record for record in parse_fluent_log_file(path) if _record_is_interesting(record))
        except OSError:
            continue
    records.sort(key=_record_sort_key, reverse=True)
    limited_records = records[: max(1, max_records)]
    diagnostic_records = sorted(records, key=_record_diagnostic_sort_key, reverse=True)
    diagnostics = diagnose_fluent_log_records(diagnostic_records[: max(200, max_records)])
    return {
        "diagnostic_version": LOG_DIAGNOSTIC_VERSION,
        "source": "latest FluentControl/VisionX logs",
        "searched_locations": [
            {"folder": str(folder), "pattern": pattern}
            for folder, pattern in (locations or DEFAULT_FLUENT_LOG_LOCATIONS)
        ],
        "file_count": len(file_items),
        "files": file_items,
        "record_count": len(limited_records),
        "diagnostic_count": len(diagnostics),
        "records": [record.as_dict() for record in limited_records],
        "diagnostics": [item.as_dict() for item in diagnostics],
    }


def render_fluent_log_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# FluentControl Log Diagnostics",
        "",
        f"- Source: `{report.get('source') or ''}`",
        f"- Parsed records: `{report.get('record_count', 0)}`",
        f"- Diagnostics: `{report.get('diagnostic_count', 0)}`",
        "",
    ]
    files = report.get("files") or []
    if files:
        lines.extend(["## Log Files Scanned", ""])
        for item in files[:12]:
            lines.append(
                f"- `{item.get('path')}`"
                f" (modified `{item.get('modified')}`, {item.get('size', 0)} bytes)"
            )
        lines.append("")
    searched = report.get("searched_locations") or []
    if searched:
        lines.extend(["## Default Locations", ""])
        for item in searched:
            lines.append(f"- `{item.get('folder')}\\{item.get('pattern')}`")
        lines.append("")
    diagnostics = report.get("diagnostics") or []
    if diagnostics:
        lines.extend(["## Diagnostics", ""])
        for item in diagnostics:
            lines.extend(
                [
                    f"### {item.get('title')}",
                    "",
                    f"- Severity: `{item.get('severity')}`",
                    f"- Category: `{item.get('category')}`",
                    f"- Likely workflow defect: {item.get('likely_workflow_defect')}",
                    f"- Suggested fix: {item.get('suggested_fix')}",
                ]
            )
            evidence = item.get("evidence") or []
            if evidence:
                lines.append("- Evidence:")
                for value in evidence[:8]:
                    lines.append(f"  - {value}")
            lines.append("")
    else:
        lines.extend(["## Diagnostics", "", "- No FluentControl error diagnostics were found.", ""])
    return "\n".join(lines).rstrip() + "\n"


def diagnostics_to_findings(diagnostics: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for item in diagnostics:
        findings.append(
            {
                "id": item.get("id") or "fluent_log.unclassified",
                "severity": item.get("severity") or "medium",
                "category": item.get("category") or "runtime",
                "title": item.get("title") or "FluentControl runtime diagnostic",
                "evidence": item.get("evidence") or [],
                "next_steps": [item.get("suggested_fix")] if item.get("suggested_fix") else [],
                "details": {
                    "likely_workflow_defect": item.get("likely_workflow_defect") or "",
                    "error_ids": item.get("error_ids") or [],
                    "records": item.get("records") or [],
                },
            }
        )
    return findings


def _parse_record_header(line: str, *, source: str, line_number: int) -> dict[str, Any]:
    attrs = _xml_attrs_from_line(line)
    timestamp_match = TIMESTAMP_RE.search(line)
    error_match = ERROR_ID_RE.search(line)
    level_match = LEVEL_RE.search(line)
    module = _module_from_line(line)
    message = attrs.get("Message") or line.strip()
    if timestamp_match and "Message" not in attrs:
        message = message.replace(timestamp_match.group("timestamp"), "", 1).strip(" -|[]")
    searchable = "\n".join(part for part in (message, line) if part)
    attribution = _script_attribution_from_text(searchable)
    script_line = _script_line_from_text(searchable)
    thread_id = _thread_id_from_text(line)
    command_hint = _command_hint_from_text(searchable)
    return {
        "timestamp": attrs.get("TimeStamp") or (timestamp_match.group("timestamp") if timestamp_match else ""),
        "application": _application_from_line(line),
        "module": module or attrs.get("Channel", ""),
        "level": (attrs.get("Severity") or (level_match.group("level").upper() if level_match else "")).upper(),
        "error_id": error_match.group(1) if error_match else "",
        "script": attribution.get("script") or "",
        "main_script": attribution.get("main_script") or "",
        "script_source": attribution.get("script_source") or "",
        "script_context": attribution.get("script_context") or "",
        "script_line": script_line,
        "thread_id": thread_id,
        "command_hint": command_hint,
        "object_name": attribution.get("script") or "",
        "message": message[:1000],
        "detail_lines": [],
        "raw_lines": [line[:1000]],
        "source": source,
        "line_number": line_number,
    }


def _record_from_parts(parts: dict[str, Any]) -> FluentLogRecord:
    detail_text = "\n".join(parts.get("detail_lines") or ())
    searchable = "\n".join(
        part
        for part in (
            parts.get("message") or "",
            detail_text,
            "\n".join(parts.get("raw_lines") or ()),
        )
        if part
    )
    attribution = {
        "script": parts.get("script") or "",
        "main_script": parts.get("main_script") or "",
        "script_source": parts.get("script_source") or "",
        "script_context": parts.get("script_context") or "",
    }
    if not attribution["script"]:
        attribution = _script_attribution_from_text(searchable) or attribution
    script_line = int(parts.get("script_line") or 0) or _script_line_from_text(searchable)
    command_hint = parts.get("command_hint") or _command_hint_from_text(searchable)
    thread_id = parts.get("thread_id") or _thread_id_from_text(searchable)
    return FluentLogRecord(
        timestamp=parts.get("timestamp") or "",
        application=parts.get("application") or "",
        module=parts.get("module") or "",
        level=parts.get("level") or "",
        error_id=parts.get("error_id") or "",
        script=attribution.get("script") or "",
        main_script=attribution.get("main_script") or "",
        script_source=attribution.get("script_source") or "",
        script_context=attribution.get("script_context") or "",
        script_line=script_line,
        thread_id=thread_id or "",
        command_hint=command_hint or "",
        object_name=parts.get("object_name") or attribution.get("script") or "",
        message=parts.get("message") or "",
        detail_lines=tuple(parts.get("detail_lines") or ()),
        raw_context="\n".join(parts.get("raw_lines") or ()),
        source=parts.get("source") or "",
        line_number=int(parts.get("line_number") or 0),
    )


def _starts_log_record(line: str) -> bool:
    return bool(
        TIMESTAMP_RE.search(line)
        or re.match(r"\s*\[[^\]]+\]\s*\[[^\]]+\]", line)
        or re.match(r"\s*<Log\b", line)
    )


def _looks_like_error(line: str) -> bool:
    searchable = re.sub(r"no checksum calculation here", "", line, flags=re.IGNORECASE)
    return bool(
        re.search(
            r"\b(error|exception|failed|failure|unable|compile|mismatch|mismatching|invalid|missing|unknown|driver|scanner|gripper|labware|undefined|undeclared|checksum|recalculate|subroutine|deltaId|DataStoreKey)\b",
            searchable,
            re.IGNORECASE,
        )
        or re.search(
            r"(FileNotFoundException|DirectoryNotFoundException|could not find file|could not find a part of the path|cannot find path)",
            searchable,
            re.IGNORECASE,
        )
        or "lower range" in searchable.lower()
    )


def _is_detail_line(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped.startswith(
            (
                "at ",
                "--->",
                "Caused by",
                "System.",
                "Tecan.",
                "<",
                "Application:",
                "Description:",
                "Reason:",
                "Solution:",
                "Error occurred:",
                "Link information:",
            )
        )
        or re.search(
            r"(VariableDefinitionHelper|xsi:type|i:type|d3p1|namespace|stack trace|LoadWorkspaceDelta|"
            r"deltaId|WorktableVXDataStoreManager|DataStoreKey|ignored for import|VxWorkspaceData|"
            r"workspaceName|VariableDeclarations|Expecting element|Encountered 'Element')",
            stripped,
            re.IGNORECASE,
        )
    )


def _application_from_line(line: str) -> str:
    lower = line.lower()
    if "script editor" in lower or "scedt" in lower:
        return "Script Editor"
    if "fluentcontrol" in lower or "fluent control" in lower:
        return "FluentControl"
    if "visionx" in lower or "vx_" in lower:
        return "VisionX"
    return ""


def _module_from_line(line: str) -> str:
    attrs = _xml_attrs_from_line(line)
    if attrs.get("Channel"):
        return attrs["Channel"]
    for match in MODULE_BRACKET_RE.finditer(line):
        value = match.group("value").strip()
        if value and not TIMESTAMP_RE.search(value) and not LEVEL_RE.fullmatch(value):
            return value
    if "Script Editor" in line:
        return "Script Editor"
    return ""


def _script_from_line(line: str) -> str:
    attribution = _script_attribution_from_text(line)
    return str(attribution.get("script") or "")


def _script_attribution_from_text(text: str) -> dict[str, str]:
    scope = _parse_scope_stack(text)
    if scope is not None:
        return {
            "script": scope["leaf"],
            "main_script": scope["main"],
            "script_source": "scope_stack",
            "script_context": scope["raw"],
        }
    match = SCRIPT_RE.search(text)
    if match:
        value = match.group("value").strip()[:200]
        return {
            "script": value,
            "main_script": value,
            "script_source": "message",
            "script_context": value,
        }
    for pattern in SCRIPT_NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            value = match.group("value").strip().rstrip(".")[:200]
            return {
                "script": value,
                "main_script": value,
                "script_source": "message",
                "script_context": value,
            }
    return {}


def _parse_scope_stack(text: str) -> dict[str, str] | None:
    match = SCOPE_STACK_RE.search(text)
    if not match:
        return None
    body = match.group("body")
    body = re.split(r"(?:&#|Value:|Expression:)", body, maxsplit=1)[0]
    names: list[str] = []
    for part in body.split("|"):
        cleaned = part.strip().rstrip(".")
        if not cleaned or cleaned.isdigit():
            continue
        names.append(cleaned[:200])
    if not names:
        return None
    return {
        "main": names[0],
        "leaf": names[-1],
        "raw": " > ".join(names),
    }


def _script_line_from_text(text: str) -> int:
    match = SCRIPT_LINE_RE.search(text)
    if not match:
        return 0
    try:
        return int(match.group("line"))
    except ValueError:
        return 0


def _thread_id_from_text(text: str) -> str:
    match = THREAD_ID_RE.search(text)
    return match.group("value") if match else ""


def _command_hint_from_text(text: str) -> str:
    match = COMMAND_HINT_RE.search(text)
    if not match:
        return ""
    return (match.group(1) or match.group("quoted") or match.group("token") or "").strip()


def _command_ids_match(command_id: str, hint: str) -> bool:
    left = re.sub(r"[^a-z0-9]", "", command_id.lower())
    right = re.sub(r"[^a-z0-9]", "", hint.lower())
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    # Class names like MoveAxisCommandScriptStatement vs MoveAxisCommandDataV1
    for token in ("moveaxis", "resolvexa200", "setvariable", "subroutine", "rup"):
        if token in left and token in right:
            return True
    return False


def _script_names_match(left: str, right: str) -> bool:
    a = left.strip().lower().rstrip(".")
    b = right.strip().lower().rstrip(".")
    return bool(a and b and (a == b or a in b or b in a))


def _rule_record_match(rule: DiagnosticRule, record: FluentLogRecord) -> bool:
    text = _record_text(record)
    if rule.id == "fluent_log.adapter_rga_command" and re.search(r"\bchecksum\b|InvalidChecksumException", text, re.IGNORECASE):
        return False
    if rule.id == "fluent_log.zeia_import_failed" and re.search(
        r"DataStoreKey\s+'(?:5|9|12)'\s+is\s+not\s+supported\s+anymore",
        text,
        re.IGNORECASE,
    ):
        return False
    if rule.id == "fluent_log.checksum_recalculate" and re.search(r"no checksum calculation here", text, re.IGNORECASE):
        real_checksum_failure = re.search(
            r"InvalidChecksumException|ChecksumException|XML checksum error|unauthorized modification|"
            r"checksum.{0,120}(invalid|required|prompt|error|unauthorized|failed|recalculat)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if not real_checksum_failure:
            return False
    if rule.error_ids and record.error_id not in rule.error_ids:
        return False
    return bool(rule.pattern.search(text))


def _records_with_nearby_error_ids(records: Sequence[FluentLogRecord]) -> list[FluentLogRecord]:
    out: list[FluentLogRecord] = []
    items = list(records)
    script_candidates = sorted(
        ((index, record) for index, record in enumerate(items) if record.script),
        key=lambda item: _timestamp_to_epoch(item[1].timestamp),
    )
    script_candidate_timestamps = [_timestamp_to_epoch(record.timestamp) for _, record in script_candidates]
    for index, record in enumerate(items):
        if not record.error_id and not _looks_like_error(_record_text(record)):
            out.append(record)
            continue
        record = _record_with_nearby_script(
            items,
            index,
            record,
            script_candidates,
            script_candidate_timestamps,
        )
        if record.error_id:
            out.append(record)
            continue
        neighbor = _nearest_error_id_record(items, index)
        if neighbor is None:
            out.append(record)
            continue
        out.append(
            FluentLogRecord(
                timestamp=record.timestamp,
                application=record.application,
                module=record.module,
                level=record.level,
                error_id=neighbor.error_id,
                script=record.script or neighbor.script,
                main_script=record.main_script or neighbor.main_script,
                script_source=record.script_source or neighbor.script_source,
                script_context=record.script_context or neighbor.script_context,
                script_line=record.script_line or neighbor.script_line,
                thread_id=record.thread_id or neighbor.thread_id,
                command_hint=record.command_hint or neighbor.command_hint,
                object_name=record.object_name or neighbor.object_name,
                message=record.message,
                detail_lines=record.detail_lines,
                raw_context="\n".join(
                    value
                    for value in (
                        record.raw_context,
                        f"Nearby error dialog: {_single_line(_record_text(neighbor))}",
                    )
                    if value
                ),
                source=record.source,
                line_number=record.line_number,
            )
        )
    return out


def _record_with_nearby_script(
    records: Sequence[FluentLogRecord],
    index: int,
    record: FluentLogRecord,
    script_candidates: Sequence[tuple[int, FluentLogRecord]],
    script_candidate_timestamps: Sequence[float],
) -> FluentLogRecord:
    """Attach a nearby named script to an error emitted by a child command."""
    if record.script:
        return record
    neighbor = _nearest_script_record(records, index, script_candidates, script_candidate_timestamps)
    if neighbor is None:
        return record
    return replace(
        record,
        script=neighbor.script,
        main_script=record.main_script or neighbor.main_script or neighbor.script,
        script_source=record.script_source or "nearby_script",
        script_context=record.script_context or neighbor.script_context or neighbor.script,
        object_name=record.object_name or neighbor.object_name,
    )


def _nearest_script_record(
    records: Sequence[FluentLogRecord],
    index: int,
    script_candidates: Sequence[tuple[int, FluentLogRecord]],
    script_candidate_timestamps: Sequence[float],
) -> FluentLogRecord | None:
    current = records[index]
    candidates: list[tuple[float, int, FluentLogRecord]] = []
    current_timestamp = _timestamp_to_epoch(current.timestamp)
    candidate_records = script_candidates
    if current_timestamp:
        start = bisect_left(script_candidate_timestamps, current_timestamp - NEARBY_SCRIPT_WINDOW_SECONDS)
        end = bisect_right(script_candidate_timestamps, current_timestamp + NEARBY_SCRIPT_WINDOW_SECONDS)
        candidate_records = script_candidates[start:end]
    for neighbor_index, candidate in candidate_records:
        if neighbor_index == index or not candidate.script or candidate.source != current.source:
            continue
        line_gap = abs((candidate.line_number or 0) - (current.line_number or 0))
        candidate_timestamp = _timestamp_to_epoch(candidate.timestamp)
        if candidate_timestamp and current_timestamp:
            time_gap = abs(candidate_timestamp - current_timestamp)
            if time_gap > NEARBY_SCRIPT_WINDOW_SECONDS:
                continue
        else:
            time_gap = 0.0
            if current.line_number and candidate.line_number and line_gap > 80:
                continue
        candidates.append((time_gap, line_gap, candidate))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _nearest_error_id_record(records: Sequence[FluentLogRecord], index: int) -> FluentLogRecord | None:
    source = records[index].source
    timestamp = _timestamp_to_epoch(records[index].timestamp)
    base_line = records[index].line_number or 0
    candidates: list[tuple[float, int, FluentLogRecord]] = []
    for direction in (-1, 1):
        neighbor_index = index + direction
        scanned = 0
        while 0 <= neighbor_index < len(records) and scanned < 80:
            other = records[neighbor_index]
            scanned += 1
            neighbor_index += direction
            line_gap = abs((other.line_number or 0) - base_line)
            if base_line and other.line_number and line_gap > 80:
                break
            if not other.error_id:
                continue
            if source and other.source and other.source != source:
                continue
            other_timestamp = _timestamp_to_epoch(other.timestamp)
            time_gap = abs(other_timestamp - timestamp) if timestamp and other_timestamp else 0.0
            if timestamp and other_timestamp and time_gap > 10:
                continue
            candidates.append((time_gap, line_gap, other))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _prune_generic_diagnostics(diagnostics: list[FluentLogDiagnostic]) -> list[FluentLogDiagnostic]:
    ids = {item.id for item in diagnostics}
    specific_script_editor_ids = {
        "fluent_log.rup_worktable_variable_labware_index",
        "fluent_log.scriptgroup_statement_list_invalid_xml_node",
        "fluent_log.variable_definition_namespace",
    }
    if ids.intersection(specific_script_editor_ids):
        return [item for item in diagnostics if item.id != "fluent_log.script_editor_load_failed"]
    return diagnostics


def _recent_records(records: Sequence[FluentLogRecord]) -> list[FluentLogRecord]:
    return sorted(records, key=_record_recency_key, reverse=True)


def _record_recency_key(record: FluentLogRecord) -> tuple[float, int]:
    return (_timestamp_to_epoch(record.timestamp), record.line_number or 0)


def _sort_diagnostics_recent_first(diagnostics: list[FluentLogDiagnostic]) -> list[FluentLogDiagnostic]:
    return sorted(diagnostics, key=_diagnostic_recency_key, reverse=True)


def _diagnostic_recency_key(diagnostic: FluentLogDiagnostic) -> tuple[float, int]:
    latest = (0.0, 0)
    for record in diagnostic.records:
        key = (_timestamp_to_epoch(str(record.get("timestamp") or "")), int(record.get("line_number") or 0))
        if key > latest:
            latest = key
    return latest


def _prune_stale_runtime_diagnostics(
    diagnostics: list[FluentLogDiagnostic],
) -> list[FluentLogDiagnostic]:
    if len(diagnostics) < 2:
        return diagnostics
    newest = diagnostics[0]
    newest_time = _diagnostic_recency_key(newest)[0]
    if newest_time <= 0 or newest.category not in CURRENT_RUNTIME_CATEGORIES:
        return diagnostics
    retained: list[FluentLogDiagnostic] = []
    for diagnostic in diagnostics:
        timestamp = _diagnostic_recency_key(diagnostic)[0]
        if timestamp <= 0 or newest_time - timestamp <= CURRENT_RUNTIME_DIAGNOSTIC_WINDOW_SECONDS:
            retained.append(diagnostic)
    return retained or diagnostics


def _record_text(record: FluentLogRecord) -> str:
    return "\n".join(
        value
        for value in [
            record.message,
            *record.detail_lines,
            record.raw_context,
        ]
        if value
    )


def _evidence_for_rule(rule: DiagnosticRule, records: Sequence[FluentLogRecord]) -> list[str]:
    evidence = []
    for record in records:
        text = _single_line(_record_text(record))
        if not text:
            continue
        if record.error_id:
            text = f"{record.error_id}: {text}"
        evidence.append(text)
    return _compact(evidence)[:8]


def _single_line(text: str) -> str:
    return " ".join(str(text).split())[:1000]


def _compact(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _xml_attrs_from_line(line: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in XML_ATTR_RE.finditer(line):
        attrs[match.group("name")] = _decode_xml_text(match.group("value"))
    return attrs


def _logical_log_lines(text: str) -> list[str]:
    """Split ULF/XML records even when the logging server wrote one huge line."""
    lines: list[str] = []
    for raw in text.splitlines():
        if "<Log " not in raw:
            lines.append(raw)
            continue
        prefix, rest = raw.split("<Log ", 1)
        if prefix.strip():
            lines.append(prefix)
        for part in re.split(r"(?=<Log\b)", "<Log " + rest):
            if part.strip():
                lines.append(part)
    return lines


def _decode_xml_text(value: str) -> str:
    return (
        value.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&#xD;&#xA;", "\n")
        .replace("&#xD;", "\n")
        .replace("&#xA;", "\n")
    )


def _record_is_interesting(record: FluentLogRecord) -> bool:
    text = _record_text(record)
    return bool(
        record.error_id
        or record.level in {"ERROR", "FATAL", "CRITICAL", "WARN", "WARNING"}
        or _looks_like_error(text)
    )


def _record_sort_key(record: FluentLogRecord) -> tuple[float, str, int]:
    return (_timestamp_to_epoch(record.timestamp), record.source, record.line_number)


def _record_diagnostic_sort_key(record: FluentLogRecord) -> tuple[int, float, str, int]:
    return (_record_diagnostic_priority(record), *_record_sort_key(record))


def _record_diagnostic_priority(record: FluentLogRecord) -> int:
    text = _record_text(record)
    if record.level in {"ERROR", "FATAL", "CRITICAL"}:
        return 5
    if record.error_id:
        return 4
    if re.search(r"(InvalidChecksumException|XML checksum error|Unable to load|defined with different scopes|LoadWorkspaceDelta|deltaId)", text, re.IGNORECASE):
        return 4
    if record.level in {"WARN", "WARNING"}:
        return 2
    return 1


def _timestamp_to_epoch(value: str) -> float:
    cleaned = str(value or "").strip().replace(",", ".")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
