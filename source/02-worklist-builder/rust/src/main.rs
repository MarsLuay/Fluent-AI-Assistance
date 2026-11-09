mod performance;
mod semantics;

use serde_json::{Value, json};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process;
use tecan_worklist_builder::{
    WashPolicy, build_worklist, load_transfers, parse_gwl_file, parse_gwl_file_permissive,
    total_volume, validate_transfers,
};

const USAGE: &str = "Rust worklist conversion, validation, summary, analysis, and optimization

USAGE:
  tecan-worklist convert <csv> --output <gwl> [--wash-policy <each|none>] [--batch-size N] [--well-rows N] [--strict] [--json]
  tecan-worklist validate <csv> [--well-rows N] [--strict] [--json]
  tecan-worklist summarize <gwl> [--json]
  tecan-worklist analyze <gwl> [--enabled-tip-mask MASK] [--supported-channels N] [--json]
  tecan-worklist optimize <gwl> --output <gwl> [--enabled-tip-mask MASK] [--supported-channels N] [--json]
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
    if command == "summarize" {
        return run_summarize(&args[1..]);
    }
    if command == "analyze" {
        return run_analyze(&args[1..]);
    }
    if command == "optimize" {
        return run_optimize(&args[1..]);
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
    let counts_repr = worklist.counts_repr();
    let payload = json!({
        "ok": true,
        "input_csv": python_path_display(input),
        "output_gwl": python_path_display(output),
        "transfer_count": transfers.len(),
        "total_volume_ul": finite_json_number(total_volume(&transfers)),
        "record_counts": worklist.counts,
        "warnings": validation.warnings,
    });
    emit_with_counts(&payload, options.as_json, false, Some(&counts_repr));
    Ok(0)
}

fn run_analyze(args: &[String]) -> Result<i32, String> {
    let mut input = None;
    let mut enabled_tip_mask = None;
    let mut supported_channels = 8usize;
    let mut as_json = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--enabled-tip-mask" => {
                index += 1;
                enabled_tip_mask = Some(
                    args.get(index)
                        .ok_or("--enabled-tip-mask requires a value")?
                        .clone(),
                );
            }
            "--supported-channels" => {
                index += 1;
                let parsed = args
                    .get(index)
                    .ok_or("--supported-channels requires a value")?
                    .parse::<i64>()
                    .map_err(|_| "--supported-channels must be an integer.")?;
                supported_channels = usize::try_from(parsed).unwrap_or(0);
            }
            "--json" => as_json = true,
            "--help" | "-h" => {
                println!(
                    "Usage: tecan-worklist analyze <gwl> [--enabled-tip-mask MASK] [--supported-channels N] [--json]"
                );
                process::exit(0);
            }
            value if value.starts_with('-') => {
                return Err(format!("unknown analyze argument: {value}"));
            }
            path => {
                if input.replace(PathBuf::from(path)).is_some() {
                    return Err(format!("unexpected extra positional argument: {path}"));
                }
            }
        }
        index += 1;
    }
    let input = input.ok_or("analyze requires a GWL path.")?;
    let worklist = match performance::measure("worklist.gwl_parse", json!({}), || {
        parse_gwl_file_permissive(&input)
    }) {
        Ok(worklist) => worklist,
        Err(error) => return Ok(semantic_failure(&error, as_json)),
    };
    let analysis = match performance::measure(
        "worklist.gwl_analysis",
        json!({ "record_count": worklist.lines.len() }),
        || semantics::analyze_worklist(&worklist, enabled_tip_mask.as_deref(), supported_channels),
    ) {
        Ok(analysis) => analysis,
        Err(error) => return Ok(semantic_failure(&error, as_json)),
    };
    let mut payload = analysis;
    if let Some(object) = payload.as_object_mut() {
        object.insert("gwl".to_owned(), json!(python_path_display(&input)));
    }
    let ok = payload["ok"].as_bool().unwrap_or(false);
    emit(&payload, as_json, false);
    Ok(if ok { 0 } else { 1 })
}

fn semantic_failure(message: &str, as_json: bool) -> i32 {
    emit(&json!({ "ok": false, "errors": [message] }), as_json, true);
    1
}

fn run_optimize(args: &[String]) -> Result<i32, String> {
    let mut input = None;
    let mut output = None;
    let mut enabled_tip_mask = None;
    let mut supported_channels = 8usize;
    let mut as_json = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--output" | "-o" => {
                index += 1;
                output = Some(PathBuf::from(
                    args.get(index).ok_or("--output requires a path")?,
                ));
            }
            "--enabled-tip-mask" => {
                index += 1;
                enabled_tip_mask = Some(
                    args.get(index)
                        .ok_or("--enabled-tip-mask requires a value")?
                        .clone(),
                );
            }
            "--supported-channels" => {
                index += 1;
                let parsed = args
                    .get(index)
                    .ok_or("--supported-channels requires a value")?
                    .parse::<i64>()
                    .map_err(|_| "--supported-channels must be an integer.")?;
                supported_channels = usize::try_from(parsed).unwrap_or(0);
            }
            "--json" => as_json = true,
            "--help" | "-h" => {
                println!(
                    "Usage: tecan-worklist optimize <gwl> --output <gwl> [--enabled-tip-mask MASK] [--supported-channels N] [--json]"
                );
                process::exit(0);
            }
            value if value.starts_with('-') => {
                return Err(format!("unknown optimize argument: {value}"));
            }
            path => {
                if input.replace(PathBuf::from(path)).is_some() {
                    return Err(format!("unexpected extra positional argument: {path}"));
                }
            }
        }
        index += 1;
    }
    let input = input.ok_or("optimize requires a GWL path.")?;
    let output = output.ok_or("optimize requires --output <gwl>.")?;
    let worklist = match performance::measure("worklist.gwl_parse", json!({}), || {
        parse_gwl_file_permissive(&input)
    }) {
        Ok(worklist) => worklist,
        Err(error) => return Ok(semantic_failure(&error, as_json)),
    };
    let (optimized, mut payload) = match performance::measure(
        "worklist.gwl_optimization",
        json!({ "record_count": worklist.lines.len() }),
        || semantics::optimize_worklist(&worklist, enabled_tip_mask.as_deref(), supported_channels),
    ) {
        Ok(result) => result,
        Err(error) => return Ok(semantic_failure(&error, as_json)),
    };
    if let Some(object) = payload.as_object_mut() {
        object.insert("gwl".to_owned(), json!(python_path_display(&input)));
        object.insert("output_gwl".to_owned(), json!(python_path_display(&output)));
    }
    let ok = payload["ok"].as_bool().unwrap_or(false);
    if ok {
        performance::measure(
            "worklist.gwl_write",
            json!({ "record_count": optimized.lines.len() }),
            || write_output(&output, &optimized.to_text()),
        )?;
    }
    emit(&payload, as_json, false);
    Ok(if ok { 0 } else { 1 })
}

fn run_summarize(args: &[String]) -> Result<i32, String> {
    let mut input = None;
    let mut as_json = false;
    for argument in args {
        match argument.as_str() {
            "--json" => as_json = true,
            "--help" | "-h" => {
                println!("Usage: tecan-worklist summarize <gwl> [--json]");
                process::exit(0);
            }
            value if value.starts_with('-') => {
                return Err(format!("unknown summarize argument: {value}"));
            }
            path => {
                if input.replace(PathBuf::from(path)).is_some() {
                    return Err(format!("unexpected extra positional argument: {path}"));
                }
            }
        }
    }
    let input = input.ok_or("summarize requires a GWL path.")?;
    let worklist =
        performance::measure("worklist.gwl_parse", json!({}), || parse_gwl_file(&input))?;
    let (pipette_records, transfer_pairs_estimate) = performance::measure(
        "worklist.gwl_summary",
        json!({ "record_count": worklist.lines.len() }),
        || {
            let aspirates = *worklist.counts.get("A").unwrap_or(&0);
            let dispenses = *worklist.counts.get("D").unwrap_or(&0);
            Ok((aspirates + dispenses, aspirates.min(dispenses)))
        },
    )?;
    let counts_repr = worklist.counts_repr();
    let payload = json!({
        "ok": true,
        "gwl": python_path_display(&input),
        "record_counts": worklist.counts,
        "pipette_records": pipette_records,
        "transfer_pairs_estimate": transfer_pairs_estimate,
    });
    emit_with_counts(&payload, as_json, false, Some(&counts_repr));
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
    emit_with_counts(payload, as_json, to_stderr, None);
}

fn emit_with_counts(payload: &Value, as_json: bool, to_stderr: bool, counts_repr: Option<&str>) {
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
        append_summary_lines(&mut lines, payload, counts_repr);
        lines.extend(warnings(payload));
        lines.join("\n")
    } else if let Some(input_csv) = payload["input_csv"].as_str() {
        let mut lines = vec![format!("Validated {input_csv}")];
        append_summary_lines(&mut lines, payload, counts_repr);
        lines.extend(warnings(payload));
        lines.join("\n")
    } else if let Some(gwl) = payload["gwl"].as_str() {
        let mut lines = vec![format!("Summarized {gwl}")];
        append_summary_lines(&mut lines, payload, counts_repr);
        lines.join("\n")
    } else {
        String::new()
    };
    #[cfg(windows)]
    let output = format!("{}\r\n", output.replace('\n', "\r\n"));
    #[cfg(not(windows))]
    let output = format!("{output}\n");
    if to_stderr {
        let _ = std::io::stderr().lock().write_all(output.as_bytes());
    } else {
        let _ = std::io::stdout().lock().write_all(output.as_bytes());
    }
}

fn append_summary_lines(lines: &mut Vec<String>, payload: &Value, counts_repr: Option<&str>) {
    for key in [
        "transfer_count",
        "total_volume_ul",
        "record_counts",
        "transfer_pairs_estimate",
    ] {
        if let Some(value) = payload.get(key) {
            let rendered = if key == "record_counts" {
                counts_repr
                    .map(str::to_owned)
                    .unwrap_or_else(|| value.to_string())
            } else {
                value.to_string()
            };
            lines.push(format!("{key}: {rendered}"));
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
