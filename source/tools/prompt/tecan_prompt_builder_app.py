"""Interactive prompt builder backed by shared ZEIA/project inspection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOURCE_ROOT.parent if SOURCE_ROOT.name == "source" else SOURCE_ROOT
PROTOCOL_BUILDER = SOURCE_ROOT / "03-protocol-builder"
PROJECT_READER = SOURCE_ROOT / "01-project-reader"

PATTERN_INDEX_DB = (
    REPO_ROOT
    / "ready-to-import"
    / "_shared"
    / "temp_files"
    / "build"
    / "tecan_project_index.sqlite"
)
PROMPT_OUT = (
    REPO_ROOT
    / "ready-to-import"
    / "_shared"
    / "temp_files"
    / "build"
    / "codex_prompt.txt"
)
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "vendor",
    "ready-to-import",
}
BACK_WORDS = {"b", "back"}
BACK_ALLOWED = False


class BackRequested(Exception):
    """Raised when the user asks to revisit the previous prompt."""


class CancelRequested(Exception):
    """Raised when the user cancels the prompt builder."""


@dataclass
class ScriptChoice:
    name: str
    source: str = ""
    entry: str = ""
    command_count: int = 0
    labware: list[str] = field(default_factory=list)
    liquid_classes: list[str] = field(default_factory=list)
    worktables: list[str] = field(default_factory=list)


@dataclass
class SourceChoice:
    kind: str
    label: str
    name: str = ""
    path: str = ""
    manifest: dict[str, Any] | None = None
    scripts: list[ScriptChoice] = field(default_factory=list)
    zeia_paths: list[str] = field(default_factory=list)
    labware: list[str] = field(default_factory=list)
    liquid_classes: list[str] = field(default_factory=list)
    worktables: list[str] = field(default_factory=list)


@dataclass
class SetupStatus:
    status: str
    summary: str
    report: Path
    install_attempted: bool = False
    detail: str = ""


@dataclass
class PatternIndexStatus:
    path: Path
    exists: bool = False
    pattern_count: int = 0
    patterns: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Codex prompt for a Tecan protocol request.")
    parser.add_argument("--detect-only", action="store_true", help="print detected contexts/ZEIA files and exit")
    parser.add_argument("--skip-setup-check", action="store_true", help="do not run protocol-builder doctor preflight")
    args = parser.parse_args()

    contexts = find_existing_contexts()
    zeia_files = find_zeia_files()
    if args.detect_only:
        print_detection_summary(contexts, zeia_files)
        return 0

    print_header(contexts, zeia_files)
    setup_status = (
        SetupStatus(
            status="skipped",
            summary="Protocol-builder setup check was skipped by the prompt builder.",
            report=PROTOCOL_BUILDER / "build" / "doctor.md",
        )
        if args.skip_setup_check
        else run_setup_preflight()
    )
    pattern_index = detect_pattern_index()
    print_pattern_index_status(pattern_index)
    request = collect_request(contexts, zeia_files, setup_status, pattern_index)

    prompt = build_prompt(request)
    PROMPT_OUT.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_OUT.write_text(prompt + "\n", encoding="utf-8")
    copied = copy_to_clipboard(prompt)

    print("\n" + bar())
    print("Generated Codex prompt")
    print(bar())
    print(prompt)
    print(bar())
    print(f"\nSaved prompt: {PROMPT_OUT}")
    if copied:
        print("Copied to clipboard. Paste it into Codex when ready.")
    else:
        print("Could not access the clipboard. Open the saved prompt file and copy it manually.")
    return 0


def collect_request(
    contexts: list[SourceChoice],
    zeia_files: list[Path],
    setup_status: SetupStatus,
    pattern_index: PatternIndexStatus,
) -> dict[str, Any]:
    state: dict[str, Any] = {"setup_status": setup_status, "pattern_index": pattern_index}
    step_keys = [
        "source", "script", "script_name", "intent", "labware_roles", "source_wells",
        "destination_wells", "volumes", "liquid_class", "tip_strategy", "worktable_policy",
        "outputs", "extra_notes",
    ]

    def choose_source() -> None:
        source = ask_source(contexts, zeia_files)
        enrich_source_from_selection(source)
        state["source"] = source
        print_source_summary(source)

    def choose_script() -> None:
        state["script"] = ask_script(state["source"].scripts)

    def choose_script_name() -> None:
        state["script_name"] = ask_optional("New edited script name")

    def choose_intent() -> None:
        state["intent"] = ask_required("What should the new script do?")

    def choose_labware() -> None:
        state["labware_roles"] = ask_labware_roles(state["source"].labware)

    def choose_source_wells() -> None:
        state["source_wells"] = ask_wells_or_positions(
            "Where should the liquid come FROM?",
            ["Same wells/positions used by the selected source script", "A1:H12", "All wells", "Column 1", "Columns 1-12", "Let AI infer from the source script", "Type custom wells/positions"],
            custom_prompt="Enter source wells/positions",
            ai_value="Let AI infer source wells/positions from the source script",
        )

    def choose_destination_wells() -> None:
        state["destination_wells"] = ask_wells_or_positions(
            "Where should the liquid go TO?",
            ["Same positions as source", "A1:H12", "All wells", "Next empty wells", "Column 1", "Columns 1-12", "Let AI infer from the source script", "Type custom wells/positions"],
            custom_prompt="Enter destination wells/positions",
            ai_value="Let AI infer destination wells/positions from the source script",
        )

    def choose_volume() -> None:
        state["volumes"] = ask_volume()

    def choose_liquid_class() -> None:
        state["liquid_class"] = ask_liquid_class(state["source"].liquid_classes)

    def choose_tip_strategy() -> None:
        state["tip_strategy"] = ask_tip_strategy()

    def choose_worktable_policy() -> None:
        state["worktable_policy"] = ask_worktable_policy()

    def choose_outputs() -> None:
        state["outputs"] = ask_output_artifacts()

    def choose_extra_notes() -> None:
        state["extra_notes"] = ask_optional("Any extra constraints or notes (optional)")

    steps = [choose_source, choose_script, choose_script_name, choose_intent, choose_labware, choose_source_wells, choose_destination_wells, choose_volume, choose_liquid_class, choose_tip_strategy, choose_worktable_policy, choose_outputs, choose_extra_notes]
    index = 0
    while index < len(steps):
        set_back_allowed(index > 0)
        try:
            steps[index]()
            index += 1
        except BackRequested:
            index = max(0, index - 1)
            clear_answers_after(state, step_keys, index)

    labware_roles = state["labware_roles"]
    return {
        "source": state["source"],
        "script": state["script"],
        "script_name": state["script_name"],
        "intent": state["intent"],
        "source_labware": labware_roles["source_labware"],
        "destination_labware": labware_roles["destination_labware"],
        "tips_labware": labware_roles.get("tips_labware", ""),
        "ai_should_choose_labware": labware_roles.get("ai_should_choose_labware", ""),
        "source_wells": state["source_wells"],
        "destination_wells": state["destination_wells"],
        "volumes": state["volumes"],
        "liquid_class": state["liquid_class"],
        "tip_strategy": state["tip_strategy"],
        "worktable_policy": state["worktable_policy"],
        "outputs": state["outputs"],
        "extra_notes": state["extra_notes"],
        "setup_status": setup_status,
        "pattern_index": pattern_index,
    }


def clear_answers_after(state: dict[str, Any], step_keys: list[str], current_index: int) -> None:
    for key in step_keys[current_index:]:
        state.pop(key, None)


def set_back_allowed(value: bool) -> None:
    global BACK_ALLOWED
    BACK_ALLOWED = value


def print_header(contexts: list[SourceChoice], zeia_files: list[Path]) -> None:
    clear_screen()
    print(bar())
    print("Tecan Codex Prompt Builder")
    print(bar())
    print("This will ask the minimum protocol questions, then copy a ready-to-paste Codex prompt.")
    print(f"Detected: {len(contexts)} imported context(s), {len(zeia_files)} ZEIA file(s).")
    print("Use 0 to go back after the first question, or cancel from the first question.")
    print(bar())


def run_setup_preflight() -> SetupStatus:
    report = PROTOCOL_BUILDER / "build" / "doctor.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    print("\nProtocol-builder setup")
    print("----------------------")
    print("Checking local dependencies...")
    doctor = run_protocol_builder_doctor(report, install_missing=False)
    if doctor.returncode == 0:
        print("Setup check passed.")
        print(f"Doctor report: {relative(report)}")
        return SetupStatus("passed", "Protocol-builder setup check already passed.", report)

    print("Something is missing. Installing local dependencies now.")
    install = run_protocol_builder_doctor(report, install_missing=True)
    if install.returncode == 0:
        print("Dependencies are installed and setup check passed.")
        print(f"Doctor report: {relative(report)}")
        return SetupStatus(
            "installed",
            "Prompt builder installed missing protocol-builder dependencies, then setup check passed.",
            report,
            install_attempted=True,
        )

    detail = trim_process_output(install.stdout, install.stderr)
    print("Setup check did not pass. The prompt will include this warning.")
    if detail:
        print(detail)
    print(f"Doctor report: {relative(report)}")
    return SetupStatus(
        "failed",
        "Protocol-builder setup preflight failed.",
        report,
        install_attempted=True,
        detail=detail,
    )


def run_protocol_builder_doctor(report: Path, *, install_missing: bool) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "fluent_pipeline.cli",
        "doctor",
        "--report",
        str(report),
    ]
    if install_missing:
        command.insert(4, "--install-missing")
    return subprocess.run(
        command,
        cwd=PROTOCOL_BUILDER,
        capture_output=True,
        text=True,
        check=False,
    )


def trim_process_output(stdout: str, stderr: str, *, limit: int = 900) -> str:
    text = "\n".join(part.strip() for part in (stderr, stdout) if part.strip())
    return text if len(text) <= limit else text[-limit:]


def detect_pattern_index() -> PatternIndexStatus:
    status = PatternIndexStatus(path=PATTERN_INDEX_DB, exists=PATTERN_INDEX_DB.exists())
    if not status.exists:
        return status
    try:
        conn = sqlite3.connect(PATTERN_INDEX_DB)
        conn.row_factory = sqlite3.Row
        try:
            status.pattern_count = int(
                conn.execute("SELECT COUNT(*) AS count FROM script_patterns").fetchone()["count"]
            )
            rows = conn.execute(
                """
                SELECT id, pattern_type, name, source_script, start_command_index, end_command_index
                FROM script_patterns
                ORDER BY
                    CASE pattern_type
                        WHEN 'aspirate' THEN 0
                        WHEN 'dispense' THEN 1
                        WHEN 'pick_up_tips' THEN 2
                        WHEN 'mix' THEN 3
                        WHEN 'wash' THEN 4
                        WHEN 'read_worklist' THEN 5
                        WHEN 'initialize_device' THEN 6
                        WHEN 'load_labware' THEN 7
                        ELSE 8
                    END,
                    id
                LIMIT 8
                """
            ).fetchall()
            status.patterns = [dict(row) for row in rows]
        finally:
            conn.close()
    except Exception as exc:
        status.error = str(exc)
    return status


def print_pattern_index_status(status: PatternIndexStatus) -> None:
    print("\nPattern index")
    print("-------------")
    if not status.exists:
        print(f"No persistent index found yet: {relative(status.path)}")
        return
    if status.error:
        print(f"Found index, but could not read patterns: {relative(status.path)}")
        print(status.error)
        return
    print(f"Found persistent index: {relative(status.path)}")
    print(f"Mined patterns: {status.pattern_count}")
    for pattern in status.patterns[:4]:
        print(
            f"- ID {pattern['id']}: {pattern['pattern_type']} "
            f"({pattern['source_script']}, commands {pattern['start_command_index']}-{pattern['end_command_index']})"
        )


def find_existing_contexts() -> list[SourceChoice]:
    out: list[SourceChoice] = []
    projects_dir = PROTOCOL_BUILDER / "projects"
    for manifest_path in sorted(projects_dir.glob("*/manifest.json")):
        out.append(source_from_manifest_file(manifest_path, kind="existing_context"))
    for manifest_path in sorted((projects_dir / ".collections").glob("*/manifest.json")):
        out.append(source_from_manifest_file(manifest_path, kind="existing_collection"))
    return out


def source_from_manifest_file(manifest_path: Path, *, kind: str) -> SourceChoice:
    return source_from_manifest(load_json(manifest_path), kind=kind, root=manifest_path.parent)


def source_from_manifest(manifest: dict[str, Any], *, kind: str, root: Path | None = None) -> SourceChoice:
    label_kind = "collection" if kind == "existing_collection" else "context"
    name = str(manifest.get("name") or (root.name if root else "ZEIA"))
    scripts = scripts_from_manifest(manifest)
    zeia_paths = zeia_paths_from_manifest(manifest, root)
    return SourceChoice(
        kind=kind,
        label=f"Existing imported {label_kind}: {name}" if kind.startswith("existing") else f"ZEIA file: {name}",
        name=name,
        path=str(root or manifest.get("source_archive") or ""),
        manifest=manifest,
        scripts=scripts,
        zeia_paths=zeia_paths,
        labware=dedupe_strings(manifest.get("labware_names") or []),
        liquid_classes=dedupe_strings(manifest.get("liquid_classes") or []),
        worktables=dedupe_strings(
            str(item.get("object_name") or item.get("qualified_name") or "")
            for item in manifest.get("workspaces") or []
            if isinstance(item, dict)
        ),
    )


def scripts_from_manifest(manifest: dict[str, Any]) -> list[ScriptChoice]:
    scripts: list[ScriptChoice] = []
    for item in manifest.get("scripts") or []:
        if not isinstance(item, dict):
            continue
        deps = item.get("dependencies") or {}
        scripts.append(
            ScriptChoice(
                name=str(item.get("qualified_name") or item.get("object_name") or item.get("entry") or "Unnamed script"),
                source=str(item.get("source_context") or item.get("source_archive") or item.get("source") or ""),
                entry=str(item.get("extracted_path") or item.get("entry") or ""),
                command_count=int(item.get("command_count") or 0),
                labware=dedupe_strings([*(deps.get("labware_names") or []), *(deps.get("rack_labels") or [])]),
                liquid_classes=dedupe_strings(deps.get("liquid_classes") or []),
                worktables=[
                    str(ref.get("object_name") or "")
                    for ref in item.get("references") or []
                    if isinstance(ref, dict) and ref.get("type_id") == "WorktableWorkspace"
                ],
            )
        )
    return scripts


def zeia_paths_from_manifest(manifest: dict[str, Any], root: Path | None = None) -> list[str]:
    if root:
        local_archives = sorted((root / "source").glob("*.zeia"))
        if local_archives:
            return [str(path) for path in local_archives]
    return dedupe_strings(
        [
            manifest.get("source_archive"),
            manifest.get("copied_archive"),
            *(manifest.get("source_archives") or []),
            *(manifest.get("copied_archives") or []),
        ]
    )


def find_zeia_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(REPO_ROOT):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
        root_path = Path(root)
        for name in names:
            if name.lower().endswith(".zeia"):
                files.append(root_path / name)
    return dedupe_zeia_files(files)


def dedupe_zeia_files(files: list[Path]) -> list[Path]:
    best_by_hash: dict[str, Path] = {}
    for path in files:
        digest = file_sha256(path)
        if not digest:
            continue
        current = best_by_hash.get(digest)
        if current is None or zeia_display_score(path) < zeia_display_score(current):
            best_by_hash[digest] = path
    return sorted(best_by_hash.values(), key=lambda path: (zeia_display_score(path), str(path)))


def file_sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def zeia_display_score(path: Path) -> tuple[int, int, str]:
    rel = relative(path)
    parts = Path(rel).parts
    if "projects" in parts and "source" in parts:
        priority = 0
    elif "projects" in parts:
        priority = 1
    elif "build" in parts:
        priority = 3
    else:
        priority = 2
    return (priority, len(parts), rel.lower())


def zeia_source_label(path: Path, all_paths: list[Path]) -> str:
    matching_names = [candidate for candidate in all_paths if candidate.name == path.name]
    if len(matching_names) == 1:
        return path.name
    parent = Path(relative(path)).parent.as_posix()
    return f"{path.name} ({parent})"


def ask_source(contexts: list[SourceChoice], zeia_files: list[Path]) -> SourceChoice:
    options: list[tuple[str, SourceChoice | Path | str]] = []
    if contexts:
        options.append(("Already imported content", "imported_content"))
    for path in zeia_files:
        options.append((zeia_source_label(path, zeia_files), path))
    options.append(("Path for a new ZEIA file or folder", "custom_path"))

    index = ask_choice("ZEIA source", [label for label, _ in options])
    selected = options[index - 1][1]
    if selected == "imported_content":
        return ask_imported_source(contexts)
    if isinstance(selected, SourceChoice):
        return selected
    if isinstance(selected, Path):
        return inspect_path_source(selected)
    path = Path(ask_required("Path to ZEIA file or folder")).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return inspect_path_source(path)


def ask_imported_source(contexts: list[SourceChoice]) -> SourceChoice:
    labels = []
    for context in contexts:
        kind = "already imported ZEIA collection" if context.kind == "existing_collection" else "already imported ZEIA project"
        labels.append(f"{context.name or context.label} ({kind}, {script_count_label(len(context.scripts))})")
    index = ask_choice("Imported content", labels)
    return contexts[index - 1]


def inspect_path_source(path: Path) -> SourceChoice:
    zeia_paths: list[Path] = []
    if path.is_dir():
        zeia_paths = sorted(path.rglob("*.zeia"))
    elif path.suffix.lower() == ".zeia":
        zeia_paths = [path]
    else:
        print(f"Warning: {path} is not a ZEIA file or folder. It will still be included in the prompt.")
    scripts: list[ScriptChoice] = []
    labware: list[str] = []
    liquid_classes: list[str] = []
    worktables: list[str] = []
    manifests: list[dict[str, Any]] = []
    for zeia_path in zeia_paths:
        try:
            inspection = inspect_zeia_source(zeia_path)
        except Exception as exc:
            print(f"Warning: could not inspect ZEIA through project inspection API: {zeia_path} ({exc})")
            continue
        manifests.append(inspection.manifest or {})
        scripts.extend(inspection.scripts)
        labware.extend(inspection.labware)
        liquid_classes.extend(inspection.liquid_classes)
        worktables.extend(inspection.worktables)
    label = f"Selected ZEIA folder: {relative(path)}" if path.is_dir() else f"Selected ZEIA file: {relative(path)}"
    return SourceChoice(
        kind="zeia_path",
        label=label,
        name=path.stem,
        path=str(path),
        manifest={"sources": manifests} if len(manifests) > 1 else (manifests[0] if manifests else None),
        scripts=scripts,
        zeia_paths=[str(item) for item in zeia_paths] or [str(path)],
        labware=dedupe_strings(labware),
        liquid_classes=dedupe_strings(liquid_classes),
        worktables=dedupe_strings(worktables),
    )


def inspect_zeia_source(path: Path) -> SourceChoice:
    # Requires protocol-builder packages on sys.path (venv / doctor install).
    # Do not mutate import paths here; keep package discovery install-based.
    try:
        from fluent_pipeline.project_archive_inspection import inspect_zeia_archive
    except ImportError as exc:
        raise ImportError(
            "fluent_pipeline is not importable. Run protocol-builder doctor "
            "(or scripts/install/install.ps1) before inspecting ZEIA files."
        ) from exc

    manifest = inspect_zeia_archive(path)
    source = source_from_manifest(manifest, kind="zeia_file", root=path.parent)
    source.label = f"ZEIA file: {relative(path)}"
    source.name = path.stem
    source.path = str(path)
    source.zeia_paths = [str(path)]
    return source


def enrich_source_from_selection(source: SourceChoice) -> None:
    if source.scripts or not source.zeia_paths:
        return
    for raw in source.zeia_paths:
        path = Path(raw)
        if not path.exists() or path.suffix.lower() != ".zeia":
            continue
        try:
            inspection = inspect_zeia_source(path)
        except Exception as exc:
            print(f"Warning: could not inspect ZEIA through project inspection API: {path} ({exc})")
            continue
        source.scripts.extend(inspection.scripts)
        source.labware = dedupe_strings([*source.labware, *inspection.labware])
        source.liquid_classes = dedupe_strings([*source.liquid_classes, *inspection.liquid_classes])
        source.worktables = dedupe_strings([*source.worktables, *inspection.worktables])


def ask_script(scripts: list[ScriptChoice]) -> ScriptChoice | None:
    if not scripts:
        ask_choice("Detected scripts", ["No scripts detected; let Codex choose from the ZEIA context"])
        return None
    labels = []
    for script in scripts:
        suffix = []
        if script.command_count:
            suffix.append(f"{script.command_count} commands")
        if script.labware:
            suffix.append("labware: " + ", ".join(script.labware[:3]))
        labels.append(f"{script.name}" + (f" ({'; '.join(suffix)})" if suffix else ""))
    labels.append("Let Codex choose the best reusable source pattern")
    index = ask_choice("Source script pattern", labels)
    if index == len(labels):
        return None
    return scripts[index - 1]


def ask_labware_roles(detected: list[str]) -> dict[str, str]:
    values = dedupe_strings(detected)
    if not values:
        return {
            "source_labware": "Let Codex infer from the selected ZEIA/source script",
            "destination_labware": "Let Codex infer from the selected ZEIA/source script",
            "tips_labware": "",
            "ai_should_choose_labware": "true",
        }
    inferred = infer_labware_roles(values)
    if inferred.get("source_labware") and inferred.get("destination_labware"):
        print("\nI found what looks like:")
        print(f"- Source labware: {inferred['source_labware']}")
        print(f"- Destination labware: {inferred['destination_labware']}")
        if inferred.get("tips_labware"):
            print(f"- Tips: {inferred['tips_labware']}")
        answer = ask_choice(
            "Use these?",
            [
                "Yes, use these detected labware choices",
                "No, let me choose manually",
                "Let Codex infer and choose",
            ],
        )
        if answer == 1:
            return inferred
        if answer == 3:
            return {
                "source_labware": "Let Codex infer from the selected ZEIA/source script",
                "destination_labware": "Let Codex infer from the selected ZEIA/source script",
                "tips_labware": inferred.get("tips_labware", ""),
                "ai_should_choose_labware": "true",
            }
    return {
        "source_labware": ask_labware_picker("For the new edited script, which one should liquid come FROM?", values, purpose="source"),
        "destination_labware": ask_labware_picker("For the new edited script, which one should liquid go TO?", values, purpose="destination"),
        "tips_labware": inferred.get("tips_labware", ""),
        "ai_should_choose_labware": "",
    }


def ask_labware_picker(question: str, values: list[str], *, purpose: str) -> str:
    ordered = order_labware_for_purpose(values, purpose)
    options = [*ordered, "I want to type a different name"]
    index = ask_choice(question, options)
    if index <= len(ordered):
        return ordered[index - 1]
    return ask_required("Labware name")


def infer_labware_roles(values: list[str]) -> dict[str, str]:
    return {
        "source_labware": first_by_role(values, "source"),
        "destination_labware": first_by_role(values, "destination"),
        "tips_labware": first_by_role(values, "tips"),
    }


def first_by_role(values: list[str], role: str) -> str:
    for value in values:
        if labware_role(value) == role:
            return value
    return ""


def order_labware_for_purpose(values: list[str], purpose: str) -> list[str]:
    priority = {
        "source": {"source": 0, "destination": 1, "other": 2, "tips": 3},
        "destination": {"destination": 0, "source": 1, "other": 2, "tips": 3},
    }.get(purpose, {"source": 0, "destination": 1, "other": 2, "tips": 3})
    return sorted(values, key=lambda value: (priority.get(labware_role(value), 9), value.lower()))


def labware_role(value: str) -> str:
    text = value.lower()
    if "tip" in text:
        return "tips"
    if any(token in text for token in ("source", "src", "input", "template")):
        return "source"
    if any(token in text for token in ("dest", "destination", "output", "target", "copy")):
        return "destination"
    return "other"


def ask_liquid_class(detected: list[str]) -> str:
    options = ["Use the same liquid class used in the selected source script", "Codex may infer the safest matching liquid class"]
    options.extend(dedupe_strings(detected))
    options.append("Type another liquid class")
    index = ask_choice("Liquid class", options)
    if index == len(options):
        return ask_required("Liquid class name")
    return options[index - 1]


def ask_volume() -> str:
    options = [
        "Same volume used by the selected source script",
        "20 uL",
        "10 uL",
        "5 uL",
        "Let AI infer from the source script",
        "Type custom volume(s)",
    ]
    index = ask_choice("How much liquid should be transferred?", options)
    selected = options[index - 1]
    if selected == "Let AI infer from the source script":
        return "Let Codex infer transfer volume(s) from the source script"
    if selected == "Type custom volume(s)":
        return ask_required("Enter volume(s), in uL if possible")
    return selected


def ask_tip_strategy() -> str:
    options = [
        "Same tip strategy used by the selected source script",
        "Fresh tips for each column",
        "Fresh tips for every transfer",
        "Reuse tips within each source/destination pair",
        "No tip changes needed",
        "Let Codex infer the safest tip strategy",
        "Type custom tip strategy",
    ]
    index = ask_choice("How should tips be handled?", options)
    selected = options[index - 1]
    if selected == "Let Codex infer the safest tip strategy":
        return "Let Codex infer the safest tip strategy from the selected ZEIA/source script"
    if selected == "Type custom tip strategy":
        return ask_required("Enter custom tip strategy")
    return selected


def ask_worktable_policy() -> str:
    options = [
        "Allow moving labware if needed; document every change",
        "Keep the current worktable if possible; report any required changes",
        "Allow adding new labware, but do not move existing labware",
        "Do not change the layout; ask me first if changes are required",
        "Let Codex choose the safest option and explain it",
    ]
    index = ask_choice("Can Codex change the deck/worktable layout?", options)
    selected = options[index - 1]
    if selected == "Let Codex choose the safest option and explain it":
        return "Let Codex choose the safest deck/worktable policy and explain any needed layout changes"
    return selected


def ask_output_artifacts() -> str:
    options = [
        "Full package: script, worklist if useful, recreate guide, and worktable notes",
        "Draft only: no simulation or compile yet",
        "Ready-to-import only if every validation check passes",
        "Let Codex choose the useful files and explain them",
        "Type custom output request",
    ]
    index = ask_choice("What should Codex create?", options)
    selected = options[index - 1]
    if selected == "Full package: script, worklist if useful, recreate guide, and worktable notes":
        return "Generate .xscr, .gwl if useful, RECREATE_SCRIPT.md, worktable_changes.md, and worktable.patch.json"
    if selected == "Draft only: no simulation or compile yet":
        return "Generate scaffold only; no simulation or compile yet"
    if selected == "Ready-to-import only if every validation check passes":
        return "Generate final ready-to-import bundle if all validation gates pass"
    if selected == "Let Codex choose the useful files and explain them":
        return "Let Codex choose the useful output files for this protocol and explain what it generated"
    return ask_required("Enter custom output request")


def ask_choice(label: str, options: list[str]) -> int:
    print(f"\n{label}")
    print("-" * min(72, max(12, len(label))))
    print("  0. Back" if BACK_ALLOWED else "  0. Cancel")
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")
    while True:
        raw = input(f"Enter 0-{len(options)}: ").strip()
        if raw.lower() in BACK_WORDS or raw == "0":
            if BACK_ALLOWED:
                raise BackRequested
            raise CancelRequested
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        print(f"Please enter a number from 0 to {len(options)}.")


def ask_required(label: str) -> str:
    while True:
        value = input(f"\n{label}: ").strip()
        if value.lower() in BACK_WORDS:
            if BACK_ALLOWED:
                raise BackRequested
            raise CancelRequested
        if value:
            return value
        print("This is required.")


def ask_wells_or_positions(title: str, options: list[str], *, custom_prompt: str, ai_value: str) -> str:
    index = ask_choice(title, options)
    selected = options[index - 1]
    lowered = selected.lower()
    if "let ai infer" in lowered or "let codex infer" in lowered:
        return ai_value
    if "type custom" in lowered:
        return ask_required(custom_prompt)
    return selected


def ask_optional(label: str) -> str:
    value = input(f"\n{label} (optional): ").strip()
    if value.lower() in BACK_WORDS:
        if BACK_ALLOWED:
            raise BackRequested
        raise CancelRequested
    return value


def build_prompt(request: dict[str, Any]) -> str:
    source: SourceChoice = request["source"]
    script: ScriptChoice | None = request["script"]
    source_lines: list[str] = []
    if source.kind.startswith("existing"):
        source_kind = source.kind.replace("existing_", "").replace("_", " ")
        source_lines.append(f"Use the existing imported {source_kind}: {source.name or source.label}.")
    elif source.label:
        source_lines.append(source.label + ".")
    if source.zeia_paths:
        source_lines.append("ZEIA source(s): " + ", ".join(display_path(Path(path)) for path in source.zeia_paths))
    elif source.path:
        source_lines.append(f"ZEIA source path: {source.path}")

    script_line = (
        f'Use the existing script "{script.name}" as the source pattern.'
        if script and script.name
        else "Choose the best reusable source pattern from the selected ZEIA context."
    )
    name_line = f'Name the new script "{request["script_name"]}".' if request["script_name"] else "Choose a clear script name."

    parts = [
        "Use these ZEIA files to make a new FluentControl script.",
        *source_lines,
        name_line,
        script_line,
        "",
        "Goal:",
        request["intent"],
        "",
        "Transfer details:",
        f'- Source labware: {request["source_labware"]}',
        f'- Destination labware: {request["destination_labware"]}',
        *([f'- Detected tips labware: {request["tips_labware"]}'] if request.get("tips_labware") else []),
        *(["- Labware instruction: Let Codex infer and choose the correct source/destination labware from the selected ZEIA/source script."] if request.get("ai_should_choose_labware") else []),
        f'- Source wells/positions: {request["source_wells"]}',
        f'- Destination wells/positions: {request["destination_wells"]}',
        f'- Volume(s): {request["volumes"]}',
        f'- Liquid class: {request["liquid_class"]}',
        f'- Tip strategy: {request["tip_strategy"]}',
        f'- Deck/worktable policy: {request["worktable_policy"]}',
        "",
        "Requested outputs:",
        request["outputs"],
        "",
        "Local setup:",
        setup_status_prompt_line(request.get("setup_status")),
        *pattern_index_prompt_lines(request.get("pattern_index")),
        "",
        "Generation instructions:",
        "- Inspect the selected project/source manifest before generation.",
        "- Reuse existing script patterns and command examples where possible.",
        "- Create and validate request.spec.yaml first.",
        "- Generate scaffold artifacts first unless final ready-to-import output was explicitly requested.",
        "- Do not claim hardware readiness; the operator must validate in FluentControl and on the target instrument.",
    ]
    if source.manifest:
        parts.extend(["", "Detected source summary:", source_summary_for_prompt(source)])
    if request.get("extra_notes"):
        parts.extend(["", "Extra notes:", request["extra_notes"]])
    return "\n".join(parts).strip()


def source_summary_for_prompt(source: SourceChoice) -> str:
    lines = [
        f"- Script count: {len(source.scripts)}",
        f"- Labware detected: {', '.join(source.labware[:12]) or 'none detected'}",
        f"- Liquid classes detected: {', '.join(source.liquid_classes[:8]) or 'none detected'}",
        f"- Worktables detected: {', '.join(source.worktables[:8]) or 'none detected'}",
    ]
    manifest = source.manifest or {}
    assessment = manifest.get("full_zeia_export") if isinstance(manifest, dict) else None
    if isinstance(assessment, dict):
        lines.append(f"- Full ZEIA export check: {assessment.get('status') or 'unknown'}")
        if assessment.get("summary"):
            lines.append(f"- Full ZEIA summary: {assessment['summary']}")
    elif isinstance(manifest, dict) and isinstance(manifest.get("sources"), list):
        assessments = [
            item.get("full_zeia_export")
            for item in manifest["sources"]
            if isinstance(item, dict) and isinstance(item.get("full_zeia_export"), dict)
        ]
        if assessments:
            statuses = [str(item.get("status") or "unknown") for item in assessments]
            lines.append(f"- Full ZEIA export checks: {', '.join(statuses)}")
            summaries = [str(item.get("summary")) for item in assessments if item.get("summary")]
            if summaries:
                lines.append(f"- Full ZEIA summaries: {'; '.join(summaries)}")
    return "\n".join(lines)


def setup_status_prompt_line(setup_status: Any) -> str:
    if isinstance(setup_status, SetupStatus):
        report = relative(setup_status.report)
        if setup_status.status in {"passed", "installed"}:
            return f"Protocol-builder setup was checked and passed; doctor report: {report}."
        if setup_status.status == "failed":
            return f"Protocol-builder setup preflight failed before this prompt was created; read {report} before simulation or compile."
        if setup_status.status == "skipped":
            return "Protocol-builder setup check was skipped; run doctor only if simulation or compile needs setup."
    return "Protocol-builder setup status was not recorded; run doctor only if simulation or compile needs setup."


def pattern_index_prompt_lines(pattern_index: Any) -> list[str]:
    if not isinstance(pattern_index, PatternIndexStatus):
        return []
    if not pattern_index.exists:
        return ["No persistent mined pattern index was detected before this prompt was created."]
    if pattern_index.error:
        return [f"Persistent mined pattern index exists at {relative(pattern_index.path)}, but could not be read: {pattern_index.error}"]
    protocol_builder_path = Path(os.path.relpath(pattern_index.path, PROTOCOL_BUILDER)).as_posix()
    lines = [
        f"Persistent mined pattern index: {relative(pattern_index.path)}.",
        f"When using mined windows, pass `--index-db {protocol_builder_path}` from source/03-protocol-builder.",
    ]
    if pattern_index.patterns:
        lines.append("Useful mined pattern IDs detected before this prompt:")
        for pattern in pattern_index.patterns:
            lines.append(
                f"- ID {pattern['id']} {pattern['pattern_type']}: {pattern['name']} "
                f"[{pattern['source_script']}, commands {pattern['start_command_index']}-{pattern['end_command_index']}]"
            )
    return lines


def copy_to_clipboard(text: str) -> bool:
    system = platform.system().lower()
    commands: list[list[str] | str]
    if system == "darwin":
        commands = [["pbcopy"]]
    elif system == "windows":
        commands = ["clip"]
    else:
        commands = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for command in commands:
        if isinstance(command, list) and shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, input=text, text=True, check=True, shell=isinstance(command, str))
            return True
        except Exception:
            continue
    return False


def print_detection_summary(contexts: list[SourceChoice], zeia_files: list[Path]) -> None:
    print("Existing contexts:")
    for context in contexts:
        print(f"- {context.label}: {len(context.scripts)} script(s)")
    print("\nDetected ZEIA files:")
    for path in zeia_files:
        print(f"- {zeia_source_label(path, zeia_files)}")


def script_count_label(count: int) -> str:
    return "1 script" if count == 1 else f"{count} scripts"


def print_source_summary(source: SourceChoice) -> None:
    print("\n" + bar())
    print("Selected source")
    print(bar())
    print(source.label)
    if source.zeia_paths:
        print(f"ZEIA source(s): {len(source.zeia_paths)}")
    if source.scripts:
        print(f"Detected script(s): {len(source.scripts)}")
    if source.labware:
        print("Detected labware: " + ", ".join(source.labware[:8]) + (" ..." if len(source.labware) > 8 else ""))
    if source.liquid_classes:
        print("Detected liquid classes: " + ", ".join(source.liquid_classes[:6]) + (" ..." if len(source.liquid_classes) > 6 else ""))
    print(bar())


def clear_screen() -> None:
    if os.environ.get("TECAN_PROMPT_NO_CLEAR"):
        return
    command = ["cmd.exe", "/c", "cls"] if platform.system().lower() == "windows" else ["clear"]
    try:
        subprocess.run(command, check=False)
    except Exception:
        pass


def bar() -> str:
    return "=" * 72


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def dedupe_strings(values: Any) -> list[str]:
    out: list[str] = []
    if values is None:
        return out
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:
        return str(path)


def display_path(path: Path) -> str:
    return relative(path) if path.exists() else str(path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, CancelRequested):
        print("\nCancelled.")
        raise SystemExit(130)
