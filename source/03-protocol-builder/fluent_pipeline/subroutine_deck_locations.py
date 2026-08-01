"""Resolve and apply CapBC / tube-scan prep bindings for verification subroutines."""

from __future__ import annotations

import re
from . import xml_compat as ET
from pathlib import Path
from typing import Any, Mapping

from .initialization_worktables import _norm, _workspace_by_name, workspace_catalog
from .request_spec import recipe_subroutine_name

INPUT_SUB_LOCATION_TARGET = "InputSubLocation"
TUBE_LOCATION_VARIABLE = "TubeLocationName"
GRIPPER_CLOSE_VARIABLE = "GripperClose"
GRIPPER_OPEN_VARIABLE = "GripperOpen"
CAPBC_SUBROUTINE_PATTERN = re.compile(r"CapBC", re.IGNORECASE)
INVALID_LOCATION_LITERALS = {"0", "0.0", ""}
CAPBC_PREP_VARIABLES = (
    GRIPPER_CLOSE_VARIABLE,
    GRIPPER_OPEN_VARIABLE,
    TUBE_LOCATION_VARIABLE,
    "TubeRunnerName",
    "TubeLabwareTypeName",
)
# Fallback emit order only when no source XSCR SetVariable sequence is available.
# Prefer ``extract_set_variable_order_from_xscr`` / ``prep_emit_order`` from ZEIA.
CAPBC_PREP_STEP_ORDER = (
    "TubeRunnerName",
    TUBE_LOCATION_VARIABLE,
    "TubeLabwareTypeName",
    GRIPPER_OPEN_VARIABLE,
    GRIPPER_CLOSE_VARIABLE,
)
CAPBC_PREP_GROUP_NAME = "CapBC prep"
STRING_PREP_VARIABLES = {TUBE_LOCATION_VARIABLE, "TubeRunnerName", "TubeLabwareTypeName"}
NUMERIC_PREP_VARIABLES = {GRIPPER_CLOSE_VARIABLE, GRIPPER_OPEN_VARIABLE}
_TUBE_LOCATION_VAR_NAMES = {INPUT_SUB_LOCATION_TARGET, TUBE_LOCATION_VARIABLE}
# Soft ranking only when mining source scripts — never the sole enable gate.
_SCRIPT_NAME_HINTS = ("CapBC", "ScanTubes")
_TUBE_PREP_VAR_NAMES = set(CAPBC_PREP_VARIABLES) | {INPUT_SUB_LOCATION_TARGET}
# Names that count as tube/CapBC prep schema when mined from subroutine decls /
# call mappings (not a closed product list — decls expand the set).
_PREP_SCHEMA_NAME_RE = re.compile(
    r"(?:"
    r"Gripper(?:Close|Open)|GripTube(?:Close|Open)?"
    r"|Tube(?:Location|Runner|Labware|Position|Type)Name"
    r"|TubeLocationName|TubeRunnerName|TubeLabwareTypeName"
    r"|InputSubLocation|InputSub(?:Location)?"
    r")",
    re.IGNORECASE,
)
_GRIP_CANONICAL = {
    "GripTubeClose": GRIPPER_CLOSE_VARIABLE,
    "griptubeclose": GRIPPER_CLOSE_VARIABLE,
    "GripTubeOpen": GRIPPER_OPEN_VARIABLE,
    "griptubeopen": GRIPPER_OPEN_VARIABLE,
}



def mappings_include_tube_prep(mappings: Any) -> bool:
    """True when variable mappings declare tube-prep / CapBC binding targets."""
    if not isinstance(mappings, list):
        return False
    for item in mappings:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or item.get("Target") or "").strip()
        if target in _TUBE_PREP_VAR_NAMES:
            return True
    return False


def subroutine_needs_tube_prep(
    *,
    name: str = "",
    mappings: Any = None,
    declared_variables: Any = None,
) -> bool:
    """Enable tube/CapBC prep from declared vars — not CapBC name alone.

    Name tokens (CapBC / ScanTubes) remain a soft secondary signal when mappings
    are empty but the call is clearly that subroutine family.
    """
    if mappings_include_tube_prep(mappings):
        return True
    if isinstance(declared_variables, (set, list, tuple)):
        names = {str(item or "").strip() for item in declared_variables if str(item or "").strip()}
        if names & _TUBE_PREP_VAR_NAMES:
            return True
        if any(is_tube_prep_schema_name(item) for item in names):
            return True
    base = str(name or "").strip().rsplit("\\", 1)[-1]
    if CAPBC_SUBROUTINE_PATTERN.search(base):
        return True
    if re.search(r"ScanTubes", base, re.IGNORECASE):
        return True
    return False


def is_tube_prep_schema_name(name: str) -> bool:
    """True when a VariableDefinition / mapping name belongs to tube/CapBC prep schema."""
    text = str(name or "").strip()
    if not text:
        return False
    if text in _TUBE_PREP_VAR_NAMES:
        return True
    return bool(_PREP_SCHEMA_NAME_RE.search(text))


def canonicalize_prep_variable_name(name: str) -> str:
    """Map CapBC subroutine targets (e.g. GripTubeClose) onto caller prep vars."""
    text = str(name or "").strip()
    if not text:
        return ""
    if text in CAPBC_PREP_VARIABLES or text in _TUBE_LOCATION_VAR_NAMES:
        return text
    return _GRIP_CANONICAL.get(text) or _GRIP_CANONICAL.get(text.casefold()) or text


def mine_prep_schema_from_mappings(mappings: Any) -> set[str]:
    """Collect prep schema names from subroutine call VariableMappings."""
    names: set[str] = set()
    if not isinstance(mappings, list):
        return names
    for item in mappings:
        if not isinstance(item, Mapping):
            continue
        for key in ("target", "Target", "source", "Source"):
            raw = str(item.get(key) or "").strip()
            if not raw or raw.startswith('"'):
                continue
            # Literal deck locations are not schema variable names.
            if _unwrap_literal(raw) != raw:
                continue
            if is_tube_prep_schema_name(raw) or canonicalize_prep_variable_name(raw) in _TUBE_PREP_VAR_NAMES:
                names.add(canonicalize_prep_variable_name(raw) or raw)
                if is_tube_prep_schema_name(raw):
                    names.add(raw)
    return names


def mine_prep_schema_from_subroutine_decls(
    xscr_path: Path | None,
) -> dict[str, dict[str, Any]]:
    """Return CapBC/tube prep VariableDefinitions from a subroutine ``.xscr``."""
    if xscr_path is None:
        return {}
    from .subroutine_variable_mappings import variable_definitions_from_xscr

    definitions = variable_definitions_from_xscr(Path(xscr_path))
    out: dict[str, dict[str, Any]] = {}
    for name, definition in definitions.items():
        if is_tube_prep_schema_name(name):
            out[name] = dict(definition)
    return out


def grip_values_from_subroutine_decls(
    declarations: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Mine GripperClose/Open defaults from CapBC subroutine VariableDefinitions."""
    if not isinstance(declarations, Mapping):
        return {}
    values: dict[str, Any] = {}
    for raw_name, definition in declarations.items():
        if not isinstance(definition, Mapping):
            continue
        canonical = canonicalize_prep_variable_name(str(raw_name))
        if canonical not in NUMERIC_PREP_VARIABLES:
            continue
        default = definition.get("default_value")
        if default is None or str(default).strip() == "":
            continue
        coerced = _coerce_prep_value(canonical, default)
        if _needs_prep_fixup(canonical, coerced):
            continue
        values.setdefault(canonical, coerced)
    return values


def looks_like_tube_runner_name(name: str) -> bool:
    """Exact phrase gate — no bare ``tube`` / Falcon invent."""
    text = str(name or "").casefold()
    return "tube runner" in text or "tube holder" in text


def mine_tube_runner_from_placements(
    manifest: Mapping[str, Any] | None,
    worktable_name: str,
) -> dict[str, str]:
    """Mine ``TubeRunnerName`` / ``TubeLabwareTypeName`` from worktable placements."""
    if not manifest or not worktable_name:
        return {}
    workspace = _workspace_by_name(workspace_catalog(manifest)).get(_norm(worktable_name))
    if not workspace:
        # Fall back to any workspace placements when recipe worktable unmatched.
        catalog = workspace_catalog(manifest)
        workspace = catalog[0] if len(catalog) == 1 else None
    if not isinstance(workspace, Mapping):
        return {}
    runner_name = ""
    labware_type = ""
    for placement in workspace.get("placements") or []:
        if not isinstance(placement, Mapping):
            continue
        catalog = str(
            placement.get("catalog")
            or placement.get("component_name")
            or placement.get("name")
            or ""
        ).strip()
        label = str(placement.get("label") or placement.get("name") or "").strip()
        if looks_like_tube_runner_name(catalog) or looks_like_tube_runner_name(label):
            runner_name = catalog or label
            # Nested/occupant catalog when present.
            nested = str(
                placement.get("labware_type")
                or placement.get("labware_catalog")
                or placement.get("child_catalog")
                or ""
            ).strip()
            if nested:
                labware_type = nested
            break
    out: dict[str, str] = {}
    if runner_name:
        out["TubeRunnerName"] = runner_name
    if labware_type:
        out["TubeLabwareTypeName"] = labware_type
    return out


def collect_tube_prep_subroutine_names(
    recipe: Mapping[str, Any] | None = None,
    ir: Mapping[str, Any] | None = None,
) -> list[str]:
    """CapBC/ScanTubes (or mapping-declared tube-prep) subroutine names from recipe/IR."""
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str, mappings: Any = None) -> None:
        text = str(name or "").strip()
        if not text or text.casefold() in seen:
            return
        if not subroutine_needs_tube_prep(name=text, mappings=mappings):
            return
        seen.add(text.casefold())
        names.append(text)

    if isinstance(recipe, Mapping):
        for group in recipe.get("groups") or []:
            if not isinstance(group, Mapping):
                continue
            for step in group.get("steps") or []:
                if not isinstance(step, Mapping):
                    continue
                sub_name = recipe_subroutine_name(step)
                mappings = None
                sub = step.get("subroutine") if isinstance(step.get("subroutine"), dict) else step
                if isinstance(sub, dict):
                    mappings = sub.get("variable_mappings_start")
                _add(sub_name, mappings)
    if isinstance(ir, Mapping):
        for step in ir.get("steps") or []:
            if not isinstance(step, Mapping) or step.get("operation") != "call_subroutine":
                continue
            params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
            _add(str(params.get("subroutine") or ""), params.get("variable_mappings_start"))
    return names


def resolve_subroutine_xscr_path(context: Any, subroutine_name: str | None) -> Path | None:
    """Resolve an imported subroutine ``.xscr`` path by object name (same as scripts)."""
    return _source_script_xscr_path(context, subroutine_name)


def build_tube_prep_schema(
    *,
    recipe: Mapping[str, Any] | None = None,
    ir: Mapping[str, Any] | None = None,
    context: Any = None,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build CapBC/tube prep schema from subroutine decls + call mappings (+ known set).

    Returns ``{names, declarations, sources, subroutine_paths}``.
    """
    names: set[str] = set(CAPBC_PREP_VARIABLES)
    declarations: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {name: "known CapBC prep set" for name in CAPBC_PREP_VARIABLES}
    subroutine_paths: dict[str, str] = {}
    manifest = manifest if isinstance(manifest, Mapping) else _manifest_from_context(context)

    if isinstance(recipe, Mapping):
        for group in recipe.get("groups") or []:
            if not isinstance(group, Mapping):
                continue
            for step in group.get("steps") or []:
                if not isinstance(step, Mapping):
                    continue
                sub = step.get("subroutine") if isinstance(step.get("subroutine"), dict) else step
                mappings = sub.get("variable_mappings_start") if isinstance(sub, dict) else None
                for name in mine_prep_schema_from_mappings(mappings):
                    names.add(name)
                    sources.setdefault(name, "recipe subroutine VariableMappings")

    if isinstance(ir, Mapping):
        for step in ir.get("steps") or []:
            if not isinstance(step, Mapping) or step.get("operation") != "call_subroutine":
                continue
            params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
            for name in mine_prep_schema_from_mappings(params.get("variable_mappings_start")):
                names.add(name)
                sources.setdefault(name, "IR subroutine VariableMappings")

    for sub_name in collect_tube_prep_subroutine_names(recipe, ir):
        path = resolve_subroutine_xscr_path(context, sub_name)
        if path is None:
            continue
        subroutine_paths[sub_name] = str(path)
        decls = mine_prep_schema_from_subroutine_decls(path)
        for name, definition in decls.items():
            canonical = canonicalize_prep_variable_name(name)
            names.add(name)
            if canonical:
                names.add(canonical)
            declarations[name] = definition
            sources[name] = f"subroutine VariableDefinition ({sub_name})"
            if canonical and canonical != name:
                sources.setdefault(canonical, f"subroutine VariableDefinition ({sub_name})")

    # Manifest scripts that are CapBC/ScanTubes subroutines (no recipe yet).
    if isinstance(manifest, Mapping):
        for script in manifest.get("scripts") or []:
            if not isinstance(script, Mapping):
                continue
            object_name = str(script.get("object_name") or script.get("name") or "")
            if not subroutine_needs_tube_prep(name=object_name):
                continue
            path = resolve_subroutine_xscr_path(context, object_name)
            if path is None:
                continue
            subroutine_paths.setdefault(object_name, str(path))
            decls = mine_prep_schema_from_subroutine_decls(path)
            for name, definition in decls.items():
                names.add(name)
                declarations.setdefault(name, definition)
                sources.setdefault(name, f"subroutine VariableDefinition ({object_name})")

    return {
        "names": sorted(names, key=str.casefold),
        "declarations": declarations,
        "sources": sources,
        "subroutine_paths": subroutine_paths,
    }


def _unwrap_literal(source: Any) -> str:
    text = str(source or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text


def _literal_mapping_source(location: str) -> str:
    text = str(location or "").strip().strip('"')
    return f'"{text}"'


def _is_zero_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        text = _unwrap_literal(value).strip()
        if text in INVALID_LOCATION_LITERALS:
            return True
        try:
            return float(text) == 0.0
        except ValueError:
            return not text
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _needs_prep_fixup(
    name: str,
    value: Any,
    *,
    string_names: set[str] | None = None,
) -> bool:
    strings = string_names if string_names is not None else STRING_PREP_VARIABLES
    if name in NUMERIC_PREP_VARIABLES or name.casefold().startswith("grip"):
        return _is_zero_like(value)
    if name in strings or name in STRING_PREP_VARIABLES:
        literal = _unwrap_literal(value)
        return literal in INVALID_LOCATION_LITERALS or literal == "0"
    # Unknown schema names: treat empty/zero as needing fillup.
    literal = _unwrap_literal(value)
    return literal in INVALID_LOCATION_LITERALS or _is_zero_like(value)


def needs_prep_fixup(name: str, value: Any) -> bool:
    """Return True when a CapBC prep default is missing or an invalid zero-like value."""
    return _needs_prep_fixup(name, value)


def is_capbc_subroutine(name: str) -> bool:
    base = str(name or "").strip().rsplit("\\", 1)[-1]
    return bool(CAPBC_SUBROUTINE_PATTERN.search(base))


def _manifest_from_context(context: Any) -> Mapping[str, Any] | None:
    if context is None:
        return None
    manifest = getattr(context, "manifest", None)
    return manifest if isinstance(manifest, Mapping) else None


def _source_script_xscr_path(context: Any, source_script_name: str | None) -> Path | None:
    if context is None or not source_script_name:
        return None
    manifest = getattr(context, "manifest", None)
    root = getattr(context, "root", None)
    if not isinstance(manifest, Mapping) or root is None:
        return None
    target_norm = str(source_script_name).strip().casefold()
    for script in manifest.get("scripts") or []:
        if not isinstance(script, dict):
            continue
        object_name = str(script.get("object_name") or script.get("name") or "").strip()
        if not object_name:
            continue
        short_name = object_name.rsplit("\\", 1)[-1]
        if object_name.casefold() != target_norm and short_name.casefold() != target_norm:
            continue
        raw = script.get("resolved_path") or script.get("extracted_path") or ""
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = (Path(root) / path).resolve()
        if path.exists():
            return path
    return None


def _iter_set_variable_statements(xscr_path: Path) -> list[tuple[str, str]]:
    """Return ``(Name, Value)`` pairs in document order from a source XSCR."""
    if not xscr_path.exists():
        return []
    try:
        tree = ET.parse(xscr_path)
    except ET.ParseError:
        return []

    rows: list[tuple[str, str]] = []
    for obj in tree.getroot().iter():
        tag = obj.tag.rsplit("}", 1)[-1] if "}" in obj.tag else obj.tag
        if tag != "SetVariableStatement":
            continue
        name = ""
        value = ""
        for child in obj:
            child_tag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
            if child_tag == "Name":
                name = (child.text or "").strip()
            elif child_tag == "Value":
                value = (child.text or "").strip()
        if name:
            rows.append((name, value))
    return rows


def extract_set_variable_defaults_from_xscr(
    xscr_path: Path,
    variable_names: set[str] | frozenset[str],
) -> dict[str, str]:
    """Collect ``SetVariableStatement`` values from a source script XSCR."""
    wanted = {str(name).strip() for name in variable_names if str(name).strip()}
    found: dict[str, str] = {}
    for name, value in _iter_set_variable_statements(xscr_path):
        if name in wanted and name not in found and value:
            found[name] = value
    return found


def extract_set_variable_order_from_xscr(
    xscr_path: Path,
    variable_names: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Return first-seen ``SetVariableStatement`` Name order from a source XSCR.

    When ``variable_names`` is set, only those names are included (still in XSCR
    document order). Used as CapBC prep emit order so IR matches the ZEIA script.
    """
    wanted = None
    if variable_names is not None:
        wanted = {str(name).strip() for name in variable_names if str(name).strip()}
    order: list[str] = []
    seen: set[str] = set()
    for name, _value in _iter_set_variable_statements(xscr_path):
        if wanted is not None and name not in wanted:
            continue
        if name in seen:
            continue
        seen.add(name)
        order.append(name)
    return order


def capbc_prep_emit_order(
    prep_defaults: Mapping[str, Any],
    *,
    source_order: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Order CapBC prep vars: source XSCR SetVariable sequence, then fallbacks."""
    names: list[str] = []
    seen: set[str] = set()
    for name in source_order or ():
        key = str(name or "").strip()
        if not key or key not in prep_defaults or key in seen:
            continue
        seen.add(key)
        names.append(key)
    for name in CAPBC_PREP_STEP_ORDER:
        if name in prep_defaults and name not in seen:
            seen.add(name)
            names.append(name)
    for name in CAPBC_PREP_VARIABLES:
        if name in prep_defaults and name not in seen:
            seen.add(name)
            names.append(name)
    for name in prep_defaults:
        key = str(name or "").strip()
        if key and key not in seen:
            seen.add(key)
            names.append(key)
    return names


def _script_worktable_name(
    recipe: Mapping[str, Any] | None,
    ir: Mapping[str, Any] | None,
) -> str:
    if isinstance(recipe, Mapping):
        name = str(recipe.get("worktable") or "").strip()
        if name:
            return name
    if isinstance(ir, Mapping):
        worktable = ir.get("worktable")
        if isinstance(worktable, Mapping):
            name = str(worktable.get("name") or "").strip()
            if name:
                return name
    return ""


def worktable_location_names(
    manifest: Mapping[str, Any] | None,
    worktable_name: str,
) -> list[str]:
    """Return location/site names for a worktable from imported ZEIA geometry."""
    if not manifest or not worktable_name:
        return []
    workspace = _workspace_by_name(workspace_catalog(manifest)).get(_norm(worktable_name))
    if not workspace:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for key in ("location_names", "pin_sites"):
        for item in workspace.get(key) or []:
            text = str(item or "").strip()
            if not text:
                continue
            key_norm = _norm(text)
            if key_norm in seen:
                continue
            seen.add(key_norm)
            names.append(text)
    for placement in workspace.get("placements") or []:
        if not isinstance(placement, Mapping):
            continue
        for key in ("site_name", "pin_name", "deck_location", "location"):
            text = str(placement.get(key) or "").strip()
            if not text:
                continue
            key_norm = _norm(text)
            if key_norm in seen:
                continue
            seen.add(key_norm)
            names.append(text)
    return names


def _location_from_ir_variables(ir: Mapping[str, Any] | None) -> str | None:
    if not isinstance(ir, Mapping):
        return None
    for variable in ir.get("variables") or []:
        if not isinstance(variable, Mapping):
            continue
        if str(variable.get("name") or "").strip() not in _TUBE_LOCATION_VAR_NAMES:
            continue
        candidate = _unwrap_literal(variable.get("value"))
        if candidate and candidate not in INVALID_LOCATION_LITERALS:
            return candidate
    return None


def _location_candidates_from_scripts(
    manifest: Mapping[str, Any] | None,
) -> list[tuple[str, str]]:
    """Return ``(location, script_name)`` mined from imported startup variables.

    Preferred CapBC/ScanTubes/Tube-named scripts come first; other scripts that
    declare tube-location variables follow. Values are never invented.
    """
    if not manifest:
        return []
    preferred: list[tuple[str, str]] = []
    fallback: list[tuple[str, str]] = []
    seen: set[str] = set()
    for script in manifest.get("scripts") or []:
        if not isinstance(script, dict):
            continue
        name = str(script.get("object_name") or script.get("name") or "")
        name_hint = any(token.casefold() in name.casefold() for token in _SCRIPT_NAME_HINTS)
        for variable in script.get("startup_variables") or script.get("variables") or []:
            if not isinstance(variable, dict):
                continue
            var_name = str(variable.get("name") or "").strip()
            if var_name not in _TUBE_LOCATION_VAR_NAMES:
                continue
            defaults = variable.get("default_values") or variable.get("values") or []
            if not defaults:
                continue
            candidate = _unwrap_literal(defaults[0])
            if not candidate or candidate in INVALID_LOCATION_LITERALS:
                continue
            key = _norm(candidate)
            if key in seen:
                continue
            seen.add(key)
            row = (candidate, name or "<script>")
            (preferred if name_hint else fallback).append(row)
    return preferred + fallback


def resolve_tube_deck_location(
    recipe: Mapping[str, Any] | None = None,
    *,
    context: Any = None,
    manifest: Mapping[str, Any] | None = None,
    ir: Mapping[str, Any] | None = None,
) -> tuple[str | None, str]:
    """Return ``(location_name, resolution_reason)`` for tube cap/scan subroutines.

    Fail-closed resolution order (no Falcon50/nest product invent):

    1. ``verification_recipe.tube_deck_location``
    2. Protocol IR ``TubeLocationName`` / ``InputSubLocation`` values
    3. Imported script/subroutine startup-variable defaults
    4. Prefer candidates that also appear on the declared worktable's imported
       geometry ``location_names`` / placements
    """
    manifest = manifest if isinstance(manifest, Mapping) else _manifest_from_context(context)

    if isinstance(recipe, Mapping):
        explicit = str(recipe.get("tube_deck_location") or "").strip().strip('"')
        if explicit:
            return explicit, "verification_recipe.tube_deck_location"

    ir_location = _location_from_ir_variables(ir)
    if ir_location:
        return ir_location, "protocol IR variables"

    candidates = _location_candidates_from_scripts(manifest)
    worktable_name = _script_worktable_name(recipe, ir)
    on_table = {_norm(name) for name in worktable_location_names(manifest, worktable_name)}

    if candidates and on_table:
        for location, script_name in candidates:
            if _norm(location) in on_table:
                return (
                    location,
                    f"source script defaults on worktable ({worktable_name}; from {script_name})",
                )

    if candidates:
        location, script_name = candidates[0]
        return location, f"source script/subroutine defaults ({script_name})"

    return None, "unresolved"


def resolve_capbc_prep_defaults(
    recipe: Mapping[str, Any] | None = None,
    *,
    context: Any = None,
    manifest: Mapping[str, Any] | None = None,
    ir: Mapping[str, Any] | None = None,
    source_script_name: str | None = None,
) -> dict[str, Any]:
    """Resolve CapBC prep values from recipe overrides or mined ZEIA sources.

    Schema (which prep vars exist) comes from CapBC/tube subroutine
    VariableDefinitions + call VariableMappings, not a closed product list.
    Values (in order): recipe overrides → source-script SetVariable → startup
    defaults → CapBC subroutine decl defaults (RGA grip) → tube-runner
    placements → worktable-ranked deck location.
    Never invent numeric grip widths (no 27/40 product default).
    """
    variables: dict[str, Any] = {}
    sources: dict[str, str] = {}
    manifest = manifest if isinstance(manifest, Mapping) else _manifest_from_context(context)
    schema = build_tube_prep_schema(recipe=recipe, ir=ir, context=context, manifest=manifest)
    schema_names = set(schema.get("names") or CAPBC_PREP_VARIABLES)
    # Always include known emit-order vars so existing CapBC scripts keep working.
    schema_names |= set(CAPBC_PREP_VARIABLES)
    string_names = set(STRING_PREP_VARIABLES) | {
        name
        for name in schema_names
        if name not in NUMERIC_PREP_VARIABLES
        and "Grip" not in name
        and "grip" not in name
    }

    if isinstance(recipe, Mapping):
        prep_block = recipe.get("capbc_prep")
        if isinstance(prep_block, Mapping):
            for name in schema_names:
                if name in prep_block and str(prep_block.get(name) or "").strip():
                    variables[name] = _coerce_prep_value(name, prep_block[name], string_names=string_names)
                    sources[name] = "verification_recipe.capbc_prep"
            # Allow recipe to introduce extra prep keys beyond mined schema.
            for name, value in prep_block.items():
                key = str(name or "").strip()
                if not key or key in variables or not str(value or "").strip():
                    continue
                if is_tube_prep_schema_name(key) or key in CAPBC_PREP_VARIABLES:
                    schema_names.add(key)
                    variables[key] = _coerce_prep_value(key, value, string_names=string_names)
                    sources[key] = "verification_recipe.capbc_prep"
        close_override = recipe.get("tube_gripper_close")
        open_override = recipe.get("tube_gripper_open")
        if close_override is not None and str(close_override).strip():
            variables[GRIPPER_CLOSE_VARIABLE] = _coerce_prep_value(
                GRIPPER_CLOSE_VARIABLE, close_override, string_names=string_names
            )
            sources[GRIPPER_CLOSE_VARIABLE] = "verification_recipe.tube_gripper_close"
        if open_override is not None and str(open_override).strip():
            variables[GRIPPER_OPEN_VARIABLE] = _coerce_prep_value(
                GRIPPER_OPEN_VARIABLE, open_override, string_names=string_names
            )
            sources[GRIPPER_OPEN_VARIABLE] = "verification_recipe.tube_gripper_open"

    xscr_path = _source_script_xscr_path(context, source_script_name)
    prep_emit_order: list[str] = []
    if xscr_path is not None:
        prep_emit_order = extract_set_variable_order_from_xscr(xscr_path, schema_names)
        extracted = extract_set_variable_defaults_from_xscr(xscr_path, schema_names)
        for name, value in extracted.items():
            if name in variables:
                continue
            if _needs_prep_fixup(name, value, string_names=string_names):
                continue
            variables[name] = _coerce_prep_value(name, value, string_names=string_names)
            sources[name] = f"source script SetVariable ({source_script_name})"

    for name, value, reason in _prep_values_from_script_startup(
        manifest,
        source_script_name=source_script_name,
        schema_names=schema_names,
    ):
        if name in variables:
            continue
        if _needs_prep_fixup(name, value, string_names=string_names):
            continue
        variables[name] = value
        sources[name] = reason

    # RGA grip widths from CapBC subroutine VariableDefinition defaults.
    for name, value in grip_values_from_subroutine_decls(schema.get("declarations")).items():
        if name in variables:
            continue
        variables[name] = value
        sources[name] = "CapBC subroutine VariableDefinition default"

    worktable_name = _script_worktable_name(recipe, ir)
    for name, value in mine_tube_runner_from_placements(manifest, worktable_name).items():
        if name in variables:
            continue
        variables[name] = value
        sources[name] = f"worktable runner placement ({worktable_name or 'imported'})"

    deck_location, deck_reason = resolve_tube_deck_location(
        recipe,
        context=context,
        manifest=manifest,
        ir=ir,
    )
    if deck_location and TUBE_LOCATION_VARIABLE not in variables:
        variables[TUBE_LOCATION_VARIABLE] = deck_location
        sources[TUBE_LOCATION_VARIABLE] = deck_reason

    return {
        "prep_variables": variables,
        "prep_sources": sources,
        "prep_emit_order": capbc_prep_emit_order(variables, source_order=prep_emit_order),
        "tube_deck_location": deck_location,
        "prep_schema": sorted(schema_names, key=str.casefold),
        "prep_schema_sources": dict(schema.get("sources") or {}),
        "subroutine_paths": dict(schema.get("subroutine_paths") or {}),
    }


def _coerce_prep_value(
    name: str,
    value: Any,
    *,
    string_names: set[str] | None = None,
) -> Any:
    strings = string_names if string_names is not None else STRING_PREP_VARIABLES
    if name in NUMERIC_PREP_VARIABLES or (
        name.casefold().startswith("grip") and name not in strings
    ):
        text = _unwrap_literal(value)
        try:
            return int(text) if str(text).strip().isdigit() else float(text)
        except (TypeError, ValueError):
            return value
    if name in strings or name in STRING_PREP_VARIABLES:
        return _unwrap_literal(value)
    return _unwrap_literal(value)


def _prep_values_from_script_startup(
    manifest: Mapping[str, Any] | None,
    *,
    source_script_name: str | None = None,
    schema_names: set[str] | None = None,
) -> list[tuple[str, Any, str]]:
    """Mine CapBC prep vars from imported script ``startup_variables`` defaults."""
    if not isinstance(manifest, Mapping):
        return []
    wanted = set(schema_names) if schema_names is not None else set(CAPBC_PREP_VARIABLES)
    wanted |= set(CAPBC_PREP_VARIABLES)
    target_norm = str(source_script_name or "").strip().casefold()
    # name -> (value, reason, priority) where lower priority wins
    best: dict[str, tuple[Any, str, int]] = {}

    for script in manifest.get("scripts") or []:
        if not isinstance(script, dict):
            continue
        object_name = str(script.get("object_name") or script.get("name") or "").strip()
        short_name = object_name.rsplit("\\", 1)[-1]
        is_named = bool(target_norm) and (
            object_name.casefold() == target_norm or short_name.casefold() == target_norm
        )
        name_hint = any(token.casefold() in object_name.casefold() for token in _SCRIPT_NAME_HINTS)
        priority = 0 if is_named else 1 if name_hint else 2
        for variable in script.get("startup_variables") or script.get("variables") or []:
            if not isinstance(variable, dict):
                continue
            var_name = str(variable.get("name") or "").strip()
            if var_name not in wanted and not is_tube_prep_schema_name(var_name):
                continue
            defaults = variable.get("default_values") or variable.get("values") or []
            if not defaults:
                continue
            raw = defaults[0]
            if _needs_prep_fixup(var_name, raw):
                continue
            coerced = _coerce_prep_value(var_name, raw)
            if _needs_prep_fixup(var_name, coerced):
                continue
            existing = best.get(var_name)
            if existing is not None and existing[2] <= priority:
                continue
            reason = (
                f"source script startup defaults ({object_name or short_name or '<script>'})"
            )
            best[var_name] = (coerced, reason, priority)
    return [(name, value, reason) for name, (value, reason, _prio) in best.items()]


def mapping_needs_input_sub_location_fix(mapping: Mapping[str, Any]) -> bool:
    if str(mapping.get("target") or "").strip() != INPUT_SUB_LOCATION_TARGET:
        return False
    source = str(mapping.get("source") or "").strip()
    if source == TUBE_LOCATION_VARIABLE:
        return True
    literal = _unwrap_literal(source)
    return literal in INVALID_LOCATION_LITERALS


def normalize_variable_mappings(
    mappings: list[dict[str, Any]] | None,
    deck_location: str,
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(mappings, list):
        return [], 0
    normalized: list[dict[str, Any]] = []
    fixups = 0
    literal = _literal_mapping_source(deck_location)
    for item in mappings:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if mapping_needs_input_sub_location_fix(row):
            row["source"] = literal
            fixups += 1
        normalized.append(row)
    return normalized, fixups


def normalize_recipe_subroutine_deck_locations(
    recipe: dict[str, Any],
    deck_location: str,
) -> list[dict[str, str]]:
    fixups: list[dict[str, str]] = []
    for group in recipe.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name") or "")
        for step in group.get("steps") or []:
            if not isinstance(step, dict) or recipe_subroutine_name(step) == "":
                continue
            if not subroutine_needs_tube_prep(
                name=recipe_subroutine_name(step),
                mappings=(
                    (step.get("subroutine") or {}).get("variable_mappings_start")
                    if isinstance(step.get("subroutine"), dict)
                    else step.get("variable_mappings_start")
                ),
            ):
                continue
            sub = step.get("subroutine") if isinstance(step.get("subroutine"), dict) else step
            if not isinstance(sub, dict):
                sub = step
            mappings = sub.get("variable_mappings_start")
            if not isinstance(mappings, list):
                mappings = step.get("variable_mappings_start")
            if not isinstance(mappings, list):
                continue
            updated, count = normalize_variable_mappings(mappings, deck_location)
            if count:
                if isinstance(step.get("subroutine"), dict):
                    step["subroutine"]["variable_mappings_start"] = updated
                else:
                    step["variable_mappings_start"] = updated
                fixups.append(
                    {
                        "group": group_name,
                        "subroutine": recipe_subroutine_name(step),
                        "target": INPUT_SUB_LOCATION_TARGET,
                        "source": _literal_mapping_source(deck_location),
                        "count": str(count),
                    }
                )
    return fixups


def _normalize_ir_prep_variables(
    variables: list[Any] | None,
    prep_defaults: Mapping[str, Any],
) -> tuple[list[Any], list[dict[str, str]]]:
    if not isinstance(variables, list):
        variables = []
    updated: list[Any] = []
    fixups: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in variables:
        if not isinstance(item, dict):
            updated.append(item)
            continue
        name = str(item.get("name") or "").strip()
        seen.add(name)
        if name in prep_defaults and _needs_prep_fixup(name, item.get("value")):
            row = dict(item)
            row["value"] = prep_defaults[name]
            updated.append(row)
            fixups.append(
                {
                    "variable": name,
                    "from": str(item.get("value")),
                    "to": str(prep_defaults[name]),
                }
            )
            continue
        updated.append(item)
    for name, value in prep_defaults.items():
        if name in seen:
            continue
        updated.append({"name": name, "value": value})
        fixups.append({"variable": name, "from": "(missing)", "to": str(value)})
    return updated, fixups


def format_set_variable_value(name: str, value: Any) -> str | int | float:
    """Format a prep default for IR / ``wt.set_variable`` (not XML-escaped)."""
    if name in STRING_PREP_VARIABLES:
        return _unwrap_literal(value)
    if name in NUMERIC_PREP_VARIABLES:
        try:
            return int(value) if str(value).strip().isdigit() else float(value)
        except (TypeError, ValueError):
            return value
    return value


def build_set_variable_ir_step(
    variable: str,
    value: Any,
    *,
    group: str,
    step_id: str,
    index: int,
) -> dict[str, Any]:
    return {
        "command_id": "SetVariableStatement",
        "group": group,
        "id": step_id,
        "index": index,
        "name": "Set Variable",
        "operation": "set_variable",
        "parameters": {
            "variable": variable,
            "value": format_set_variable_value(variable, value),
        },
        "safety_flags": [],
    }


def _set_variable_names_from_steps(steps: list[Any] | None) -> set[str]:
    names: set[str] = set()
    for step in steps or []:
        if not isinstance(step, dict) or step.get("operation") != "set_variable":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        name = str(params.get("variable") or "").strip()
        if name:
            names.add(name)
    return names


def _first_tube_prep_call_index(steps: list[Any] | None) -> int | None:
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        subroutine = str(params.get("subroutine") or "")
        if subroutine_needs_tube_prep(
            name=subroutine,
            mappings=params.get("variable_mappings_start"),
        ):
            return index
    return None


def reindex_ir_steps(steps: list[dict[str, Any]]) -> None:
    for index, step in enumerate(steps, start=1):
        step["index"] = index
        step["id"] = f"step_{index:03d}"


def emit_capbc_prep_set_variable_steps(
    ir: dict[str, Any],
    prep_defaults: Mapping[str, Any],
    *,
    group_name: str = CAPBC_PREP_GROUP_NAME,
    enabled: bool = True,
    source_order: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """Insert explicit ``set_variable`` IR steps before the first CapBC subroutine call.

    Emit order prefers ``source_order`` (source XSCR ``SetVariable`` sequence).
    """
    if not enabled or not prep_defaults:
        return []
    steps = ir.get("steps")
    if not isinstance(steps, list) or not steps:
        return []
    capbc_index = _first_tube_prep_call_index(steps)
    if capbc_index is None:
        return []

    existing = _set_variable_names_from_steps(steps)
    emitted: list[dict[str, str]] = []
    new_steps: list[dict[str, Any]] = []
    for name in capbc_prep_emit_order(prep_defaults, source_order=source_order):
        if name not in prep_defaults or name in existing:
            continue
        value = prep_defaults[name]
        new_steps.append(
            build_set_variable_ir_step(
                name,
                value,
                group=group_name,
                step_id="pending",
                index=0,
            )
        )
        emitted.append({"variable": name, "value": str(value), "group": group_name})
    if not new_steps:
        return []

    updated = list(steps[:capbc_index]) + new_steps + list(steps[capbc_index:])
    reindex_ir_steps(updated)
    ir["steps"] = updated
    report = ir.setdefault("source", {}).setdefault("subroutine_deck_locations", {})
    prior = list(report.get("prep_steps_emitted") or [])
    report["prep_steps_emitted"] = prior + emitted
    return emitted


def apply_subroutine_deck_location_bindings(
    ir: dict[str, Any],
    *,
    recipe: Mapping[str, Any] | None = None,
    context: Any = None,
    manifest: Mapping[str, Any] | None = None,
    source_script_name: str | None = None,
    emit_prep_steps: bool = True,
    prep_group_name: str = CAPBC_PREP_GROUP_NAME,
) -> dict[str, Any]:
    """Normalize CapBC prep bindings (deck location, grip widths, tube metadata) in IR."""
    prep = resolve_capbc_prep_defaults(
        recipe,
        context=context,
        manifest=manifest,
        ir=ir,
        source_script_name=source_script_name,
    )
    prep_defaults = dict(prep.get("prep_variables") or {})
    prep_emit_order = list(prep.get("prep_emit_order") or [])
    deck_location = str(prep.get("tube_deck_location") or "").strip() or None
    report: dict[str, Any] = {
        "tube_deck_location": deck_location,
        "prep_variables": prep_defaults,
        "prep_sources": dict(prep.get("prep_sources") or {}),
        "prep_emit_order": prep_emit_order,
        "prep_schema": list(prep.get("prep_schema") or []),
        "prep_schema_sources": dict(prep.get("prep_schema_sources") or {}),
        "mapping_fixups": [],
        "variable_fixups": [],
    }

    if deck_location:
        literal = _literal_mapping_source(deck_location)
        mapping_fixups: list[dict[str, str]] = []
        for step in ir.get("steps") or []:
            if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
                continue
            params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
            subroutine = str(params.get("subroutine") or "")
            if not subroutine_needs_tube_prep(
                name=subroutine,
                mappings=params.get("variable_mappings_start"),
            ):
                continue
            mappings = params.get("variable_mappings_start")
            if not isinstance(mappings, list):
                continue
            updated, count = normalize_variable_mappings(mappings, deck_location)
            if count:
                params["variable_mappings_start"] = updated
                step["parameters"] = params
                mapping_fixups.append(
                    {
                        "step_id": str(step.get("id") or ""),
                        "subroutine": subroutine,
                        "target": INPUT_SUB_LOCATION_TARGET,
                        "source": literal,
                    }
                )
        report["mapping_fixups"] = mapping_fixups

    if prep_defaults:
        variables, variable_fixups = _normalize_ir_prep_variables(ir.get("variables"), prep_defaults)
        if variable_fixups:
            ir["variables"] = variables
        report["variable_fixups"] = variable_fixups

    if prep_defaults:
        report["prep_steps_emitted"] = emit_capbc_prep_set_variable_steps(
            ir,
            prep_defaults,
            group_name=prep_group_name,
            enabled=emit_prep_steps,
            source_order=prep_emit_order,
        )

    ir.setdefault("source", {})["subroutine_deck_locations"] = report
    return report


def apply_deck_location_fixups_to_xscr(xscr_path: Any, deck_location: str) -> list[dict[str, str]]:
    """Backward-compatible wrapper for deck-location-only XSCR fixups."""
    return apply_capbc_prep_fixups_to_xscr(xscr_path, {TUBE_LOCATION_VARIABLE: deck_location})


def apply_capbc_prep_fixups_to_xscr(
    xscr_path: Any,
    prep_variables: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Post-compile safety net for CapBC prep variables and ``InputSubLocation`` mappings."""
    path = Path(xscr_path)
    if not prep_variables or not path.exists():
        return []
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return []

    fixups: list[dict[str, str]] = []
    changed = False
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def _text(parent: Any, name: str) -> str:
        for child in parent:
            if _local(child.tag) == name:
                return (child.text or "").strip()
        return ""

    def _set_text(parent: Any, name: str, value: str) -> None:
        for child in parent:
            if _local(child.tag) == name:
                child.text = value
                return
        elem = ET.SubElement(parent, name)
        elem.text = value

    deck_location = str(prep_variables.get(TUBE_LOCATION_VARIABLE) or "").strip()
    if deck_location:
        literal = _literal_mapping_source(deck_location)
        for obj in root.iter():
            if _local(obj.tag) != "Object" or obj.attrib.get("Type") != "Tecan.Core.Scripting.SubRoutineStatement":
                continue
            statement = next((c for c in obj if _local(c.tag) == "SubRoutineStatement"), None)
            if statement is None:
                continue
            subroutine = _text(statement, "SubRoutine")
            start_section = next((c for c in statement if _local(c.tag) == "VariableMappingsStart"), None)
            mapping_dicts: list[dict[str, str]] = []
            if start_section is not None:
                for mapping_object in start_section:
                    variable_mapping = next(
                        (c for c in mapping_object if _local(c.tag) == "VariableMapping"),
                        None,
                    )
                    if variable_mapping is None:
                        continue
                    mapping_dicts.append({"target": _text(variable_mapping, "Target")})
            if not subroutine_needs_tube_prep(name=subroutine, mappings=mapping_dicts):
                continue
            line = _text(obj, "LineNumber")
            for section_name in ("VariableMappingsStart", "VariableMappingsEnd"):
                section = next((c for c in statement if _local(c.tag) == section_name), None)
                if section is None:
                    continue
                for mapping_object in section:
                    variable_mapping = next(
                        (c for c in mapping_object if _local(c.tag) == "VariableMapping"),
                        None,
                    )
                    if variable_mapping is None:
                        continue
                    target = _text(variable_mapping, "Target")
                    if target != INPUT_SUB_LOCATION_TARGET:
                        continue
                    source = _text(variable_mapping, "Source")
                    if source == TUBE_LOCATION_VARIABLE or _unwrap_literal(source) in INVALID_LOCATION_LITERALS:
                        _set_text(variable_mapping, "Source", literal)
                        changed = True
                        fixups.append(
                            {
                                "line": line,
                                "subroutine": subroutine,
                                "target": INPUT_SUB_LOCATION_TARGET,
                                "source": literal,
                            }
                        )

    # Variable declarations live under <Properties><VariableDeclarations>, not inside
    # <Object> command nodes, so scan every VariableDefinitionHelper anyType element
    # directly. (The previous Object-only walk silently skipped the real startup
    # declarations, leaving mined/explicit CapBC prep values stuck at 0.)
    for helper in root.iter():
        if _local(helper.tag) != "anyType":
            continue
        if "VariableDefinitionHelper" not in " ".join(str(value) for value in helper.attrib.values()):
            continue
        name = _text(helper, "Name")
        if name not in prep_variables:
            continue
        desired = prep_variables[name]
        type_name = _text(helper, "TypeName")
        values_parent = next((c for c in helper if _local(c.tag) == "Values"), None)
        current = ""
        if values_parent is not None:
            for value_node in values_parent:
                if _local(value_node.tag) == "string":
                    current = (value_node.text or "").strip()
                    break
        if not _needs_prep_fixup(name, current if current else 0):
            continue
        rendered = _unwrap_literal(desired) if name in STRING_PREP_VARIABLES else str(desired)
        target_type = "String" if name in STRING_PREP_VARIABLES else "Floating Point"
        for child in helper:
            if _local(child.tag) == "TypeName":
                child.text = target_type
            elif _local(child.tag) == "Values":
                for value_node in list(child):
                    child.remove(value_node)
                value_elem = ET.SubElement(
                    child,
                    "{http://schemas.microsoft.com/2003/10/Serialization/Arrays}string",
                )
                value_elem.text = rendered
        changed = True
        fixups.append({"variable": name, "from": current or type_name, "to": rendered})

    if changed:
        tree.write(path, encoding="utf-8", xml_declaration=True)
    return fixups
