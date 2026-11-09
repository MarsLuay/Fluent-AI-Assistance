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
