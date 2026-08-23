"""
Renderer for converting IR to Tecan XML format.

Takes validated Protocol IR and renders it to .xscr XML format
using templates and command definitions from the reference.
"""

import json
import os
import re
import uuid
import warnings
import yaml

_LIQUID_CLASS_INSTANCE_SUFFIX_RE = re.compile(r"\[\s*[^\]]+\s*\]\s*$")


def sanitize_text(text: str) -> str:
    """Sanitize text for XML - replace problematic characters."""
    if not text:
        return ""
    # Replace ampersand with "and" (Fluent Control doesn't like &)
    text = text.replace("&", "and")
    # Replace angle brackets with alternatives
    text = text.replace("<", "(").replace(">", ")")
    return text


def liquid_class_guid_from_catalog_entries(name: str, entries) -> str:
    """Name/alias → GUID matching for portable ``liquid_classes.json`` entries.

    Mirrors ``fluent_pipeline.liquid_classes_export.resolve_liquid_class_guid``:
    compare casefolded query and stripped ``Name [instance]`` bare form against
    entry ``name`` / ``aliases``. Never invents a GUID.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    bare = _LIQUID_CLASS_INSTANCE_SUFFIX_RE.sub("", text).strip() or text
    wanted = {text.casefold(), bare.casefold()}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        candidates = [entry.get("name"), *(entry.get("aliases") or [])]
        if any(str(candidate or "").strip().casefold() in wanted for candidate in candidates):
            guid = str(entry.get("guid") or "").strip()
            if guid:
                return guid
    return ""
from pathlib import Path
from typing import Optional, Dict

from ..catalog.fc_install import rewrite_checksum_in_place
from ..expressions import (
    BinaryExpression,
    NumberLiteral,
    StringLiteral,
    coerce_literal_expression,
    coerce_source_expression,
    expression_initial_value_text,
    expression_python_value,
    parse_expression,
    render_expression,
)
from ..fc_variables import decode_fc_variable
from ..ir.schema import (
    Protocol, Group, Step, StepType, STEP_TO_COMMAND_ID,
    AddLabwareStep, RemoveLabwareStep,
    GetHeadAdapterStep, DropHeadAdapterStep,
    PickUpTipsStep, SetTipsBackStep,
    AspirateStep, DispenseStep,
    RgaTransferLabwareStep, CgaGetFingersStep, CgaDropFingersStep,
    Mca384MixStep, WaitStep, LoopStep,
    ConditionalStep, CommentStep, UserPromptStep,
    SetVariableStep, CalculateVariableStep,
    Mca384EmptyTipsStep,
    Mca384GetTipsStep, Mca384DropTipsStep, Mca384MoveArmStep,
    LihaAspirateStep, LihaDispenseStep, LihaMixStep,
    LihaDetectLiquidStep, GenerateReportStep,
    LihaGetTipsStep, LihaDropTipsStep, LihaEmptyTipsStep,
    ExportVariableStep, ImportVariableStep, QueryVariableStep,
    InitializeDeviceStep, ExecuteApplicationStep, DelayStep, SetLocationStep, SubRoutineStep,
    MoveAxisCommandStep, StartMoveCommandStep, WaitForAsyncResponseStep, EndScriptStep,
    ExecuteVbScriptStep, TeGioSetPwmOutputStep, LeaveStep,
    VariableMapping, GenericStep, ScriptGroupStep, ApplicationDriverMacroStep,
)


_EVA_CONFIG = {
    "name": "EVA",
    "display_name": "EVA (Extended Volume)",
    "x_count": 12,
    "y_count": 8,
    "x_spacing": 9,
    "y_spacing": 9,
    "tool_id": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi96.ExtVol",
    "can_mount_tecan_ditis": True,
    "tip_type": "MCA96",
    "partial_columns": 12,
    "partial_rows": 8,
    "last_tip_x": 12,
    "last_tip_y": 8,
}

_384_COMBO_CONFIG = {
    "name": "384_Combo",
    "display_name": "384 Tips Combo (Partial Tips)",
    "x_count": 24,
    "y_count": 16,
    "x_spacing": 4.5,
    "y_spacing": 4.5,
    "tool_id": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi384.Combo",
    "can_mount_tecan_ditis": False,
    "tip_type": "MCA384",
    "partial_columns": 24,
    "partial_rows": 16,
    "last_tip_x": 24,
    "last_tip_y": 16,
}

_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[\])?$")


def _is_valid_variable_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    candidate = name.strip()
    if not candidate:
        return False
    return bool(_VARIABLE_NAME_RE.fullmatch(candidate))


def _get_adapter_config(labware_name: str) -> Dict:
    """Get adapter configuration from database by labware name."""
    # Fast path: recognize EVA by labware name
    if labware_name and "eva" in labware_name.lower():
        return dict(_EVA_CONFIG)

    try:
        from ..database import get_database
        db = get_database()
        config = db.get_adapter_config(labware_name)
        # Database returns 384 Combo default when adapter not found;
        # detect EVA patterns even if DB doesn't know the labware name.
        if config.get("name") == "384_Combo" and labware_name and "eva" in labware_name.lower():
            return dict(_EVA_CONFIG)
        return config
    except Exception:
        return dict(_384_COMBO_CONFIG)


class RenderError(Exception):
    """Raised when rendering fails."""
    pass


# RFC 4122 uuid5 namespace for stable WorkspaceDelta identifiers.
_WORKSPACE_DELTA_NAMESPACE = uuid.UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")

_WORKSPACE_BINDING_STRICT_ERROR = (
    "Protocol is not bound to a specific worktable workspace. "
    "Set Protocol.worktable_guid/worktable_name or build the worktable "
    "with Worktable.from_workspace(...). "
    "Strict workspace binding is enabled "
    "(validation.strict_workspace_binding or Renderer(strict_workspace_binding=True))."
)

_WORKSPACE_BINDING_MISSING_ERROR = (
    "Protocol is not bound to a specific worktable workspace and "
    "generation.yaml provides no worktable fallback. "
    "Set Protocol.worktable_guid/worktable_name or build the worktable "
    "with Worktable.from_workspace(...)."
)

_WORKSPACE_BINDING_FALLBACK_WARNING = (
    "Protocol lacks explicit worktable_guid/worktable_name; falling back to "
    "generation.yaml default ({name!r}, guid={guid!r}). Bind the protocol with "
    "Worktable.from_workspace(...) or set Protocol.worktable_guid/worktable_name. "
    "Set validation.strict_workspace_binding: true to fail instead."
)

_DEVICE_BINDING_MISSING_ERROR = (
    "Device-bearing step has empty DeviceAlias and/or AvailableID and "
    "generation.yaml provides no device/cga_device/liha_device fallback. "
    "Set step.device_alias + step.available_id from ZEIA/recipe (or Protocol / "
    "site overlay). Never invent USB:… or Instrument=… placeholders."
)

_DEVICE_BINDING_CROSS_FILL_ERROR = (
    "AvailableID must be a ZEIA/install device path (typically USB:…), not a "
    "DeviceAlias (Instrument=…). Refusing alias→AvailableID cross-fill."
)

_RGA_MODULE_MISSING_ERROR = (
    "RGA transfer step has empty ModuleName and generation.yaml provides no "
    "fallback. Set step.module_name from ZEIA/recipe — never invent 'RGA 1'."
)


class Renderer:
    """
    Renders Protocol IR to Tecan XML format.

    Uses templates from the templates/ directory and command
    definitions from the reference/commands.yaml file.
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        reference_path: Optional[Path] = None,
        templates_path: Optional[Path] = None,
        *,
        deterministic: bool = False,
        strict_workspace_binding: Optional[bool] = None,
    ):
        """
        Initialize renderer.

        Args:
            config_path: Path to generation config YAML
            reference_path: Path to commands.yaml reference
            templates_path: Path to templates directory
            deterministic: When True, emit a stable WorkspaceDelta GUID derived
                from the protocol name instead of a fresh random UUID.
            strict_workspace_binding: When True, fail if the protocol lacks
                explicit ``worktable_guid``/``worktable_name`` instead of
                falling back to ``generation.yaml``. When None, read
                ``validation.strict_workspace_binding`` (default False).
        """
        self.deterministic = deterministic
        self.strict_workspace_binding = strict_workspace_binding
        # Asset bundle lives inside the package at fluentcoder/_assets/.
        assets_dir = Path(__file__).resolve().parent.parent / "_assets"

        self.config_path = config_path or assets_dir / "config" / "generation.yaml"
        self.reference_path = reference_path or assets_dir / "reference" / "commands.yaml"
        self.templates_path = templates_path or assets_dir / "templates"

        self.config = self._load_config()
        self.commands = self._load_commands()
        self.templates = self._load_templates()
        self.labware_reference = self._load_labware_reference()
        self._current_adapter_config: Optional[Dict] = None  # Tracks adapter state during rendering
        self._labware_types: Dict[str, str] = {}  # label -> labware_type mapping for tip type lookup
        self._labware_placements: Dict[tuple[str, int], str] = {}  # (location, position) -> label

    @staticmethod
    def _step_type_name(step) -> str:
        step_type = getattr(step, "step_type", None)
        return getattr(step_type, "value", step_type or "")

    def _workspace_delta_guid(self, protocol: Protocol) -> str:
        if not self.deterministic:
            return str(uuid.uuid4())
        name = (protocol.name or "").strip() or "unnamed"
        return str(uuid.uuid5(_WORKSPACE_DELTA_NAMESPACE, name))

    def _strict_workspace_binding_enabled(self) -> bool:
        if self.strict_workspace_binding is not None:
            return bool(self.strict_workspace_binding)
        validation = self.config.get("validation") or {}
        return bool(validation.get("strict_workspace_binding", False))

    def _resolve_worktable_binding(self, protocol: Protocol) -> tuple[str, str]:
        """Resolve worktable GUID/name from protocol IR or config fallback."""
        worktable_guid = (protocol.worktable_guid or "").strip()
        worktable_name = (protocol.worktable_name or "").strip()
        if worktable_guid and worktable_name:
            return worktable_guid, worktable_name

        if self._strict_workspace_binding_enabled():
            raise RenderError(_WORKSPACE_BINDING_STRICT_ERROR)

        config_wt = self.config.get("worktable") or {}
        config_guid = (config_wt.get("guid") or "").strip()
        config_name = (config_wt.get("name") or "").strip()
        if not config_guid or not config_name:
            raise RenderError(_WORKSPACE_BINDING_MISSING_ERROR)

        warnings.warn(
            _WORKSPACE_BINDING_FALLBACK_WARNING.format(
                name=config_name,
                guid=config_guid,
            ),
            UserWarning,
            stacklevel=3,
        )
        return config_guid, config_name

    def _device_config_section(self, role: str) -> dict:
        key = {"device": "device", "cga": "cga_device", "liha": "liha_device"}.get(role, "device")
        section = self.config.get(key)
        return section if isinstance(section, dict) else {}

    def _resolve_device_pair(
        self,
        protocol: Protocol,
        *,
        step_alias: str | None = None,
        step_available_id: str | None = None,
        role: str = "device",
    ) -> tuple[str, str]:
        """Resolve DeviceAlias + AvailableID without invent or alias↔id cross-fill.

        Order: step fields → Protocol.device_alias / Protocol.available_id →
        generation.yaml ``device`` / ``cga_device`` / ``liha_device``.
        """
        cfg = self._device_config_section(role)
        alias = str(
            step_alias
            or getattr(protocol, "device_alias", None)
            or cfg.get("alias")
            or ""
        ).strip()
        available_id = str(
            step_available_id
            or getattr(protocol, "available_id", None)
            or cfg.get("available_id")
            or ""
        ).strip()
        return alias, available_id

    def _require_device_pair(self, alias: str, available_id: str) -> tuple[str, str]:
        """Fail closed when DeviceAlias/AvailableID unset or cross-filled."""
        alias = str(alias or "").strip()
        available_id = str(available_id or "").strip()
        if not alias or not available_id:
            raise RenderError(_DEVICE_BINDING_MISSING_ERROR)
        # Refuse Instrument=… stuffed into AvailableID (alias→id cross-fill invent).
        if available_id.casefold().startswith("instrument="):
            raise RenderError(_DEVICE_BINDING_CROSS_FILL_ERROR)
        return alias, available_id

    def _assert_template_device_bindings(self, template: str, params: dict) -> None:
        """When the command template binds a device, require non-empty ZEIA IDs."""
        if "{{DeviceAlias}}" in template:
            alias = str(params.get("DeviceAlias") or "").strip()
            available_id = str(params.get("AvailableID") or "").strip()
            self._require_device_pair(alias, available_id)
            return
        # RGA / driver macros: AvailableID without DeviceAlias.
        if "{{AvailableID}}" in template and "{{ModuleName}}" in template:
            available_id = str(params.get("AvailableID") or "").strip()
            module_name = str(params.get("ModuleName") or "").strip()
            if not module_name:
                raise RenderError(_RGA_MODULE_MISSING_ERROR)
            if not available_id:
                raise RenderError(_DEVICE_BINDING_MISSING_ERROR)
            if available_id.casefold().startswith("instrument="):
                raise RenderError(_DEVICE_BINDING_CROSS_FILL_ERROR)

    def _load_config(self) -> dict:
        """Load generation configuration."""
        if not self.config_path.exists():
            raise RenderError(f"Config not found: {self.config_path}")
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _load_commands(self) -> dict:
        """Load command definitions from reference."""
        if not self.reference_path.exists():
            raise RenderError(f"Reference not found: {self.reference_path}")
        with open(self.reference_path) as f:
            data = yaml.safe_load(f)
        # Index commands by ID
        return {cmd["id"]: cmd for cmd in data.get("commands", [])}

    def _load_templates(self) -> dict:
        """Load XML templates."""
        templates = {}
        for template_file in self.templates_path.glob("*.xml"):
            templates[template_file.stem] = template_file.read_text(encoding="utf-8")
        return templates

    def _load_labware_reference(self) -> dict:
        """Load labware metadata (wells, category) keyed by labware name.

        Shipped ``labware.yaml`` is a curated dump (Falcon/Resolvex/…) — not product
        law. Opt in with ``FLUENTCODER_USE_LABWARE_YAML=1`` or prefer ZEIA
        ``labware_catalog.json`` / Components for wells and category.
        """
        import os

        if os.environ.get("FLUENTCODER_USE_LABWARE_YAML", "").strip().lower() not in {"1", "true", "yes"}:
            return {}
        ref_path = Path(__file__).resolve().parent.parent / "_assets" / "reference" / "labware.yaml"
        if not ref_path.exists():
            return {}
        with open(ref_path) as f:
            data = yaml.safe_load(f) or {}
        labware = {}
        for entry in data.get("labware", []):
            name = entry.get("name")
            if name:
                labware[name] = {
                    "wells": entry.get("wells"),
                    "category": entry.get("category"),
                    "functional_group": entry.get("functional_group"),
                }
        for entry in data.get("other", {}).get("types", []):
            name = entry.get("name")
            if name:
                labware[name] = {
                    "wells": entry.get("wells"),
                    "category": "other",
                    "functional_group": entry.get("functional_group"),
                }
        return labware

    def render(self, protocol: Protocol) -> str:
        """
        Render a Protocol IR to XML string.

        Args:
            protocol: The protocol to render

        Returns:
            Complete XML string for .xscr file
        """
        # Rendering performs compatibility normalizations and line-number
        # assignment on a private copy so callers keep a stable canonical IR.
        protocol = protocol.model_copy(deep=True)

        # Reset state for this render
        self._current_adapter_config = None
        self._labware_types = {}
        self._labware_placements = {}

        # Deck-compatibility normalization for transfers onto magnet cover sites.
        # This is protocol-agnostic and only applies when a protocol includes such transfers.
        self._normalize_for_magnet_cover_site(protocol)

        # Exact ZEIA labware_catalog canonicalize only — never fuzzy-rewrite
        # unknown names toward install DB hits.
        self._normalize_labware_names(protocol)

        # Pre-scan set_variable steps to build variable value map
        # (needed to resolve variable references in labware types, e.g. DitiType)
        self._variable_values: Dict[str, str] = {}
        for var_name, value in (getattr(protocol, "variable_defaults", {}) or {}).items():
            self._variable_values[var_name] = expression_initial_value_text(value)
        for group in protocol.groups:
            for step in group.steps:
                if self._step_type_name(step) == "set_variable":
                    self._variable_values[step.variable_name] = expression_initial_value_text(step.value)

        # Assign line numbers if not already done
        protocol.assign_line_numbers()

        # Render all groups
        groups_xml = []
        for group in protocol.groups:
            group_xml = self._render_group(group, protocol)
            groups_xml.append(group_xml)

        # Build variable declarations XML
        variable_declarations_xml = ""
        declared_variables = [
            v for v in (protocol.variables or [])
            if _is_valid_variable_name(v)
        ]
        declared_defaults = {
            name: value
            for name, value in (getattr(protocol, "variable_defaults", {}) or {}).items()
            if _is_valid_variable_name(name)
        }
        variable_metadata = {
            name: metadata
            for name, metadata in (getattr(protocol, "variable_metadata", {}) or {}).items()
            if _is_valid_variable_name(name) and isinstance(metadata, dict)
        }
        for var_name in declared_defaults:
            if var_name not in declared_variables:
                declared_variables.append(var_name)
        for var_name in variable_metadata:
            if var_name not in declared_variables:
                declared_variables.append(var_name)
        # Auto-declare target variables from calculate_variable steps — they are
        # runtime-computed variables that FC must know about but the model often
        # omits from the protocol.variables list.
        declared_set = set(declared_variables)
        for _grp in protocol.groups:
            for _stp in _grp.steps:
                stype = self._step_type_name(_stp)
                if stype == "calculate_variable":
                    tv = (_stp.target_variable or "").strip()
                    if tv and _is_valid_variable_name(tv) and tv not in declared_set:
                        declared_variables.append(tv)
                        declared_set.add(tv)
                elif stype == "loop":
                    # Auto-declare the loop variable so it's resolvable in expressions.
                    lv = (_stp.loop_variable or "").strip()
                    if lv and _is_valid_variable_name(lv) and lv not in declared_set:
                        declared_variables.append(lv)
                        declared_set.add(lv)
                    for _inner in (_stp.steps or []):
                        if self._step_type_name(_inner) == "calculate_variable":
                            tv = (_inner.target_variable or "").strip()
                            if tv and _is_valid_variable_name(tv) and tv not in declared_set:
                                declared_variables.append(tv)
                                declared_set.add(tv)
        if declared_variables:
            # Detect variable types and initial values from declared defaults and runtime set steps
            var_info: dict[str, tuple[str, str]] = {}  # name -> (type, initial_value)

            # First pass: collect variables used in string-typed fields (labware names, types)
            string_vars: set[str] = set()
            volume_vars: set[str] = set()
            STRING_FIELDS = {'labware_name', 'labware_type', 'label', 'location',
                             'destination_location', 'liquid_class'}
            VOLUME_FIELDS = {'volume'}
            for group in protocol.groups:
                for step in group.steps:
                    for field_name in STRING_FIELDS:
                        val = getattr(step, field_name, None)
                        if not isinstance(val, str):
                            continue
                        fc_name = decode_fc_variable(val)
                        if fc_name:
                            string_vars.add(fc_name)
                        elif val in declared_variables:
                            string_vars.add(val)
                    for field_name in VOLUME_FIELDS:
                        val = getattr(step, field_name, None)
                        if isinstance(val, str) and val in declared_variables:
                            volume_vars.add(val)

            def _infer_var_info(name: str, value):
                expr_value = coerce_literal_expression(value)
                py_value = expression_python_value(expr_value)
                if name in string_vars or name.endswith("LocationName") or name.endswith("RunnerName"):
                    return ("String", str(py_value) if py_value is not None else "")
                if name in volume_vars:
                    try:
                        return ("Floating Point", str(float(py_value)))
                    except (TypeError, ValueError):
                        return ("Floating Point", "0")
                if isinstance(expr_value, NumberLiteral):
                    number = expr_value.value
                    return ("Floating Point", str(float(number)) if isinstance(number, int) else str(number))
                if isinstance(py_value, str) and py_value.replace('.', '', 1).lstrip('-').isdigit():
                    return ("Floating Point", py_value)
                return ("String", str(py_value) if py_value is not None else "")

            def _metadata_type(name: str) -> Optional[str]:
                metadata = variable_metadata.get(name) or {}
                raw = str(metadata.get("type_name") or metadata.get("type") or "").strip()
                if not raw:
                    return None
                folded = raw.casefold()
                if folded in {"system.string", "string"}:
                    return "String"
                if folded in {"system.int32", "system.integer", "integer", "int", "int32"}:
                    return "Integer"
                if folded in {"system.double", "system.single", "double", "single", "float", "floating point"}:
                    return "Floating Point"
                return raw

            def _metadata_scope(name: str) -> str:
                metadata = variable_metadata.get(name) or {}
                return str(metadata.get("scope") or "Script").strip() or "Script"

            def _coerce_initial_value(var_type: str, value) -> str:
                text = expression_initial_value_text(value)
                if var_type == "Integer":
                    try:
                        return str(int(float(text)))
                    except (TypeError, ValueError):
                        return "0"
                return text

            # Second pass: get types and initial values from declared defaults and runtime set_variable steps
            for var_name, value in declared_defaults.items():
                var_info[var_name] = _infer_var_info(var_name, value)

            for group in protocol.groups:
                for step in group.steps:
                    if self._step_type_name(step) == "set_variable":
                        if step.variable_name not in var_info:
                            var_info[step.variable_name] = _infer_var_info(step.variable_name, step.value)

            ns = "http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared"
            vars_list = []
            for var_name in declared_variables:
                if var_name in string_vars:
                    default_type = ("String", "")
                elif var_name in volume_vars:
                    default_type = ("Floating Point", "0")
                else:
                    default_type = ("Floating Point", "0")
                var_type, initial_value = var_info.get(var_name, default_type)
                var_type = _metadata_type(var_name) or var_type
                initial_value = _coerce_initial_value(var_type, initial_value)
                scope = _metadata_scope(var_name)
                vars_list.append(
                    f'                <d2p1:anyType xmlns:d3p1="{ns}" i:type="d3p1:VariableDefinitionHelper">\n'
                    f'                  <d3p1:IdOfParentItem>00000000-0000-0000-0000-000000000000</d3p1:IdOfParentItem>\n'
                    f'                  <d3p1:Item></d3p1:Item>\n'
                    f'                  <d3p1:Name>{var_name}</d3p1:Name>\n'
                    f'                  <d3p1:QueryOnStartup>false</d3p1:QueryOnStartup>\n'
                    f'                  <d3p1:QueryOnStartupString></d3p1:QueryOnStartupString>\n'
                    f'                  <d3p1:ReadOnly>false</d3p1:ReadOnly>\n'
                    f'                  <d3p1:Scope>{sanitize_text(scope)}</d3p1:Scope>\n'
                    f'                  <d3p1:TypeName>{sanitize_text(var_type)}</d3p1:TypeName>\n'
                    f'                  <d3p1:Values>\n'
                    f'                    <d2p1:string>{sanitize_text(str(initial_value))}</d2p1:string>\n'
                    f'                  </d3p1:Values>\n'
                    f'                </d2p1:anyType>'
                )
            variable_declarations_xml = "\n".join(vars_list)

        # Fill in script wrapper template
        wrapper = self.templates.get("script_wrapper")
        if not wrapper:
            raise RenderError("Missing script_wrapper.xml template")

        # Worktable workspace binding: prefer explicit protocol fields; legacy
        # generation.yaml fallback warns unless strict mode is enabled.
        worktable_guid, worktable_name = self._resolve_worktable_binding(protocol)
        liquid_class_name = self._protocol_liquid_class_name(protocol)
        liquid_class_guid = self._resolve_liquid_class_guid(liquid_class_name) if liquid_class_name else ""

        xml = self._fill_template(wrapper, {
            "script_name": sanitize_text(protocol.name),  # Escape & < > for XML
            "comment": sanitize_text(protocol.comment or ""),
            "worktable_guid": worktable_guid,
            "worktable_name": worktable_name,
            "liquid_class_guid": liquid_class_guid,
            "liquid_class_name": liquid_class_name,
            "script_version": self.config["script"]["version"],
            "data_version": self.config["script"]["data_version"],
            "expected_duration": str(self.config["script"]["expected_duration"]),
            "workspace_delta_guid": self._workspace_delta_guid(protocol),
            "groups": "\n".join(groups_xml),
            "variable_declarations": variable_declarations_xml
        })

        return xml

    def _render_group(self, group: Group, protocol: Protocol) -> str:
        """Render a single group to XML."""
        template = self.templates.get("script_group")
        if not template:
            raise RenderError("Missing script_group.xml template")

        # Render all statements in the group
        statements_xml = []
        for step in group.steps:
            step_xml = self._render_step(step, protocol, group, loop_depth=0)
            if step_xml and step_xml.strip():
                statements_xml.append(step_xml)

        return self._fill_template(template, {
            "group_name": sanitize_text(group.name),  # Escape & < > for XML
            "group_line_number": str(group.line_number or 1),
            "statements": "\n".join(statements_xml)
        })

    def _render_step(self, step: Step, protocol: Protocol, group: Group, loop_depth: int = 0) -> str:
        """Render a single step to XML using command templates."""
        stype = self._step_type_name(step)
        if isinstance(step, GenericStep) and step.parameters.get("raw_xml"):
            lines = str(step.parameters["raw_xml"]).strip().split("\n")
            return "\n".join("                        " + line for line in lines)
        if isinstance(step, ApplicationDriverMacroStep) and step.raw_xml:
            lines = step.raw_xml.strip().split("\n")
            return "\n".join("                        " + line for line in lines)
        if isinstance(step, UserPromptStep) and step.raw_xml and step.rup_kind == "worktable":
            lines = str(step.raw_xml).strip().split("\n")
            return "\n".join("                        " + line for line in lines)
        if isinstance(step, UserPromptStep) and (
            step.rup_kind == "worktable"
            or (step.image_path is not None and step.rup_kind != "standard")
        ):
            xml = self._render_rup_worktable_prompt_step(step)
            lines = xml.strip().split("\n")
            return "\n".join("                        " + line for line in lines)
        if isinstance(step, (MoveAxisCommandStep, StartMoveCommandStep, WaitForAsyncResponseStep, EndScriptStep,
                           ExecuteVbScriptStep, TeGioSetPwmOutputStep, LeaveStep)) and getattr(step, "raw_xml", None):
            lines = str(step.raw_xml).strip().split("\n")
            return "\n".join("                        " + line for line in lines)
        if isinstance(step, (LihaAspirateStep, LihaDispenseStep, LihaMixStep, LihaDetectLiquidStep, GenerateReportStep)) and getattr(step, "raw_xml", None):
            lines = str(step.raw_xml).strip().split("\n")
            return "\n".join("                        " + line for line in lines)

        if stype == "set_variable":
            var_name = (step.variable_name or "").strip()
            if not _is_valid_variable_name(var_name):
                return ""
        elif stype == "calculate_variable":
            target_var = (step.target_variable or "").strip()
            if not _is_valid_variable_name(target_var):
                return ""

        # Special handling for Loops (recursive) — must be an actual LoopStep,
        # not a GenericStep with step_type="loop" (which lacks .steps).
        if stype == "loop":
            return self._render_loop(step, protocol, group, loop_depth=loop_depth + 1)
        if stype == "conditional":
            return self._render_conditional(step, protocol, group, loop_depth=loop_depth + 1)
        if stype == "script_group":
            return self._render_script_group_step(step, protocol, group, loop_depth=loop_depth + 1)
        if stype == "application_driver_macro":
            return self._render_application_driver_macro_step(step, params={
                "LineNumber": str(step.line_number or 0),
                "IsBreakpoint": str(step.breakpoint).lower(),
                "IsDisabledForExecution": str(step.disabled).lower(),
            })

        command_id = None

        # 1. If it's a typed step, use the explicit mapping
        if not isinstance(step, GenericStep):
            command_id = STEP_TO_COMMAND_ID.get(step.step_type)

        # 2. If it's a GenericStep, or we haven't found an ID yet...
        if not command_id:
            # Try to see if the string matches a known StepType enum value
            # This handles cases where dict_to_protocol fell back to GenericStep
            # but the type string was actually valid (just params were wrong)
            try:
                # normalize to snake_case if possible?
                # For now just try direct match
                enum_type = StepType(step.step_type)
                command_id = STEP_TO_COMMAND_ID.get(enum_type)
            except ValueError:
                pass

        # 3. If still no ID, use the snake_to_pascal conversion
        if not command_id:
            if isinstance(step, GenericStep) and step.step_type in self.commands:
                command_id = step.step_type

        if not command_id:
            command_id = self._step_type_to_command_id(step.step_type)

        # Legacy image-capable Standard prompts (rup_kind=standard) compile to
        # RUPStandardStatement. Worktable-bound prompts are handled above and
        # use RUPWorktableStatement with CustomDetailImageFilePath instead.
        if (
            self._step_type_name(step) == "user_prompt"
            and not isinstance(step, GenericStep)
            and getattr(step, "image_path", None) is not None
            and getattr(step, "rup_kind", None) == "standard"
        ):
            command_id = "UserPromptImage"

        if not command_id:
            raise RenderError(f"Unknown step type: {step.step_type}")

        command = self.commands.get(command_id)
        if not command:
            # Fallback: Check if it's an RGA command that maps to ApplicationDriverMacro
            if "Rga" in command_id and "Transfer" in command_id:
                 # Check if we have ApplicationDriverMacro in reference
                 if "ApplicationDriverMacro" in self.commands:
                     command_id = "ApplicationDriverMacro"
                     command = self.commands.get(command_id)

        if not command:
            raise RenderError(f"Command '{command_id}' not found in reference")

        template = command.get("template")
        if not template:
            raise RenderError(f"No template for command '{command_id}'")

        # Build parameter values from step
        params = self._step_to_params(step, protocol, group, loop_depth=loop_depth)

        # Fail closed: device-bearing templates need ZEIA DeviceAlias + AvailableID.
        self._assert_template_device_bindings(template, params)

        # Fill in template
        xml = self._fill_template(template, params)

        # Post-process LiHa commands to fix hardcoded template values
        if self._is_liha_step(step, command_id):
            xml = self._post_process_liha_xml(xml, step, params)
        xml = self._post_process_step_xml(xml, step, params)

        # Indent for proper nesting in group
        lines = xml.strip().split("\n")
        indented = "\n".join("                        " + line for line in lines)

        return indented

    def _render_script_group_step(
        self,
        step: ScriptGroupStep,
        protocol: Protocol,
        group: Group,
        loop_depth: int = 1,
    ) -> str:
        template = self.templates.get("script_group")
        if not template:
            raise RenderError("Missing script_group.xml template")

        inner_steps_xml = []
        for inner_step in step.steps:
            inner_xml = self._render_step(inner_step, protocol, group, loop_depth=loop_depth)
            trimmed = inner_xml.strip()
            if trimmed:
                inner_steps_xml.append(trimmed)
        statements = "\n".join(
            "                        " + line
            for step_xml in inner_steps_xml
            for line in step_xml.split("\n")
        )
        xml = self._fill_template(template, {
            "group_name": sanitize_text(step.name),
            "group_line_number": str(step.line_number or 1),
            "statements": statements,
        })
        lines = xml.strip().split("\n")
        return "\n".join("                        " + line for line in lines)

    def _render_application_driver_macro_step(
        self,
        step: ApplicationDriverMacroStep,
        *,
        params: dict,
    ) -> str:
        """Render an application-driver macro, preserving ExecutionSettings."""
        if (
            step.macro_name == "RGA1_TransferLabware"
            and step.parameters
            and not step.execution_settings
        ):
            dest_site = step.parameters.get("Site", "1")
            try:
                site_int = max(1, int(dest_site))
            except (ValueError, TypeError):
                site_int = 1
            transfer = RgaTransferLabwareStep(
                labware_name=step.parameters.get("Labware", ""),
                destination_location=step.parameters.get("Location", "Site"),
                destination_site=site_int,
                fixed_site=str(step.parameters.get("FixedSite", "true")).lower() == "true",
                move_to_base=str(step.parameters.get("MoveToBase", "false")).lower() == "true",
                module_name=step.module_name,
                available_id=step.available_id,
                line_number=step.line_number,
                disabled=step.disabled,
                breakpoint=step.breakpoint,
            )
            return self._render_step(transfer, Protocol(name=""), Group(name=""), loop_depth=0)

        # FluentControl stores ExecutionSettings as escaped inner XML.  The
        # decompiler receives its once-decoded text (``&lt;...``), so escape it
        # once more for the outer XSCR element.  Emitting it verbatim makes
        # transfer parameters become XML children and loses the RGA labware
        # target on the next parse.
        settings = self._xml_escape(step.execution_settings or "")
        xml = (
            '<Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">\n'
            f'  <ApplicationDriverMacro Version="1" Name="{sanitize_text(step.macro_name)}" '
            f'ModuleName="{sanitize_text(step.module_name)}" ExecutionTime="PT2S" '
            f'IsBreakpoint="{params["IsBreakpoint"]}" '
            f'IsDisabledForExecution="{params["IsDisabledForExecution"]}" '
            f'LineNumber="{params["LineNumber"]}">\n'
            f"    <ExecutionSettings>{settings}</ExecutionSettings>\n"
            "  </ApplicationDriverMacro>\n"
            "</Object>"
        )
        lines = xml.strip().split("\n")
        return "\n".join("                        " + line for line in lines)

    def _render_loop(self, step: LoopStep, protocol: Protocol, group: Group, loop_depth: int = 1) -> str:
        """Render a LoopStep recursively."""
        template = self.templates.get("loop_group")
        if not template:
            raise RenderError("Missing loop_group.xml template")

        # Render all steps inside the loop
        inner_steps_xml = []
        for inner_step in step.steps:
            inner_xml = self._render_step(inner_step, protocol, group, loop_depth=loop_depth)
            # Remove the base indentation added by _render_step because we'll add it ourselves
            trimmed = inner_xml.strip()
            if trimmed:
                inner_steps_xml.append(trimmed)

        # Indent inner steps for loop nesting (one level deeper than group statements)
        loop_statements = "\n".join("                              " + line 
                                   for step_xml in inner_steps_xml 
                                   for line in step_xml.split("\n"))

        # Use the loop variable name from the step so FC makes it available in-scope.
        # This allows calculate_variable expressions inside the loop to reference it.
        lv_raw = (step.loop_variable or "").strip()
        loop_variable = lv_raw if _is_valid_variable_name(lv_raw) else ""

        if step.number_of_loops is not None:
            from ..expressions import loop_count_expression_error

            if error := loop_count_expression_error(step.number_of_loops):
                raise RenderError(error)
            raw_count = self._expression_text(step.number_of_loops).strip()
        else:
            raw_count = str(step.iterations)

        # The expression validator has restricted this to a positive literal,
        # variable reference, or integer arithmetic accepted by FluentControl.
        number_of_loops = raw_count

        params = {
            "LoopName": sanitize_text(step.name),
            "NumberOfLoops": number_of_loops,
            "LoopVariable": loop_variable,
            "LineNumber": str(step.line_number or 0),
            "IsBreakpoint": str(step.breakpoint).lower().capitalize(),
            "IsDisabledForExecution": str(step.disabled).lower().capitalize(),
            "LoopStatements": loop_statements
        }

        xml = self._fill_template(template, params)
        
        # Indent the loop group itself to align with other steps in the group
        lines = xml.strip().split("\n")
        indented = "\n".join("                        " + line for line in lines)
        return indented

    def _render_conditional(self, step: ConditionalStep, protocol: Protocol, group: Group, loop_depth: int = 1) -> str:
        if_template = self.templates.get("conditional_group")
        else_template = self.templates.get("alternate_group")
        if not if_template or not else_template:
            raise RenderError("Missing conditional XML templates")

        then_xml = []
        for inner_step in step.then_steps:
            inner = self._render_step(inner_step, protocol, group, loop_depth=loop_depth)
            trimmed = inner.strip()
            if trimmed:
                then_xml.append(trimmed)
        then_statements = "\n".join(
            "                              " + line
            for step_xml in then_xml
            for line in step_xml.split("\n")
        )

        if step.condition is not None:
            condition = self._expression_text(step.condition)
        else:
            fc_operator = "=" if step.operator == "==" else step.operator
            if step.right_is_variable:
                right_text = str(self._expression_python_value(step.right_value))
            else:
                right_text = self._expression_text(step.right_value)
            condition = f"{step.left_variable}{fc_operator}{right_text}"

        if_xml = self._fill_template(
            if_template,
            {
                "ConditionName": sanitize_text(step.name),
                "Condition": self._xml_escape(condition),
                "LineNumber": str(step.line_number or 0),
                "IsBreakpoint": str(step.breakpoint).lower().capitalize(),
                "IsDisabledForExecution": str(step.disabled).lower().capitalize(),
                "ThenStatements": then_statements,
            },
        )

        parts = [if_xml.strip()]
        if step.else_steps:
            else_xml = []
            for inner_step in step.else_steps:
                inner = self._render_step(inner_step, protocol, group, loop_depth=loop_depth)
                trimmed = inner.strip()
                if trimmed:
                    else_xml.append(trimmed)
            else_statements = "\n".join(
                "                              " + line
                for step_xml in else_xml
                for line in step_xml.split("\n")
            )
            parts.append(
                self._fill_template(
                    else_template,
                    {
                        "AlternateName": sanitize_text(f"{step.name} Else"),
                        "LineNumber": str(step.line_number or 0),
                        "IsBreakpoint": str(step.breakpoint).lower().capitalize(),
                        "IsDisabledForExecution": str(step.disabled).lower().capitalize(),
                        "ElseStatements": else_statements,
                    },
                ).strip()
            )

        lines = "\n".join(parts).split("\n")
        return "\n".join("                        " + line for line in lines)

    def _post_process_step_xml(self, xml: str, step: Step, params: dict) -> str:
        stype = self._step_type_name(step)
        if stype in {"export_variable", "import_variable"}:
            xml = re.sub(
                r"<Variables>.*?</Variables>",
                lambda _: f"<Variables>\n{params.get('Variables', '')}\n    </Variables>",
                xml,
                count=1,
                flags=re.DOTALL,
            )
            tag = "ExportFile" if stype == "export_variable" else "ImportFile"
            for name in (tag, "WriteHeader", "ReplaceExistingFile", "ExportStringsWithQuotes", "DelimiterCode",
                         "ReadLine", "Line", "StartInColumn", "Column", "HasHeader"):
                value = params.get(name)
                if value is None:
                    continue
                xml = re.sub(fr"<{name}>.*?</{name}>", lambda _, n=name, v=value: f"<{n}>{v}</{n}>", xml, count=1, flags=re.DOTALL)

        elif stype == "query_variable":
            for name in ("Name", "QueryPrompt", "LimitRange"):
                xml = re.sub(fr"<{name}>.*?</{name}>", lambda _, n=name: f"<{n}>{params.get(n, '')}</{n}>", xml, count=1, flags=re.DOTALL)

        elif stype == "execute_application":
            for name in ("Application", "Wait", "StoreReturn", "IsBreakpoint", "IsDisabledForExecution", "LineNumber"):
                xml = re.sub(fr"<{name}>.*?</{name}>", lambda _, n=name: f"<{n}>{params.get(n, '')}</{n}>", xml, count=1, flags=re.DOTALL)
            arguments = params.get("Arguments", "")
            variable = params.get("Variable", "")
            xml = re.sub(r"<Arguments\s*/>", lambda _: f"<Arguments>{arguments}</Arguments>", xml, count=1)
            xml = re.sub(r"<Arguments>.*?</Arguments>", lambda _: f"<Arguments>{arguments}</Arguments>", xml, count=1, flags=re.DOTALL)
            xml = re.sub(r"<Variable\s*/>", lambda _: f"<Variable>{variable}</Variable>", xml, count=1)
            xml = re.sub(r"<Variable>.*?</Variable>", lambda _: f"<Variable>{variable}</Variable>", xml, count=1, flags=re.DOTALL)

        elif stype == "user_prompt":
            sound_file = params.get("SoundFile", "")
            sound_xml = f"<SoundFile>{sound_file}</SoundFile>" if sound_file else "<SoundFile />"
            xml = re.sub(
                r"<SoundFile\b[^>]*>.*?</SoundFile>|<SoundFile\s*/>",
                lambda _: sound_xml,
                xml,
                count=1,
                flags=re.DOTALL,
            )
            xml = re.sub(
                r"<RepeatSound\b[^>]*>.*?</RepeatSound>|<RepeatSound\s*/>",
                lambda _: f"<RepeatSound>{params.get('RepeatSound', 'False')}</RepeatSound>",
                xml,
                count=1,
                flags=re.DOTALL,
            )

        elif stype == "delay":
            xml = re.sub(r"<Delay>.*?</Delay>", lambda _: f"<Delay>{params.get('Delay', '')}</Delay>", xml, count=1, flags=re.DOTALL)

        elif stype == "initialize_device":
            for name in ("InitType", "DeviceAlias", "AvailableID"):
                value = params.get(name)
                if value is None:
                    continue
                xml = re.sub(
                    fr"<{name}>.*?</{name}>",
                    lambda _, n=name, v=value: f"<{n}>{v}</{n}>",
                    xml,
                    count=1,
                    flags=re.DOTALL,
                )

        elif stype == "set_location":
            for name in ("Labware", "Location", "Site", "Rotation"):
                xml = re.sub(fr"<{name}>.*?</{name}>", lambda _, n=name: f"<{n}>{params.get(n, '')}</{n}>", xml, count=1, flags=re.DOTALL)

        elif stype == "subroutine":
            xml = re.sub(r"<SubRoutine>.*?</SubRoutine>", lambda _: f"<SubRoutine>{params.get('SubRoutine', '')}</SubRoutine>", xml, count=1, flags=re.DOTALL)
            xml = re.sub(r"<ExecutionMode>.*?</ExecutionMode>", lambda _: f"<ExecutionMode>{params.get('ExecutionMode', '')}</ExecutionMode>", xml, count=1, flags=re.DOTALL)
            for name in ("VariableMappingsStart", "VariableMappingsEnd"):
                if re.search(fr"<{name}\s*/>", xml):
                    xml = re.sub(
                        fr"<{name}\s*/>",
                        lambda _, n=name: f"<{n}>\n{params.get(n, '')}\n    </{n}>",
                        xml,
                        count=1,
                    )
                xml = re.sub(
                    fr"<{name}>.*?</{name}>",
                    lambda _, n=name: f"<{n}>\n{params.get(n, '')}\n    </{n}>",
                    xml,
                    count=1,
                    flags=re.DOTALL,
                )
        return xml

    def _step_type_to_command_id(self, step_type: str) -> str:
        """Convert snake_case step_type to PascalCase command ID."""
        # snake_case to PascalCase: mca384_pick_up_tips -> Mca384PickUpTips
        parts = step_type.split('_')
        return ''.join(word.capitalize() for word in parts)

    def _render_rup_worktable_prompt_step(self, step: UserPromptStep) -> str:
        """Render a generated TouchTools worktable prompt.

        Source loading prompts use RUPWorktableStatement and display
        detail media via CustomDetailImageFilePath. Generated verification
        prompts default to RUPStandardStatement; this template is for legacy
        worktable-bound prompts only.
        """
        auto_close = bool(step.auto_close) if step.auto_close is not None else step.timeout > 0
        line_number = str(step.line_number or 0)
        prompt = self._xml_escape_multiline(step.prompt)
        image_path = self._xml_escape(step.image_path or "")
        image_used = self._xs_bool_text(bool(step.image_path))
        screen_title = self._xml_escape(step.screen_title or "User Prompt")
        labware_name = self._xml_escape(step.selected_labware_name or "")
        labware_type = self._xml_escape(step.selected_labware_type or "")
        grid = self._worktable_int_text(step.grid)
        site = self._worktable_int_text(step.site)
        return f'''<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPWorktableStatement">
  <RUPWorktableStatement>
    <WorktableProperties>
      <WorktableStatementDataClass>
        <SelectedLabware>
          <ConfigureDataLabwareDataModel>
            <LabwareName>{labware_name}</LabwareName>
            <LabwareTypeName>{labware_type}</LabwareTypeName>
            <IsLabwareExistenceMandatory>false</IsLabwareExistenceMandatory>
            <LabwareImageState>UseAsLabwareImageOnTheLeft</LabwareImageState>
            <Grid>{grid}</Grid>
            <Site>{site}</Site>
            <LabwareState>Highlighted</LabwareState>
            <LabwareDescription />
            <LabwareDescriptionGui>{prompt}</LabwareDescriptionGui>
            <ActiveWorktableVisibility>Collapsed</ActiveWorktableVisibility>
            <ActiveWorktable>0</ActiveWorktable>
            <CustomDetailImageFilePath>{image_path}</CustomDetailImageFilePath>
            <IsCustomDetailImageUsed>{image_used}</IsCustomDetailImageUsed>
            <IsCarrier>false</IsCarrier>
            <MarkerColorAlpha>192</MarkerColorAlpha>
            <MarkerColorRed>255</MarkerColorRed>
            <MarkerColorGreen>153</MarkerColorGreen>
            <MarkerColorBlue>0</MarkerColorBlue>
          </ConfigureDataLabwareDataModel>
        </SelectedLabware>
        <WorktableFilePath />
        <IsCustomWorktableShown>false</IsCustomWorktableShown>
        <IsAdvancedLabwaresettingsUsed>false</IsAdvancedLabwaresettingsUsed>
        <DefaultItemState>Deactivated</DefaultItemState>
        <IsUserAproveRequired>false</IsUserAproveRequired>
      </WorktableStatementDataClass>
    </WorktableProperties>
    <IsBreakpoint>{self._bool_text(step.breakpoint)}</IsBreakpoint>
    <IsDisabledForExecution>{self._bool_text(step.disabled)}</IsDisabledForExecution>
    <LineNumber>{line_number}</LineNumber>
    <RUPScreenTitle>{screen_title}</RUPScreenTitle>
    <RUPDisplayAndWait>{self._bool_text(not auto_close)}</RUPDisplayAndWait>
    <RUPAutoClose>{self._bool_text(auto_close)}</RUPAutoClose>
    <RUPTimeOut>{step.timeout if step.timeout > 0 else 1}</RUPTimeOut>
    <ChangeStatusLightColor>False</ChangeStatusLightColor>
    <StatusLightColorString />
  </RUPWorktableStatement>
</Object>'''

    @staticmethod
    def _bool_text(value: bool) -> str:
        return str(bool(value)).lower().capitalize()

    @staticmethod
    def _xs_bool_text(value: bool) -> str:
        """Lowercase XML schema booleans inside ConfigureDataLabwareDataModel."""
        return str(bool(value)).lower()

    @staticmethod
    def _worktable_int_text(value: object) -> str:
        text = "" if value is None else str(value).strip()
        return text or "0"

    @staticmethod
    def _xml_escape(value: str) -> str:
        text = "" if value is None else str(value)
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @classmethod
    def _xml_escape_multiline(cls, value: str) -> str:
        text = cls._xml_escape(value).replace("\r\n", "\n").replace("\r", "\n")
        return text.replace("\n", "&#10;")

    def _string_objects_xml(self, values: list[str]) -> str:
        if not values:
            return ""
        lines = []
        for value in values:
            escaped = self._xml_escape(value)
            lines.append('      <Object Type="System.String">')
            lines.append(f'        <string>{escaped}</string>')
            lines.append('      </Object>')
        return "\n".join(lines)

    def _variable_mappings_xml(self, mappings: list[VariableMapping]) -> str:
        if not mappings:
            return ""
        lines = []
        for mapping in mappings:
            lines.append('      <Object Type="Tecan.Core.Scripting.VariableMapping">')
            lines.append('        <VariableMapping>')
            lines.append(f'          <Target>{self._xml_escape(mapping.target)}</Target>')
            lines.append(f'          <Source>{self._xml_escape(self._expression_text(mapping.source))}</Source>')
            lines.append('        </VariableMapping>')
            lines.append('      </Object>')
        return "\n".join(lines)

    @staticmethod
    def _expression_text(value) -> str:
        return render_expression(coerce_source_expression(value))

    @classmethod
    def _expression_xml_text(cls, value) -> str:
        return cls._xml_escape(cls._expression_text(value))

    @staticmethod
    def _expression_python_value(value):
        return expression_python_value(coerce_source_expression(value))

    def _step_to_params(self, step: Step, protocol: Protocol, group: Group, loop_depth: int = 0) -> dict:
        """Convert step to template parameters."""
        # Resolve device / LC defaults from protocol + site config only — no USB invent.
        default_device, default_available_id = self._resolve_device_pair(protocol, role="device")
        default_liquid_class = str(
            protocol.liquid_class or self.config.get("liquid_class", {}).get("name") or ""
        ).strip()

        # Normalize liquid classes. Prefer protocol / catalog / generation.yaml
        # (site-supplied). Never invent a lab liquid-class name like AcidExtract
        # or a hardcoded "Water Free Single" string in product code.
        def _normalize_liquid_class(requested: str | None, *, mix: bool = False) -> str:
            config_lc = self.config.get("liquid_class") if isinstance(self.config.get("liquid_class"), dict) else {}
            if mix:
                mix_default = str(config_lc.get("mix_name") or "").strip()
                return (requested or mix_default or "").strip()

            candidate = (requested or default_liquid_class or "").strip()
            if not candidate:
                return ""

            bad = {
                "default init fca",
                "default init air fca",
                "default init",
            }
            config_default = str(config_lc.get("name") or "").strip()
            if candidate.lower() in bad:
                return config_default
            # "Water Mix" is for mix steps; for aspirate/dispense prefer config default when set.
            if candidate.lower() == "water mix" and config_default:
                return config_default
            return candidate

        def _normalize_well_offset_expr(offset_value) -> str:
            """Normalize common malformed loop-offset expressions to FC-compatible forms."""
            if offset_value is None:
                return "0"
            expr = self._expression_text(offset_value).strip()
            if not expr:
                return "0"
            if re.fullmatch(r"[+-]?\d+", expr):
                return expr

            # Local-model artifact: "<loop_var> + (col-1)*8" where col is undefined.
            m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*\+\s*\(col-1\)\*8", expr)
            if m:
                return f"({m.group(1)})*8"

            # If still references undefined placeholder 'col', strip that term conservatively.
            if "col" in expr:
                expr = expr.replace("(col-1)*8", "0")
                expr = re.sub(r"\bcol\b", "0", expr)
                expr = re.sub(r"\s+", "", expr)
                expr = re.sub(r"\+\s*0\b", "", expr)
                expr = re.sub(r"\b0\+", "", expr)
                expr = expr or "0"
            # Last guard: if expression is still a plain identifier, convert only known loop-ish
            # symbols and otherwise fall back to zero to avoid FC parse failures.
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
                return "0"
            return expr

        # CGA / LiHa labware overlays from config; device pair resolved via `_step_device`.
        cga_config = self._device_config_section("cga")
        cga_get_fingers_labware = str(cga_config.get("get_fingers_labware") or "").strip()
        cga_drop_fingers_labware = str(cga_config.get("drop_fingers_labware") or "").strip()

        liha_config = self._device_config_section("liha")
        liha_device, liha_available_id = self._resolve_device_pair(protocol, role="liha")
        liha_diti_type = str(liha_config.get("diti_type") or "").strip()
        liha_waste_labware = str(liha_config.get("waste_labware") or "").strip()

        def _step_device(step_obj, *, role: str = "device") -> tuple[str, str]:
            return self._resolve_device_pair(
                protocol,
                step_alias=getattr(step_obj, "device_alias", None),
                step_available_id=getattr(step_obj, "available_id", None),
                role=role,
            )
        params = {
            "LineNumber": str(step.line_number or 0),
            "GroupLineNumber": "0",  # Always 0 for individual steps (matches Tecan convention)
            "IsBreakpoint": str(step.breakpoint).lower().capitalize(),
            "IsDisabledForExecution": str(step.disabled).lower().capitalize(),
        }

        # Handle GenericStep - use parameters dict directly
        if isinstance(step, GenericStep):
            # Check if this is a get_head_adapter step to update adapter tracking
            step_type_lower = step.step_type.lower()
            if "get_head_adapter" in step_type_lower:
                labware_name = step.parameters.get("labware_name", "")
                self._current_adapter_config = _get_adapter_config(labware_name)
            elif "drop_head_adapter" in step_type_lower:
                self._current_adapter_config = None

            for key, value in step.parameters.items():
                # Convert snake_case to PascalCase for template placeholders
                pascal_key = ''.join(word.capitalize() for word in key.split('_'))
                params[pascal_key] = str(value) if value is not None else ""

            # Fill missing keys from protocol/config only (shipped yaml leaves these
            # empty). Template assert fails closed when DeviceAlias is required.
            if "DeviceAlias" not in params:
                params["DeviceAlias"] = default_device
            if "AvailableID" not in params:
                params["AvailableID"] = default_available_id
            if "LiquidClassName" not in params:
                params["LiquidClassName"] = default_liquid_class

            # Prefer explicit GenericStep device fields when present.
            if any(k in step.parameters for k in ("device_alias", "DeviceAlias", "available_id", "AvailableID")):
                alias, avail = self._resolve_device_pair(
                    protocol,
                    step_alias=step.parameters.get("device_alias") or step.parameters.get("DeviceAlias") or params.get("DeviceAlias"),
                    step_available_id=step.parameters.get("available_id") or step.parameters.get("AvailableID") or params.get("AvailableID"),
                    role="device",
                )
                params["DeviceAlias"] = alias
                params["AvailableID"] = avail

            # Add adapter-aware defaults for MCA operations
            adapter_params = self._get_adapter_params(default_available_id)
            for key, value in adapter_params.items():
                params.setdefault(key, value)

            return params

        match step.step_type:
            case StepType.ADD_LABWARE:
                fc_var_name = decode_fc_variable(step.labware_type)
                if fc_var_name:
                    # Emit the FluentControl variable name; do not substitute defaults.
                    labware_type_out = fc_var_name
                else:
                    # Resolve legacy variable-name labware types (e.g. "McaTipType" → "MCA384, 50ul")
                    labware_type_out = self._variable_values.get(
                        step.labware_type, step.labware_type
                    )
                # Track labware label -> type for later lookup (e.g. DitiType in GetTips)
                self._labware_types[step.label] = step.labware_type
                # Track placement so we can infer cover-site moves (e.g. placing a plate onto a magnet plate).
                position_text = self._expression_text(step.position)
                try:
                    self._labware_placements[(step.location, int(self._expression_python_value(step.position)))] = step.label
                except Exception:
                    pass
                params.update({
                    "LabwareType": labware_type_out,
                    "LabwareLable": step.label,  # Note: Tecan uses "Lable" not "Label"
                    "Location": step.location,
                    "Position": position_text,
                    "Rotation": str(step.rotation),
                    "HasLid": str(step.has_lid).lower().capitalize(),
                })

            case StepType.REMOVE_LABWARE:
                params.update({
                    "LabwareName": step.labware_name,
                })

            case StepType.GET_HEAD_ADAPTER:
                # Update adapter tracking
                self._current_adapter_config = _get_adapter_config(step.labware_name)
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "BlowoutAirgap": str(step.blowout_airgap),
                })

            case StepType.DROP_HEAD_ADAPTER:
                # Clear adapter tracking
                self._current_adapter_config = None
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "BlowoutAirgap": str(step.blowout_airgap),
                    "UseSourceAsBackPosition": step.back_position,
                    "AdapterAfterDrop": str(step.adapter_after_drop).lower().capitalize(),
                })

            case StepType.PICK_UP_TIPS:
                # Use adapter config for defaults if available
                adapter = self._current_adapter_config
                default_cols = adapter["partial_columns"] if adapter else 24
                default_rows = adapter["partial_rows"] if adapter else 16
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "BlowoutAirgap": str(step.blowout_airgap),
                    "PartialColumns": str(step.partial_columns if step.partial_columns else default_cols),
                    "PartialRows": str(step.partial_rows if step.partial_rows else default_rows),
                    "HeadPosition": step.head_position,
                })

            case StepType.SET_TIPS_BACK:
                # Use adapter config for defaults if available
                adapter = self._current_adapter_config
                default_cols = adapter["partial_columns"] if adapter else 24
                default_rows = adapter["partial_rows"] if adapter else 16
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "UseSourceAsBackPosition": step.back_position,
                    "PartialColumns": str(step.partial_columns if step.partial_columns else default_cols),
                    "PartialRows": str(step.partial_rows if step.partial_rows else default_rows),
                    "HeadPosition": step.head_position,
                })

            case StepType.ASPIRATE:
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": self._expression_text(step.volume),
                })

            case StepType.DISPENSE:
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": self._expression_text(step.volume),
                })

            case StepType.RGA_TRANSFER_LABWARE:
                # Correctly mapping to the template placeholders found in reference/commands.yaml
                # Template uses: {{LabwareName}}, {{DestinationLocation}}, {{DestinationSite}}, {{FixedSite}}, {{MoveToBase}}

                dest_location = step.destination_location
                dest_site = self._expression_text(step.destination_site)
                fixed_site = str(step.fixed_site).lower()

                # If the destination coordinates are occupied by a magnet plate, move to its "cover site"
                # using VisionX helpers: GetCoverSiteName/Index(<magnet_label>).
                magnet_label = None
                try:
                    magnet_label = self._labware_placements.get((step.destination_location, int(self._expression_python_value(step.destination_site))))
                except Exception:
                    magnet_label = None

                if magnet_label and magnet_label != step.labware_name:
                    meta = self._resolve_labware_meta(magnet_label)
                    magnet_type = (meta.get("type") or "").lower()
                    is_magnet = meta.get("category") == "magnet_plate" or any(
                        k in magnet_type for k in ("alpaqua", "magniflex", "magnet")
                    )
                    if is_magnet:
                        dest_location = f'GetCoverSiteName("{magnet_label}")'
                        dest_site = f'GetCoverSiteIndex("{magnet_label}")'
                        fixed_site = "true"

                params.update({
                    "LabwareName": step.labware_name,
                    "DestinationLocation": dest_location,
                    "DestinationSite": dest_site,
                    "FixedSite": fixed_site,
                    "MoveToBase": str(step.move_to_base).lower(),
                    "ModuleName": str(
                        getattr(step, "module_name", None)
                        or getattr(protocol, "rga_module_name", None)
                        or ""
                    ).strip(),
                    "AvailableID": str(getattr(step, "available_id", None) or default_available_id).strip(),
                    # Robotic driver macros require lowercase bools for these specific attributes
                    "IsBreakpoint": str(step.breakpoint).lower(),
                    "IsDisabledForExecution": str(step.disabled).lower(),
                })
                if not params["ModuleName"]:
                    raise RenderError(_RGA_MODULE_MISSING_ERROR)



            case StepType.CGA_GET_FINGERS:
                device_alias, available_id = _step_device(step, role="cga")
                params.update({
                    "LabwareName": cga_get_fingers_labware if step.labware_name is None else step.labware_name,
                    "DeviceAlias": device_alias,
                    "AvailableID": available_id,
                })

            case StepType.CGA_DROP_FINGERS:
                device_alias, available_id = _step_device(step, role="cga")
                params.update({
                    "LabwareName": cga_drop_fingers_labware if step.labware_name is None else step.labware_name,
                    "DeviceAlias": device_alias,
                    "AvailableID": available_id,
                    "UseSourceAsBackPosition": step.use_source_as_back_position,
                })

            case StepType.MCA384_MIX:
                # FluentControl requires a mix-capable liquid subclass for MCA mix.
                mca_mix_liquid_class = _normalize_liquid_class(step.liquid_class, mix=True)
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "LiquidClassName": mca_mix_liquid_class,
                    "Volume": self._expression_text(step.volume),
                    "Cycles": self._expression_text(step.cycles),
                })

            case StepType.WAIT:
                params.update({
                    "Duration": self._expression_text(step.duration_seconds),
                })

            case StepType.LIHA_ASPIRATE:
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": self._expression_text(step.volume),
                })
                if step.well_offset is not None:
                    params["WellOffset"] = _normalize_well_offset_expr(step.well_offset)
                meta = self._resolve_labware_meta(step.labware_name)
                if meta:
                    params["_labware_type"] = meta.get("type")
                    params["_labware_wells"] = meta.get("wells")
                    params["_labware_category"] = meta.get("category")

            case StepType.LIHA_DISPENSE:
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": self._expression_text(step.volume),
                })
                if step.well_offset is not None:
                    params["WellOffset"] = _normalize_well_offset_expr(step.well_offset)
                meta = self._resolve_labware_meta(step.labware_name)
                if meta:
                    params["_labware_type"] = meta.get("type")
                    params["_labware_wells"] = meta.get("wells")
                    params["_labware_category"] = meta.get("category")

            case StepType.LIHA_MIX:
                # FluentControl expects a mixing-specific liquid class for LiHa mix.
                # Operator convention (current): "Water Mix".
                mix_liquid_class = _normalize_liquid_class(step.liquid_class, mix=True)
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "LiquidClassName": mix_liquid_class,
                    "Volume": self._expression_text(step.volume),
                    "Cycles": self._expression_text(step.cycles),
                })
                if step.well_offset is not None:
                    params["WellOffset"] = _normalize_well_offset_expr(step.well_offset)
                meta = self._resolve_labware_meta(step.labware_name)
                if meta:
                    params["_labware_type"] = meta.get("type")
                    params["_labware_wells"] = meta.get("wells")
                    params["_labware_category"] = meta.get("category")

            case StepType.LIHA_GET_TIPS:
                # GetTips: LabwareName is empty (tip identity comes from DitiType)
                # Look up tip type from the labware added to worktable
                tip_labware_type = None
                if step.labware_name:
                    raw_type = self._labware_types.get(step.labware_name)
                    if raw_type:
                        # Resolve through variable map if it's a variable reference
                        tip_labware_type = self._variable_values.get(raw_type, raw_type)
                params.update({
                    "LabwareName": "",
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "_tip_labware_type": tip_labware_type,  # passed to post-processing
                })

            case StepType.LIHA_DROP_TIPS:
                # DropTips always targets the waste chute/disposal target.
                #
                # Local models frequently (and incorrectly) provide the tipbox name here.
                # FluentControl expects a waste chute labware (see extracted example:
                # "FCA Thru Deck Waste Chute_1"), not a tipbox.
                params.update({
                    "LabwareName": liha_waste_labware,
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                })

            case StepType.MCA384_EMPTY_TIPS:
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": self._expression_text(step.volume),
                })

            case StepType.MCA384_GET_TIPS:
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                })

            case StepType.MCA384_DROP_TIPS:
                params.update({
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                })

            case StepType.MCA384_MOVE_ARM:
                params.update({
                    "MovementType": step.movement_type,
                    "LabwareName": step.labware_name or "",
                    "DeviceAlias": step.device_alias or default_device,
                    "AvailableID": step.available_id or default_available_id,
                })

            case StepType.LIHA_EMPTY_TIPS:
                params.update({
                    "LabwareName": step.labware_name,
                    "DeviceAlias": step.device_alias or liha_device,
                    "AvailableID": step.available_id or liha_available_id,
                    "LiquidClassName": _normalize_liquid_class(step.liquid_class),
                    "Volume": self._expression_text(step.volume),
                })

            case StepType.COMMENT:
                params.update({
                    "Comment": step.comment,
                })

            case StepType.USER_PROMPT:
                auto_close = bool(step.auto_close) if step.auto_close is not None else step.timeout > 0
                # FluentControl requires Timeout 1-7200 even when AutoClose is False
                # (wait-for-operator). Source scripts use 1 as the sentinel minimum.
                timeout_value = step.timeout if auto_close else max(step.timeout, 1)
                prompt_text = self._xml_escape_multiline(step.prompt)
                params.update({
                    "Prompt": prompt_text,
                    "AutoClose": str(auto_close),
                    "Timeout": str(timeout_value),
                    # RUPStandardStatement (image-capable) fields. These are
                    # ignored by the plain UserPrompt template and only consumed
                    # by the UserPromptImage template when image_path is set.
                    "MessageText": prompt_text,
                    "SelectedImagePath": step.image_path or "",
                    "SelectedSoundPath": step.sound_path or "",
                    "SoundFile": step.sound_path or "",
                    "RepeatSound": "False",
                    "RUPScreenTitle": step.screen_title or "User Prompt",
                    "RUPDisplayAndWait": str(not auto_close),
                    "RUPAutoClose": str(auto_close),
                    "RUPTimeOut": str(timeout_value),
                })

            case StepType.START_TIMER:
                params.update({
                    "Timer": str(step.timer),
                })

            case StepType.WAIT_FOR_TIMER:
                params.update({
                    "Timer": str(step.timer),
                    "Duration": self._expression_text(step.duration_seconds),
                })

            case StepType.EXPORT_VARIABLE:
                params.update({
                    "Variables": self._string_objects_xml(step.variables),
                    "ExportFile": f'"{self._xml_escape(step.export_file)}"',
                    "WriteHeader": self._bool_text(step.write_header),
                    "ReplaceExistingFile": self._bool_text(step.replace_existing_file),
                    "ExportStringsWithQuotes": self._bool_text(step.export_strings_with_quotes),
                    "DelimiterCode": str(step.delimiter_code),
                })

            case StepType.IMPORT_VARIABLE:
                params.update({
                    "Variables": self._string_objects_xml(step.variables),
                    "ImportFile": f'"{self._xml_escape(step.import_file)}"',
                    "ReadLine": self._bool_text(step.read_line),
                    "Line": str(step.line),
                    "StartInColumn": self._bool_text(step.start_in_column),
                    "Column": str(step.column),
                    "HasHeader": self._bool_text(step.has_header),
                    "DelimiterCode": str(step.delimiter_code),
                })

            case StepType.QUERY_VARIABLE:
                params.update({
                    "Name": step.variable_name,
                    "QueryPrompt": step.query_prompt,
                    "LimitRange": self._bool_text(step.limit_range),
                })

            case StepType.INITIALIZE_DEVICE:
                # No alias↔AvailableID cross-fill — both must come from ZEIA/recipe/config.
                device_alias, available_id = _step_device(step, role="device")
                params.update({
                    "InitType": step.init_type or "Initialize",
                    "DeviceAlias": device_alias,
                    "AvailableID": available_id,
                })

            case StepType.EXECUTE_APPLICATION:
                params.update({
                    "Application": self._xml_escape(step.application),
                    "Arguments": self._xml_escape(step.arguments),
                    "Wait": self._bool_text(step.wait),
                    "StoreReturn": self._bool_text(step.store_return),
                    "Variable": self._xml_escape(step.variable),
                })

            case StepType.DELAY:
                params.update({
                    "Delay": self._expression_text(step.delay),
                })

            case StepType.SET_LOCATION:
                params.update({
                    "Labware": step.labware,
                    "Location": step.location,
                    "Site": self._expression_text(step.site),
                    "Rotation": str(step.rotation),
                })

            case StepType.SUBROUTINE:
                params.update({
                    "SubRoutine": f'"{self._xml_escape(step.subroutine)}"',
                    "ExecutionMode": self._xml_escape(step.execution_mode),
                    "VariableMappingsStart": self._variable_mappings_xml(step.variable_mappings_start),
                    "VariableMappingsEnd": self._variable_mappings_xml(step.variable_mappings_end),
                })

            case StepType.SET_VARIABLE:
                params.update({
                    "Name": (step.variable_name or "").strip(),
                    "Value": self._xml_escape(render_expression(coerce_literal_expression(step.value))),
                })

            case StepType.CALCULATE_VARIABLE:
                # Tecan has no separate CalculateVariable command — render as
                # SetVariable with an expression value that FluentControl evaluates.
                op_map = {
                    "add": "+",
                    "subtract": "-",
                    "sub": "-",
                    "multiply": "*",
                    "mul": "*",
                    "divide": "/",
                    "div": "/",
                    "+": "+",
                    "-": "-",
                    "*": "*",
                    "/": "/",
                }
                op_sym = op_map.get(str(step.operation or "").strip().lower(), "+")
                expr = render_expression(BinaryExpression(
                    operator=op_sym,  # type: ignore[arg-type]
                    left=self._calculation_operand_expression(step.operand_a),
                    right=self._calculation_operand_expression(step.operand_b),
                ))
                params.update({
                    "Name": (step.target_variable or "").strip(),
                    "Value": self._xml_escape(expr),
                })

            case StepType.LOOP:
                # Loop rendering is handled separately in _render_step
                pass

        # Normalize DeviceAlias/AvailableID through resolve (no invent / no cross-fill).
        # Template assert then fails closed when the command requires a device binding.
        if "DeviceAlias" in params or "AvailableID" in params:
            stype = step.step_type if hasattr(step, "step_type") else None
            role = "device"
            if stype in (StepType.CGA_GET_FINGERS, StepType.CGA_DROP_FINGERS):
                role = "cga"
            elif stype in (
                StepType.LIHA_ASPIRATE,
                StepType.LIHA_DISPENSE,
                StepType.LIHA_MIX,
                StepType.LIHA_GET_TIPS,
                StepType.LIHA_DROP_TIPS,
                StepType.LIHA_EMPTY_TIPS,
                StepType.LIHA_DETECT_LIQUID,
            ):
                role = "liha"
            alias, avail = _step_device(step, role=role)
            if "DeviceAlias" in params:
                params["DeviceAlias"] = alias
            if "AvailableID" in params and stype != StepType.RGA_TRANSFER_LABWARE:
                params["AvailableID"] = avail
            elif "AvailableID" in params and stype == StepType.RGA_TRANSFER_LABWARE:
                # RGA uses AvailableID only (no DeviceAlias); keep step/config resolve.
                params["AvailableID"] = str(getattr(step, "available_id", None) or default_available_id).strip()

        # Add adapter params for MCA operations that need AdapterData
        adapter_params = self._get_adapter_params(default_available_id)
        for key, value in adapter_params.items():
            params.setdefault(key, value)

        return params

    @staticmethod
    def _calculation_operand_expression(value) -> object:
        if isinstance(value, str):
            return parse_expression(value.strip())
        return coerce_literal_expression(value)

    def _get_adapter_params(self, default_available_id: str) -> dict:
        """Get adapter-specific parameters for MCA commands."""
        if self._current_adapter_config:
            adapter = self._current_adapter_config
            # Build UsableTips XML based on adapter type
            if adapter["name"] == "EVA":
                usable_tips_xml = (
                    '                <UsableTips>\n'
                    '                  <UsableTips>All</UsableTips>\n'
                    '                </UsableTips>\n'
                )
                sort_number = "50"
                mount_column_row_wise = "false"
            else:
                # 384 Combo
                usable_tips_xml = (
                    '                <UsableTips>\n'
                    '                  <UsableTips>All</UsableTips>\n'
                    '                  <UsableTips>Column</UsableTips>\n'
                    '                  <UsableTips>Row</UsableTips>\n'
                    '                </UsableTips>\n'
                )
                sort_number = "10"
                mount_column_row_wise = "true"

            return {
                "BlowoutAirgap": "0",
                "HeadPosition": "Left",
                "PartialColumns": str(adapter["partial_columns"]),
                "PartialRows": str(adapter["partial_rows"]),
                "UseSourceAsBackPosition": "BackToPosition",
                "AdapterAfterDrop": "False",
                "LastTipXPosition": str(adapter["last_tip_x"]),
                "LastTipYPosition": str(adapter["last_tip_y"]),
                "AvailableID": default_available_id,
                # AdapterData block placeholders
                "AdapterName": adapter["display_name"],
                "CanMountTecanDiTis": str(adapter["can_mount_tecan_ditis"]).lower(),
                "AdapterXCount": str(adapter["x_count"]),
                "AdapterYCount": str(adapter["y_count"]),
                "AdapterXSpacing": str(adapter["x_spacing"]),
                "AdapterYSpacing": str(adapter["y_spacing"]),
                "AdapterToolId": adapter["tool_id"],
                "AdapterUsableTipsXml": usable_tips_xml,
                "AdapterSortNumber": sort_number,
                "AdapterMountColumnRowWise": mount_column_row_wise,
            }
        else:
            # No adapter mounted - use 384 Combo defaults
            return {
                "BlowoutAirgap": "0",
                "HeadPosition": "Left",
                "PartialColumns": "24",
                "PartialRows": "16",
                "UseSourceAsBackPosition": "BackToPosition",
                "AdapterAfterDrop": "False",
                "LastTipXPosition": "24",
                "LastTipYPosition": "16",
                "AvailableID": default_available_id,
                # AdapterData block placeholders (384 Combo defaults)
                "AdapterName": "384 Tips Combo (Partial Tips)",
                "CanMountTecanDiTis": "false",
                "AdapterXCount": "24",
                "AdapterYCount": "16",
                "AdapterXSpacing": "4.5",
                "AdapterYSpacing": "4.5",
                "AdapterToolId": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi384.Combo",
                "AdapterUsableTipsXml": (
                    '                <UsableTips>\n'
                    '                  <UsableTips>All</UsableTips>\n'
                    '                  <UsableTips>Column</UsableTips>\n'
                    '                  <UsableTips>Row</UsableTips>\n'
                    '                </UsableTips>\n'
                ),
                "AdapterSortNumber": "10",
                "AdapterMountColumnRowWise": "true",
            }

    # LiHa step types that need XML post-processing
    _LIHA_STEP_TYPES = {
        StepType.LIHA_ASPIRATE, StepType.LIHA_DISPENSE, StepType.LIHA_MIX,
        StepType.LIHA_GET_TIPS, StepType.LIHA_DROP_TIPS, StepType.LIHA_EMPTY_TIPS,
    }

    def _is_liha_step(self, step: Step, command_id: str | None) -> bool:
        if hasattr(step, "step_type"):
            step_type = step.step_type
            if isinstance(step_type, StepType):
                return step_type in self._LIHA_STEP_TYPES
            if isinstance(step_type, str):
                if step_type in (t.value for t in StepType):
                    return StepType(step_type) in self._LIHA_STEP_TYPES
                if step_type.startswith("liha_"):
                    return True
        return bool(command_id and command_id.startswith("Liha"))

    def _build_liha_volumes_xml(self, volume: str, num_channels: int = 8, volumes: list | None = None) -> str:
        """Build XML fragment for LiHa per-channel volumes."""
        rendered_volumes = [self._expression_text(item) for item in volumes] if volumes else [volume] * num_channels
        entries = []
        for rendered_volume in rendered_volumes:
            entries.append(
                f'          <Object Type="System.String">\n'
                f'            <string>{rendered_volume}</string>\n'
                f'          </Object>'
            )
        return "\n".join(entries)

    def _build_liha_tips_xml(self, channels: list[int] | None = None, *, num_tips: int = 8) -> str:
        """Build XML fragment for LiHa SelectedTipsIndexes."""
        selected = list(range(num_tips)) if channels is None else list(channels)
        entries = []
        for i in selected:
            entries.append(
                f'                  <Object Type="System.Int32">\n'
                f'                    <int>{i}</int>\n'
                f'                  </Object>'
            )
        return "\n".join(entries)

    @staticmethod
    def _resolve_liha_get_tips_channels(step: LihaGetTipsStep) -> list[int]:
        if step.tip_channels:
            return sorted(set(step.tip_channels))
        if step.tip_index is not None:
            return [step.tip_index]
        return list(range(8))

    def _build_liha_well_selection(self, num_channels: int = 8, mode: str = "range") -> tuple:
        """Build serialized well indexes and well string for LiHa.

        Returns (SerializedWellIndexes, SelectedWellsString) for column A1-H1.
        Tecan uses range encoding: start&gt;step&gt;end;
        """
        if mode == "repeat_single":
            indexes = ";".join(["0"] * num_channels) + ";"
            wells = f"{num_channels} * A1"
            return indexes, wells

        # Default: wells 0 through num_channels-1 with step 1
        indexes = f"0&gt;1&gt;{num_channels - 1};"
        first = f"{chr(65)}1"
        last = f"{chr(65 + num_channels - 1)}1"
        wells = f"{first} - {last}"
        return indexes, wells

    def _resolve_labware_type(self, label: Optional[str]) -> Optional[str]:
        if not label:
            return None
        raw_type = self._labware_types.get(label)
        if not raw_type:
            return None
        fc_var_name = decode_fc_variable(raw_type)
        if fc_var_name:
            return self._variable_values.get(fc_var_name, fc_var_name)
        return self._variable_values.get(raw_type, raw_type)

    def _resolve_labware_meta(self, label: Optional[str]) -> dict:
        labware_type = self._resolve_labware_type(label)
        if not labware_type:
            return {}
        meta = self.labware_reference.get(labware_type, {})
        if not meta:
            return {"type": labware_type}
        meta = dict(meta)
        meta["type"] = labware_type
        return meta

    def _build_liha_liquid_class_names_xml(self, liquid_class: str, num_channels: int) -> str:
        """Build the <LiquidClassNames> block for all channels."""
        objects = []
        for _ in range(num_channels):
            objects.append(
                '          <Object Type="System.String">\n'
                f'            <string>{liquid_class}</string>\n'
                '          </Object>'
            )
        return "\n".join(objects)

    def _post_process_liha_xml(self, xml: str, step: Step, params: dict) -> str:
        """Replace hardcoded values in LiHa template XML with actual values."""
        num_channels = 8
        volume = params.get("Volume", "10")
        liquid_class = params.get("LiquidClassName", "")

        # Replace hardcoded volumes block (8 entries of <string>NNN</string>)
        if step.step_type in (StepType.LIHA_ASPIRATE, StepType.LIHA_DISPENSE, StepType.LIHA_MIX):
            new_lc_names = self._build_liha_liquid_class_names_xml(liquid_class, num_channels)
            xml = re.sub(
                r'(<LiquidClassNames>\s*)(?:<Object Type="System\.String">\s*<string\s*/>\s*</Object>\s*|<Object Type="System\.String">\s*<string>[^<]*</string>\s*</Object>\s*)+(\s*</LiquidClassNames>)',
                lambda m: m.group(1) + "\n" + new_lc_names + "\n        " + m.group(2).strip(),
                xml,
                flags=re.DOTALL
            )
            new_volumes = self._build_liha_volumes_xml(volume, num_channels, getattr(step, "volumes", None))
            # Match the <Volumes>...</Volumes> block and replace its content
            xml = re.sub(
                r'(<Volumes>\s*)(?:<Object Type="System\.String">\s*<string>[^<]*</string>\s*</Object>\s*)+(\s*</Volumes>)',
                lambda m: m.group(1) + "\n" + new_volumes + "\n        " + m.group(2).strip(),
                xml,
                flags=re.DOTALL
            )

        # Replace hardcoded SelectedTipsIndexes block
        if step.step_type == StepType.LIHA_GET_TIPS and isinstance(step, LihaGetTipsStep):
            selected_channels = self._resolve_liha_get_tips_channels(step)
        else:
            selected_channels = None
        new_tips = self._build_liha_tips_xml(selected_channels, num_tips=num_channels)
        xml = re.sub(
            r'(<SelectedTipsIndexes>\s*)(?:<Object Type="System\.Int32">\s*<int>\d+</int>\s*</Object>\s*)+(\s*</SelectedTipsIndexes>)',
            lambda m: m.group(1) + "\n" + new_tips + "\n                " + m.group(2).strip(),
            xml,
            flags=re.DOTALL
        )

        # Replace hardcoded well indexes for aspirate/dispense/mix
        if step.step_type in (StepType.LIHA_ASPIRATE, StepType.LIHA_DISPENSE, StepType.LIHA_MIX):
            labware_wells = params.get("_labware_wells")
            labware_category = params.get("_labware_category")
            labware_type = str(params.get("_labware_type") or "").lower()
            # Fallback heuristic: treat trough/reservoir/waste-style labware as single well
            # even when reference metadata is missing/incomplete.
            inferred_single = any(
                token in labware_type
                for token in ("trough", "reservoir", "waste", "25ml", "100ml", "300ml")
            )
            is_single_well = labware_wells == 1 or labware_category == "reservoir" or inferred_single
            mode = "repeat_single" if is_single_well else "range"
            indexes, wells = self._build_liha_well_selection(num_channels, mode=mode)
            xml = re.sub(
                r'<SerializedWellIndexes>[^<]*</SerializedWellIndexes>',
                f'<SerializedWellIndexes>{indexes}</SerializedWellIndexes>',
                xml
            )
            xml = re.sub(
                r'<SelectedWellsString>[^<]*</SelectedWellsString>',
                f'<SelectedWellsString>{wells}</SelectedWellsString>',
                xml
            )
            # Some extracted templates contain non-zero hardcoded offsets.
            # Force explicit offset: use provided value, otherwise reset to 0.
            well_offset = params.get("WellOffset", "0")
            xml = re.sub(
                r'<WellOffset>[^<]*</WellOffset>',
                f'<WellOffset>{well_offset}</WellOffset>',
                xml,
                count=1
            )

        # Replace hardcoded cycles for mix
        if step.step_type == StepType.LIHA_MIX:
            cycles = params.get("Cycles", "10")
            xml = re.sub(
                r'<Cycles>\d+</Cycles>',
                f'<Cycles>{cycles}</Cycles>',
                xml,
                count=1
            )

        # Fix DitiType AvailableID for LiHa tips (should use TOOLTYPE identifier)
        # Derive from the tip labware type placed on worktable, fall back to config default
        if step.step_type in (StepType.LIHA_GET_TIPS, StepType.LIHA_DROP_TIPS):
            tip_labware_type = params.get("_tip_labware_type")
            if tip_labware_type:
                diti_type_id = f"TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:{tip_labware_type}"
            else:
                liha_config = self.config.get("liha_device", {}) if isinstance(self.config.get("liha_device"), dict) else {}
                # Config/site overlay only — never invent FCA tip TOOLNAME placeholders.
                diti_type_id = str(liha_config.get("diti_type") or "").strip()
            if diti_type_id:
                # Replace <DitiType><AvailableID>...</AvailableID></DitiType>
                xml = re.sub(
                    r'(<DitiType>\s*<AvailableID>)[^<]*(</AvailableID>)',
                    lambda m: m.group(1) + diti_type_id + m.group(2),
                    xml
                )
                # Replace <DitiType>...</DitiType> without <AvailableID>
                xml = re.sub(
                    r'(<DitiType>)(?![\s\n]*<AvailableID>)[^<]*(</DitiType>)',
                    lambda m: m.group(1) + diti_type_id + m.group(2),
                    xml
                )

        return xml

    def _fill_template(self, template: str, params: dict) -> str:
        """Fill in {{placeholder}} values in template."""
        result = template
        for key, value in params.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        return result

    # ------------------------------------------------------------------
    # Labware-name exact catalog canonicalize (no fuzzy invent)
    # ------------------------------------------------------------------

    _EXACT_LABWARE_BY_CASEFOLD: Optional[Dict[str, str]] = None
    _EXACT_LABWARE_SOURCE: Optional[str] = None

    def _labware_catalog_paths(self) -> list[Path]:
        """Candidate ZEIA ``labware_catalog.json`` paths (first existing wins)."""
        paths: list[Path] = []
        env = os.environ.get("FLUENTCODER_LABWARE_CATALOG", "").strip()
        if env:
            paths.append(Path(env).expanduser())
        cfg = self.config.get("labware_catalog") if isinstance(self.config.get("labware_catalog"), dict) else {}
        cfg_path = str(cfg.get("path") or "").strip()
        if cfg_path:
            path = Path(cfg_path).expanduser()
            if not path.is_absolute():
                path = (self.config_path.parent / path).resolve()
            paths.append(path)
        return paths

    def _get_exact_labware_map(self) -> Dict[str, str]:
        """Return casefold→canonical name from ZEIA labware_catalog only.

        No install-DB fuzzy set. Missing catalog → empty map (leave names as-is).
        """
        source_key = "|".join(str(p) for p in self._labware_catalog_paths()) or "<none>"
        if (
            Renderer._EXACT_LABWARE_BY_CASEFOLD is not None
            and Renderer._EXACT_LABWARE_SOURCE == source_key
        ):
            return Renderer._EXACT_LABWARE_BY_CASEFOLD

        mapping: Dict[str, str] = {}
        for path in self._labware_catalog_paths():
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for entry in payload.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                for raw in (entry.get("name"), *(entry.get("aliases") or [])):
                    name = str(raw or "").strip()
                    if not name:
                        continue
                    canonical = str(entry.get("name") or name).strip() or name
                    mapping.setdefault(name.casefold(), canonical)
            if mapping:
                break

        Renderer._EXACT_LABWARE_BY_CASEFOLD = mapping
        Renderer._EXACT_LABWARE_SOURCE = source_key
        return mapping

    def _normalize_labware_names(self, protocol: Protocol) -> None:
        """Canonicalize labware type names via exact ZEIA catalog match only.

        Unknown names stay unchanged — never fuzzy-map to a different install
        catalog entry (that invents wrong-lab identity across ZEIA/installs).
        Case-only differences map to the catalog's canonical spelling.
        """
        exact = self._get_exact_labware_map()
        corrections: list[tuple[str, str]] = []
        variable_names = {str(v) for v in (protocol.variables or []) if isinstance(v, str)}

        def _iter_steps_recursive(steps):
            for st in steps or []:
                yield st
                if self._step_type_name(st) == "loop":
                    yield from _iter_steps_recursive(getattr(st, "steps", []) or [])

        def _exact_only(value: str) -> str:
            if not value:
                return value
            if decode_fc_variable(value):
                return value
            stripped = value.strip()
            if not stripped or stripped.replace(".", "").replace("-", "").isdigit():
                return value
            if stripped in variable_names or stripped.endswith("Type"):
                return value
            if any(ch in stripped for ch in "()+-*/="):
                return value
            canonical = exact.get(stripped.casefold())
            if canonical and canonical != stripped:
                corrections.append((stripped, canonical))
                return canonical
            return value

        default_var_values = getattr(protocol, "variable_defaults", {}) or {}

        for group in protocol.groups:
            for step in _iter_steps_recursive(group.steps):
                stype = self._step_type_name(step)
                if stype == "add_labware" and step.labware_type:
                    step.labware_type = _exact_only(step.labware_type)
                elif stype == "set_variable":
                    expr_value = coerce_literal_expression(step.value)
                    if not isinstance(expr_value, StringLiteral):
                        continue
                    var_name = (step.variable_name or "").lower()
                    if any(tok in var_name for tok in ("type", "labware", "plate", "tip", "reservoir", "trough")):
                        step.value = StringLiteral(value=_exact_only(expr_value.value))

        for var_name, value in list(default_var_values.items()):
            expr_value = coerce_literal_expression(value)
            if not isinstance(expr_value, StringLiteral):
                continue
            low_name = str(var_name).lower()
            if any(tok in low_name for tok in ("type", "labware", "plate", "tip", "reservoir", "trough")):
                default_var_values[var_name] = StringLiteral(value=_exact_only(expr_value.value))

        if corrections:
            for old, new in corrections:
                print(f"  [labware-exact] '{old}' -> '{new}'")

    def _normalize_for_magnet_cover_site(self, protocol: Protocol) -> None:
        """No-op: do not invent magnet-cover plate types.

        Cover-site coordinate rewriting (GetCoverSiteName/Index) stays in the
        RGA transfer path when IR placements mark a magnet. Plate catalog names
        must come from ZEIA/recipe — never rewrite to a stock Thermo/ABgene type.
        """
        del protocol
        return

    def _protocol_liquid_class_name(self, protocol: Protocol) -> str:
        """Resolve script-level liquid class without inventing product defaults.

        Order: explicit ``Protocol.liquid_class`` → site ``generation.yaml``
        (when configured) → first liquid_class used by a protocol step.
        """
        explicit = str(protocol.liquid_class or "").strip()
        if explicit:
            return explicit
        config_lc = self.config.get("liquid_class") if isinstance(self.config.get("liquid_class"), dict) else {}
        configured = str(config_lc.get("name") or "").strip()
        if configured:
            return configured
        for group in protocol.groups:
            for step in group.steps:
                for attr in ("liquid_class", "LiquidClassName"):
                    value = getattr(step, attr, None)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                params = getattr(step, "parameters", None)
                if isinstance(params, dict):
                    for key in ("liquid_class", "LiquidClassName"):
                        value = params.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
        return ""

    def _resolve_liquid_class_guid(self, name: str) -> str:
        """Look up a liquid-class GUID by name.

        Source of truth is the SQL catalog index (.xlqc from install/ZEIA), then
        an optional portable ``liquid_classes.json`` (ZEIA export). Falls back to
        ``generation.yaml`` liquid_class.guid only when that file is
        site-configured with a non-empty guid — never invent a product GUID.
        """
        text = str(name or "").strip()
        if not text:
            return ""
        try:
            from ..catalog import index_exists, resolve_liquid_class_by_name
            if index_exists():
                entry = resolve_liquid_class_by_name(text)
                if entry is not None:
                    return entry.guid
        except Exception:
            pass
        catalog_guid = self._resolve_liquid_class_guid_from_json(text)
        if catalog_guid:
            return catalog_guid
        config_lc = self.config.get("liquid_class") if isinstance(self.config.get("liquid_class"), dict) else {}
        config_name = str(config_lc.get("name") or "").strip()
        config_guid = str(config_lc.get("guid") or "").strip()
        if config_guid and (not config_name or config_name.casefold() == text.casefold()):
            return config_guid
        return ""

    def _resolve_liquid_class_guid_from_json(self, name: str) -> str:
        """Resolve via portable ZEIA ``liquid_classes.json`` when present."""
        candidates: list[Path] = []
        for key in ("liquid_classes_catalog_path", "liquid_classes_json"):
            raw = self.config.get(key)
            if raw:
                candidates.append(Path(str(raw)))
        env_path = os.environ.get("FLUENTCODER_LIQUID_CLASSES_JSON")
        if env_path:
            candidates.append(Path(env_path))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            guid = ""
            try:
                # Prefer pipeline helper when available (same vault tree).
                from fluent_pipeline.liquid_classes_export import (  # type: ignore
                    load_liquid_classes_catalog,
                    resolve_liquid_class_guid,
                )

                catalog = load_liquid_classes_catalog(path)
                guid = resolve_liquid_class_guid(name, catalog) or ""
            except Exception:
                guid = ""
            if not guid:
                # Same matching rules as resolve_liquid_class_guid (instance suffix).
                guid = liquid_class_guid_from_catalog_entries(name, payload.get("entries"))
            if guid:
                return guid
        return ""

    def render_to_file(self, protocol: Protocol, output_path: Path) -> Path:
        """
        Render protocol to file.

        Args:
            protocol: The protocol to render
            output_path: Output file path

        Returns:
            Path to written file
        """
        xml = self.render(protocol)

        # Ensure .xscr extension
        if output_path.suffix != ".xscr":
            output_path = output_path.with_suffix(".xscr")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(xml, encoding="utf-8")
        rewrite_checksum_in_place(output_path)

        return output_path
