use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

use serde_json::{Value, json};

use crate::paths::RepoPaths;

const REQUIRED_TOOLS: &[&str] = &[
    "fluent_status",
    "fluent_agent_brief",
    "fluent_resolve_brief_mode",
    "fluent_bootstrap_status",
    "fluent_list_projects",
    "fluent_import_project",
    "fluent_inspect_project",
    "fluent_project_query",
    "fluent_inspect_script",
    "fluent_find_external_command",
    "fluent_diagnose",
    "fluent_parse_fluent_log",
    "fluent_create_request_spec",
    "fluent_validate_request_spec",
    "fluent_generate_protocol",
    "fluent_plan_repair",
    "fluent_apply_repair",
    "fluent_verify_bundle",
    "fluent_process_media",
    "fluent_verify_archive",
    "fluent_list_capabilities",
    "fluent_run_safe_cli",
    "fluent_run_opt_in_cli",
    "fluent_worktable_diff",
    "fluent_summarize_simulation",
];
const REQUIRED_NEXT_STEP_KEYS: &[&str] = &[
    "action",
    "tool",
    "cli",
    "arguments",
    "brief_mode",
    "reason",
    "allowed_tools",
    "blocked_tools",
    "unlock_generate_after",
];

struct McpClient {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    next_id: u64,
}

impl McpClient {
    fn start(python: &Path, package_root: &Path) -> Result<Self, String> {
        let mut child = Command::new(python)
            .args(["-m", "fluent_pipeline.mcp_server"])
            .current_dir(package_root)
            .env("PYTHONUTF8", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|error| format!("could not start MCP server: {error}"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "MCP server stdin was unavailable".to_owned())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "MCP server stdout was unavailable".to_owned())?;
        Ok(Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
            next_id: 1,
        })
    }

    fn notify(&mut self, method: &str, params: Value) -> Result<(), String> {
        self.write_message(json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }))
    }

    fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id;
        self.next_id += 1;
        self.write_message(json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        }))?;

        loop {
            let mut line = String::new();
            let bytes = self
                .stdout
                .read_line(&mut line)
                .map_err(|error| format!("could not read MCP response: {error}"))?;
            if bytes == 0 {
                return Err(format!(
                    "MCP server closed stdout before responding to {method}"
                ));
            }
            if line.trim().is_empty() {
                continue;
            }
            let message: Value = serde_json::from_str(line.trim())
                .map_err(|error| format!("MCP server emitted invalid JSON: {error}"))?;
            if message.get("id") != Some(&json!(id)) {
                continue;
            }
            if let Some(error) = message.get("error") {
                return Err(format!("MCP {method} request failed: {error}"));
            }
            return message
                .get("result")
                .cloned()
                .ok_or_else(|| format!("MCP {method} response omitted result"));
        }
    }

    fn write_message(&mut self, message: Value) -> Result<(), String> {
        let text = serde_json::to_string(&message)
            .map_err(|error| format!("could not encode MCP request: {error}"))?;
        self.stdin
            .write_all(text.as_bytes())
            .and_then(|()| self.stdin.write_all(b"\n"))
            .and_then(|()| self.stdin.flush())
            .map_err(|error| format!("could not write MCP request: {error}"))
    }
}

impl Drop for McpClient {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn tool_payload(result: &Value) -> Result<Value, String> {
    if result.get("isError").and_then(Value::as_bool) == Some(true) {
        return Err(format!("MCP tool returned error: {result}"));
    }
    let mut chunks = Vec::new();
    if let Some(content) = result.get("content").and_then(Value::as_array) {
        for item in content {
            if let Some(text) = item.get("text").and_then(Value::as_str) {
                if !text.is_empty() {
                    chunks.push(text);
                }
            }
        }
    }
    if !chunks.is_empty() {
        let raw = chunks.join("\n");
        let value: Value = serde_json::from_str(raw.trim())
            .map_err(|error| format!("MCP tool returned invalid JSON payload: {error}"))?;
        if value.is_object() {
            return Ok(value);
        }
        return Err(format!(
            "MCP tool payload must be an object, got {}",
            value_kind(&value)
        ));
    }
    for key in ["structuredContent", "data"] {
        if let Some(value) = result.get(key).filter(|value| value.is_object()) {
            return Ok(value.clone());
        }
    }
    Err(format!("MCP tool returned no JSON payload: {result}"))
}

fn value_kind(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

pub fn validate_smoke_result(
    tool_names: &[String],
    status_ok: bool,
    bootstrap: Option<&Value>,
) -> Vec<String> {
    let mut failures = Vec::new();
    let present = tool_names
        .iter()
        .map(String::as_str)
        .collect::<std::collections::HashSet<_>>();
    let mut missing = REQUIRED_TOOLS
        .iter()
        .filter(|name| !present.contains(**name))
        .copied()
        .collect::<Vec<_>>();
    missing.sort_unstable();
    if !missing.is_empty() {
        failures.push(format!(
            "missing required MCP tools: {}",
            missing.join(", ")
        ));
    }
    if !status_ok {
        failures.push("fluent_status failed".to_owned());
    }
    let Some(bootstrap) = bootstrap else {
        failures.push("fluent_bootstrap_status returned no payload".to_owned());
        return failures;
    };
    let Some(next_step) = bootstrap.get("next_step").and_then(Value::as_object) else {
        failures.push("fluent_bootstrap_status missing next_step object".to_owned());
        return failures;
    };
    let mut absent = REQUIRED_NEXT_STEP_KEYS
        .iter()
        .filter(|key| !next_step.contains_key(**key))
        .copied()
        .collect::<Vec<_>>();
    absent.sort_unstable();
    if !absent.is_empty() {
        failures.push(format!("next_step missing keys: {}", absent.join(", ")));
    }
    if next_step
        .get("action")
        .is_none_or(|value| !is_truthy(value))
    {
        failures.push("next_step.action is empty".to_owned());
    }
    if next_step.get("tool").is_none_or(|value| !is_truthy(value)) {
        failures.push("next_step.tool is empty".to_owned());
    }
    for key in ["allowed_tools", "blocked_tools"] {
        if next_step.get(key).is_some_and(|value| !value.is_array()) {
            failures.push(format!("next_step.{key} must be a list"));
        }
    }
    failures
}

fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn resource_text(result: &Value) -> String {
    result
        .get("contents")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|content| content.get("text").and_then(Value::as_str))
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn run(paths: &RepoPaths) -> Result<Value, String> {
    let python = crate::install::find_python(paths)?;
    let mut client = McpClient::start(&python, &paths.package)?;
    client.request(
        "initialize",
        json!({
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {
                "name": "fluent-assistance-tools",
                "version": env!("CARGO_PKG_VERSION"),
            },
        }),
    )?;
    client.notify("notifications/initialized", json!({}))?;

    let tools_result = client.request("tools/list", json!({}))?;
    let resources_result = client.request("resources/list", json!({}))?;
    let templates_result = client.request("resources/templates/list", json!({}))?;
    let status_result = client.request(
        "tools/call",
        json!({"name": "fluent_status", "arguments": {}}),
    )?;
    let status_payload = tool_payload(&status_result)?;
    let bootstrap_result = client.request(
        "tools/call",
        json!({
            "name": "fluent_bootstrap_status",
            "arguments": {"write_report": false},
        }),
    )?;
    let bootstrap_payload = tool_payload(&bootstrap_result)?;
    let bootstrap_resource =
        client.request("resources/read", json!({"uri": "fluent://bootstrap"}))?;
    let brief_resource =
        client.request("resources/read", json!({"uri": "fluent://brief/status"}))?;

    let mut tool_names = tools_result
        .get("tools")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|tool| tool.get("name").and_then(Value::as_str))
        .map(str::to_owned)
        .collect::<Vec<_>>();
    tool_names.sort();
    let resource_uris = resources_result
        .get("resources")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| item.get("uri").and_then(Value::as_str))
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let template_uris = templates_result
        .get("resourceTemplates")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            item.get("uriTemplate")
                .or_else(|| item.get("uri"))
                .and_then(Value::as_str)
        })
        .map(str::to_owned)
        .collect::<Vec<_>>();

    let mut failures = validate_smoke_result(
        &tool_names,
        status_payload
            .as_object()
            .is_some_and(|payload| !payload.is_empty()),
        Some(&bootstrap_payload),
    );
    if !resource_uris.iter().any(|uri| uri == "fluent://bootstrap") {
        failures.push("missing resource fluent://bootstrap".to_owned());
    }
    if !template_uris
        .iter()
        .chain(resource_uris.iter())
        .any(|uri| uri.contains("fluent://brief/") || uri.contains("brief/{mode}"))
    {
        failures.push("missing resource template fluent://brief/{mode}".to_owned());
    }

    let bootstrap_mirror_text = resource_text(&bootstrap_resource);
    match serde_json::from_str::<Value>(&bootstrap_mirror_text) {
        Ok(value) if value.get("next_step").is_some_and(Value::is_object) => {}
        Ok(_) => failures.push("fluent://bootstrap missing next_step".to_owned()),
        Err(error) => failures.push(format!("fluent://bootstrap unreadable: {error}")),
    }
    let brief_mirror_text = resource_text(&brief_resource);
    match serde_json::from_str::<Value>(&brief_mirror_text) {
        Ok(value) if value.get("ok").is_some_and(is_truthy) => {}
        Ok(_) => failures.push("fluent://brief/status not ok".to_owned()),
        Err(error) => failures.push(format!("fluent://brief/status unreadable: {error}")),
    }

    Ok(json!({
        "ok": failures.is_empty(),
        "failures": failures,
        "tool_count": tool_names.len(),
        "tools": tool_names,
        "resource_count": resource_uris.len(),
        "resource_uris": resource_uris,
        "resource_template_count": template_uris.len(),
        "resource_templates": template_uris,
        "status": status_payload,
        "bootstrap": {
            "ok": bootstrap_payload.get("ok"),
            "doctor_ok": bootstrap_payload.get("doctor_ok"),
            "project_count": bootstrap_payload.get("project_count"),
            "next_step": bootstrap_payload.get("next_step"),
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn next_step() -> Value {
        json!({
            "action": "inspect_project",
            "tool": "fluent_inspect_project",
            "cli": "project-info",
            "arguments": {},
            "brief_mode": "new-script",
            "reason": "ok",
            "allowed_tools": ["fluent_inspect_project"],
            "blocked_tools": ["fluent_generate_protocol"],
            "unlock_generate_after": ["fluent_inspect_project"],
        })
    }

    #[test]
    fn validator_accepts_complete_mcp_surface() {
        let names = REQUIRED_TOOLS
            .iter()
            .map(|name| (*name).to_owned())
            .collect::<Vec<_>>();
        let bootstrap = json!({"next_step": next_step()});
        assert!(validate_smoke_result(&names, true, Some(&bootstrap)).is_empty());
    }

    #[test]
    fn validator_reports_missing_tool_and_next_step() {
        let names = REQUIRED_TOOLS
            .iter()
            .filter(|name| **name != "fluent_bootstrap_status")
            .map(|name| (*name).to_owned())
            .collect::<Vec<_>>();
        let failures = validate_smoke_result(&names, true, Some(&json!({})));
        assert!(
            failures
                .iter()
                .any(|failure| failure.contains("fluent_bootstrap_status"))
        );
        assert!(failures.iter().any(|failure| failure.contains("next_step")));
    }

    #[test]
    fn validator_checks_required_next_step_fields() {
        let failures = validate_smoke_result(
            &[],
            false,
            Some(&json!({"next_step": {"action": "", "tool": ""}})),
        );
        assert!(
            failures
                .iter()
                .any(|failure| failure.contains("missing required MCP tools"))
        );
        assert!(
            failures
                .iter()
                .any(|failure| failure.contains("next_step missing keys"))
        );
        assert!(
            failures
                .iter()
                .any(|failure| failure.contains("fluent_status failed"))
        );
    }
}
