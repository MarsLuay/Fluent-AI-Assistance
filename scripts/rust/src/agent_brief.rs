use std::env;

use serde_json::{Value, json};

use crate::paths::RepoPaths;

const INSTALL_KEYWORDS: &[&str] = &[
    "cargo run --manifest-path scripts/rust/cargo.toml -- install",
    "wire mcp",
    "mcp config",
    "server-config.json",
    "clone the repo",
    "clone repo",
    "install fluent",
    "set up mcp",
    "setup mcp",
    "install the repo",
];
const SIMULATOR_KEYWORDS: &[&str] = &[
    "launch-simulator",
    "protocol simulator",
    "simulator ui",
    "simulator assets",
    "mesh glb",
    "deck view",
    "vite",
    "simulator",
];
const REPAIR_KEYWORDS: &[&str] = &[
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
];
const NEW_SCRIPT_KEYWORDS: &[&str] = &[
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
];
const STATUS_KEYWORDS: &[&str] = &[
    "bootstrap-status",
    "bootstrap status",
    "list-projects",
    "list projects",
    "doctor",
    "health check",
    "toolchain",
    "status",
];

pub fn normalize_mode(mode: Option<&str>) -> Result<String, String> {
    let requested = mode.unwrap_or("status").trim().to_lowercase();
    let normalized = if requested == "script" {
        "new-script".to_owned()
    } else {
        requested
    };
    if matches!(
        normalized.as_str(),
        "install" | "status" | "new-script" | "repair" | "simulator"
    ) {
        Ok(normalized)
    } else {
        Err(format!(
            "unknown agent brief mode {:?}; expected one of: install, status, new-script, repair, simulator",
            mode.unwrap_or("status")
        ))
    }
}

pub fn resolve_mode(intent: &str, default: Option<&str>) -> Result<Value, String> {
    let default_mode = normalize_mode(default)?;
    let text = intent
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    if text.is_empty() {
        return Ok(json!({
            "mode": default_mode,
            "matched_keyword": Value::Null,
            "confidence": "low",
            "reason": "empty intent; using default mode",
        }));
    }

    for (mode, keywords) in [
        ("install", INSTALL_KEYWORDS),
        ("simulator", SIMULATOR_KEYWORDS),
        ("repair", REPAIR_KEYWORDS),
        ("new-script", NEW_SCRIPT_KEYWORDS),
        ("status", STATUS_KEYWORDS),
    ] {
        let mut ordered = keywords.to_vec();
        ordered.sort_by_key(|keyword| std::cmp::Reverse(keyword.len()));
        if let Some(keyword) = ordered.into_iter().find(|keyword| text.contains(keyword)) {
            return Ok(json!({
                "mode": mode,
                "matched_keyword": keyword,
                "confidence": if keyword.len() >= 8 { "high" } else { "medium" },
                "reason": format!("matched '{}' → {}", keyword, mode),
            }));
        }
    }

    Ok(json!({
        "mode": default_mode,
        "matched_keyword": Value::Null,
        "confidence": "low",
        "reason": format!("no keyword match; defaulting to {}", default_mode),
    }))
}

fn python_label(paths: &RepoPaths) -> String {
    if let Ok(value) = env::var("FLUENTCODER_PYTHON") {
        let configured = std::path::PathBuf::from(value);
        if configured.exists() {
            return configured.display().to_string();
        }
    }
    if let Some(python) = paths.venv_python() {
        return python.display().to_string();
    }
    "python3".to_owned()
}

fn header(paths: &RepoPaths, mode: &str) -> Vec<String> {
    vec![
        format!("# Fluent AI-Assistance agent brief ({mode})"),
        format!("repo: {}", paths.root.display()),
        format!("python: {}", python_label(paths)),
        "code is authority; this brief is a checklist, not a second AGENTS.md".to_owned(),
        String::new(),
    ]
}

pub fn render(mode: &str, paths: &RepoPaths) -> Result<String, String> {
    let normalized = normalize_mode(Some(mode))?;
    let mut lines = header(paths, &normalized);
    let ready_dir = paths.root.join("ready-to-import");
    let shared_temp = ready_dir.join("_shared").join("temp_files");
    let project_dir = &paths.package;

    match normalized.as_str() {
        "install" => lines.extend([
            "GOAL: install full repo + wire MCP for this client".to_owned(),
            "STEPS:".to_owned(),
            "1. Ensure clone is Fluent-AI-Assistance at a user-owned path".to_owned(),
            "2. From repo root:".to_owned(),
            "   cargo run --manifest-path scripts/rust/Cargo.toml -- install".to_owned(),
            "3. Merge only fluent-ai-assistance from .mcp/server-config.json into client MCP config".to_owned(),
            "4. Reload MCP; call fluent_status then fluent_bootstrap_status".to_owned(),
            "DO NOT: install Tecan drivers, write C:\\ProgramData\\Tecan, network-expose MCP, upload ZEIA/logs".to_owned(),
            "READ IF STUCK: README.md, docs/INSTALLATION.md, docs/AI_INSTALL_PROMPT.md".to_owned(),
        ]),
        "status" => {
            let doctor_report = shared_temp.join("logs").join("doctor.md");
            lines.extend([
                "GOAL: confirm local tooling before protocol work".to_owned(),
                "PREFERRED: fluent_bootstrap_status  OR  python -m fluent_pipeline.cli bootstrap-status".to_owned(),
                "CLI FALLBACK (doctor only):".to_owned(),
                format!("  cd {}", project_dir.display()),
                format!(
                    "  {} -m fluent_pipeline.cli doctor --install-missing --report {}",
                    python_label(paths),
                    doctor_report.display()
                ),
                format!("  {} -m fluent_pipeline.cli list-projects", python_label(paths)),
                "THEN: ask user for ZEIA path + what script/change they want".to_owned(),
                "MODE PICK: fluent_agent_brief(intent='...')  # repair/new-script/simulator keywords".to_owned(),
                "NEXT MODE: fluent_agent_brief(mode='new-script') or mode='repair'".to_owned(),
            ]);
        }
        "new-script" => lines.extend([
            "GOAL: ZEIA -> reviewed request.spec + IR -> generate -> ready-to-import bundle".to_owned(),
            "CWD: source/03-protocol-builder".to_owned(),
            "CONTRACT: open source/03-protocol-builder/AGENTS.md ONLY for sections named in NEED MORE".to_owned(),
            String::new(),
            "RULES:".to_owned(),
            "- Lab names/worktables/labware/LCs come from user ZEIA only (lab-agnostic)".to_owned(),
            "- Do not invent FluentControl params or deck geometry".to_owned(),
            "- Prefer MCP tools or CLI: python -m fluent_pipeline.cli ...".to_owned(),
            format!("- Artifacts only under {}/<project>/temp_files/ (shared tooling under {}/)", ready_dir.display(), shared_temp.display()),
            String::new(),
            "STEPS:".to_owned(),
            "0. bootstrap-status / fluent_bootstrap_status  # honor blocked_tools".to_owned(),
            "0b. after inspect: bootstrap-status --inspected / inspected=true  # unlocks generate".to_owned(),
            "1. resolve-spec latest:<protocol-stem>  # reuse ready bundle if present".to_owned(),
            "2. doctor --install-missing (skip only if bootstrap_status was green)".to_owned(),
            "3. import-project <zeia> (if no usable ready context)".to_owned(),
            "4. inspect-project (summary only) then project-find / fluent_project_query".to_owned(),
            "5. request-spec -> validate-spec".to_owned(),
            "6. generate scaffold (no claim ready yet)".to_owned(),
            "7. review spec+IR with user; final generate; verify-bundle / publish".to_owned(),
            String::new(),
            "NEED MORE: ## Default New-Script Workflow ; ## Regeneration Preflight (mandatory) ; ## Required Verification".to_owned(),
        ]),
        "repair" => lines.extend([
            "GOAL: debug FluentControl import/open/runtime failure on a published bundle".to_owned(),
            "STEPS:".to_owned(),
            "1. From published bundle root (not source template):".to_owned(),
            "   run_tecan_bundle_setup.bat --logs-only --log-profile script-errors --no-pause".to_owned(),
            "2. Read ready-to-import/<project>/temp_files/error_logs_*/diagnosis.md".to_owned(),
            "3. Optional follow-up: parse-fluent-log / fluent_parse_fluent_log on a focused ULF".to_owned(),
            "4. Prefer surgical XSCR edit + compare_xscr_minimal_edit over full regen".to_owned(),
            "NEED MORE: ## Command Rules (error-log bullets) ; ## Existing Script Minimal Edits".to_owned(),
        ]),
        "simulator" => lines.extend([
            "GOAL: simulator UI/assets only".to_owned(),
            "READ: source/04-protocol-simulator/AGENTS.md".to_owned(),
            "LAUNCH: python -m fluent_pipeline.cli launch-simulator  (from 03-protocol-builder)".to_owned(),
            "ASSETS: regenerate into public/models/fluent/local/ via source/tools/; do not hand-edit host GLBs".to_owned(),
        ]),
        _ => unreachable!("mode was normalized"),
    }
    Ok(lines.join("\n"))
}

pub fn run(args: &[String], paths: &RepoPaths) -> Result<(), String> {
    let mut mode = "status".to_owned();
    let mut intent = String::new();
    let mut resolve_only = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--mode" => {
                index += 1;
                mode = args.get(index).ok_or("--mode requires a value")?.clone();
            }
            "--intent" => {
                index += 1;
                intent = args.get(index).ok_or("--intent requires a value")?.clone();
            }
            "--resolve-only" => resolve_only = true,
            "--help" | "-h" => {
                println!(
                    "Usage: fluent-assistance-tools agent-brief [--mode MODE] [--intent TEXT] [--resolve-only]"
                );
                return Ok(());
            }
            unknown => return Err(format!("unknown agent-brief argument: {unknown}")),
        }
        index += 1;
    }

    if !intent.trim().is_empty() {
        let resolution = resolve_mode(&intent, Some("status"))?;
        if resolve_only {
            println!("{}", serde_json::to_string_pretty(&json!({"ok": true, "mode": resolution["mode"], "matched_keyword": resolution["matched_keyword"], "confidence": resolution["confidence"], "reason": resolution["reason"]})).map_err(|error| error.to_string())?);
            return Ok(());
        }
        eprintln!(
            "# resolved mode={} ({})",
            resolution["mode"].as_str().unwrap_or("status"),
            resolution["reason"].as_str().unwrap_or("")
        );
        println!(
            "{}",
            render(resolution["mode"].as_str().unwrap_or("status"), paths)?
        );
        return Ok(());
    }

    let normalized = normalize_mode(Some(&mode))?;
    if resolve_only {
        println!(
            "{}",
            serde_json::to_string_pretty(&json!({
                "ok": true,
                "mode": normalized,
                "matched_keyword": Value::Null,
                "confidence": "low",
                "reason": "no intent; using --mode",
            }))
            .map_err(|error| error.to_string())?
        );
        return Ok(());
    }
    println!("{}", render(&normalized, paths)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn test_paths() -> RepoPaths {
        RepoPaths {
            root: PathBuf::from("/tmp/fluent"),
            package: PathBuf::from("/tmp/fluent/source/03-protocol-builder"),
        }
    }

    #[test]
    fn script_alias_normalizes() {
        assert_eq!(normalize_mode(Some(" SCRIPT ")).unwrap(), "new-script");
    }

    #[test]
    fn intent_prefers_install_rule() {
        let value = resolve_mode("wire MCP after installing", None).unwrap();
        assert_eq!(value["mode"], "install");
        assert_eq!(value["reason"], "matched 'wire mcp' → install");
    }

    #[test]
    fn renders_new_script_brief() {
        let text = render("new-script", &test_paths()).unwrap();
        assert!(text.contains("fluent_bootstrap_status"));
        assert!(text.contains("ready-to-import"));
    }
}
