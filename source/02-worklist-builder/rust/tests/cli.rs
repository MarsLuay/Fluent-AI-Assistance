use serde_json::json;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_dir() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before Unix epoch")
        .as_nanos();
    let path = std::env::temp_dir().join(format!(
        "tecan-worklist-rust-{}-{nonce}",
        std::process::id()
    ));
    fs::create_dir_all(&path).expect("create temporary test directory");
    path
}

fn sample_gwl(path: &PathBuf) {
    fs::write(
        path,
        "A;src;;plate;1;;20;lc;;1;\r\nD;dst;;plate;2;;20;lc;;1;\r\nW;\r\nC;note;detail\r\nB;\r\nF;opaque\r\nS;setting;value\r\n",
    )
    .expect("write synthetic GWL fixture");
}

fn expected_path(path: &std::path::Path) -> String {
    let value = path.display().to_string();
    #[cfg(windows)]
    {
        value.replace('/', "\\")
    }
    #[cfg(not(windows))]
    {
        value
    }
}

#[test]
fn summarize_json_and_markers_are_separate_and_path_free() {
    let directory = temp_dir();
    let input = directory.join("sample.gwl");
    sample_gwl(&input);
    let output = Command::new(env!("CARGO_BIN_EXE_tecan-worklist"))
        .arg("summarize")
        .arg(&input)
        .arg("--json")
        .env("TECAN_PERFORMANCE_MARKERS", "1")
        .output()
        .expect("run Rust summarize CLI");
    let _ = fs::remove_dir_all(&directory);

    assert!(output.status.success());
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        payload,
        json!({
            "ok": true,
            "gwl": expected_path(&input),
            "record_counts": {
                "A": 1,
                "B": 1,
                "C": 1,
                "D": 1,
                "F": 1,
                "S": 1,
                "W": 1,
            },
            "pipette_records": 2,
            "transfer_pairs_estimate": 1,
        })
    );
    let marker_text = String::from_utf8(output.stderr).unwrap();
    let markers = marker_text
        .lines()
        .filter(|line| line.starts_with("PERF "))
        .collect::<Vec<_>>();
    assert_eq!(markers.len(), 2);
    assert!(markers.iter().all(|line| !line.contains("sample.gwl")));
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.gwl_parse"))
    );
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.gwl_summary"))
    );
}

#[test]
fn summarize_text_matches_existing_cli_shape_and_markers_default_off() {
    let directory = temp_dir();
    let input = directory.join("sample.gwl");
    sample_gwl(&input);
    let output = Command::new(env!("CARGO_BIN_EXE_tecan-worklist"))
        .arg("summarize")
        .arg(&input)
        .env_remove("TECAN_PERFORMANCE_MARKERS")
        .output()
        .expect("run Rust summarize CLI");
    let _ = fs::remove_dir_all(&directory);

    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    let text = String::from_utf8(output.stdout)
        .unwrap()
        .replace("\r\n", "\n");
    assert_eq!(
        text,
        format!(
            "Summarized {}\nrecord_counts: {{'A': 1, 'D': 1, 'W': 1, 'C': 1, 'B': 1, 'F': 1, 'S': 1}}\ntransfer_pairs_estimate: 1\n",
            expected_path(&input)
        )
    );
}

#[test]
fn analyze_preserves_windows_barriers_and_opt_in_markers() {
    let directory = temp_dir();
    let input = directory.join("analysis.gwl");
    fs::write(
        &input,
        "C;group-note\nA;source-a;;rack;1;;2;lc;;1;\nD;target-a;;rack;2;;2;lc;;1;\nA;source-b;;rack;3;;2;lc;;2;\nD;target-b;;rack;4;;2;lc;;2;\nB;\nA;source-c;;rack;5;;2;lc;;;\nD;target-c;;rack;6;;2;lc;;;\n",
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_tecan-worklist"))
        .arg("analyze")
        .arg(&input)
        .arg("--enabled-tip-mask")
        .arg("3")
        .arg("--json")
        .env("TECAN_PERFORMANCE_MARKERS", "1")
        .output()
        .expect("run Rust analyze CLI");
    let _ = fs::remove_dir_all(&directory);

    assert!(output.status.success());
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        payload,
        json!({
            "ok": true,
            "gwl": expected_path(&input),
            "schema_version": "tecan.worklist.semantics.v1",
            "record_count": 8,
            "unit_count": 3,
            "explicit_tip_assignments": { "1": 1, "2": 1 },
            "automatic_tip_units": 1,
            "enabled_tip_mask": 3,
            "barriers": [{
                "record_index": 5,
                "line_number": 6,
                "type": "B",
                "raw_line": "B;",
                "reason": "explicit execution barrier",
            }],
            "parallel_windows": [
                {
                    "index": 0,
                    "unit_indices": [0, 1],
                    "independent": true,
                    "explicit_tips": true,
                    "candidate_channels": [1, 2],
                    "reason": "independent explicit-tip units may be interleaved deterministically",
                },
                {
                    "index": 1,
                    "unit_indices": [2],
                    "independent": true,
                    "explicit_tips": false,
                    "candidate_channels": [],
                    "reason": "independent units may be analyzed, but automatic tips are not rewritten",
                },
            ],
            "diagnostics": [],
            "runtime_parallelism": "FluentControl-dependent; offline grouping is not a timing guarantee.",
        })
    );
    let marker_text = String::from_utf8(output.stderr).unwrap();
    let markers = marker_text
        .lines()
        .filter(|line| line.starts_with("PERF "))
        .collect::<Vec<_>>();
    assert_eq!(markers.len(), 2);
    assert!(markers.iter().all(|line| !line.contains("analysis.gwl")));
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.gwl_parse"))
    );
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.gwl_analysis"))
    );
}

#[test]
fn analyze_permissive_records_remain_barriers_and_invalid_tips_are_reported() {
    let directory = temp_dir();
    let input = directory.join("permissive.gwl");
    fs::write(
        &input,
        "A;source;;rack;1;;2;lc;;-1;\nD;target;;rack;2;;2;lc;;-1;\nW5;opaque\n",
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_tecan-worklist"))
        .arg("analyze")
        .arg(&input)
        .arg("--json")
        .env_remove("TECAN_PERFORMANCE_MARKERS")
        .output()
        .expect("run Rust analyze CLI");
    let _ = fs::remove_dir_all(&directory);

    assert_eq!(output.status.code(), Some(1));
    assert!(output.stderr.is_empty());
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["ok"], false);
    assert_eq!(payload["barriers"][0]["type"], "W5");
    assert_eq!(payload["barriers"][0]["raw_line"], "W5;opaque");
    assert_eq!(
        payload["parallel_windows"][0]["reason"],
        "W5 state boundary"
    );
    assert_eq!(payload["diagnostics"][0]["code"], "tip_mask_non_positive");
    assert_eq!(payload["diagnostics"][0]["line_numbers"], json!([1]));
    assert_eq!(
        payload["diagnostics"][2]["code"],
        "worklist_tip_selection_unavailable"
    );
    assert_eq!(payload["diagnostics"][3]["code"], "unknown_record_barrier");
}

#[test]
fn optimize_interleaves_independent_explicit_tips_and_keeps_comments_attached() {
    let directory = temp_dir();
    let input = directory.join("input.gwl");
    let output_path = directory.join("optimized.gwl");
    fs::write(
        &input,
        "C;unit-one\nA;src-a;;rack;1;;2;lc;;1;\nD;dst-a;;rack;1;;2;lc;;1;\nA;src-b;;rack;2;;2;lc;;1;\nD;dst-b;;rack;2;;2;lc;;1;\nA;src-c;;rack;3;;2;lc;;2;\nD;dst-c;;rack;3;;2;lc;;2;\nA;src-d;;rack;4;;2;lc;;2;\nD;dst-d;;rack;4;;2;lc;;2;\n",
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_tecan-worklist"))
        .arg("optimize")
        .arg(&input)
        .arg("-o")
        .arg(&output_path)
        .arg("--enabled-tip-mask")
        .arg("3")
        .arg("--json")
        .env("TECAN_PERFORMANCE_MARKERS", "1")
        .output()
        .expect("run Rust optimize CLI");

    assert!(output.status.success());
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["ok"], true);
    assert_eq!(payload["changed"], true);
    assert_eq!(payload["reordered_units"], json!([[0, 2, 1, 3]]));
    assert_eq!(payload["reasons"], json!([]));
    assert_eq!(payload["gwl"], expected_path(&input));
    assert_eq!(payload["output_gwl"], expected_path(&output_path));
    let optimized = String::from_utf8(fs::read(&output_path).unwrap())
        .unwrap()
        .replace("\r\n", "\n");
    assert_eq!(
        optimized,
        "C;unit-one\nA;src-a;;rack;1;;2;lc;;1;\nD;dst-a;;rack;1;;2;lc;;1;\nA;src-c;;rack;3;;2;lc;;2;\nD;dst-c;;rack;3;;2;lc;;2;\nA;src-b;;rack;2;;2;lc;;1;\nD;dst-b;;rack;2;;2;lc;;1;\nA;src-d;;rack;4;;2;lc;;2;\nD;dst-d;;rack;4;;2;lc;;2;\n"
    );
    let marker_text = String::from_utf8(output.stderr).unwrap();
    let markers = marker_text
        .lines()
        .filter(|line| line.starts_with("PERF "))
        .collect::<Vec<_>>();
    assert_eq!(markers.len(), 3);
    assert!(markers.iter().all(|line| !line.contains("input.gwl")));
    assert!(markers.iter().all(|line| !line.contains("optimized.gwl")));
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.gwl_parse"))
    );
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.gwl_optimization"))
    );
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.gwl_write"))
    );
    let _ = fs::remove_dir_all(&directory);
}

#[test]
fn optimize_preserves_shared_state_and_refuses_semantic_errors() {
    let directory = temp_dir();
    let input = directory.join("shared.gwl");
    let output_path = directory.join("optimized.gwl");
    fs::write(
        &input,
        "A;src;;rack;1;;2;lc;;1;\nD;dst-a;;rack;1;;2;lc;;1;\nA;src;;rack;1;;2;lc;;2;\nD;dst-b;;rack;2;;2;lc;;2;\n",
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_tecan-worklist"))
        .arg("optimize")
        .arg(&input)
        .arg("--output")
        .arg(&output_path)
        .arg("--enabled-tip-mask")
        .arg("3")
        .arg("--json")
        .env_remove("TECAN_PERFORMANCE_MARKERS")
        .output()
        .expect("run Rust optimize CLI");
    assert!(output.status.success());
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["changed"], false);
    assert_eq!(
        payload["reasons"],
        json!(["window 0: shared source/destination state prevents reordering"])
    );
    assert!(output_path.is_file());

    let invalid_input = directory.join("invalid.gwl");
    let invalid_output = directory.join("invalid-optimized.gwl");
    fs::write(
        &invalid_input,
        "A;src;;rack;1;;2;lc;;3;\nD;dst;;rack;1;;2;lc;;3;\n",
    )
    .unwrap();
    let invalid = Command::new(env!("CARGO_BIN_EXE_tecan-worklist"))
        .arg("optimize")
        .arg(&invalid_input)
        .arg("--output")
        .arg(&invalid_output)
        .arg("--enabled-tip-mask")
        .arg("3")
        .arg("--json")
        .output()
        .expect("run Rust optimize CLI on invalid selection");

    assert_eq!(invalid.status.code(), Some(1));
    let invalid_payload: serde_json::Value = serde_json::from_slice(&invalid.stdout).unwrap();
    assert_eq!(invalid_payload["changed"], false);
    assert_eq!(
        invalid_payload["reasons"],
        json!(["optimization refused because the worklist has semantic errors"])
    );
    assert!(!invalid_output.exists());
    let _ = fs::remove_dir_all(&directory);
}

#[test]
fn contract_cli_preserves_vendor_fields_and_execute_boundaries_with_markers() {
    let directory = temp_dir();
    let input = directory.join("commands.json");
    let commands = json!([
        {
            "command_id": "LoadWorklist",
            "parameters": {
                "WorklistName": "x.gwl",
                "SelectedTipsIndexes": "5",
                "TipSystem": "Fixed Tips",
                "VendorField": "retain",
                "options": { "retry": false },
            },
            "provenance": { "origin": "synthetic" },
        },
        {
            "command_id": "ExecuteWorklist",
            "parameters": {
                "Unknown": "kept",
                "line_number": 12,
                "empty": [],
                "zero": 0,
                "disabled": false,
            },
            "provenance": { "origin": "test" },
        },
        { "command_id": "Other", "parameters": { "untouched": true } },
    ]);
    fs::write(&input, serde_json::to_vec(&commands).unwrap()).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_tecan-worklist"))
        .arg("contract")
        .arg(&input)
        .arg("--json")
        .env("TECAN_PERFORMANCE_MARKERS", "1")
        .output()
        .expect("run Rust contract CLI");
    let _ = fs::remove_dir_all(&directory);

    assert!(output.status.success());
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        payload,
        json!([
            {
                "schema_version": "tecan.worklist.contract.v2",
                "kind": "load_worklist",
                "worklist": "x.gwl",
                "selected_tip_mask": "5",
                "selected_arm": null,
                "default_liquid_class": null,
                "tip_system": "fixed_tip",
                "selected_tip_mask_value": 5,
                "selected_tip_mask_status": "valid",
                "selected_tip_channels": [1, 3],
                "options": { "retry": false },
                "extra_fields": { "VendorField": "retain" },
                "command_id": "LoadWorklist",
                "command_version": "",
                "provenance": { "origin": "synthetic" },
            },
            {
                "schema_version": "tecan.worklist.contract.v2",
                "kind": "execute_worklist",
                "loads_before_execute": ["x.gwl"],
                "command_id": "ExecuteWorklist",
                "extra_fields": { "Unknown": "kept", "zero": 0, "disabled": false },
                "provenance": { "origin": "test" },
            },
            { "command_id": "Other", "parameters": { "untouched": true } },
        ])
    );
    let markers = String::from_utf8(output.stderr)
        .unwrap()
        .lines()
        .filter(|line| line.starts_with("PERF "))
        .map(str::to_owned)
        .collect::<Vec<_>>();
    assert_eq!(markers.len(), 2);
    assert!(markers.iter().all(|line| !line.contains("commands.json")));
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.contract_json_load"))
    );
    assert!(
        markers
            .iter()
            .any(|line| line.contains("worklist.execution_contract"))
    );
}
