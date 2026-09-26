"""Conservative, source-backed contracts for external process invocations.

The protocol IR intentionally keeps ``ExecuteApplication`` and
``ExecuteVbScript`` compatible with FluentControl.  This module derives a
versioned review contract from those existing steps without executing a
launcher, shell, script, or network client.  Paths and data-flow edges are
only promoted when the invocation shape or a supplied source contract proves
their role; otherwise the raw spelling is retained and the result is marked
for review.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Any, Callable, Iterable, Mapping, Sequence


EXTERNAL_PROCESS_CONTRACT_SCHEMA = "tecan.external_process_contracts.v1"
EXTERNAL_PROCESS_LINEAGE_SCHEMA = "tecan.external_process_lineage.v1"

_PATH_SUFFIXES = frozenset(
    {
        ".bat",
        ".cmd",
        ".com",
        ".config",
        ".csv",
        ".dll",
        ".gwl",
        ".ini",
        ".json",
        ".prf",
        ".ps1",
        ".py",
        ".pyw",
        ".tsv",
        ".txt",
        ".vb",
        ".vbe",
        ".vbs",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_SCRIPT_SUFFIXES = frozenset({".bat", ".cmd", ".ps1", ".py", ".pyw", ".vb", ".vbe", ".vbs"})
_CONFIG_SUFFIXES = frozenset({".config", ".ini", ".json", ".prf", ".xml", ".yaml", ".yml"})
_INPUT_OPTIONS = frozenset(
    {"-i", "--in", "--input", "--input-file", "--source", "--worklist-input", "/i", "/input"}
)
_OUTPUT_OPTIONS = frozenset(
    {"-o", "--out", "--output", "--output-file", "--gwl-output", "--worklist-output", "/o", "/output"}
)
_CONFIG_OPTIONS = frozenset({"--config", "--configuration", "--profile", "--settings", "/config", "/profile"})
_SHELL_NAMES = frozenset({"cmd.exe", "command.com", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe"})
_PYTHON_NAMES = frozenset({"python", "python.exe", "python3", "python3.exe", "py", "py.exe"})
_SHELL_SWITCHES = frozenset({"/c", "/k", "-c", "-command", "-file", "--command", "--file"})
_SECRET_OPTION_RE = re.compile(
    r"(?:token|api[-_]?key|secret|password|passwd|credential|client[-_]?secret|access[-_]?key)",
    re.IGNORECASE,
)
_FLUENT_VARIABLE_RE = re.compile(r"~([^~]+)~|@fc:([A-Za-z_][A-Za-z0-9_]*)|\[([A-Za-z_][A-Za-z0-9_]*)\]")
_ENV_VARIABLE_RE = re.compile(r"%(?:[^%]+)%|\$env:[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE)
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")
_SHELL_META_RE = re.compile(r"&&|\|\||[&|<>;`]|")
_XML_COMMAND_RE = re.compile(
    r"<(?P<tag>ExecuteApplicationStatement|ExecuteVbScriptStatement)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_LINE_RE = re.compile(r"<LineNumber>\s*(\d+)\s*</LineNumber>", re.IGNORECASE)


def _basename(value: str) -> str:
    text = str(value or "").strip().strip('"').strip("'").replace("/", "\\")
    return PureWindowsPath(text).name.casefold()


def _suffix(value: str) -> str:
    return PureWindowsPath(str(value or "").strip().strip('"').strip("'")).suffix.casefold()


def _is_path_token(value: str) -> bool:
    text = str(value or "").strip().strip('"').strip("'")
    if not text or text.startswith(("-", "/")) and not _WINDOWS_ABSOLUTE_RE.match(text):
        return False
    return bool(_WINDOWS_ABSOLUTE_RE.match(text) or _suffix(text) in _PATH_SUFFIXES)


def tokenize_windows_command_line(arguments: str) -> list[str]:
    """Tokenize a Windows command line without starting a process.

    This follows the backslash/quote rules used by ``CommandLineToArgvW`` for
    the common argument forms.  It deliberately does not interpret shell
    operators; callers must inspect :func:`shell_syntax` separately.
    """
    text = str(arguments or "")
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if char in " \t\r\n" and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            index += 1
            continue
        if char == "\\":
            start = index
            while index < len(text) and text[index] == "\\":
                index += 1
            slashes = index - start
            if index < len(text) and text[index] == '"':
                current.extend("\\" * (slashes // 2))
                if slashes % 2:
                    current.append('"')
                    index += 1
                else:
                    quoted = not quoted
                    index += 1
                continue
            current.extend("\\" * slashes)
            continue
        if char == '"':
            quoted = not quoted
            index += 1
            continue
        current.append(char)
        index += 1
    if current or text.endswith((' ', '\t', '\r', '\n')) is False and text:
        tokens.append("".join(current))
    return tokens


def shell_syntax(arguments: str) -> list[str]:
    """Return shell constructs outside quoted strings, preserving raw args."""
    findings: list[str] = []
    quoted = False
    index = 0
    text = str(arguments or "")
    while index < len(text):
        char = text[index]
        if char == '"':
            quoted = not quoted
            index += 1
            continue
        if not quoted:
            if text[index : index + 2] in {"&&", "||"}:
                findings.append(text[index : index + 2])
                index += 2
                continue
            if char in "&|<>;`":
                findings.append(char)
        index += 1
    if _ENV_VARIABLE_RE.search(text):
        findings.append("environment-variable-expansion")
    return list(dict.fromkeys(findings))


def _xml_value(body: str, name: str) -> str:
    match = re.search(fr"<{name}\b[^>]*>(.*?)</{name}>", body, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def _valid_variable(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\[\d+\])?", str(name or "").strip()))


def _stable_id(*parts: Any) -> str:
    material = "|".join(str(part or "") for part in parts)
    return "ep_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _option_role(option: str, source_contract: Mapping[str, Any]) -> str | None:
    normalized = str(option or "").casefold()
    explicit = source_contract.get("argument_roles") if isinstance(source_contract, Mapping) else None
    if isinstance(explicit, Mapping):
        for key, role in explicit.items():
            if str(key).casefold() == normalized and str(role) in {"config", "input", "output"}:
                return str(role)
    if normalized in _INPUT_OPTIONS:
        return "input"
    if normalized in _OUTPUT_OPTIONS:
        return "output"
    if normalized in _CONFIG_OPTIONS:
        return "config"
    return None


def _redact_arguments(arguments: str, tokens: Sequence[str]) -> tuple[str, list[dict[str, str]]]:
    """Return safe diagnostic text and findings without retaining secret values."""
    redacted = str(arguments or "")
    findings: list[dict[str, str]] = []
    for index, token in enumerate(tokens):
        if not _SECRET_OPTION_RE.search(token.lstrip("-/")):
            continue
        if "=" in token:
            option, value = token.split("=", 1)
            if value:
                redacted = redacted.replace(value, "[REDACTED]", 1)
                findings.append({"code": "plaintext_secret_argument", "severity": "review", "argument": option})
        elif index + 1 < len(tokens) and tokens[index + 1]:
            value = tokens[index + 1]
            redacted = redacted.replace(value, "[REDACTED]", 1)
            findings.append({"code": "plaintext_secret_argument", "severity": "review", "argument": token})
    return redacted, findings


def _runtime_evidence_for(path: str, host_environment: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(host_environment, Mapping):
        return None
    records: list[Any] = []
    for key in ("external_runtimes", "runtime_inventory", "runtimes", "executables"):
        raw = host_environment.get(key)
        if isinstance(raw, Mapping):
            records.extend({"name": name, **(value if isinstance(value, Mapping) else {"present": value})} for name, value in raw.items())
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            records.extend(item for item in raw if isinstance(item, Mapping))
    target_name = _basename(path)
    for record in records:
        names = {str(record.get(key) or "").casefold() for key in ("name", "command", "executable", "basename", "path")}
        if target_name and any(_basename(name) == target_name or name == target_name for name in names if name):
            return dict(record)
    return None


def _dependency(
    path: str,
    *,
    kind: str,
    source: str,
    token: str = "",
    option: str | None = None,
    host_environment: Mapping[str, Any] | None = None,
    dynamic: bool = False,
) -> dict[str, Any]:
    evidence = _runtime_evidence_for(path, host_environment) if kind == "launcher" else None
    status = "unknown"
    resolution = "source"
    if evidence is not None:
        present = evidence.get("present")
        status = "present" if present is not False else "missing_on_target_host"
        resolution = "target_host_evidence"
    elif kind == "launcher":
        resolution = "unverified_target_host"
    return {
        "path": str(path),
        "kind": kind,
        "source": source,
        "raw_token": token or str(path),
        "option": option,
        "dynamic": bool(dynamic),
        "status": status,
        "resolution": resolution,
        "host_evidence": evidence,
    }


def _layer(kind: str, value: str, *, source: str, evidence: str = "") -> dict[str, str]:
    return {"kind": kind, "value": value, "source": source, "evidence": evidence}


def analyze_external_process_contract(
    application: str | Mapping[str, Any],
    arguments: str = "",
    *,
    invocation_kind: str = "execute_application",
    wait: bool = True,
    store_return: bool = False,
    return_variable: str = "",
    source: Mapping[str, Any] | None = None,
    source_contract: Mapping[str, Any] | None = None,
    host_environment: Mapping[str, Any] | None = None,
    wrapper_body: str | None = None,
) -> dict[str, Any]:
    """Build one normalized, side-effect-free external invocation contract."""
    if isinstance(application, Mapping):
        step = application
        parameters = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else step
        invocation_kind = str(step.get("operation") or parameters.get("invocation_kind") or invocation_kind)
        source = {**(source or {}), **{key: step.get(key) for key in ("source_script", "source_path", "line_number", "id") if step.get(key) is not None}}
        application = parameters.get("path") or parameters.get("application") or parameters.get("vb_script") or ""
        arguments = parameters.get("arguments") or parameters.get("args") or ""
        wait = bool(parameters.get("wait", wait))
        store_return = bool(parameters.get("store_return", store_return))
        return_variable = str(parameters.get("variable") or parameters.get("return_variable") or return_variable)
        source_contract = source_contract or parameters.get("source_contract")
    source = dict(source or {})
    source_contract = dict(source_contract or {})
    application_text = str(application or "").strip().strip('"').strip("'")
    raw_arguments = str(arguments or "")
    tokens = tokenize_windows_command_line(raw_arguments)
    shell_tokens = shell_syntax(raw_arguments)
    parser_mode = "windows_argv"
    findings: list[dict[str, Any]] = []
    if shell_tokens:
        parser_mode = "shell_review"
        findings.append({"code": "shell_invocation_needs_review", "severity": "review", "syntax": shell_tokens})
    if invocation_kind == "execute_vb_script":
        parser_mode = "script_path"
    basename = _basename(application_text)
    is_shell = basename in _SHELL_NAMES
    is_python = basename in _PYTHON_NAMES or basename.startswith("python") and basename.endswith(".exe")
    layers: list[dict[str, str]] = []
    dependencies: list[dict[str, Any]] = []
    if application_text:
        top_kind = "script" if _suffix(application_text) in _SCRIPT_SUFFIXES else "launcher"
        dependencies.append(_dependency(application_text, kind=top_kind, source="application", token=application_text, host_environment=host_environment))
        layers.append(_layer("script" if top_kind == "script" else "launcher", application_text, source="application"))
    if is_shell:
        layers[0]["kind"] = "launcher"
        layers[0]["evidence"] = "shell launcher; nested command syntax retained"
    option_roles: dict[int, tuple[str, str]] = {}
    for index, token in enumerate(tokens):
        option = token.split("=", 1)[0] if token.startswith(("-", "/")) else ""
        if option:
            role = _option_role(option, source_contract)
            if role and "=" not in token and index + 1 < len(tokens):
                option_roles[index + 1] = (role, option)
            elif role and "=" in token:
                option_roles[index] = (role, option)
    nested_start = 0
    if is_shell:
        for index, token in enumerate(tokens):
            if token.casefold() in _SHELL_SWITCHES:
                nested_start = index + 1
                break
        layers.append(_layer("shell", application_text, source="application", evidence="shell switch present"))
    nested_launcher_seen = False
    for index, token in enumerate(tokens):
        stripped = token.strip().strip('"').strip("'")
        if not stripped or stripped.startswith(("-", "/")) and not _WINDOWS_ABSOLUTE_RE.match(stripped):
            continue
        token_suffix = _suffix(stripped)
        token_base = _basename(stripped)
        role_option = option_roles.get(index)
        if index < nested_start:
            continue
        if is_shell and not nested_launcher_seen and (token_base in _PYTHON_NAMES or token_base in _SHELL_NAMES or token_suffix in {".exe", ".bat", ".cmd", ".ps1"}):
            dependencies.append(_dependency(stripped, kind="launcher", source="nested_argument", token=token, host_environment=host_environment))
            layers.append(_layer("launcher", stripped, source="argument", evidence="nested shell command"))
            nested_launcher_seen = True
            continue
        if (is_python or nested_launcher_seen) and token_suffix in _SCRIPT_SUFFIXES and not role_option:
            dependencies.append(_dependency(stripped, kind="script", source="argument", token=token, host_environment=host_environment))
            layers.append(_layer("script", stripped, source="argument", evidence="launcher script argument"))
            continue
        if role_option and _is_path_token(stripped):
            role, option = role_option
            dependencies.append(_dependency(stripped, kind=role, source="argument", token=token, option=option, host_environment=host_environment, dynamic=bool(_FLUENT_VARIABLE_RE.search(stripped))))
            continue
        if _is_path_token(stripped):
            kind = "config" if token_suffix in _CONFIG_SUFFIXES else "unknown"
            dependencies.append(_dependency(stripped, kind=kind, source="argument", token=token, host_environment=host_environment, dynamic=bool(_FLUENT_VARIABLE_RE.search(stripped))))
            if kind == "unknown":
                findings.append({"code": "argument_path_role_unknown", "severity": "review", "path": stripped})
    if invocation_kind == "execute_vb_script" and application_text:
        layers = [_layer("script", application_text, source="vb_script", evidence="Execute VBScript path")]
    if wrapper_body:
        if re.search(r"ProcessStartInfo|WScript\.Shell|CreateObject\s*\(\s*[\"']WScript\.Shell", wrapper_body, re.IGNORECASE):
            layers.append(_layer("wrapper", "VB/process wrapper", source="wrapper_body", evidence="source wrapper contains process launch API"))
        else:
            layers.append(_layer("unknown", "wrapper body", source="wrapper_body", evidence="wrapper source was supplied but launch shape is unrecognized"))
            findings.append({"code": "wrapper_launch_shape_unknown", "severity": "review"})
    variable_references = sorted({match.group(group) for match in _FLUENT_VARIABLE_RE.finditer(raw_arguments) for group in range(1, 4) if match.group(group)})
    environment_references = sorted(set(_ENV_VARIABLE_RE.findall(raw_arguments)))
    if environment_references:
        findings.append({"code": "dynamic_environment_reference", "severity": "review", "references": environment_references})
    redacted_arguments, security_findings = _redact_arguments(raw_arguments, tokens)
    findings.extend(security_findings)
    if store_return and not return_variable:
        findings.append({"code": "return_code_target_missing", "severity": "error"})
    elif store_return and not _valid_variable(return_variable):
        findings.append({"code": "return_code_target_invalid", "severity": "error", "target": return_variable})
    elif not store_return and return_variable:
        findings.append({"code": "return_code_target_without_store", "severity": "review", "target": return_variable})
    if store_return and not wait:
        findings.append({"code": "async_return_capture_needs_review", "severity": "review"})
    for dependency in dependencies:
        if dependency.get("dynamic"):
            findings.append({"code": "dynamic_argument_path", "severity": "review", "path": dependency.get("path")})
        if dependency.get("status") == "missing_on_target_host":
            findings.append({"code": "runtime_missing_on_target_host", "severity": "error", "path": dependency.get("path")})
    invocation_id = str(source.get("invocation_id") or source.get("id") or _stable_id(source.get("source_script"), source.get("line_number"), invocation_kind, application_text, raw_arguments))
    declared_outputs = [item["path"] for item in dependencies if item.get("kind") == "output"]
    contract = {
        "schema_version": EXTERNAL_PROCESS_CONTRACT_SCHEMA,
        "invocation_id": invocation_id,
        "invocation_kind": invocation_kind,
        "source": source,
        "application": application_text,
        "raw_arguments": raw_arguments,
        "redacted_arguments": redacted_arguments,
        "parsed_argument_tokens": tokens,
        "parser_mode": parser_mode,
        "needs_review": bool(findings),
        "layers": layers,
        "dependencies": dependencies,
        "declared_outputs": declared_outputs,
        "variable_references": variable_references,
        "environment_references": environment_references,
        "wait": bool(wait),
        "store_return": bool(store_return),
        "return_code_variable": return_variable,
        "return_code_semantics": source_contract.get("return_code_meaning") or "opaque",
        "runtime_evidence": [item.get("host_evidence") for item in dependencies if item.get("host_evidence")],
        "findings": findings,
        "raw_metadata": {"source_contract": source_contract, "wrapper_body_available": bool(wrapper_body)},
    }
    return contract


def extract_external_process_contracts_from_xscr_text(
    text: str,
    *,
    source_script: str = "",
    source_path: str = "",
    host_environment: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Extract typed invocation evidence from XSCR without executing it."""
    contracts: list[dict[str, Any]] = []
    for match in _XML_COMMAND_RE.finditer(str(text or "")):
        tag = match.group("tag")
        body = match.group("body")
        line = _LINE_RE.search(body)
        line_number = int(line.group(1)) if line else None
        source = {"source_script": source_script, "source_path": source_path, "line_number": line_number, "command": tag}
        if tag.casefold() == "executevbscriptstatement":
            contract = analyze_external_process_contract(
                _xml_value(body, "VbScript"),
                invocation_kind="execute_vb_script",
                wait=True,
                source=source,
                host_environment=host_environment,
            )
        else:
            contract = analyze_external_process_contract(
                _xml_value(body, "Application"),
                _xml_value(body, "Arguments"),
                invocation_kind="execute_application",
                wait=_xml_value(body, "Wait").casefold() != "false",
                store_return=_xml_value(body, "StoreReturn").casefold() == "true",
                return_variable=_xml_value(body, "Variable"),
                source=source,
                host_environment=host_environment,
            )
        contracts.append(contract)
    return contracts


def build_external_process_contracts(
    protocol_ir: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    host_environment: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Derive contracts from canonical IR steps, preserving their order."""
    steps = protocol_ir.get("steps") if isinstance(protocol_ir, Mapping) else protocol_ir
    contracts: list[dict[str, Any]] = []
    for index, step in enumerate(_iter_step_mappings(steps or [])):
        operation = str(step.get("operation") or "").casefold()
        if operation not in {"execute_application", "execute_vb_script"}:
            continue
        source = {"step_index": index, "group": step.get("group"), "command_id": step.get("command_id"), "source_script": step.get("source_script"), "line_number": step.get("line_number")}
        contract = analyze_external_process_contract(step, source=source, host_environment=host_environment)
        contract["step_index"] = index
        contracts.append(contract)
    return contracts


def _iter_step_mappings(value: Iterable[Any]) -> Iterable[Mapping[str, Any]]:
    for item in value:
        if not isinstance(item, Mapping):
            continue
        if "operation" in item:
            yield item
        for key in ("steps", "then_steps", "else_steps", "children"):
            nested = item.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                yield from _iter_step_mappings(nested)


def build_external_process_lineage(
    protocol_ir: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    contracts: Sequence[Mapping[str, Any]] | None = None,
    host_environment: Mapping[str, Any] | None = None,
    gwl_validator: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Build conservative variable/file/process lineage for a method."""
    steps = list(_iter_step_mappings(protocol_ir.get("steps", []) if isinstance(protocol_ir, Mapping) else protocol_ir))
    contracts_list = [dict(item) for item in (contracts or build_external_process_contracts(steps, host_environment=host_environment))]
    edges: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []

    def add_node(node_id: str, kind: str, label: str, **metadata: Any) -> str:
        nodes.setdefault(node_id, {"id": node_id, "kind": kind, "label": label, **metadata})
        return node_id

    def add_edge(source: str, target: str, kind: str, evidence: str, confidence: str = "source_backed") -> None:
        add_node(source, source.split(":", 1)[0], source.split(":", 1)[-1])
        add_node(target, target.split(":", 1)[0], target.split(":", 1)[-1])
        edges.append({"from": source, "to": target, "kind": kind, "evidence": evidence, "confidence": confidence})

    step_file_roles: dict[str, list[tuple[str, str]]] = {}
    for index, step in enumerate(steps):
        operation = str(step.get("operation") or "").casefold()
        params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
        file_value = params.get("export_file") if operation == "export_variable" else params.get("import_file") if operation == "import_variable" else params.get("path") or params.get("worklist") or params.get("file")
        if file_value:
            normalized = _lineage_file_key(file_value)
            if normalized:
                role = "export" if operation == "export_variable" else "import" if operation == "import_variable" else "worklist"
                step_file_roles.setdefault(normalized, []).append((role, str(index)))
                add_node(f"file:{normalized}", "file", normalized, role=role)
                add_node(f"step:{index}", "step", operation)
                add_edge(f"step:{index}", f"file:{normalized}", f"{role}_file", "canonical IR file operation")
        if operation in {"export_variable", "import_variable"}:
            variables = params.get("variables") or params.get("variable") or []
            if isinstance(variables, str):
                variables = [variables]
            for variable in variables:
                if variable:
                    add_edge(f"variable:{variable}", f"file:{_lineage_file_key(file_value)}", "variable_file", "canonical IR variable operation") if operation == "export_variable" else add_edge(f"file:{_lineage_file_key(file_value)}", f"variable:{variable}", "file_variable", "canonical IR variable operation")

    for contract in contracts_list:
        invocation_id = str(contract.get("invocation_id") or _stable_id(contract.get("application"), contract.get("raw_arguments")))
        invocation_node = add_node(f"invocation:{invocation_id}", "invocation", str(contract.get("application") or ""), invocation_kind=contract.get("invocation_kind"))
        for variable in contract.get("variable_references") or []:
            add_edge(f"variable:{variable}", invocation_node, "argument_variable", "source argument variable reference")
        return_variable = str(contract.get("return_code_variable") or "")
        if contract.get("store_return") and return_variable:
            add_edge(invocation_node, f"variable:{return_variable}", "return_code", "FluentControl StoreReturn target")
        for dependency in contract.get("dependencies") or []:
            path = _lineage_file_key(dependency.get("path"))
            if not path:
                continue
            kind = dependency.get("kind")
            file_node = f"file:{path}"
            add_node(file_node, "file", path, role=kind)
            if kind == "output":
                add_edge(invocation_node, file_node, "external_output", "explicit output option/source contract")
            elif kind in {"input", "config"}:
                add_edge(file_node, invocation_node, f"external_{kind}", "explicit option/source contract")
            if kind == "unknown":
                findings.append({"code": "external_dependency_role_unknown", "severity": "review", "path": path, "invocation_id": invocation_id})
            for role, step_index in step_file_roles.get(path, []):
                if role == "import":
                    add_edge(file_node, f"step:{step_index}", "external_output_import", "later Import Variable consumes the path")
                elif role == "worklist" and str(path).casefold().endswith(".gwl"):
                    add_edge(file_node, f"step:{step_index}", "gwl_handoff", "GWL handoff remains owned by #161")
        for dependency in contract.get("dependencies") or []:
            if dependency.get("kind") == "output":
                output_path = _lineage_file_key(dependency.get("path"))
                if output_path and str(output_path).casefold().endswith(".gwl") and gwl_validator:
                    try:
                        result = gwl_validator(str(dependency.get("path")))
                    except Exception as exc:  # pragma: no cover - defensive boundary
                        result = {"status": "error", "error_type": type(exc).__name__}
                    findings.append({"code": "gwl_validation_delegated", "severity": "review", "path": output_path, "owner": "#161", "result": _sanitize_value(result)})
        if contract.get("store_return") and return_variable:
            for index, step in enumerate(steps):
                operation = str(step.get("operation") or "").casefold()
                params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
                condition = params.get("condition") or params.get("condition_expression") or ""
                if operation in {"conditional_branch", "default_branch"} and return_variable.casefold() in str(condition).casefold():
                    add_edge(f"variable:{return_variable}", f"step:{index}", "return_code_branch", "downstream branch references StoreReturn variable")
    for path, roles in step_file_roles.items():
        if any(role == "import" for role, _ in roles) and not any(edge.get("kind") == "external_output_import" and edge.get("to", "").endswith(next(index for role, index in roles if role == "import")) for edge in edges):
            findings.append({"code": "expected_external_output_unproven", "severity": "review", "path": path})
    return {
        "schema_version": EXTERNAL_PROCESS_LINEAGE_SCHEMA,
        "nodes": list(nodes.values()),
        "edges": edges,
        "findings": findings,
        "contracts": contracts_list,
    }


def analyze_external_processes(
    protocol_ir: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    host_environment: Mapping[str, Any] | None = None,
    gwl_validator: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Return contracts, lineage, and deterministic review findings."""
    contracts = build_external_process_contracts(protocol_ir, host_environment=host_environment)
    lineage = build_external_process_lineage(protocol_ir, contracts=contracts, host_environment=host_environment, gwl_validator=gwl_validator)
    findings = [finding for contract in contracts for finding in contract.get("findings") or []]
    findings.extend(lineage.get("findings") or [])
    return {
        "schema_version": EXTERNAL_PROCESS_CONTRACT_SCHEMA,
        "contracts": contracts,
        "lineage": lineage,
        "findings": findings,
        "needs_review": bool(findings),
    }


def simulate_external_process(
    contract: Mapping[str, Any],
    *,
    return_code: int | None = None,
    output_files: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe an injected outcome; never starts a process or writes a file."""
    store_return = bool(contract.get("store_return"))
    return {
        "schema_version": EXTERNAL_PROCESS_CONTRACT_SCHEMA,
        "invocation_id": contract.get("invocation_id"),
        "spawned": False,
        "side_effects": [],
        "return_code": return_code if store_return else None,
        "return_code_variable": contract.get("return_code_variable") if store_return else "",
        "output_files": dict(output_files or {}),
        "outputs_materialized": False,
        "return_code_semantics": contract.get("return_code_semantics") or "opaque",
    }


def _lineage_file_key(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip("'").replace("/", "\\")
    return text.casefold() if text else ""


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize_value(item) for key, item in value.items() if str(key).casefold() not in {"token", "password", "secret", "credential"}}
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str) and _SECRET_OPTION_RE.search(value):
        return "[REDACTED]"
    return value

