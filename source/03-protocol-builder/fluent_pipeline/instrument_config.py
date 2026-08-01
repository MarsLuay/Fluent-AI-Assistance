"""Host-local FluentControl/VisionX instrument configuration inspection."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


DEFAULT_INSTRUMENT_CONFIG_DIR = Path(r"C:\ProgramData\Tecan\VisionX\InstrumentConfigurations")
INSTRUMENT_CONFIG_DIR_ENV = "TECAN_VISIONX_INSTRUMENT_CONFIG_DIR"
HOST_CONFIG_REPORT_VERSION = "tecan.host_instrument_config.v1"


def resolve_instrument_config_dir(path: str | Path | None = None) -> Path:
    """Return the host directory that contains VisionX ``.config`` files."""
    raw = path if path is not None else os.environ.get(INSTRUMENT_CONFIG_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_INSTRUMENT_CONFIG_DIR


def list_installed_config_names(path: str | Path | None = None) -> list[str]:
    """List installed FluentControl/VisionX configuration names from ``.config`` files."""
    config_dir = resolve_instrument_config_dir(path)
    if not config_dir.exists() or not config_dir.is_dir():
        return []
    names = {
        item.stem
        for item in config_dir.iterdir()
        if item.is_file() and item.suffix.casefold() == ".config"
    }
    return sorted(names, key=str.casefold)


def infer_expected_host_config(
    *,
    intent: str = "",
    source_manifest: dict[str, Any] | None = None,
    selected_source_scripts: list[dict[str, Any]] | None = None,
    protocol_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer conservative host configuration hints from request/source evidence."""
    manifest = source_manifest or {}
    exact_names = _exact_names_from_manifest(manifest)
    haystack = _evidence_text(intent, manifest, selected_source_scripts or [], protocol_ir or {})
    tokens = {token for token in ("rga", "a200", "fluent") if token in haystack}

    patterns: list[str] = []
    reasons: list[str] = []
    if "rga" in tokens:
        patterns.append("RGA")
        reasons.append("RGA evidence was found in the request/source context.")
    if "a200" in tokens and "rga" in tokens:
        reasons.append("A200 evidence was found together with RGA evidence; use an RGA/A200-capable host configuration.")
    if exact_names:
        reasons.append("Instrument configuration filenames were present in source snapshot evidence.")

    status = "inferred" if exact_names or patterns else "unknown"
    return {
        "status": status,
        "exact_names": exact_names,
        "patterns": patterns,
        "required": False,
        "tokens": sorted(tokens),
        "summary": _hint_summary(exact_names, patterns),
        "reasons": reasons,
        "ask_user": _ask_user(exact_names, patterns),
    }


def inspect_host_instrument_configs(
    expected: dict[str, Any] | None = None,
    *,
    config_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Compare installed host configurations against expected exact names/patterns."""
    hint = _normalize_expected(expected)
    directory = resolve_instrument_config_dir(config_dir)
    installed = list_installed_config_names(directory)
    exact_matches = [name for name in installed if name in set(hint["exact_names"])]
    pattern_matches = [
        name
        for name in installed
        if any(_pattern_matches(pattern, name) for pattern in hint["patterns"])
        and name not in exact_matches
    ]
    matched = [*exact_matches, *pattern_matches]

    if not installed:
        status = "failed" if hint["required"] else "needs_review"
        summary = f"No `.config` files were found under `{directory}`."
    elif matched:
        status = "matched"
        summary = "At least one installed host configuration matches the expected hints."
    elif hint["exact_names"] or hint["patterns"]:
        status = "failed" if hint["required"] else "needs_review"
        summary = "Installed host configurations do not match the expected hints."
    else:
        status = "needs_review"
        summary = "No expected host configuration could be inferred; operator confirmation is required."

    return {
        "schema_version": HOST_CONFIG_REPORT_VERSION,
        "config_dir": str(directory),
        "installed_configs": installed,
        "expected": hint,
        "matches": matched,
        "status": status,
        "summary": summary,
        "user_instruction": _instruction(status, installed, hint, matched),
    }


def render_host_instrument_config_markdown(report: dict[str, Any]) -> str:
    """Render host instrument configuration status for generated reports."""
    expected = report.get("expected") or {}
    lines = [
        "# Host Instrument Configuration Check",
        "",
        f"- Status: `{report.get('status') or 'unknown'}`",
        f"- Config folder: `{report.get('config_dir') or ''}`",
        f"- Summary: {report.get('summary') or ''}",
        "",
        "## Expected Configuration",
        "",
    ]
    if expected.get("exact_names"):
        lines.append(f"- Exact names: `{', '.join(expected['exact_names'])}`")
    if expected.get("patterns"):
        lines.append(f"- Name patterns: `{', '.join(expected['patterns'])}`")
    if not expected.get("exact_names") and not expected.get("patterns"):
        lines.append("- No exact name or pattern was inferred from the request/source context.")
    if expected.get("reasons"):
        for reason in expected["reasons"]:
            lines.append(f"- Evidence: {reason}")

    lines.extend(["", "## Installed Configurations", ""])
    installed = report.get("installed_configs") or []
    if installed:
        for name in installed:
            marker = " (matches expected hint)" if name in set(report.get("matches") or []) else ""
            lines.append(f"- `{name}`{marker}")
    else:
        lines.append("- None detected on this host.")

    lines.extend(
        [
            "",
            "## User Action",
            "",
            report.get("user_instruction") or _instruction("needs_review", installed, expected, report.get("matches") or []),
            "",
        ]
    )
    return "\n".join(lines)


def _normalize_expected(expected: dict[str, Any] | None) -> dict[str, Any]:
    value = expected or {}
    exact_names = _dedupe_strings(value.get("exact_names") or value.get("allowed_names") or [])
    patterns = _dedupe_strings(value.get("patterns") or value.get("allowed_patterns") or [])
    return {
        "status": value.get("status") or ("inferred" if exact_names or patterns else "unknown"),
        "exact_names": exact_names,
        "patterns": patterns,
        "required": bool(value.get("required")),
        "tokens": _dedupe_strings(value.get("tokens") or []),
        "summary": value.get("summary") or _hint_summary(exact_names, patterns),
        "reasons": _dedupe_strings(value.get("reasons") or []),
        "ask_user": value.get("ask_user") or _ask_user(exact_names, patterns),
    }


def _exact_names_from_manifest(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("snapshot_summary") or {}
    candidates: list[str] = []
    for raw in summary.get("instrument_configuration_files") or []:
        path = Path(str(raw).replace("\\", "/"))
        if path.suffix.casefold() == ".config":
            candidates.append(path.stem)
    return _dedupe_strings(candidates)


def _evidence_text(
    intent: str,
    manifest: dict[str, Any],
    selected_source_scripts: list[dict[str, Any]],
    protocol_ir: dict[str, Any],
) -> str:
    values: list[Any] = [intent, manifest.get("snapshot_summary"), manifest.get("device_aliases"), manifest.get("available_ids")]
    values.extend(selected_source_scripts)
    values.append(protocol_ir.get("source") if isinstance(protocol_ir, dict) else {})
    values.append(protocol_ir.get("dependencies") if isinstance(protocol_ir, dict) else [])
    values.append(protocol_ir.get("steps") if isinstance(protocol_ir, dict) else [])
    return " ".join(_flatten_text(values)).casefold()


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_flatten_text(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(_flatten_text(item))
        return out
    return [str(value)]


def _pattern_matches(pattern: str, name: str) -> bool:
    try:
        return re.search(pattern, name, flags=re.IGNORECASE) is not None
    except re.error:
        return pattern.casefold() in name.casefold()


def _hint_summary(exact_names: list[str], patterns: list[str]) -> str:
    if exact_names:
        return "Use one of the exact host configurations captured from source evidence."
    if patterns:
        return "Use an installed host configuration whose name matches the inferred pattern(s)."
    return "No specific host configuration was inferred."


def _ask_user(exact_names: list[str], patterns: list[str]) -> str:
    if exact_names or patterns:
        expected = []
        if exact_names:
            expected.append(f"one of: {', '.join(exact_names)}")
        if patterns:
            expected.append(f"a name matching: {', '.join(patterns)}")
        return (
            "Before import/run, verify FluentControl is using "
            f"{' or '.join(expected)}. If it is not, switch via the FluentControl/VisionX "
            "configuration dropdown before opening, simulating, or running the generated method."
        )
    return (
        "Before import/run, review the installed FluentControl/VisionX configuration dropdown "
        "and confirm the active configuration matches the target instrument, arm/head setup, "
        "RGA/finger hardware, carriers, and deck for this method."
    )


def _instruction(status: str, installed: list[str], expected: dict[str, Any], matches: list[str]) -> str:
    if matches:
        return (
            "Before import/run, verify FluentControl's configuration dropdown is set to one of "
            f"`{', '.join(matches)}`. If another configuration is active, switch to the matching "
            "configuration before opening, simulating, or running the method."
        )
    if expected.get("exact_names") or expected.get("patterns"):
        installed_text = ", ".join(installed) if installed else "none detected"
        return (
            f"{expected.get('ask_user') or _ask_user(expected.get('exact_names') or [], expected.get('patterns') or [])} "
            f"Installed configs on this host: {installed_text}."
        )
    return _ask_user([], [])


def _dedupe_strings(values: Any) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out
