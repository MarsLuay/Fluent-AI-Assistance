"""Short mode-scoped briefs for AI agents (token-cheap vs full AGENTS.md)."""

from __future__ import annotations

from .config import PROJECT_DIR, READY_TO_IMPORT_DIR, REPO_ROOT, SHARED_TEMP_DIR, fluentcoder_python

AGENT_BRIEF_MODES = (
    "install",
    "status",
    "new-script",
    "script",
    "repair",
    "simulator",
)

# First matching rule wins. Keywords are casefolded substrings (longer first).
_INTENT_MODE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "install",
        (
            "install.ps1",
            "wire mcp",
            "mcp config",
            "server-config.json",
            "clone the repo",
            "clone repo",
            "install fluent",
            "set up mcp",
            "setup mcp",
            "install the repo",
        ),
    ),
    (
        "simulator",
        (
            "launch-simulator",
            "protocol simulator",
            "simulator ui",
            "simulator assets",
            "mesh glb",
            "deck view",
            "vite",
            "simulator",
        ),
    ),
    (
        "repair",
        (
            "diagnosis.md",
            "error_logs",
            "script-errors",
            "runtime error",
            "runtime failure",
            "failed to open",
            "won't open",
            "will not open",
            "import failed",
            "import failure",
            "fluentcontrol error",
            "crash dump",
            "minimal edit",
            "surgical xscr",
            "xscr edit",
            ".ulf",
            "parse-fluent-log",
            "parse fluent log",
            "repair",
            "diagnosis",
            "dialog error",
            "open failure",
        ),
    ),
    (
        "new-script",
        (
            "new script",
            "generate protocol",
            "request.spec",
            "ready-to-import",
            "create a script",
            "create script",
            "scaffold",
            "protocol.ir",
            "from zeia",
            "using zeia",
            "zeia",
            "generate",
        ),
    ),
    (
        "status",
        (
            "bootstrap-status",
            "bootstrap status",
            "list-projects",
            "list projects",
            "doctor",
            "health check",
            "toolchain",
            "status",
        ),
    ),
)


def _python_label() -> str:
    python = fluentcoder_python()
    return str(python if python.exists() else "python3")


def normalize_agent_brief_mode(mode: str | None) -> str:
    """Normalize aliases (`script` → `new-script`) and validate."""
    normalized = (mode or "status").strip().casefold()
    if normalized == "script":
        normalized = "new-script"
    if normalized not in AGENT_BRIEF_MODES and normalized != "new-script":
        known = ", ".join(m for m in AGENT_BRIEF_MODES if m != "script")
        raise ValueError(f"unknown agent brief mode {mode!r}; expected one of: {known}")
    return normalized


def resolve_agent_brief_mode(intent: str, *, default: str = "status") -> dict[str, str | None]:
    """Map free-text user intent to a brief mode (one-liner; no guessing)."""
    text = " ".join((intent or "").casefold().split())
    default_mode = normalize_agent_brief_mode(default)
    if not text:
        return {
            "mode": default_mode,
            "matched_keyword": None,
            "confidence": "low",
            "reason": "empty intent; using default mode",
        }
    for mode, keywords in _INTENT_MODE_RULES:
        for keyword in sorted(keywords, key=len, reverse=True):
            if keyword in text:
                return {
                    "mode": mode,
                    "matched_keyword": keyword,
                    "confidence": "high" if len(keyword) >= 8 else "medium",
                    "reason": f"matched {keyword!r} → {mode}",
                }
    return {
        "mode": default_mode,
        "matched_keyword": None,
        "confidence": "low",
        "reason": f"no keyword match; defaulting to {default_mode}",
    }


def render_agent_brief(mode: str) -> str:
    """Return the checklist text for one agent mode."""
    normalized = normalize_agent_brief_mode(mode)
    renderer = {
        "install": _brief_install,
        "status": _brief_status,
        "new-script": _brief_new_script,
        "repair": _brief_repair,
        "simulator": _brief_simulator,
    }[normalized]
    return renderer()


def _header(mode: str) -> list[str]:
    return [
        f"# Fluent AI-Assistance agent brief ({mode})",
        f"repo: {REPO_ROOT}",
        f"python: {_python_label()}",
        "code is authority; this brief is a checklist, not a second AGENTS.md",
        "",
    ]


def _brief_install() -> str:
    lines = _header("install")
    lines.extend(
        [
            "GOAL: install full repo + wire MCP for this client",
            "STEPS:",
            "1. Ensure clone is Fluent-AI-Assistance at a user-owned path",
            "2. From repo root:",
            "   powershell -ExecutionPolicy Bypass -File .\\scripts\\install\\install.ps1",
            "   # Unix: create .venv then python -m fluent_pipeline.bootstrap from source/03-protocol-builder",
            "3. Merge only fluent-ai-assistance from .mcp/server-config.json into client MCP config",
            "4. Reload MCP; call fluent_status then fluent_bootstrap_status",
            "DO NOT: install Tecan drivers, write C:\\ProgramData\\Tecan, network-expose MCP, upload ZEIA/logs",
            "READ IF STUCK: README.md, docs/INSTALLATION.md, docs/AI_INSTALL_PROMPT.md",
        ]
    )
    return "\n".join(lines)


def _brief_status() -> str:
    doctor_report = SHARED_TEMP_DIR / "logs" / "doctor.md"
    lines = _header("status")
    lines.extend(
        [
            "GOAL: confirm local tooling before protocol work",
            "PREFERRED: fluent_bootstrap_status  OR  python -m fluent_pipeline.cli bootstrap-status",
            "CLI FALLBACK (doctor only):",
            f"  cd {PROJECT_DIR}",
            f"  {_python_label()} -m fluent_pipeline.cli doctor --install-missing --report {doctor_report}",
            f"  {_python_label()} -m fluent_pipeline.cli list-projects",
            "THEN: ask user for ZEIA path + what script/change they want",
            "MODE PICK: fluent_agent_brief(intent='...')  # repair/new-script/simulator keywords",
            "NEXT MODE: fluent_agent_brief(mode='new-script') or mode='repair'",
        ]
    )
    return "\n".join(lines)


def _brief_new_script() -> str:
    lines = _header("new-script")
    lines.extend(
        [
            "GOAL: ZEIA -> reviewed request.spec + IR -> generate -> ready-to-import bundle",
            "CWD: source/03-protocol-builder",
            "CONTRACT: open source/03-protocol-builder/AGENTS.md ONLY for sections named in NEED MORE",
            "",
            "RULES:",
            "- Lab names/worktables/labware/LCs come from user ZEIA only (lab-agnostic)",
            "- Do not invent FluentControl params or deck geometry",
            "- Prefer MCP tools or CLI: python -m fluent_pipeline.cli ...",
            f"- Artifacts only under {READY_TO_IMPORT_DIR}/<project>/temp_files/ "
            f"(shared tooling under {SHARED_TEMP_DIR}/)",
            "",
            "STEPS:",
            "0. bootstrap-status / fluent_bootstrap_status  # honor blocked_tools",
            "0b. after inspect: bootstrap-status --inspected / inspected=true  # unlocks generate",
            "1. resolve-spec latest:<protocol-stem>  # reuse ready bundle if present",
            "2. doctor --install-missing (skip only if bootstrap_status was green)",
            "3. import-project <zeia> (if no usable ready context)",
            "4. inspect-project (summary only) then project-find / fluent_project_query",
            "5. request-spec -> validate-spec",
            "6. generate scaffold (no claim ready yet)",
            "7. review spec+IR with user; final generate; verify-bundle / publish",
            "",
            "NEED MORE: ## Default New-Script Workflow ; ## Regeneration Preflight (mandatory) ; ## Required Verification",
        ]
    )
    return "\n".join(lines)


def _brief_repair() -> str:
    lines = _header("repair")
    lines.extend(
        [
            "GOAL: debug FluentControl import/open/runtime failure on a published bundle",
            "STEPS:",
            "1. From published bundle root (not source template):",
            "   run_tecan_bundle_setup.bat --logs-only --log-profile script-errors --no-pause",
            "2. Read ready-to-import/<project>/temp_files/error_logs_*/diagnosis.md",
            "3. Optional follow-up: parse-fluent-log / fluent_parse_fluent_log on a focused ULF",
            "4. Prefer surgical XSCR edit + compare_xscr_minimal_edit over full regen",
            "NEED MORE: ## Command Rules (error-log bullets) ; ## Existing Script Minimal Edits",
        ]
    )
    return "\n".join(lines)


def _brief_simulator() -> str:
    lines = _header("simulator")
    lines.extend(
        [
            "GOAL: simulator UI/assets only",
            "READ: source/04-protocol-simulator/AGENTS.md",
            "LAUNCH: python -m fluent_pipeline.cli launch-simulator  (from 03-protocol-builder)",
            "ASSETS: regenerate into public/models/fluent/local/ via source/tools/; do not hand-edit host GLBs",
        ]
    )
    return "\n".join(lines)
