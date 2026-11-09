use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};

use serde_json::json;

use crate::mcp_smoke;
use crate::paths::RepoPaths;

pub fn find_python(paths: &RepoPaths) -> Result<PathBuf, String> {
    if let Some(path) = paths.venv_python() {
        return Ok(path);
    }
    for name in ["python3", "python"] {
        if command_available(name) {
            return Ok(PathBuf::from(name));
        }
    }
    Err("Python 3 was not found. Run `fluent-assistance-tools install` or make sure python3/python is on PATH.".to_owned())
}

fn command_available(name: &str) -> bool {
    Command::new(name)
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn supported_python(executable: &str, prefix: &[&str]) -> bool {
    Command::new(executable)
        .args(prefix)
        .args([
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn create_venv(python: &Path, venv_root: &Path) -> bool {
    Command::new(python)
        .args(["-m", "venv"])
        .arg(venv_root)
        .status()
        .is_ok_and(|status| status.success())
}

fn ensure_venv(paths: &RepoPaths) -> Result<PathBuf, String> {
    if let Some(python) = paths.venv_python() {
        return Ok(python);
    }
    let venv_root = paths.root.join(".venv");
    #[cfg(windows)]
    let names = ["python3", "python"];
    #[cfg(not(windows))]
    let names = ["python3", "python"];

    for name in names {
        if supported_python(name, &[]) && create_venv(Path::new(name), &venv_root) {
            if let Some(python) = paths.venv_python() {
                return Ok(python);
            }
        }
    }

    #[cfg(windows)]
    {
        if supported_python("py", &["-3"]) {
            let status = Command::new("py")
                .args(["-3", "-m", "venv"])
                .arg(&venv_root)
                .status();
            if status.is_ok_and(|status| status.success()) {
                if let Some(python) = paths.venv_python() {
                    return Ok(python);
                }
            }
        }
    }

    Err("Could not create a virtual environment with Python 3.11 or newer.".to_owned())
}

fn run_command(command: &mut Command, label: &str) -> Result<ExitStatus, String> {
    println!("$ {}", display_command(command));
    command
        .status()
        .map_err(|error| format!("could not run {label}: {error}"))
}

fn display_command(command: &Command) -> String {
    let mut parts = vec![command.get_program().to_string_lossy().into_owned()];
    parts.extend(
        command
            .get_args()
            .map(|arg| arg.to_string_lossy().into_owned()),
    );
    parts.join(" ")
}

fn run_checked(command: &mut Command, label: &str) -> Result<(), String> {
    let status = run_command(command, label)?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "{label} failed with exit code {}",
            status.code().unwrap_or(1)
        ))
    }
}

fn write_mcp_config(paths: &RepoPaths, python: &Path) -> Result<PathBuf, String> {
    let config_dir = paths.root.join(".mcp");
    let config_path = config_dir.join("server-config.json");
    fs::create_dir_all(&config_dir)
        .map_err(|error| format!("could not create {}: {error}", config_dir.display()))?;
    let config = json!({
        "mcpServers": {
            "fluent-ai-assistance": {
                "command": python.display().to_string(),
                "args": ["-m", "fluent_pipeline.mcp_server"],
                "cwd": paths.package.display().to_string(),
                "env": {"PYTHONUTF8": "1"},
            }
        }
    });
    let text = serde_json::to_string_pretty(&config)
        .map_err(|error| format!("could not encode MCP config: {error}"))?;
    fs::write(&config_path, format!("{text}\n"))
        .map_err(|error| format!("could not write {}: {error}", config_path.display()))?;
    Ok(config_path)
}

pub fn install(paths: &RepoPaths, skip_smoke_test: bool) -> Result<(), String> {
    let python = ensure_venv(paths)?;

    run_checked(
        Command::new(&python)
            .args(["-m", "fluent_pipeline.bootstrap"])
            .current_dir(&paths.package),
        "workspace bootstrap",
    )?;

    if !skip_smoke_test {
        println!("\n==> MCP self-test");
        run_checked(
            Command::new(&python)
                .args(["-m", "fluent_pipeline.mcp_server", "--self-test"])
                .current_dir(&paths.package),
            "MCP self-test (required tools / bootstrap next_step)",
        )?;

        println!("\n==> MCP smoke (status + bootstrap)");
        let smoke = mcp_smoke::run(paths)?;
        println!(
            "{}",
            serde_json::to_string_pretty(&smoke)
                .map_err(|error| format!("could not encode MCP smoke result: {error}"))?
        );
        if smoke.get("ok").and_then(serde_json::Value::as_bool) != Some(true) {
            return Err("MCP smoke failed (missing tools or bootstrap next_step).".to_owned());
        }

        println!("\n==> CLI bootstrap-status");
        let status = run_command(
            Command::new(&python)
                .args([
                    "-m",
                    "fluent_pipeline.cli",
                    "bootstrap-status",
                    "--no-report",
                ])
                .current_dir(&paths.package)
                .stdout(Stdio::null()),
            "CLI bootstrap-status",
        )?;
        // Exit 0 means doctor is healthy; 1 means doctor failed but next_step
        // was produced. Codes >=2 are hard errors, matching the former installer.
        if status.code().unwrap_or(2) > 1 {
            return Err(format!(
                "CLI bootstrap-status failed with exit code {}",
                status.code().unwrap_or(2)
            ));
        }
    }

    let config_path = write_mcp_config(paths, &python)?;
    println!("\nFluent AI-Assistance MCP is installed.");
    println!("Client configuration: {}", config_path.display());
    println!(
        "Add the fluent-ai-assistance entry to your AI client's MCP configuration, reload the client, then call fluent_status and fluent_bootstrap_status."
    );
    Ok(())
}

pub fn is_pytest_available(python: &Path) -> bool {
    Command::new(python)
        .args(["-m", "pytest", "--version"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}
