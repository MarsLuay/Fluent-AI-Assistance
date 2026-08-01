"""Extract SiLA connector capabilities into command registry suggestions.

Pass one or more ``--repo`` checkouts of connector sources. The retired
``Inspiration/`` tree is no longer a default search path. Parses ``*.sila.xml``
feature definitions, mines connector code aliases, and emits suggested
additions for ``source_command_registry_path()`` without modifying the registry
itself.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tecan_common.command_registry import source_command_registry_path


SOURCE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOURCE_ROOT.parent if SOURCE_ROOT.name == "source" else SOURCE_ROOT
_SHARED_BUILD = REPO_ROOT / "ready-to-import" / "_shared" / "temp_files" / "build"
DEFAULT_REGISTRY = source_command_registry_path()
DEFAULT_OUTPUT_JSON = _SHARED_BUILD / "connector_capability_suggestions.json"
DEFAULT_OUTPUT_MD = _SHARED_BUILD / "connector_capability_suggestions.md"
# Inspiration/ checkouts are retired. Pass --repo explicitly for local connector trees.
DEFAULT_CONNECTOR_REPOS: tuple[Path, ...] = ()
REGISTRY_VERSION = "tecan.command_registry.v2"
SUGGESTION_VERSION = "tecan.connector_capability_suggestions.v1"
TRIAGE_BUCKETS = (
    "safe_metadata_status",
    "risky_execution",
    "ambiguous_aliases",
    "needs_custom_ir_support",
)
TRIAGE_BUCKET_TITLES = {
    "safe_metadata_status": "Safe Metadata/Status Or Existing-IR Additions",
    "risky_execution": "Risky Execution Commands",
    "ambiguous_aliases": "Ambiguous Aliases",
    "needs_custom_ir_support": "Needs Custom IR Support",
}


@dataclass
class DataField:
    identifier: str
    display_name: str = ""
    description: str = ""
    data_type: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "identifier": self.identifier,
            "display_name": self.display_name,
            "description": self.description,
            "data_type": self.data_type,
        }


@dataclass
class Capability:
    repo: str
    feature: str
    feature_display_name: str
    feature_category: str
    kind: str
    identifier: str
    display_name: str = ""
    description: str = ""
    observable: str = ""
    parameters: list[DataField] = field(default_factory=list)
    responses: list[DataField] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source_xml: str = ""
    code_aliases: list[str] = field(default_factory=list)
    code_paths: list[str] = field(default_factory=list)
    raw_command_types: list[str] = field(default_factory=list)
    code_notes: list[str] = field(default_factory=list)

    @property
    def qualified_identifier(self) -> str:
        return f"{self.feature}.{self.identifier}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "feature": self.feature,
            "feature_display_name": self.feature_display_name,
            "feature_category": self.feature_category,
            "kind": self.kind,
            "identifier": self.identifier,
            "qualified_identifier": self.qualified_identifier,
            "display_name": self.display_name,
            "description": self.description,
            "observable": self.observable,
            "parameters": [item.as_dict() for item in self.parameters],
            "responses": [item.as_dict() for item in self.responses],
            "errors": self.errors,
            "source_xml": self.source_xml,
            "code_aliases": self.code_aliases,
            "code_paths": self.code_paths,
            "raw_command_types": self.raw_command_types,
            "code_notes": self.code_notes,
        }


@dataclass
class RegistryHit:
    command_id: str
    entry: dict[str, Any]
    matched_key: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "matched_key": self.matched_key,
            "operation": self.entry.get("operation"),
            "support_status": self.entry.get("support_status"),
            "family": self.entry.get("family"),
            "pattern_type": self.entry.get("pattern_type"),
        }


class RegistryLookup:
    def __init__(self, registry_path: Path):
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != REGISTRY_VERSION:
            raise ValueError(f"Unsupported command registry version: {payload.get('schema_version')!r}")
        commands = payload.get("commands")
        if not isinstance(commands, dict):
            raise ValueError("command_registry.json must contain a commands object")
        self.path = registry_path
        self.payload = payload
        self.index: dict[str, RegistryHit] = {}
        self.direct_index: dict[str, RegistryHit] = {}
        for command_id, raw_entry in commands.items():
            if not isinstance(raw_entry, dict):
                continue
            entry = {"id": command_id, **raw_entry}
            for alias in [command_id, *(raw_entry.get("aliases") or [])]:
                self.direct_index.setdefault(normalize_key(alias), RegistryHit(command_id=command_id, entry=entry, matched_key=alias))
                for key in lookup_keys(alias):
                    self.index.setdefault(key, RegistryHit(command_id=command_id, entry=entry, matched_key=alias))

    def lookup(self, value: Any) -> RegistryHit | None:
        for key in lookup_keys(value):
            hit = self.index.get(key)
            if hit:
                return hit
        return None

    def lookup_all(self, values: list[Any]) -> list[RegistryHit]:
        hits: dict[str, RegistryHit] = {}
        for value in values:
            hit = self.lookup(value)
            if hit:
                hits.setdefault(hit.command_id, hit)
        return list(hits.values())

    def direct_lookup(self, value: Any) -> RegistryHit | None:
        return self.direct_index.get(normalize_key(str(value or "")))

    def direct_lookup_all(self, values: list[Any]) -> list[RegistryHit]:
        hits: dict[str, RegistryHit] = {}
        for value in values:
            hit = self.direct_lookup(value)
            if hit:
                hits.setdefault(hit.command_id, hit)
        return list(hits.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract connector capabilities into registry suggestions.")
    parser.add_argument(
        "--repo",
        action="append",
        type=Path,
        required=False,
        help="Connector repo to scan (repeatable). No default — Inspiration/ checkouts are retired.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="Path to the editable command registry source file.",
    )
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON, help="Suggestion JSON output path.")
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD, help="Markdown report output path.")
    parser.add_argument(
        "--include-properties",
        action="store_true",
        help="Also suggest approved_non_command registry entries for SiLA properties.",
    )
    args = parser.parse_args()

    repos = tuple(path.resolve() for path in (args.repo or DEFAULT_CONNECTOR_REPOS))
    if not repos:
        parser.error("pass at least one --repo <connector-checkout> (Inspiration/ defaults are retired)")
    registry = RegistryLookup(args.registry.resolve())
    capabilities = extract_capabilities(repos)
    payload = build_suggestions(
        capabilities=capabilities,
        registry=registry,
        repos=repos,
        include_properties=args.include_properties,
    )

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_markdown(payload), encoding="utf-8")

    summary = payload["summary"]
    print(
        "Extracted {capabilities} capabilities from {xml_files} SiLA XML files; "
        "wrote {suggestions} suggested registry additions to {json_path}".format(
            capabilities=summary["capabilities"],
            xml_files=summary["sila_xml_files"],
            suggestions=summary["suggested_additions"],
            json_path=args.output_json,
        )
    )
    return 0


def extract_capabilities(repos: tuple[Path, ...]) -> list[Capability]:
    code_index = mine_code_index(repos)
    capabilities: list[Capability] = []
    for repo in repos:
        if not repo.exists():
            continue
        for xml_path in sorted(repo.rglob("*.sila.xml")):
            capabilities.extend(parse_sila_xml(repo, xml_path, code_index))
    return sorted(capabilities, key=lambda item: (item.repo, item.feature, item.kind, item.identifier))


def parse_sila_xml(repo: Path, xml_path: Path, code_index: dict[str, dict[str, Any]]) -> list[Capability]:
    root = ET.fromstring(xml_path.read_text(encoding="utf-8-sig").lstrip())
    feature = child_text(root, "Identifier")
    feature_display_name = child_text(root, "DisplayName")
    feature_category = root.attrib.get("Category", "")
    repo_name = repo.name
    source_xml = relpath(xml_path)
    capabilities: list[Capability] = []

    for node in direct_children(root, {"Command", "Property"}):
        kind = local_name(node.tag).lower()
        identifier = child_text(node, "Identifier")
        if not identifier:
            continue
        key = capability_key(feature, identifier)
        code = code_index.get(key, {})
        capabilities.append(
            Capability(
                repo=repo_name,
                feature=feature,
                feature_display_name=feature_display_name,
                feature_category=feature_category,
                kind=kind,
                identifier=identifier,
                display_name=child_text(node, "DisplayName"),
                description=clean_text(child_text(node, "Description")),
                observable=child_text(node, "Observable"),
                parameters=parse_fields(node, "Parameter"),
                responses=parse_fields(node, "Response"),
                errors=parse_defined_errors(node),
                source_xml=source_xml,
                code_aliases=sorted(code.get("aliases", [])),
                code_paths=sorted(code.get("paths", [])),
                raw_command_types=sorted(code.get("raw_command_types", [])),
                code_notes=sorted(code.get("notes", [])),
            )
        )
    return capabilities


def parse_fields(node: ET.Element, tag: str) -> list[DataField]:
    fields: list[DataField] = []
    for field_node in direct_children(node, {tag}):
        identifier = child_text(field_node, "Identifier")
        if not identifier:
            continue
        fields.append(
            DataField(
                identifier=identifier,
                display_name=child_text(field_node, "DisplayName"),
                description=clean_text(child_text(field_node, "Description")),
                data_type=parse_data_type(first_direct_child(field_node, "DataType")),
            )
        )
    return fields


def parse_defined_errors(node: ET.Element) -> list[str]:
    errors: list[str] = []
    for container in direct_children(node, {"DefinedExecutionErrors"}):
        for identifier in direct_children(container, {"Identifier"}):
            if identifier.text and identifier.text.strip():
                errors.append(identifier.text.strip())
    return errors


def parse_data_type(node: ET.Element | None) -> str:
    if node is None:
        return ""
    basic = first_descendant_text(node, "Basic")
    if basic:
        return basic
    custom = first_descendant_text(node, "DataTypeIdentifier")
    if custom:
        if any(local_name(child.tag) == "List" for child in node.iter()):
            return f"List[{custom}]"
        return custom
    if any(local_name(child.tag) == "List" for child in node.iter()):
        return "List"
    if any(local_name(child.tag) == "Structure" for child in node.iter()):
        return "Structure"
    return ""


def mine_code_index(repos: tuple[Path, ...]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for repo in repos:
        if not repo.exists():
            continue
        for path in sorted(repo.rglob("*.py")):
            mine_python_code(repo, path, index)
        for path in sorted(repo.rglob("*.cs")):
            mine_csharp_code(repo, path, index)
    return index


def mine_python_code(repo: Path, path: Path, index: dict[str, dict[str, Any]]) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeDecodeError):
        return
    rel = relpath(path)
    for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        class_name = class_node.name
        if class_name.startswith("_"):
            continue
        for method in (node for node in class_node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
            if method.name.startswith("_"):
                continue
            identifier = snake_to_pascal(method.name)
            feature_identifier = class_name
            decorators = " ".join(_call_name(decorator) for decorator in method.decorator_list)
            if "Property" in decorators and method.name.startswith(("get_", "subscribe_")):
                identifier = snake_to_pascal(method.name.removeprefix("get_").removeprefix("subscribe_"))
            if class_name == "Fluent":
                for sila_feature, sila_member in iter_sila_client_calls(method):
                    add_code_hit(
                        index,
                        feature=sila_feature,
                        identifier=sila_member,
                        aliases=[method.name, f"{class_name}.{method.name}", sila_member],
                        paths=[rel],
                        notes=[first_doc_line(ast.get_docstring(method) or "")],
                    )
                continue
            add_code_hit(
                index,
                feature=feature_identifier,
                identifier=identifier,
                aliases=[method.name, f"{class_name}.{method.name}"],
                paths=[rel],
                notes=[first_doc_line(ast.get_docstring(method) or "")],
            )


def mine_csharp_code(repo: Path, path: Path, index: dict[str, dict[str, Any]]) -> None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return
    rel = relpath(path)
    class_match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", text)
    class_name = class_match.group(1) if class_match else ""
    feature = "SilaFluentController" if "SilaFluentController" in path.parts else class_name
    for command_name in re.findall(r'Register(?:Unobservable|Observable)Command<[^>]+>\("([A-Za-z0-9_]+)"', text):
        add_code_hit(index, feature=feature, identifier=command_name, aliases=[command_name], paths=[rel])
    for method_name in re.findall(
        r"\bpublic\s+(?:void|string|bool|int|ICollection<[^>]+>|System\.Collections\.Generic\.ICollection<[^>]+>)\s+([A-Z][A-Za-z0-9_]*)\s*\(",
        text,
    ):
        add_code_hit(index, feature=feature, identifier=method_name, aliases=[method_name], paths=[rel])
    raw_types = [
        raw
        for raw in re.findall(r'<Object\s+Type=""([^"]+)""', text)
        if ("ScriptCommand" in raw or "Statement" in raw) and not raw.startswith("System.")
    ]
    if class_name and raw_types:
        add_code_hit(
            index,
            feature=feature,
            identifier=class_name,
            aliases=[class_name, *raw_types],
            paths=[rel],
            raw_command_types=raw_types,
        )


def iter_sila_client_calls(method: ast.AST) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        member = node.func.attr
        value = node.func.value
        if isinstance(value, ast.Attribute):
            feature = value.attr
            if feature.startswith("Sila") or feature.endswith(("Controller", "Provider")):
                calls.append((feature, member))
    return calls


def add_code_hit(
    index: dict[str, dict[str, Any]],
    *,
    feature: str,
    identifier: str,
    aliases: list[str],
    paths: list[str],
    raw_command_types: list[str] | None = None,
    notes: list[str] | None = None,
) -> None:
    key = capability_key(feature, identifier)
    entry = index.setdefault(key, {"aliases": set(), "paths": set(), "raw_command_types": set(), "notes": set()})
    entry["aliases"].update(alias for alias in aliases if alias)
    entry["paths"].update(path for path in paths if path)
    entry["raw_command_types"].update(raw for raw in (raw_command_types or []) if raw)
    entry["notes"].update(note for note in (notes or []) if note)


def build_suggestions(
    *,
    capabilities: list[Capability],
    registry: RegistryLookup,
    repos: tuple[Path, ...],
    include_properties: bool,
) -> dict[str, Any]:
    suggestions: dict[str, dict[str, Any]] = {}
    notes: dict[str, dict[str, Any]] = {}
    covered: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    triage: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in TRIAGE_BUCKETS}
    reserved_alias_keys: set[str] = set()

    for capability in capabilities:
        classification = classify_capability(capability)
        aliases = candidate_aliases(capability)
        hits = registry.lookup_all(aliases)
        direct_connector_hits = registry.direct_lookup_all(connector_facing_aliases(capability))
        hit_payloads = [hit.as_dict() for hit in hits]
        conflict = detect_conflict(capability, classification, hits, registry)
        if conflict:
            conflicts.append(conflict)
        triage_item = triage_capability(
            capability=capability,
            classification=classification,
            conflict=conflict,
            existing_hits=hit_payloads,
        )
        triage[triage_item["bucket"]].append(triage_item)

        if capability.kind == "property" and not include_properties:
            covered.append(
                {
                    "capability": capability.qualified_identifier,
                    "kind": capability.kind,
                    "reason": "property_not_suggested_by_default",
                    "existing_hits": hit_payloads,
                }
            )
            continue

        if is_covered(classification, direct_connector_hits):
            covered.append(
                {
                    "capability": capability.qualified_identifier,
                    "kind": capability.kind,
                    "reason": "covered_by_existing_registry",
                    "existing_hits": [hit.as_dict() for hit in direct_connector_hits],
                }
            )
            continue

        entry_id = capability.qualified_identifier
        entry_aliases = free_aliases(capability, aliases, registry, reserved_alias_keys)
        entry = registry_entry_for(capability, classification, entry_aliases)
        suggestions[entry_id] = entry
        reserved_alias_keys.update(normalize_key(alias) for alias in entry_aliases)
        notes[entry_id] = {
            "confidence": classification["confidence"],
            "classification_reason": classification["reason"],
            "source_xml": capability.source_xml,
            "code_paths": capability.code_paths,
            "raw_command_types": capability.raw_command_types,
            "existing_hits": hit_payloads,
        }

    command_count = sum(1 for item in capabilities if item.kind == "command")
    property_count = sum(1 for item in capabilities if item.kind == "property")
    xml_files = {item.source_xml for item in capabilities}
    return {
        "schema_version": SUGGESTION_VERSION,
        "registry_path": relpath(registry.path),
        "connector_repos": [relpath(path) for path in repos],
        "summary": {
            "sila_xml_files": len(xml_files),
            "capabilities": len(capabilities),
            "commands": command_count,
            "properties": property_count,
            "suggested_additions": len(suggestions),
            "covered_or_skipped": len(covered),
            "conflicts": len(conflicts),
            "triage_counts": {bucket: len(items) for bucket, items in triage.items()},
        },
        "suggested_command_registry_additions": suggestions,
        "suggestion_notes": notes,
        "triage": triage,
        "conflicts": conflicts,
        "covered_or_skipped_capabilities": covered,
        "all_capabilities": [capability.as_dict() for capability in capabilities],
    }


def classify_capability(capability: Capability) -> dict[str, Any]:
    name = capability.identifier
    norm = normalize_key(name)
    feature_norm = normalize_key(capability.feature)
    text = normalize_key(" ".join([name, capability.display_name, capability.description]))

    if capability.kind == "property":
        return passthrough("approved_non_command", property_pattern(name, feature_norm), property_family(feature_norm), "SiLA property, not a script command.")

    exact = exact_command_classification(capability)
    if exact:
        return exact

    if norm in {"transferlabware", "transferlabwarebacktobase"}:
        return mapped("move_plate", "move_plate", "RGA/CGA", "Connector labware transfer command maps to existing move_plate IR operation.")
    if norm == "initializeinstrument":
        return mapped("initialize_device", "initialize_device", "Device", "Runtime instrument initialization maps to existing initialize_device IR operation.")

    if norm == "start" or any(token in norm for token in ("startfluent", "startinsimulationmode", "startinintegrationmode", "startandlogin", "shutdown", "forceshutdown", "getruntime")):
        return passthrough("approved_passthrough", "runtime_integration", "FluentControl runtime", "Connector lifecycle operation.")
    if any(token in norm for token in ("preparemethod", "runmethod", "pauserun", "resumerun", "stopmethod", "finishexecution", "closemethod")):
        return passthrough("approved_passthrough", "method_execution", "FluentControl runtime", "Runtime method execution control.")
    if feature_norm == "runcontroller" or norm in {"run", "getparameters", "methods"}:
        return passthrough("approved_passthrough", "method_execution", "FluentControl runtime", "High-level method execution capability.")
    if any(token in norm for token in ("variable", "resolveexpression")):
        return passthrough("approved_passthrough", "variables", "Variables", "Runtime variable/expression capability.")
    if any(token in norm for token in ("dialog", "queryatstartup")):
        return passthrough("approved_passthrough", "runtime_dialog", "User/script flow", "Runtime dialog response capability.")
    if any(token in norm for token in ("loginuser", "validateuser", "username")):
        return passthrough("approved_passthrough", "runtime_auth", "FluentControl runtime", "Runtime user authentication capability.")
    if any(token in norm for token in ("executecommand", "genericcommand")):
        return passthrough("approved_passthrough", "runtime_integration", "Application driver", "Raw XML command execution capability.")
    if any(token in norm for token in ("abortcommand", "cancelcommand", "finishcommand", "pause", "resume")):
        return passthrough("approved_passthrough", "execution_control", "FluentControl runtime", "Command execution control capability.")
    if any(token in norm for token in ("getfingers", "dropfingers")):
        return passthrough("approved_passthrough", "gripper", "RGA/CGA", "Gripper finger hardware operation.")
    if any(token in norm for token in ("removelabware", "setlocation")):
        return passthrough("approved_passthrough", "worktable_runtime", "Worktable", "Runtime worktable mutation not represented in protocol IR.")
    if "interiorlight" in norm:
        return passthrough("approved_passthrough", "hardware_io", "Device", "Instrument hardware I/O operation.")
    if "simulation" in text or "realmode" in norm:
        return passthrough("approved_passthrough", "runtime_integration", "Simulation", "Simulation mode control.")

    return passthrough("approved_passthrough", "runtime_integration", capability.feature_display_name or capability.feature, "Connector capability not represented in protocol IR.")


def exact_command_classification(capability: Capability) -> dict[str, Any] | None:
    norm = normalize_key(capability.identifier)
    raw_text = " ".join(capability.raw_command_types)
    if norm == "addlabware":
        return mapped("add_labware", "load_labware", "Worktable", "Connector command name matches existing add_labware IR operation.")
    if norm == "userprompt":
        return mapped("prompt_user", "prompt_user", "User/script flow", "Connector command name matches existing prompt_user IR operation.")
    if norm == "aspirate":
        family = "LiHa/FCA" if "Liha" in raw_text else "MCA384"
        operation = "liha_aspirate" if family == "LiHa/FCA" else "aspirate"
        return mapped(operation, "aspirate", family, "Connector command emits aspirate script command XML.")
    if norm == "dispense":
        family = "LiHa/FCA" if "Liha" in raw_text else "MCA384"
        operation = "liha_dispense" if family == "LiHa/FCA" else "dispense"
        return mapped(operation, "dispense", family, "Connector command emits dispense script command XML.")
    if norm == "gettips":
        family = "LiHa/FCA" if "Liha" in raw_text or "diti" in normalize_key(" ".join(field.identifier for field in capability.parameters)) else "MCA384"
        operation = "liha_get_tips" if family == "LiHa/FCA" else "pick_up_tips"
        return mapped(operation, "pick_up_tips", family, "Connector command emits get-tips script command XML.")
    if norm == "droptips":
        family = "LiHa/FCA" if "Liha" in raw_text else "MCA384"
        operation = "liha_drop_tips" if family == "LiHa/FCA" else "mca384_drop_tips"
        return mapped(operation, "drop_tips", family, "Connector command emits drop-tips script command XML.")
    return None


def mapped(operation: str, pattern_type: str, family: str, reason: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "pattern_type": pattern_type,
        "family": family,
        "support_status": "",
        "confidence": "medium",
        "reason": reason,
    }


def passthrough(support_status: str, pattern_type: str, family: str, reason: str) -> dict[str, Any]:
    return {
        "operation": "",
        "pattern_type": pattern_type,
        "family": family,
        "support_status": support_status,
        "confidence": "medium",
        "reason": reason,
    }


def property_pattern(name: str, feature_norm: str) -> str:
    norm = normalize_key(name)
    if any(token in norm for token in ("state", "progress", "error", "ready", "running", "attached", "alive", "notification")):
        return "runtime_status"
    if any(token in norm for token in ("method", "variable", "username")):
        return "runtime_metadata"
    if "dialog" in norm:
        return "runtime_dialog"
    if feature_norm == "simulationcontroller":
        return "simulation_status"
    return "connector_property"


def property_family(feature_norm: str) -> str:
    if "runtime" in feature_norm or "fluent" in feature_norm or "runcontroller" in feature_norm:
        return "FluentControl runtime"
    if "simulation" in feature_norm:
        return "Simulation"
    return "Connector property"


def candidate_aliases(capability: Capability) -> list[str]:
    aliases = [
        capability.qualified_identifier,
        capability.identifier,
        capability.display_name,
        snake_case(capability.identifier),
        *capability.code_aliases,
        *capability.raw_command_types,
    ]
    return unique(alias for alias in aliases if alias)


def connector_facing_aliases(capability: Capability) -> list[str]:
    return unique(
        alias
        for alias in [
            capability.qualified_identifier,
            capability.identifier,
            capability.display_name,
            snake_case(capability.identifier),
            *capability.code_aliases,
        ]
        if alias
    )


def free_aliases(
    capability: Capability,
    aliases: list[str],
    registry: RegistryLookup,
    reserved_alias_keys: set[str],
) -> list[str]:
    kept: list[str] = []
    for alias in aliases:
        alias_key = normalize_key(alias)
        if alias_key in reserved_alias_keys:
            continue
        hit = registry.direct_lookup(alias)
        if hit is None:
            kept.append(alias)
            continue
    return unique(kept)


def registry_entry_for(capability: Capability, classification: dict[str, Any], aliases: list[str]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "pattern_type": classification["pattern_type"],
        "family": classification["family"],
        "requires": requires_for(capability, classification),
        "aliases": aliases,
    }
    if classification["operation"]:
        entry["operation"] = classification["operation"]
    else:
        entry["support_status"] = classification["support_status"]
        entry["passthrough_reason"] = classification["reason"]
    field_aliases = field_aliases_for(capability)
    if field_aliases:
        entry["field_aliases"] = field_aliases
    entry["manual_step"] = manual_step_for(capability, classification)
    display_name = (capability.display_name or "").strip()
    if display_name:
        entry["fluentcontrol_name"] = display_name
        entry["fluentcontrol_name_source"] = "connector_display_name"
        entry["confidence"] = classification.get("confidence") or "medium"
    return entry


def requires_for(capability: Capability, classification: dict[str, Any]) -> list[str]:
    operation = classification.get("operation")
    if operation in {"add_labware", "move_plate", "aspirate", "dispense", "liha_aspirate", "liha_dispense"}:
        requires = ["labware"]
    elif operation in {"pick_up_tips", "liha_get_tips", "drop_tips", "liha_drop_tips", "mca384_drop_tips"}:
        requires = []
    else:
        requires = []
    if operation in {"aspirate", "dispense", "liha_aspirate", "liha_dispense"}:
        requires.extend(["volume_ul", "liquid_class"])
    return requires


def field_aliases_for(capability: Capability) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for field_item in [*capability.parameters, *capability.responses]:
        canonical = canonical_field(field_item.identifier)
        if canonical:
            aliases.setdefault(canonical, []).append(field_item.identifier)
    return {key: unique(values) for key, values in aliases.items()}


def canonical_field(name: str) -> str:
    norm = normalize_key(name)
    mappings = (
        ("labware_type", ("labwaretype", "racktype")),
        ("labware", ("labwarename", "labware", "labwaretolocation")),
        ("source_location", ("sourcelocation", "fromlocation")),
        ("destination_location", ("targetlocation", "destinationlocation", "tolocation")),
        ("position", ("position", "targetposition", "targetsite")),
        ("rotation", ("rotation",)),
        ("volume_ul", ("volume", "airgapvolume")),
        ("speed", ("airgapspeed",)),
        ("liquid_class", ("liquidclass", "liquidclassname")),
        ("tip_type", ("diti", "ditype", "dititype", "gripperfingers")),
        ("device_alias", ("devicealias", "availableid")),
        ("method", ("method", "toprepare", "subroutinename", "workspaceormethodname")),
        ("variable", ("variablename", "name")),
        ("value", ("value", "returnvalue", "result")),
        ("prompt", ("text", "description")),
        ("username", ("username",)),
        ("password", ("password",)),
        ("timeout", ("timeout",)),
        ("command_xml", ("command", "content")),
        ("dialog", ("dialogidentifier", "buttontopress")),
        ("workspace", ("workspaceselection",)),
        ("on", ("on", "acceptvalues")),
    )
    for canonical, candidates in mappings:
        if norm in candidates:
            return canonical
    return ""


def manual_step_for(capability: Capability, classification: dict[str, Any]) -> str:
    operation = classification.get("operation")
    if operation == "add_labware":
        return "Load labware {labware}"
    if operation == "move_plate":
        return f"{capability.display_name or capability.identifier} {{labware}}"
    if operation in {"aspirate", "liha_aspirate"}:
        return "Aspirate {volume_ul} uL from {labware} using {liquid_class}"
    if operation in {"dispense", "liha_dispense"}:
        return "Dispense {volume_ul} uL into {labware} using {liquid_class}"
    if operation in {"pick_up_tips", "liha_get_tips"}:
        return "Get tips using connector runtime"
    if operation in {"drop_tips", "liha_drop_tips", "mca384_drop_tips"}:
        return "Drop tips at {labware}"
    if operation == "prompt_user":
        return "Prompt user: {prompt}"
    return capability.display_name or split_identifier(capability.identifier)


def is_covered(classification: dict[str, Any], hits: list[RegistryHit]) -> bool:
    if not hits:
        return False
    operation = classification.get("operation")
    support_status = classification.get("support_status")
    if operation:
        return any(hit.entry.get("operation") == operation for hit in hits)
    return any(hit.entry.get("support_status") == support_status for hit in hits)


def detect_conflict(
    capability: Capability,
    classification: dict[str, Any],
    hits: list[RegistryHit],
    registry: RegistryLookup,
) -> dict[str, Any] | None:
    expected_operation = classification.get("operation")
    if not expected_operation:
        return None
    mismatched = [hit.as_dict() for hit in hits if hit.entry.get("operation") and hit.entry.get("operation") != expected_operation]
    raw_hits = registry.lookup_all(capability.raw_command_types)
    raw_matches = [hit.as_dict() for hit in raw_hits if hit.entry.get("operation") == expected_operation]
    if mismatched and raw_matches:
        return {
            "capability": capability.qualified_identifier,
            "expected_operation": expected_operation,
            "mismatched_existing_hits": mismatched,
            "matching_raw_type_hits": raw_matches,
            "note": "Bare connector command name is ambiguous; prefer the qualified feature alias or raw FluentControl command type.",
        }
    return None


def triage_capability(
    *,
    capability: Capability,
    classification: dict[str, Any],
    conflict: dict[str, Any] | None,
    existing_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    bucket, reason = triage_bucket_for(capability, classification, conflict, existing_hits)
    return {
        "bucket": bucket,
        "capability": capability.qualified_identifier,
        "repo": capability.repo,
        "kind": capability.kind,
        "feature": capability.feature,
        "identifier": capability.identifier,
        "display_name": capability.display_name,
        "pattern_type": classification["pattern_type"],
        "family": classification["family"],
        "operation": classification["operation"],
        "support_status": classification["support_status"],
        "confidence": classification["confidence"],
        "reason": reason,
        "review_action": triage_review_action(bucket),
        "aliases": connector_facing_aliases(capability)[:16],
        "existing_hits": existing_hits,
        "source_xml": capability.source_xml,
        "code_paths": capability.code_paths,
        "raw_command_types": capability.raw_command_types,
    }


def triage_bucket_for(
    capability: Capability,
    classification: dict[str, Any],
    conflict: dict[str, Any] | None,
    existing_hits: list[dict[str, Any]],
) -> tuple[str, str]:
    pattern_type = str(classification.get("pattern_type") or "")
    operation = str(classification.get("operation") or "")
    norm = normalize_key(capability.identifier)

    if conflict or is_ambiguous_connector_alias(capability, existing_hits):
        return (
            "ambiguous_aliases",
            "Bare connector alias collides with existing registry language; prefer qualified feature aliases and manual review.",
        )
    if is_risky_execution_capability(capability, pattern_type):
        return (
            "risky_execution",
            "Capability can start, stop, authenticate, prepare, run, or otherwise control FluentControl/runtime state.",
        )
    if needs_custom_ir_support(pattern_type):
        return (
            "needs_custom_ir_support",
            "Capability exposes connector behavior that needs canonical IR fields or a simulator/provider contract before registry approval.",
        )
    if operation:
        return (
            "safe_metadata_status",
            "Connector capability maps to an existing protocol IR operation; safe as a registry addition after field alias review.",
        )
    if capability.kind == "property" or pattern_type in {"runtime_status", "runtime_metadata", "simulation_status", "connector_property"}:
        return (
            "safe_metadata_status",
            "Observe-only metadata/status capability; safe candidate for registry addition without run-control actions.",
        )
    if norm.startswith(("get", "list", "read")):
        return (
            "safe_metadata_status",
            "Read-like connector capability; verify it does not mutate runtime state before approving.",
        )
    return (
        "needs_custom_ir_support",
        "Connector capability is not represented by current protocol IR and needs a typed mapping decision.",
    )


def is_ambiguous_connector_alias(capability: Capability, existing_hits: list[dict[str, Any]]) -> bool:
    norm = normalize_key(capability.identifier)
    generic_names = {
        "abort",
        "cancel",
        "execute",
        "getparameters",
        "methods",
        "pause",
        "resume",
        "run",
        "start",
        "stop",
    }
    if norm in generic_names:
        return True
    if len(existing_hits) > 1:
        return True
    return any(
        normalize_key(alias) in generic_names
        for alias in [capability.display_name, snake_case(capability.identifier), *capability.code_aliases]
    )


def is_risky_execution_capability(capability: Capability, pattern_type: str) -> bool:
    norm = normalize_key(" ".join([capability.identifier, capability.display_name, capability.description]))
    risky_tokens = (
        "abort",
        "cancel",
        "closemethod",
        "executecommand",
        "finishexecution",
        "forceshutdown",
        "genericcommand",
        "login",
        "preparemethod",
        "runmethod",
        "shutdown",
        "startfluent",
        "startinintegrationmode",
        "startinsimulationmode",
        "stopmethod",
        "validateuser",
    )
    if pattern_type in {"method_execution", "execution_control", "runtime_auth"}:
        return True
    if pattern_type == "runtime_integration" and any(token in norm for token in risky_tokens):
        return True
    return any(token in norm for token in risky_tokens)


def needs_custom_ir_support(pattern_type: str) -> bool:
    return pattern_type in {
        "gripper",
        "hardware_io",
        "runtime_dialog",
        "variables",
        "worktable_runtime",
    }


def triage_review_action(bucket: str) -> str:
    actions = {
        "safe_metadata_status": "Review aliases/fields, then allow as observe/status metadata or existing-IR registry additions.",
        "risky_execution": "Do not add to default registry until explicit operator opt-in and run-control safeguards exist.",
        "ambiguous_aliases": "Do not add bare aliases; keep qualified connector names or create manual conflict-resolved entries.",
        "needs_custom_ir_support": "Define canonical IR fields/provider behavior before accepting the connector capability.",
    }
    return actions[bucket]


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Connector Capability Suggestions",
        "",
        "Generated by `source/tools/connectors/connector_capability_extractor.py`.",
        "",
        "## Summary",
        "",
        f"- SiLA XML files: {summary['sila_xml_files']}",
        f"- Capabilities: {summary['capabilities']} ({summary['commands']} commands, {summary['properties']} properties)",
        f"- Suggested command_registry additions: {summary['suggested_additions']}",
        f"- Covered or skipped capabilities: {summary['covered_or_skipped']}",
        f"- Ambiguous existing aliases: {summary['conflicts']}",
        f"- Triage: {format_triage_counts(summary.get('triage_counts') or {})}",
        "",
    ]
    triage = payload.get("triage") or {}
    if triage:
        lines.extend(["## Triage", ""])
        for bucket in TRIAGE_BUCKETS:
            items = triage.get(bucket) or []
            lines.extend([f"### {TRIAGE_BUCKET_TITLES[bucket]}", "", f"- Count: {len(items)}"])
            for item in items[:25]:
                operation = item.get("operation") or item.get("support_status") or item.get("pattern_type")
                lines.append(f"- `{item['capability']}` ({operation}): {item['reason']}")
            hidden = max(0, len(items) - 25)
            if hidden:
                lines.append(f"- ... {hidden} more in JSON output")
            lines.append("")
    suggestions = payload["suggested_command_registry_additions"]
    if suggestions:
        lines.extend(
            [
                "## Suggested Additions",
                "",
                "Copy entries from this object into the file returned by `source_command_registry_path()` after reviewing the notes.",
                "",
                "```json",
                json.dumps(suggestions, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    if payload["conflicts"]:
        lines.extend(["## Ambiguous Aliases", ""])
        for conflict in payload["conflicts"]:
            lines.append(f"- `{conflict['capability']}` expects `{conflict['expected_operation']}`.")
            lines.append(f"  {conflict['note']}")
        lines.append("")
    notes = payload["suggestion_notes"]
    if notes:
        lines.extend(["## Review Notes", ""])
        for entry_id, note in sorted(notes.items()):
            lines.append(f"- `{entry_id}`: {note['classification_reason']}")
            if note.get("raw_command_types"):
                lines.append(f"  Raw types: {', '.join(note['raw_command_types'])}")
            if note.get("code_paths"):
                lines.append(f"  Code: {', '.join(note['code_paths'])}")
        lines.append("")
    lines.extend(
        [
            "## Connector Repos",
            "",
            *[f"- `{path}`" for path in payload["connector_repos"]],
            "",
        ]
    )
    return "\n".join(lines)


def format_triage_counts(counts: dict[str, Any]) -> str:
    return ", ".join(f"{bucket}={int(counts.get(bucket) or 0)}" for bucket in TRIAGE_BUCKETS)


def direct_children(node: ET.Element, names: set[str]) -> list[ET.Element]:
    return [child for child in list(node) if local_name(child.tag) in names]


def first_direct_child(node: ET.Element, name: str) -> ET.Element | None:
    for child in list(node):
        if local_name(child.tag) == name:
            return child
    return None


def child_text(node: ET.Element, name: str) -> str:
    child = first_direct_child(node, name)
    return clean_text(child.text or "") if child is not None else ""


def first_descendant_text(node: ET.Element, name: str) -> str:
    for child in node.iter():
        if local_name(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return ""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def first_doc_line(value: str) -> str:
    return clean_text(value).split(". ")[0].strip()


def snake_to_pascal(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_") if part)


def snake_case(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()


def split_identifier(value: str) -> str:
    return snake_case(value).replace("_", " ").capitalize()


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def capability_key(feature: str, identifier: str) -> str:
    return f"{normalize_key(feature)}::{normalize_key(identifier)}"


def lookup_keys(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    local = text.rsplit(".", 1)[-1].strip()
    candidates = {
        text,
        local,
        re.sub(r"ScriptCommandDataV\d+$", "ScriptCommand", local),
        re.sub(r"CommandDataV\d+$", "Command", local),
        re.sub(r"DataV\d+$", "", local),
        re.sub(r"DataV\d+$", "Command", local),
    }
    return [normalize_key(candidate) for candidate in candidates if candidate]


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = normalize_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def relpath(path: Path) -> str:
    path = path.resolve()
    for base in (REPO_ROOT.resolve(), SOURCE_ROOT.resolve()):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
