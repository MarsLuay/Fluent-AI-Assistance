"""Source-native 50 mL cap-seat diagnostic harness.

This module deliberately does *not* alter the shared cap subroutine.  It builds
one standalone FluentControl script that structurally clones the native cap
sequence and its finger setup from known-good source XSCRs.  Each selectable
case executes exactly one decap/recap cycle, then stops for operator inspection.

The only permitted experimental mutations are the two values being diagnosed:

* ``GripCapClose``: 25 or 33.
* The literal ``ZOffset`` in the final ``cap_50mL`` vector: 7.5 or 8.5.

All other native command XML, raw axis routes, force values, and sequencing are
copied from the source.  This keeps the test useful for isolating the cap-seat
problem without changing the production subscript.
"""

from __future__ import annotations

import copy
import html
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from . import xml_compat as ET
from .checksums import entry_checksum_state, recompute_checksum_bytes


TARGET_SCRIPT_NAME = "TubeFixingScript"
TARGET_SCRIPT_FOLDER = "Demo scripts"

# Role discovery in an imported ZEIA (ObjectName heuristics). Callers may still
# pass explicit archive entry paths to build_tube_fixing_xscr(...).
_FINGERS_NAME_RE = re.compile(r"get[_\s-]*fingers", re.I)
_CAP_NAME_RE = re.compile(r"capbc|sub[_\s-]*cap|cap[_\s-]*bc|cap[_\s-]*handl", re.I)


@dataclass(frozen=True)
class TubeFixingCase:
    """One bounded diagnostic case.

    ``source_status`` deliberately distinguishes source-backed values from the
    one cross-combination that has no single matching source script.
    """

    case_id: int
    grip_cap_close: int
    cap_seat_z_offset: float
    source_status: str
    description: str


TUBE_FIXING_CASES: tuple[TubeFixingCase, ...] = (
    TubeFixingCase(
        1,
        25,
        8.5,
        "current-baseline",
        "Baseline source recap mapping: GripCapClose 25; final cap vector ZOffset 8.5.",
    ),
    TubeFixingCase(
        2,
        25,
        7.5,
        "source-backed-height",
        "Source-backed height comparison: GripCapClose 25; final cap vector ZOffset 7.5.",
    ),
    TubeFixingCase(
        3,
        33,
        8.5,
        "source-backed-width",
        "Source-backed cap-width comparison: GripCapClose 33; final cap vector ZOffset 8.5.",
    ),
    TubeFixingCase(
        4,
        33,
        7.5,
        "experimental-cross-combination",
        "Experimental cross-combination: GripCapClose 33; final cap vector ZOffset 7.5.",
    ),
)


class TubeFixingHarnessError(RuntimeError):
    """Raised when a source-native test harness cannot be built safely."""


def tube_fixing_case_matrix() -> list[dict[str, Any]]:
    """Return the finite, bounded 2 x 2 test matrix for companion artifacts."""

    return [asdict(case) for case in TUBE_FIXING_CASES]


def build_tube_fixing_xscr(
    source_bundle: Path,
    output_xscr: Path,
    *,
    script_name: str = TARGET_SCRIPT_NAME,
    script_folder: str = TARGET_SCRIPT_FOLDER,
    source_dir: Path | None = None,
    main_entry: str | None = None,
    cap_entry: str | None = None,
    fingers_entry: str | None = None,
) -> dict[str, Any]:
    """Build and validate a standalone native-only cap diagnostic XSCR.

    ``source_bundle`` is an imported full ZEIA (or Snapshot) that contains the
    main script, cap subroutine, and get-fingers subroutine. Entry paths are
    discovered from ObjectName when omitted. The function writes the three
    copied sources into ``source_dir`` when supplied for provenance.
    """

    source_bundle = Path(source_bundle).resolve()
    output_xscr = Path(output_xscr).resolve()
    if not source_bundle.is_file():
        raise TubeFixingHarnessError(f"Source bundle does not exist: {source_bundle}")

    resolved = resolve_tube_fixing_entries(
        source_bundle,
        main_entry=main_entry,
        cap_entry=cap_entry,
        fingers_entry=fingers_entry,
    )
    entries = _read_required_entries(source_bundle, resolved)
    main_root = _parse_xscr(entries[resolved["main"]], resolved["main"])
    cap_root = _parse_xscr(entries[resolved["cap"]], resolved["cap"])
    fingers_root = _parse_xscr(entries[resolved["fingers"]], resolved["fingers"])

    root = _build_root(
        main_root=main_root,
        cap_root=cap_root,
        fingers_root=fingers_root,
        script_name=script_name,
        script_folder=script_folder,
    )
    _register_source_namespaces(entries.values())
    _renumber_line_numbers(root)
    _fill_empty_group_names(root)

    output_xscr.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_xscr, encoding="utf-8", xml_declaration=True)
    _recompute_checksum(output_xscr)

    validation = validate_tube_fixing_xscr(output_xscr)
    if not validation["ok"]:
        raise TubeFixingHarnessError("Harness structural validation failed: " + "; ".join(validation["errors"]))

    source_paths: dict[str, Path] = {}
    if source_dir is not None:
        source_dir = Path(source_dir).resolve()
        source_dir.mkdir(parents=True, exist_ok=True)
        for entry, payload in entries.items():
            destination = source_dir / Path(entry).name
            destination.write_bytes(payload)
            source_paths[entry] = destination

    return {
        "xscr": output_xscr,
        "source_paths": source_paths,
        "validation": validation,
        "cases": tube_fixing_case_matrix(),
    }


def validate_tube_fixing_xscr(xscr_path: Path) -> dict[str, Any]:
    """Validate the standalone/native-only invariants of a generated harness."""

    xscr_path = Path(xscr_path)
    errors: list[str] = []
    if not xscr_path.is_file():
        return {"ok": False, "errors": [f"Missing XSCR: {xscr_path}"], "cases": []}

    try:
        root = ET.parse(xscr_path).getroot()
    except Exception as exc:  # pragma: no cover - caller needs the exact parse failure
        return {"ok": False, "errors": [f"XSCR is not parseable: {exc}"], "cases": []}

    object_name = _direct_text(_first_descendant(root, "Payload"), "ObjectName")
    if object_name != TARGET_SCRIPT_NAME:
        errors.append(f"Expected ObjectName {TARGET_SCRIPT_NAME!r}, found {object_name!r}")

    subroutine_objects = [
        obj
        for obj in _iter_objects(root)
        if "SubRoutineStatement" in str(obj.attrib.get("Type") or "")
        or _first_descendant(obj, "SubRoutineStatement") is not None
    ]
    if subroutine_objects:
        errors.append(f"Found {len(subroutine_objects)} SubRoutineStatement object(s); harness must be native-only")

    script_references = [
        reference
        for reference in _iter_local(root, "Reference")
        if _direct_text(reference, "TypeId").casefold() == "script"
    ]
    if script_references:
        errors.append(f"Found {len(script_references)} Script reference(s); harness must be standalone")

    checksum_state = entry_checksum_state(xscr_path.read_bytes())
    if checksum_state != "valid":
        errors.append(f"XSCR checksum is {checksum_state}, expected valid")

    lines = _line_numbers(root)
    if not lines:
        errors.append("No LineNumber values were found")
    elif len(lines) != len(set(lines)):
        errors.append("Generated XSCR has duplicate LineNumber values")

    empty_group_names = _empty_group_names(root)
    if empty_group_names:
        errors.append(f"Generated XSCR has {len(empty_group_names)} empty group name(s)")

    discovered_cases = _discover_cases(root)
    expected = {case.case_id: case for case in TUBE_FIXING_CASES}
    if set(discovered_cases) != set(expected):
        errors.append(
            "Case IDs differ from the required 2 x 2 matrix: "
            f"expected {sorted(expected)}, found {sorted(discovered_cases)}"
        )
    else:
        for case_id, case in expected.items():
            actual = discovered_cases[case_id]
            if actual.get("grip_cap_close") != case.grip_cap_close:
                errors.append(
                    f"Case {case_id} GripCapClose is {actual.get('grip_cap_close')!r}, expected {case.grip_cap_close}"
                )
            if actual.get("cap_seat_z_offset") != case.cap_seat_z_offset:
                errors.append(
                    f"Case {case_id} cap_50mL ZOffset is {actual.get('cap_seat_z_offset')!r}, "
                    f"expected {case.cap_seat_z_offset}"
                )
            if actual.get("native_cycle_count") != 1:
                errors.append(
                    f"Case {case_id} has {actual.get('native_cycle_count')} native cap cycle(s), expected exactly one"
                )

    native_counts = _native_command_counts(root)
    for command_name in ("ApplicationDriverMacro", "TeGioSetPWMOutputStatement", "MoveAxisCommandScriptStatement"):
        if native_counts.get(command_name, 0) <= 0:
            errors.append(f"No native {command_name} command was retained")

    return {
        "ok": not errors,
        "errors": errors,
        "script_name": object_name,
        "checksum": checksum_state,
        "cases": discovered_cases,
        "native_command_counts": native_counts,
        "subroutine_object_count": len(subroutine_objects),
        "script_reference_count": len(script_references),
        "line_number_count": len(lines),
    }


def render_tube_fixing_harness_markdown(validation: dict[str, Any]) -> str:
    """Render concise operator-facing matrix and structural validation notes."""

    lines = [
        "# TubeFixingScript",
        "",
        "Standalone native cap-seat diagnostic. It does not replace or call the production cap subscript.",
        "",
        "Run exactly one case per fresh capped 50 mL tube and matching cap. The script decaps, recaps, then stops for visual inspection.",
        "",
        "| Case | GripCapClose | cap_50mL ZOffset | Status |",
        "| --- | ---: | ---: | --- |",
    ]
    for case in TUBE_FIXING_CASES:
        lines.append(
            f"| {case.case_id} | {case.grip_cap_close} | {case.cap_seat_z_offset:g} | {case.source_status} |"
        )
    lines.extend(
        [
            "",
            "Fixed harness knobs: GripCapForce 60, GripForce 70, Speed 100, tube/holder slot 0. GripTubeClose/Open and deck locations come from the cloned source script.",
            "",
            "Do not run cases sequentially on the same tube/cap. Case 4 is the one experimental cross-combination; the other values were mined from source scripts.",
            "",
            "## Structural checks",
            "",
            f"- Native-only: `{'yes' if validation.get('subroutine_object_count') == 0 else 'no'}`",
            f"- Script references: `{validation.get('script_reference_count', 'unknown')}`",
            f"- Checksum: `{validation.get('checksum', 'unknown')}`",
            f"- Validation: `{'passed' if validation.get('ok') else 'failed'}`",
        ]
    )
    if validation.get("errors"):
        lines.extend(["", "## Errors", "", *[f"- {error}" for error in validation["errors"]]])
    return "\n".join(lines).rstrip() + "\n"


def resolve_tube_fixing_entries(
    source_bundle: Path,
    *,
    main_entry: str | None = None,
    cap_entry: str | None = None,
    fingers_entry: str | None = None,
) -> dict[str, str]:
    """Resolve main/cap/fingers XSCR entry paths inside a ZEIA.

    Prefer explicit paths. Otherwise discover by FluentControl ObjectName inside
    the archive so the harness works for any lab export.
    """
    source_bundle = Path(source_bundle)
    try:
        with zipfile.ZipFile(source_bundle) as archive:
            named = _xscr_object_names(archive)
            return {
                "main": _pick_entry(named, main_entry, role="main"),
                "cap": _pick_entry(named, cap_entry, role="cap"),
                "fingers": _pick_entry(named, fingers_entry, role="fingers"),
            }
    except zipfile.BadZipFile as exc:
        raise TubeFixingHarnessError(f"Source bundle is not a readable ZEIA: {source_bundle}") from exc


def _xscr_object_names(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if not name.lower().endswith(".xscr"):
            continue
        try:
            root = ET.fromstring(archive.read(info))
        except Exception:
            continue
        payload = _first_descendant(root, "Payload")
        object_name = _direct_text(payload, "ObjectName") if payload is not None else ""
        records.append((name, object_name or Path(name).stem))
    return records


def _pick_entry(named: list[tuple[str, str]], explicit: str | None, *, role: str) -> str:
    if explicit:
        normalized = explicit.replace("\\", "/")
        available = {path.replace("\\", "/"): path for path, _ in named}
        if normalized in available:
            return available[normalized]
        # allow bare GUID.xscr
        for path, _ in named:
            if path.endswith(normalized) or Path(path).name == Path(normalized).name:
                return path
        raise TubeFixingHarnessError(f"Explicit {role} entry not found in ZEIA: {explicit}")

    if role == "fingers":
        matches = [path for path, object_name in named if _FINGERS_NAME_RE.search(object_name)]
    elif role == "cap":
        matches = [path for path, object_name in named if _CAP_NAME_RE.search(object_name)]
    else:
        # Prefer a non-subroutine main script: exclude fingers/cap matches.
        matches = [
            path
            for path, object_name in named
            if not _FINGERS_NAME_RE.search(object_name)
            and not _CAP_NAME_RE.search(object_name)
            and "sub" not in object_name.casefold()
        ]
        if not matches:
            matches = [path for path, _ in named]
    if not matches:
        raise TubeFixingHarnessError(
            f"Could not discover a {role} XSCR in the ZEIA by ObjectName; "
            f"pass an explicit {role}_entry path"
        )
    return sorted(matches)[0]


def _read_required_entries(source_bundle: Path, required: dict[str, str]) -> dict[str, bytes]:
    paths = (required["main"], required["cap"], required["fingers"])
    try:
        with zipfile.ZipFile(source_bundle) as archive:
            namemap = {name.replace("\\", "/"): name for name in archive.namelist()}
            out: dict[str, bytes] = {}
            for entry in paths:
                key = entry.replace("\\", "/")
                if key not in namemap:
                    raise TubeFixingHarnessError(f"Source bundle is missing required XSCR entry: {entry}")
                out[entry] = archive.read(namemap[key])
            return out
    except zipfile.BadZipFile as exc:
        raise TubeFixingHarnessError(f"Source bundle is not a readable ZEIA: {source_bundle}") from exc


def _parse_xscr(payload: bytes, label: str) -> Any:
    try:
        return ET.fromstring(payload)
    except Exception as exc:
        raise TubeFixingHarnessError(f"Could not parse source XSCR {label}: {exc}") from exc


def _build_root(
    *,
    main_root: Any,
    cap_root: Any,
    fingers_root: Any,
    script_name: str,
    script_folder: str,
) -> Any:
    root = copy.deepcopy(cap_root)
    payload = _first_descendant(root, "Payload")
    main_payload = _first_descendant(main_root, "Payload")
    if payload is None or main_payload is None:
        raise TubeFixingHarnessError("Required Payload element is missing from source XSCR")

    _set_direct_text(payload, "ObjectName", script_name)
    _set_direct_text(payload, "ObjectSubfolderPath", script_folder)
    _set_direct_text(
        payload,
        "Comment",
        "Standalone native cap-seat diagnostic. Four bounded GripCapClose/ZOffset cases; does not call the production subscript.",
    )
    _replace_payload_references(payload, main_payload)
    _replace_workspace_data(payload, main_payload)
    _configure_variables(payload, fingers_root)
    _replace_commands(payload, cap_root=cap_root, fingers_root=fingers_root)

    checksum = _first_descendant(root, "Checksum")
    if checksum is None:
        checksum = ET.SubElement(root, "Checksum")
    checksum.text = ""
    return root


def _replace_payload_references(payload: Any, main_payload: Any) -> None:
    for child in list(payload):
        if _local(child.tag) in {"Reference", "FileReference"}:
            payload.remove(child)

    main_workspace = next(
        (
            copy.deepcopy(reference)
            for reference in list(main_payload)
            if _local(reference.tag) == "Reference"
            and _direct_text(reference, "TypeId") == "WorktableWorkspace"
        ),
        None,
    )
    if main_workspace is None:
        raise TubeFixingHarnessError("Current Script 1 source is missing its WorktableWorkspace reference")

    payload_data_index = next(
        (index for index, child in enumerate(list(payload)) if _local(child.tag) == "PayloadData"),
        len(list(payload)),
    )
    payload.insert(payload_data_index, main_workspace)


def _replace_workspace_data(payload: Any, main_payload: Any) -> None:
    target_workspace = _first_descendant(payload, "VxWorkspaceData")
    source_workspace = _first_descendant(main_payload, "VxWorkspaceData")
    if target_workspace is None or source_workspace is None:
        raise TubeFixingHarnessError("Could not locate VxWorkspaceData for source-backed worktable transplant")
    parent = _parent_map(payload).get(id(target_workspace))
    if parent is None:
        raise TubeFixingHarnessError("VxWorkspaceData has no XML parent")
    position = list(parent).index(target_workspace)
    parent.remove(target_workspace)
    parent.insert(position, copy.deepcopy(source_workspace))


def _variable_value_text(definition: Any) -> str:
    values = _direct_child(definition, "Values")
    if values is None:
        return ""
    for child in values:
        if _local(child.tag) == "string":
            return (child.text or "").strip().strip('"')
    return ""


def _configure_variables(payload: Any, fingers_root: Any) -> None:
    # Force only harness-control knobs. Tube grip widths and deck locations must
    # stay whatever the cloned source ZEIA/script already declares — never invent
    # lab-specific location or grip-width product defaults.
    expected_defaults = {
        "InputNumSampleCount": "1",
        "GripCapClose": "25",
        "GripCapForce": "60",
        "Speed": "100",
        "GripForce": "70",
        "InputLidAdd": "1",
        "InputScanBarcode": "0",
        "InputLidRemove": "1",
        "InputStartPosition": "0",
        "zoffset": "0",
        "capoffset": "0",
        "uncapafterscan": "0",
    }
    required_from_source = (
        "GripTubeClose",
        "GripTubeOpen",
        "InputSubLocation",
        "InputSubLidLocation",
    )
    for name in required_from_source:
        definition = _variable_definition(payload, name)
        if definition is None:
            raise TubeFixingHarnessError(
                f"Cap source is missing required variable declaration {name!r}; "
                "mine it from the imported ZEIA instead of hardcoding a lab deck name"
            )
        current = _variable_value_text(definition)
        if not current or current.strip() in {"0", "0.0", ""}:
            raise TubeFixingHarnessError(
                f"Cap source variable {name!r} has no usable default; provide a source "
                "script whose ZEIA declares this value"
            )
        _set_direct_text(definition, "QueryOnStartup", "false")
    for name, value in expected_defaults.items():
        definition = _variable_definition(payload, name)
        if definition is None:
            raise TubeFixingHarnessError(f"Cap source is missing required variable declaration {name!r}")
        _set_variable_value(definition, value)
        _set_direct_text(definition, "QueryOnStartup", "false")

    input_count = _variable_definition(payload, "InputNumSampleCount")
    if input_count is None:
        raise TubeFixingHarnessError("Could not clone the InputNumSampleCount declaration")
    case_variable = copy.deepcopy(input_count)
    _set_direct_text(case_variable, "Name", "TubeFixingCase")
    _set_direct_text(case_variable, "QueryOnStartup", "true")
    _set_direct_text(
        case_variable,
        "QueryOnStartupString",
        "TubeFixingScript case: 1=25/8.5, 2=25/7.5, 3=33/8.5, 4=33/7.5 experimental.",
    )
    _set_variable_value(case_variable, "1")

    finger_variable = _variable_definition(fingers_root, "FingerSelection")
    if finger_variable is None:
        raise TubeFixingHarnessError("GetFingers source is missing FingerSelection declaration")
    finger_variable = copy.deepcopy(finger_variable)
    _set_variable_value(finger_variable, "3")
    _set_direct_text(finger_variable, "QueryOnStartup", "false")

    variable_definitions = _first_descendant(payload, "VariableDeclarations")
    if variable_definitions is None:
        raise TubeFixingHarnessError("Cap source is missing VariableDeclarations")
    target = _variable_definitions_container(variable_definitions)
    if target is None:
        raise TubeFixingHarnessError("Could not locate the variable declaration list")
    target.append(case_variable)
    target.append(finger_variable)


def _replace_commands(payload: Any, *, cap_root: Any, fingers_root: Any) -> None:
    target_commands = _first_descendant(payload, "Commands")
    cap_commands = _first_descendant(cap_root, "Commands")
    finger_commands = _first_descendant(fingers_root, "Commands")
    if target_commands is None or cap_commands is None or finger_commands is None:
        raise TubeFixingHarnessError("Required Commands element is missing from a source XSCR")

    cap_objects = _top_level_objects(cap_commands)
    finger_objects = _top_level_objects(finger_commands)
    standalone = _find_conditional_object(cap_objects, "StandAlone")
    cycle = _find_loop_object(cap_objects, "Cycling thru tubes")
    prompt_template = _find_object_with_type(finger_objects, "UserPromptStatement")
    set_variable_template = _find_object_with_type(cap_objects, "SetVariableStatement", recursive=True)
    release_comment = _find_comment_object(cap_objects, "vial gripper release")
    release_pwm = _find_object_with_type(cap_objects, "TeGioSetPWMOutputStatement")
    if not all((standalone, cycle, prompt_template, set_variable_template, release_comment, release_pwm)):
        raise TubeFixingHarnessError("Cap source no longer matches the expected native test structure")

    new_commands = copy.deepcopy(target_commands)
    script_group = _direct_child(new_commands, "ScriptGroup")
    objects = _direct_child(script_group, "Objects") if script_group is not None else None
    if objects is None:
        raise TubeFixingHarnessError("Commands does not contain ScriptGroup/Objects")
    for child in list(objects):
        objects.remove(child)

    setup_prompt = _prompt_object(
        prompt_template,
        "TubeFixingScript: load one fresh capped 50 mL test tube at the InputSubLocation / InputSubLidLocation declared by the source script. "
        "Select one case at startup. The script performs one native decap/recap cycle, then stops for inspection. Close the door before pressing OK.",
    )
    objects.append(setup_prompt)
    objects.append(copy.deepcopy(release_comment))
    objects.append(copy.deepcopy(release_pwm))
    for finger_object in finger_objects:
        objects.append(copy.deepcopy(finger_object))

    for case in TUBE_FIXING_CASES:
        objects.append(
            _case_group(
                group_template=standalone,
                prompt_template=prompt_template,
                set_variable_template=set_variable_template,
                cycle_template=cycle,
                case=case,
            )
        )

    invalid_case = _case_group(
        group_template=standalone,
        prompt_template=prompt_template,
        set_variable_template=set_variable_template,
        cycle_template=None,
        case=None,
    )
    invalid_conditional = _direct_child(invalid_case, "ConditionalGroup")
    if invalid_conditional is None:
        raise TubeFixingHarnessError("Invalid-case group is missing ConditionalGroup")
    _set_direct_text(
        invalid_conditional,
        "Condition",
        "TubeFixingCase<>1 AND TubeFixingCase<>2 AND TubeFixingCase<>3 AND TubeFixingCase<>4",
    )
    _set_direct_text(invalid_conditional, "Name", "Invalid TubeFixingScript case")

    target_parent = _parent_map(payload).get(id(target_commands))
    if target_parent is None:
        raise TubeFixingHarnessError("Commands has no parent")
    index = list(target_parent).index(target_commands)
    target_parent.remove(target_commands)
    target_parent.insert(index, new_commands)


def _case_group(
    *,
    group_template: Any,
    prompt_template: Any,
    set_variable_template: Any,
    cycle_template: Any | None,
    case: TubeFixingCase | None,
) -> Any:
    wrapper = copy.deepcopy(group_template)
    group = _direct_child(wrapper, "ConditionalGroup")
    objects = _direct_child(group, "Objects") if group is not None else None
    if group is None or objects is None:
        raise TubeFixingHarnessError("Conditional group template is malformed")
    for child in list(objects):
        objects.remove(child)

    if case is None:
        objects.append(
            _prompt_object(
                prompt_template,
                "Invalid TubeFixingScript case. Enter 1, 2, 3, or 4 at startup; no cap motion was executed.",
            )
        )
        return wrapper

    _set_direct_text(group, "Condition", f"TubeFixingCase={case.case_id}")
    _set_direct_text(
        group,
        "Name",
        f"Case {case.case_id}: cap close {case.grip_cap_close}, seat Z {case.cap_seat_z_offset:g}",
    )
    objects.append(
        _prompt_object(
            prompt_template,
            f"Case {case.case_id}: GripCapClose {case.grip_cap_close}; final cap_50mL ZOffset {case.cap_seat_z_offset:g}. "
            "This uses one fresh tube/cap only. Close the door before pressing OK.",
        )
    )
    objects.append(_set_variable_object(set_variable_template, "GripCapClose", str(case.grip_cap_close)))
    if cycle_template is None:  # pragma: no cover - protects accidental future misuse
        raise TubeFixingHarnessError("Native cap cycle is required for every test case")
    cycle = _native_cycle_object(cycle_template, case.cap_seat_z_offset)
    objects.append(cycle)
    objects.append(
        _prompt_object(
            prompt_template,
            f"Case {case.case_id} complete. Inspect whether the cap is level and fully seated. "
            "Record PASS, visible gap, skew, or malfunction. Do not run another case with this tube/cap.",
        )
    )
    return wrapper


def _native_cycle_object(cycle_template: Any, cap_seat_z_offset: float) -> Any:
    cycle = copy.deepcopy(cycle_template)
    loop = _direct_child(cycle, "LoopGroup")
    objects = _direct_child(loop, "Objects") if loop is not None else None
    if loop is None or objects is None:
        raise TubeFixingHarnessError("Native cap cycle template is malformed")

    for child in list(objects):
        conditional = _direct_child(child, "ConditionalGroup")
        name = _direct_text(conditional, "Name") if conditional is not None else ""
        if name.strip() in {"No BC Scan AND Cap handling", "scan Barcode"}:
            objects.remove(child)

    retained_names = {
        _direct_text(_direct_child(child, "ConditionalGroup"), "Name").strip()
        for child in list(objects)
        if _direct_child(child, "ConditionalGroup") is not None
    }
    required = {"Remove cap from tube", "Add Cap to tube"}
    missing = sorted(required - retained_names)
    if missing:
        raise TubeFixingHarnessError("Native cap cycle lost required group(s): " + ", ".join(missing))
    if any("SubRoutineStatement" in str(obj.attrib.get("Type") or "") for obj in _iter_objects(cycle)):
        raise TubeFixingHarnessError("Native cap cycle still contains a SubRoutineStatement")

    _set_direct_text(loop, "Name", "Native decap and recap cycle")
    patched = _patch_final_cap_seat_offset(cycle, cap_seat_z_offset)
    if patched != 1:
        raise TubeFixingHarnessError(f"Expected exactly one final cap_50mL seat vector, patched {patched}")
    return cycle


def _patch_final_cap_seat_offset(cycle: Any, cap_seat_z_offset: float) -> int:
    patched = 0
    for obj in _iter_objects(cycle):
        if "ApplicationDriverMacro" not in str(obj.attrib.get("Type") or ""):
            continue
        settings = _first_descendant(obj, "ExecutionSettings")
        if settings is None or not (settings.text or "").strip():
            continue
        decoded = _decode_embedded_xml(settings.text or "")
        if not _is_final_cap_seat_vector(decoded):
            continue
        pattern = r"(<ZOffset>)8(?:\.0)?\.5(</ZOffset>)"
        if re.search(pattern, decoded) is None:
            raise TubeFixingHarnessError("Final cap_50mL vector did not contain literal ZOffset 8.5")
        replacement = re.sub(
            pattern,
            rf"\g<1>{cap_seat_z_offset:g}\g<2>",
            decoded,
            count=1,
        )
        settings.text = replacement
        patched += 1
    return patched


def _is_final_cap_seat_vector(settings: str) -> bool:
    try:
        inner = ET.fromstring(settings)
    except Exception:
        return False
    return (
        _first_text(inner, "VectorName") == "cap_50mL"
        and _first_text(inner, "Location") == "VialGripper_Right"
        and _first_text(inner, "MoveAction") == "FromSafePositionToEndPosition"
        # The source contains two otherwise similar cap_50mL vectors.  The
        # pickup vector has ExecutePostAction=true and ZOffset 0; only the
        # final seat vector has ExecutePostAction=false and source ZOffset 8.5.
        and _first_text(inner, "ExecutePostAction").casefold() == "false"
    )


def _decode_embedded_xml(value: str) -> str:
    decoded = value
    for _ in range(3):
        candidate = html.unescape(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    return decoded.strip()


def _prompt_object(template: Any, prompt: str) -> Any:
    result = copy.deepcopy(template)
    statement = _direct_child(result, "UserPromptStatement")
    if statement is None:
        raise TubeFixingHarnessError("User prompt template is malformed")
    _set_direct_text(statement, "Prompt", prompt)
    _set_direct_text(statement, "AutoClose", "false")
    _set_direct_text(statement, "Timeout", "1")
    return result


def _set_variable_object(template: Any, name: str, value: str) -> Any:
    result = copy.deepcopy(template)
    statement = _direct_child(result, "SetVariableStatement")
    if statement is None:
        raise TubeFixingHarnessError("SetVariable template is malformed")
    _set_direct_text(statement, "Name", name)
    _set_direct_text(statement, "Value", value)
    return result


def _discover_cases(root: Any) -> dict[int, dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}
    for obj in _iter_objects(root):
        group = _direct_child(obj, "ConditionalGroup")
        if group is None:
            continue
        condition = _direct_text(group, "Condition")
        match = re.fullmatch(r"TubeFixingCase=(\d+)", condition.strip())
        if not match:
            continue
        case_id = int(match.group(1))
        grip_values = [
            _direct_text(_direct_child(child, "SetVariableStatement"), "Value")
            for child in _direct_objects(_direct_child(group, "Objects"))
            if _direct_text(_direct_child(child, "SetVariableStatement"), "Name") == "GripCapClose"
        ]
        loops = [
            child
            for child in _direct_objects(_direct_child(group, "Objects"))
            if _direct_child(child, "LoopGroup") is not None
        ]
        offsets: list[float] = []
        for loop in loops:
            for command in _iter_objects(loop):
                settings = _first_descendant(command, "ExecutionSettings")
                if settings is None:
                    continue
                decoded = _decode_embedded_xml(settings.text or "")
                if not _is_final_cap_seat_vector(decoded):
                    continue
                match_offset = re.search(r"<ZOffset>([^<]+)</ZOffset>", decoded)
                if match_offset:
                    offsets.append(float(match_offset.group(1)))
        found[case_id] = {
            "grip_cap_close": int(grip_values[0]) if len(grip_values) == 1 and grip_values[0].isdigit() else None,
            "cap_seat_z_offset": offsets[0] if len(offsets) == 1 else None,
            "native_cycle_count": len(loops),
        }
    return found


def _native_command_counts(root: Any) -> dict[str, int]:
    names = (
        "ApplicationDriverMacro",
        "TeGioSetPWMOutputStatement",
        "MoveAxisCommandScriptStatement",
        "StartMoveCommandScriptStatement",
        "WaitForAsyncResponseScriptStatement",
        "CgaGetFingersScriptCommandDataV1",
        "CgaDropFingersScriptCommandDataV1",
    )
    counts = {name: 0 for name in names}
    for obj in _iter_objects(root):
        type_name = str(obj.attrib.get("Type") or "")
        for name in names:
            if name in type_name:
                counts[name] += 1
    return counts


def _line_numbers(root: Any) -> list[int]:
    values: list[int] = []
    for element in root.iter():
        if _local(element.tag) == "LineNumber" and (element.text or "").strip().isdigit():
            values.append(int((element.text or "").strip()))
        raw = element.attrib.get("LineNumber")
        if raw is not None and str(raw).strip().isdigit():
            values.append(int(str(raw).strip()))
    return values


def _renumber_line_numbers(root: Any) -> None:
    next_line = 1
    for element in root.iter():
        if _local(element.tag) == "LineNumber":
            element.text = str(next_line)
            next_line += 1
        if "LineNumber" in element.attrib:
            element.attrib["LineNumber"] = str(next_line)
            next_line += 1


def _empty_group_names(root: Any) -> list[str]:
    group_tags = {"ScriptGroup", "ConditionalGroup", "LoopGroup", "ScriptGroupDataV1", "AlternateGroup"}
    missing = []
    for element in root.iter():
        if _local(element.tag) not in group_tags:
            continue
        name = _direct_child(element, "Name")
        if name is not None and not (name.text or "").strip():
            missing.append(_local(element.tag))
    return missing


def _fill_empty_group_names(root: Any) -> None:
    group_tags = {"ScriptGroup", "ConditionalGroup", "LoopGroup", "ScriptGroupDataV1", "AlternateGroup"}
    counter = 1
    for element in root.iter():
        if _local(element.tag) not in group_tags:
            continue
        name = _direct_child(element, "Name")
        if name is not None and not (name.text or "").strip():
            name.text = f"TubeFixing native group {counter}"
            counter += 1


def _recompute_checksum(path: Path) -> None:
    rewritten = recompute_checksum_bytes(path.read_bytes())
    if rewritten is None:
        raise TubeFixingHarnessError("No checksum backend could recompute the generated XSCR checksum")
    path.write_bytes(rewritten)
    state = entry_checksum_state(path.read_bytes())
    if state != "valid":
        raise TubeFixingHarnessError(f"Generated XSCR checksum is {state}, expected valid")


def _variable_definition(root: Any, name: str) -> Any | None:
    for element in root.iter():
        if "VariableDefinitionHelper" not in " ".join(str(value) for value in element.attrib.values()):
            continue
        if _direct_text(element, "Name") == name:
            return element
    return None


def _variable_definitions_container(variable_declarations: Any) -> Any | None:
    for element in variable_declarations.iter():
        if any("VariableDefinitionHelper" in str(value) for value in element.attrib.values()):
            return _parent_map(variable_declarations).get(id(element))
    return None


def _set_variable_value(definition: Any, value: str) -> None:
    values = _direct_child(definition, "Values")
    if values is None:
        raise TubeFixingHarnessError("Variable definition is missing Values")
    value_node = next((child for child in list(values) if _local(child.tag) == "string"), None)
    if value_node is None:
        raise TubeFixingHarnessError("Variable definition is missing its default value")
    value_node.text = value


def _top_level_objects(commands: Any) -> list[Any]:
    script_group = _direct_child(commands, "ScriptGroup")
    objects = _direct_child(script_group, "Objects") if script_group is not None else None
    if objects is None:
        raise TubeFixingHarnessError("Source Commands does not contain ScriptGroup/Objects")
    return _direct_objects(objects)


def _find_conditional_object(objects: Iterable[Any], name: str) -> Any | None:
    for obj in objects:
        conditional = _direct_child(obj, "ConditionalGroup")
        if conditional is not None and _direct_text(conditional, "Name").strip() == name:
            return obj
    return None


def _find_loop_object(objects: Iterable[Any], name: str) -> Any | None:
    for obj in objects:
        loop = _direct_child(obj, "LoopGroup")
        if loop is not None and _direct_text(loop, "Name").strip() == name:
            return obj
    return None


def _find_object_with_type(objects: Iterable[Any], fragment: str, *, recursive: bool = False) -> Any | None:
    for obj in objects:
        candidates = _iter_objects(obj) if recursive else (obj,)
        for candidate in candidates:
            if fragment in str(candidate.attrib.get("Type") or ""):
                return candidate
    return None


def _find_comment_object(objects: Iterable[Any], text: str) -> Any | None:
    for obj in objects:
        comment = _direct_child(obj, "CommentStatement")
        if comment is not None and _direct_text(comment, "Comment").strip() == text:
            return obj
    return None


def _direct_objects(parent: Any | None) -> list[Any]:
    if parent is None:
        return []
    return [child for child in list(parent) if _local(child.tag) == "Object"]


def _iter_objects(root: Any) -> Iterable[Any]:
    return (element for element in root.iter() if _local(element.tag) == "Object")


def _iter_local(root: Any, name: str) -> Iterable[Any]:
    return (element for element in root.iter() if _local(element.tag) == name)


def _first_descendant(root: Any | None, name: str) -> Any | None:
    if root is None:
        return None
    return next(_iter_local(root, name), None)


def _first_text(root: Any, name: str) -> str:
    element = _first_descendant(root, name)
    return (element.text or "").strip() if element is not None else ""


def _direct_child(root: Any | None, name: str) -> Any | None:
    if root is None:
        return None
    return next((child for child in list(root) if _local(child.tag) == name), None)


def _direct_text(root: Any | None, name: str) -> str:
    child = _direct_child(root, name)
    return (child.text or "").strip() if child is not None else ""


def _set_direct_text(root: Any, name: str, value: str) -> None:
    child = _direct_child(root, name)
    if child is None:
        raise TubeFixingHarnessError(f"Expected direct XML field {name!r} is missing")
    child.text = value


def _parent_map(root: Any) -> dict[int, Any]:
    return {id(child): parent for parent in root.iter() for child in list(parent)}


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _register_source_namespaces(payloads: Iterable[bytes]) -> None:
    for payload in payloads:
        text = payload.decode("utf-8-sig", errors="replace")
        for prefix, uri in re.findall(r"\sxmlns(?::([A-Za-z_][\w.-]*))?=[\"']([^\"']+)[\"']", text):
            if not prefix or re.fullmatch(r"ns\d+", prefix):
                continue
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                # An existing registration or a reserved prefix only affects
                # cosmetic serialization; the namespace URI remains intact.
                continue
