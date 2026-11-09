mod performance;

use serde_json::{Value, json};
use std::path::{Path, PathBuf};
use std::process;
use tecan_worklist_builder::{
    WashPolicy, build_worklist, load_transfers, total_volume, validate_transfers,
};

const USAGE: &str = "Rust worklist conversion and validation

USAGE:
  tecan-worklist convert <csv> --output <gwl> [--wash-policy <each|none>] [--batch-size N] [--well-rows N] [--strict] [--json]
  tecan-worklist validate <csv> [--well-rows N] [--strict] [--json]
";

fn main() {
    let code = match run() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("error: {error}");
            2
        }
    };
    process::exit(code);
}

fn run() -> Result<i32, String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let Some(command) = args.first().map(String::as_str) else {
        print!("{USAGE}");
        return Ok(0);
    };
    if matches!(command, "help" | "--help" | "-h") {
        print!("{USAGE}");
        return Ok(0);
    }
    if command != "convert" && command != "validate" {
        return Err(format!(
            "unsupported Rust migration command {command:?}.\n{USAGE}"
        ));
    }

    let options = parse_options(command, &args[1..])?;
    let input = options
        .input
        .as_ref()
        .ok_or_else(|| format!("{command} requires a CSV path."))?;
    let transfers = performance::measure("worklist.csv_load", json!({}), || {
        load_transfers(input, options.well_rows)
    })?;
    let validation = performance::measure(
        "worklist.transfer_validation",
        json!({ "transfer_count": transfers.len() }),
        || Ok(validate_transfers(&transfers, options.strict)),
    )?;

    if command == "validate" {
        let payload = json!({
            "ok": validation.ok(),
            "input_csv": python_path_display(input),
            "transfer_count": transfers.len(),
            "total_volume_ul": finite_json_number(total_volume(&transfers)),
            "errors": validation.errors,
            "warnings": validation.warnings,
        });
        emit(&payload, options.as_json, false);
        return Ok(if validation.ok() { 0 } else { 1 });
    }

    if !validation.ok() {
        let payload = json!({
            "ok": false,
            "errors": validation.errors,
            "warnings": validation.warnings,
        });
        emit(&payload, options.as_json, true);
        return Ok(1);
    }

    let output = options
        .output
        .as_ref()
        .ok_or_else(|| "convert requires --output <gwl>.".to_owned())?;
    let name = output
        .file_stem()
        .and_then(|stem| stem.to_str())
        .unwrap_or("worklist");
    let worklist = performance::measure(
        "worklist.gwl_record_assembly",
        json!({ "transfer_count": transfers.len() }),
        || build_worklist(&transfers, name, options.wash_policy, options.batch_size),
    )?;
    let text = worklist.to_text();
    performance::measure(
        "worklist.gwl_write",
        json!({ "record_count": worklist.lines.len() }),
        || write_output(output, &text),
    )?;
    let payload = json!({
        "ok": true,
        "input_csv": python_path_display(input),
        "output_gwl": python_path_display(output),
        "transfer_count": transfers.len(),
        "total_volume_ul": finite_json_number(total_volume(&transfers)),
        "record_counts": worklist.counts,
        "warnings": validation.warnings,
    });
    emit(&payload, options.as_json, false);
    Ok(0)
}

#[derive(Debug)]
struct Options {
    input: Option<PathBuf>,
    output: Option<PathBuf>,
    wash_policy: WashPolicy,
    batch_size: Option<usize>,
    well_rows: i64,
    strict: bool,
    as_json: bool,
}

fn parse_options(command: &str, args: &[String]) -> Result<Options, String> {
    let mut options = Options {
        input: None,
        output: None,
        wash_policy: WashPolicy::Each,
        batch_size: None,
        well_rows: 8,
        strict: false,
        as_json: false,
    };
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--output" | "-o" if command == "convert" => {
                index += 1;
                options.output = Some(PathBuf::from(
                    args.get(index).ok_or("--output requires a path")?,
                ));
            }
            "--wash-policy" if command == "convert" => {
                index += 1;
                options.wash_policy =
                    WashPolicy::parse(args.get(index).ok_or("--wash-policy requires a value")?)?;
            }
            "--batch-size" if command == "convert" => {
                index += 1;
                let value = args
                    .get(index)
                    .ok_or("--batch-size requires a value")?
                    .parse::<i64>()
                    .map_err(|_| "--batch-size must be an integer.")?;
                if value < 1 {
                    return Err("batch_size must be positive.".to_owned());
                }
                options.batch_size = Some(value as usize);
            }
            "--well-rows" => {
                index += 1;
                options.well_rows = args
                    .get(index)
                    .ok_or("--well-rows requires a value")?
                    .parse::<i64>()
                    .map_err(|_| "--well-rows must be an integer.")?;
            }
            "--strict" => options.strict = true,
            "--json" => options.as_json = true,
            "--help" | "-h" => {
                print!("{USAGE}");
                process::exit(0);
            }
            argument if argument.starts_with('-') => {
                return Err(format!("unknown {command} argument: {argument}"));
            }
            path => {
                if options.input.replace(PathBuf::from(path)).is_some() {
                    return Err(format!("unexpected extra positional argument: {path}"));
                }
            }
        }
        index += 1;
    }
    Ok(options)
}

fn write_output(path: &Path, text: &str) -> Result<(), String> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("could not create output directory: {error}"))?;
    }
    #[cfg(windows)]
    let platform_text = text.replace('\n', "\r\n");
    #[cfg(not(windows))]
    let platform_text = text.to_owned();
    std::fs::write(path, platform_text)
        .map_err(|error| format!("could not write GWL file: {error}"))
}

fn python_path_display(path: &Path) -> String {
    let display = path.display().to_string();
    #[cfg(windows)]
    {
        display.replace('/', "\\")
    }
    #[cfg(not(windows))]
    {
        display
    }
}

fn finite_json_number(value: f64) -> Value {
    if value.is_finite() {
        json!(value)
    } else {
        Value::Null
    }
}

fn emit(payload: &Value, as_json: bool, to_stderr: bool) {
    let output = if as_json {
        serde_json::to_string_pretty(payload).unwrap_or_else(|_| "{}".to_owned())
    } else if payload["ok"] == false {
        let mut lines = vec!["Validation failed.".to_owned()];
        if let Some(errors) = payload["errors"].as_array() {
            lines.extend(
                errors
                    .iter()
                    .map(|error| format!("  error: {}", error.as_str().unwrap_or(""))),
            );
        }
        if let Some(warnings) = payload["warnings"].as_array() {
            lines.extend(
                warnings
                    .iter()
                    .map(|warning| format!("  warning: {}", warning.as_str().unwrap_or(""))),
            );
        }
        lines.join("\n")
    } else if let Some(output_gwl) = payload["output_gwl"].as_str() {
        let mut lines = vec![format!("Wrote {output_gwl}")];
        append_summary_lines(&mut lines, payload);
        lines.extend(warnings(payload));
        lines.join("\n")
    } else if let Some(input_csv) = payload["input_csv"].as_str() {
        let mut lines = vec![format!("Validated {input_csv}")];
        append_summary_lines(&mut lines, payload);
        lines.extend(warnings(payload));
        lines.join("\n")
    } else {
        String::new()
    };
    if to_stderr {
        eprintln!("{output}");
    } else {
        println!("{output}");
    }
}

fn append_summary_lines(lines: &mut Vec<String>, payload: &Value) {
    for key in ["transfer_count", "total_volume_ul", "record_counts"] {
        if let Some(value) = payload.get(key) {
            lines.push(format!("{key}: {value}"));
        }
    }
}

fn warnings(payload: &Value) -> Vec<String> {
    payload["warnings"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|warning| format!("warning: {warning}"))
        .collect()
}
