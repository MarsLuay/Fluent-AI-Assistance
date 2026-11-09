mod agent_brief;
mod check_shadow;
mod install;
mod mcp_smoke;
mod paths;
mod test_suite;

use std::process;

use paths::RepoPaths;
use test_suite::Suite;

const USAGE: &str = "Fluent AI-Assistance Rust helpers

USAGE:
  fluent-assistance-tools agent-brief [--mode MODE] [--intent TEXT] [--resolve-only]
  fluent-assistance-tools install [--skip-smoke-test]
  fluent-assistance-tools mcp-smoke
  fluent-assistance-tools check-shadow-instructions
  fluent-assistance-tools test --suite <fast|mcp|simulator|all>

From the repository root, invoke with Cargo:
  cargo run --manifest-path scripts/rust/Cargo.toml -- <COMMAND>
";

fn main() {
    let code = match run() {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("error: {error}");
            1
        }
    };
    process::exit(code);
}

fn run() -> Result<(), String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let Some(command) = args.first().map(String::as_str) else {
        print!("{USAGE}");
        return Ok(());
    };
    if matches!(command, "help" | "--help" | "-h") {
        print!("{USAGE}");
        return Ok(());
    }

    let paths = RepoPaths::discover()?;
    match command {
        "agent-brief" => agent_brief::run(&args[1..], &paths),
        "install" => {
            let mut skip_smoke_test = false;
            for argument in &args[1..] {
                match argument.as_str() {
                    "--skip-smoke-test" => skip_smoke_test = true,
                    "--help" | "-h" => {
                        println!("Usage: fluent-assistance-tools install [--skip-smoke-test]");
                        return Ok(());
                    }
                    unknown => return Err(format!("unknown install argument: {unknown}")),
                }
            }
            install::install(&paths, skip_smoke_test)
        }
        "mcp-smoke" => {
            if args.len() > 1 {
                return Err(format!("unknown mcp-smoke argument: {}", args[1]));
            }
            let report = mcp_smoke::run(&paths)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&report)
                    .map_err(|error| format!("could not encode MCP smoke result: {error}"))?
            );
            if report.get("ok").and_then(serde_json::Value::as_bool) == Some(true) {
                Ok(())
            } else {
                Err("MCP smoke failed (missing tools or bootstrap next_step).".to_owned())
            }
        }
        "check-shadow-instructions" => {
            if args.len() > 1 {
                return Err(format!(
                    "unknown check-shadow-instructions argument: {}",
                    args[1]
                ));
            }
            check_shadow::run(&paths.root)
        }
        "test" => run_test_command(&args[1..], &paths),
        unknown => Err(format!("unknown command {unknown:?}.\n{USAGE}")),
    }
}

fn run_test_command(args: &[String], paths: &RepoPaths) -> Result<(), String> {
    let mut suite = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--suite" => {
                index += 1;
                suite = Some(Suite::parse(
                    args.get(index).ok_or("--suite requires a value")?,
                )?);
            }
            "--help" | "-h" => {
                println!("Usage: fluent-assistance-tools test --suite <fast|mcp|simulator|all>");
                return Ok(());
            }
            unknown => return Err(format!("unknown test argument: {unknown}")),
        }
        index += 1;
    }
    let suite = suite.ok_or("test requires --suite <fast|mcp|simulator|all>")?;
    test_suite::run_suite(paths, suite)
}
