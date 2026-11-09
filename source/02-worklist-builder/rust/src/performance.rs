use serde_json::{Map, Value, json};
use std::io::Write;
use std::time::Instant;

fn enabled_value(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn enabled() -> bool {
    std::env::var("TECAN_PERFORMANCE_MARKERS")
        .map(|value| enabled_value(&value))
        .unwrap_or(false)
}

pub fn measure<T>(
    name: &str,
    metrics: Value,
    operation: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    if !enabled() {
        return operation();
    }
    let start = Instant::now();
    let result = operation();
    emit(
        name,
        start.elapsed().as_secs_f64() * 1000.0,
        &metrics,
        result.is_ok(),
    );
    result
}

fn marker_record(name: &str, duration_ms: f64, metrics: &Value, succeeded: bool) -> Value {
    let mut safe_metrics = Map::new();
    if let Some(object) = metrics.as_object() {
        for (key, value) in object {
            if value.is_number() || value.is_boolean() {
                safe_metrics.insert(key.clone(), value.clone());
            }
        }
    }
    json!({
        "type": "performance_marker",
        "name": name,
        "duration_ms": (duration_ms * 1000.0).round() / 1000.0,
        "outcome": if succeeded { "ok" } else { "error" },
        "metrics": Value::Object(safe_metrics),
    })
}

fn emit(name: &str, duration_ms: f64, metrics: &Value, succeeded: bool) {
    let record = marker_record(name, duration_ms, metrics, succeeded);
    if let Ok(encoded) = serde_json::to_string(&record) {
        let mut stderr = std::io::stderr().lock();
        let _ = writeln!(stderr, "PERF {encoded}");
    }
}

#[cfg(test)]
mod tests {
    use super::{enabled_value, marker_record};
    use serde_json::json;

    #[test]
    fn marker_switch_is_opt_in_and_accepts_documented_values() {
        assert!(!enabled_value(""));
        assert!(!enabled_value("0"));
        for value in ["1", "true", " YES ", "on"] {
            assert!(enabled_value(value), "{value}");
        }
    }

    #[test]
    fn marker_record_excludes_string_metadata_and_paths() {
        let record = marker_record(
            "worklist.csv_load",
            2.34567,
            &json!({ "transfer_count": 3, "path": "private/file.csv" }),
            true,
        );
        assert_eq!(record["duration_ms"], 2.346);
        assert_eq!(record["metrics"]["transfer_count"], 3);
        assert!(record["metrics"].get("path").is_none());
        assert!(!record.to_string().contains("private/file.csv"));
    }
}
