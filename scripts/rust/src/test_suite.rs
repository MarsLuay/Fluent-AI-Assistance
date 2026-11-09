use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus};

use crate::install;
use crate::mcp_smoke;
use crate::paths::RepoPaths;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Suite {
    Fast,
    Mcp,
    Simulator,
    All,
}

impl Suite {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "fast" => Ok(Self::Fast),
            "mcp" => Ok(Self::Mcp),
            "simulator" => Ok(Self::Simulator),
            "all" => Ok(Self::All),
            _ => Err(format!(
                "unknown test suite {value:?}; expected fast, mcp, simulator, or all"
            )),
        }
    }
}

fn display_command(executable: &Path, args: &[String]) -> String {
    std::iter::once(executable.display().to_string())
        .chain(args.iter().cloned())
        .collect::<Vec<_>>()
        .join(" ")
}

fn run(executable: &Path, cwd: &Path, args: &[String]) -> Result<ExitStatus, String> {
    println!("\n==> {}", cwd.display());
    println!("    {}", display_command(executable, args));
    Command::new(executable)
        .args(args)
        .current_dir(cwd)
        .status()
        .map_err(|error| format!("could not run {}: {error}", executable.display()))
}

fn run_checked(executable: &Path, cwd: &Path, args: &[String]) -> Result<(), String> {
    let status = run(executable, cwd, args)?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "command failed with exit code {}",
            status.code().unwrap_or(1)
        ))
    }
}

fn python_args(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| (*value).to_owned()).collect()
}

fn ensure_python_environment(paths: &RepoPaths) -> Result<PathBuf, String> {
    if paths.venv_python().is_none() {
        println!("\n==> bootstrap");
        install::install(paths, true)?;
    }
    let mut python = install::find_python(paths)?;
    if !install::is_pytest_available(&python) {
        println!("\n==> bootstrap");
        install::install(paths, true)?;
        python = install::find_python(paths)?;
        if !install::is_pytest_available(&python) {
            return Err(
                "pytest is still unavailable after bootstrapping the environment.".to_owned(),
            );
        }
    }
    Ok(python)
}

fn invoke_python(python: &Path, working_directory: &Path, args: &[&str]) -> Result<(), String> {
    run_checked(python, working_directory, &python_args(args))
}

fn fast_suite(paths: &RepoPaths, python: &Path) -> Result<(), String> {
    println!("\n==> fast");
    invoke_python(
        python,
        &paths.package,
        &["-m", "tools.sync_readiness_gate_registry", "--check"],
    )?;
    invoke_python(
        python,
        &paths.root,
        &[
            "-m",
            "pytest",
            "source/01-project-reader/tests",
            "source/02-worklist-builder/tests",
            "-q",
        ],
    )?;
    invoke_python(
        python,
        &paths.root,
        &[
            "-m",
            "pytest",
            "source/03-protocol-builder/tests",
            "-m",
            "not fluentcontrol_shell",
            "--ignore",
            "source/03-protocol-builder/tests/test_mcp_gateway.py",
            "-q",
        ],
    )?;
    invoke_python(
        python,
        &paths.root,
        &[
            "-m",
            "pytest",
            "source/03-protocol-builder/libs/fluentcoder/tests",
            "-m",
            "not fluentcontrol_shell",
            "-q",
        ],
    )
}

fn mcp_suite(paths: &RepoPaths, python: &Path) -> Result<(), String> {
    println!("\n==> mcp");
    invoke_python(
        python,
        &paths.root,
        &[
            "-m",
            "pytest",
            "source/03-protocol-builder/tests/test_mcp_gateway.py",
            "source/03-protocol-builder/tests/test_architecture_import_paths.py",
            "-q",
        ],
    )?;
    invoke_python(
        python,
        &paths.package,
        &["-m", "fluent_pipeline.mcp_server", "--self-test"],
    )?;
    let report = mcp_smoke::run(paths)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&report)
            .map_err(|error| format!("could not encode MCP smoke result: {error}"))?
    );
    if report.get("ok").and_then(serde_json::Value::as_bool) != Some(true) {
        return Err("MCP smoke failed (missing tools or bootstrap next_step).".to_owned());
    }
    Ok(())
}

fn simulator_suite(paths: &RepoPaths, python: &Path) -> Result<(), String> {
    println!("\n==> simulator");
    invoke_python(
        python,
        &paths.root,
        &[
            "-m",
            "pytest",
            "source/03-protocol-builder/tests",
            "source/03-protocol-builder/libs/fluentcoder/tests",
            "-m",
            "fluentcontrol_shell",
            "-q",
        ],
    )
}

pub fn run_suite(paths: &RepoPaths, suite: Suite) -> Result<(), String> {
    let python = ensure_python_environment(paths)?;
    match suite {
        Suite::Fast => fast_suite(paths, &python),
        Suite::Mcp => mcp_suite(paths, &python),
        Suite::Simulator => simulator_suite(paths, &python),
        Suite::All => {
            fast_suite(paths, &python)?;
            mcp_suite(paths, &python)?;
            simulator_suite(paths, &python)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suite_names_are_validated() {
        assert_eq!(Suite::parse("fast").unwrap(), Suite::Fast);
        assert_eq!(Suite::parse("all").unwrap(), Suite::All);
        assert!(Suite::parse("unknown").is_err());
    }
}
