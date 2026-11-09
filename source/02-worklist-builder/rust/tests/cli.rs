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
