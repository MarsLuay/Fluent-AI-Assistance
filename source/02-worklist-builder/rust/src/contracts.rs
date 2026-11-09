use serde_json::{Map, Number, Value, json};
use std::collections::BTreeSet;

pub const SCHEMA_VERSION: &str = "tecan.worklist.contract.v2";

pub const LOAD_COMMAND_IDS: &[&str] = &[
    "LoadWorklistStatementDataV4",
    "WorklistImportStatementDataV2",
    "LoadWorklist",
    "ReadWorklist",
    "ImportWorklist",
];

pub const EXECUTE_COMMAND_IDS: &[&str] = &["ExecuteWorklistStatementDataV1", "ExecuteWorklist"];

const IR_CONTEXT_FIELDS: &[&str] = &[
    "labware",
    "volume_ul",
    "liquid_class",
    "device_alias",
    "available_id",
    "head_position",
    "back_position",
    "raw_xml",
    "compiled_path",
    "source_entry",
    "command_index",
    "line_number",
];

const KNOWN_LOAD_FIELDS: &[&str] = &[
    "worklist",
    "WorklistName",
    "FileName",
    "Path",
    "selected_tip_mask",
    "selected_tip_indexes",
    "SelectedTipsIndexes",
    "SerializedTipsIndexes",
    "TipMask",
    "selected_arm",
    "DeviceAlias",
    "Arm",
    "ArmName",
    "default_liquid_class",
    "LiquidClass",
    "DefaultLiquidClass",
    "tip_system",
    "TipSystem",
    "options",
    "command_id",
    "command_version",
    "provenance",
    "LineNumber",
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TipMaskParse {
    pub value: Option<i128>,
    pub status: &'static str,
    pub channels: Vec<usize>,
}

pub fn parse_command_tip_mask(value: &Value, supported_channels: usize) -> TipMaskParse {
    if value.is_null() || value.as_str() == Some("") {
        return TipMaskParse {
            value: None,
            status: "unavailable",
            channels: Vec::new(),
        };
    }
    let Some(mask) = parse_decimal_i128(&python_string(value)) else {
        return TipMaskParse {
            value: None,
            status: "malformed",
            channels: Vec::new(),
        };
    };
    let maximum = if supported_channels >= 128 {
        u128::MAX
    } else if supported_channels == 0 {
        0
    } else {
        (1u128 << supported_channels) - 1
    };
    if mask <= 0 || (mask as u128) & !maximum != 0 {
        return TipMaskParse {
            value: Some(mask),
            status: "out_of_range",
            channels: Vec::new(),
        };
    }
    let channels = (1..=supported_channels.min(128))
        .filter(|channel| (mask as u128) & (1u128 << (channel - 1)) != 0)
        .collect();
    TipMaskParse {
        value: Some(mask),
        status: "valid",
        channels,
    }
}

pub fn parse_selected_tip_indexes(value: &Value, supported_channels: usize) -> TipMaskParse {
    if value.is_null() || value.as_str() == Some("") {
        return TipMaskParse {
            value: None,
            status: "unavailable",
            channels: Vec::new(),
        };
    }
    let values = match value {
        Value::Array(values) => values.iter().collect::<Vec<_>>(),
        _ => vec![value],
    };
    let mut indexes = BTreeSet::new();
    for item in values {
        let Some(index) = parse_decimal_i128(&python_string(item)) else {
            return TipMaskParse {
                value: None,
                status: "malformed",
                channels: Vec::new(),
            };
        };
        if index < 0 || index as usize >= supported_channels || index >= 128 {
            return TipMaskParse {
                value: None,
                status: "out_of_range",
                channels: Vec::new(),
            };
        }
        indexes.insert(index as usize);
    }
    if indexes.is_empty() {
        return TipMaskParse {
            value: None,
            status: "out_of_range",
            channels: Vec::new(),
        };
    }
    let mask = indexes
        .iter()
        .fold(0u128, |mask, index| mask | (1u128 << index));
    let Ok(mask) = i128::try_from(mask) else {
        return TipMaskParse {
            value: None,
            status: "out_of_range",
            channels: Vec::new(),
        };
    };
    TipMaskParse {
        value: Some(mask),
        status: "valid",
        channels: indexes.into_iter().map(|index| index + 1).collect(),
    }
}

pub fn normalize_load_worklist(
    fields: &Value,
    command_id: &str,
    command_version: &str,
    provenance: Option<&Value>,
) -> Result<Value, String> {
    let fields = fields
        .as_object()
        .ok_or_else(|| "worklist_contract_fields_not_object".to_owned())?;
    let worklist = first(fields, &["worklist", "WorklistName", "FileName", "Path"]);
    let selected_tip_mask = first(
        fields,
        &[
            "selected_tip_mask",
            "SelectedTipsIndexes",
            "SerializedTipsIndexes",
            "TipMask",
        ],
    );
    let selected_arm = first(fields, &["selected_arm", "DeviceAlias", "Arm", "ArmName"]);
    let default_liquid_class = first(
        fields,
        &["default_liquid_class", "LiquidClass", "DefaultLiquidClass"],
    );
    let tip_system_value = first(fields, &["tip_system", "TipSystem"]);
    let tip_system = normalize_tip_system(tip_system_value);
    let selected_tip_indexes = first(fields, &["selected_tip_indexes", "SelectedTipIndexes"]);
    let selection = if let Some(indexes) = selected_tip_indexes {
        parse_selected_tip_indexes(indexes, 8)
    } else if let Some(mask) = selected_tip_mask {
        parse_command_tip_mask(mask, 8)
    } else {
        TipMaskParse {
            value: None,
            status: "unavailable",
            channels: Vec::new(),
        }
    };
    let options = fields
        .get("options")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let extra_fields = fields
        .iter()
        .filter(|(key, _)| !KNOWN_LOAD_FIELDS.contains(&key.as_str()))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect::<Map<_, _>>();
    let normalized_command_id = if !command_id.is_empty() {
        command_id.to_owned()
    } else {
        fields
            .get("command_id")
            .filter(|value| truthy(value))
            .map(python_string)
            .unwrap_or_default()
    };
    let normalized_command_version = if !command_version.is_empty() {
        command_version.to_owned()
    } else {
        fields
            .get("command_version")
            .filter(|value| truthy(value))
            .map(python_string)
            .unwrap_or_default()
    };
    let provenance_value = provenance
        .filter(|value| truthy(value))
        .or_else(|| fields.get("provenance").filter(|value| truthy(value)));
    let provenance = object_or_empty(provenance_value)?;

    Ok(json!({
        "schema_version": SCHEMA_VERSION,
        "kind": "load_worklist",
        "worklist": worklist.cloned().unwrap_or(Value::Null),
        "selected_tip_mask": selected_tip_mask.cloned().unwrap_or(Value::Null),
        "selected_arm": selected_arm.cloned().unwrap_or(Value::Null),
        "default_liquid_class": default_liquid_class.cloned().unwrap_or(Value::Null),
        "tip_system": tip_system,
        "selected_tip_mask_value": selection.value,
        "selected_tip_mask_status": selection.status,
        "selected_tip_channels": selection.channels,
        "options": options,
        "extra_fields": extra_fields,
        "command_id": normalized_command_id,
        "command_version": normalized_command_version,
        "provenance": provenance,
    }))
}

pub fn build_execution_contract(commands: &[Value]) -> Result<Value, String> {
    let mut loaded_worklists = Vec::<Value>::new();
    let mut result = Vec::<Value>::new();
    for command in commands {
        let command_object = command
            .as_object()
            .ok_or_else(|| "worklist_contract_command_not_object".to_owned())?;
        let command_id_value = command_object
            .get("command_id")
            .filter(|value| truthy(value))
            .or_else(|| command_object.get("id").filter(|value| truthy(value)));
        let command_id = command_id_value.map(python_string).unwrap_or_default();
        let fields = command_object
            .get("parameters")
            .filter(|value| value.is_object())
            .unwrap_or(command);

        if LOAD_COMMAND_IDS.contains(&command_id.as_str()) {
            let contract =
                normalize_load_worklist(fields, &command_id, "", command_object.get("provenance"))?;
            loaded_worklists.push(contract["worklist"].clone());
            result.push(contract);
        } else if EXECUTE_COMMAND_IDS.contains(&command_id.as_str()) {
            let parameters = fields
                .as_object()
                .ok_or_else(|| "worklist_contract_execute_parameters_not_object".to_owned())?;
            let extra_fields = parameters
                .iter()
                .filter(|(key, value)| {
                    !["command_id", "provenance"]
                        .iter()
                        .chain(IR_CONTEXT_FIELDS.iter())
                        .any(|known| key.as_str() == *known)
                        && !empty_value(value)
                })
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect::<Map<_, _>>();
            let loads_before_execute = loaded_worklists
                .iter()
                .map(|worklist| {
                    if truthy(worklist) {
                        python_string(worklist)
                    } else {
                        String::new()
                    }
                })
                .collect::<Vec<_>>();
            let provenance = object_or_empty(
                command_object
                    .get("provenance")
                    .filter(|value| truthy(value)),
            )?;
            result.push(json!({
                "schema_version": SCHEMA_VERSION,
                "kind": "execute_worklist",
                "loads_before_execute": loads_before_execute,
                "command_id": command_id,
                "extra_fields": extra_fields,
                "provenance": provenance,
            }));
            loaded_worklists.clear();
        } else {
            result.push(command.clone());
        }
    }
    Ok(Value::Array(result))
}

fn first<'a>(fields: &'a Map<String, Value>, names: &[&str]) -> Option<&'a Value> {
    names.iter().find_map(|name| {
        fields
            .get(*name)
            .filter(|value| !value.is_null() && value.as_str() != Some(""))
    })
}

fn normalize_tip_system(value: Option<&Value>) -> &'static str {
    let normalized = value
        .filter(|value| truthy(value))
        .map(python_string)
        .unwrap_or_default()
        .trim()
        .to_lowercase()
        .replace(' ', "_");
    match normalized.as_str() {
        "fixed" | "fixed_tip" | "fixedtips" | "fixed_tips" => "fixed_tip",
        "diti" | "disposable_tip" | "disposable_tips" | "disposable_tip_adapter" => "diti",
        _ => "unknown",
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_none_or(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn empty_value(value: &Value) -> bool {
    value.is_null()
        || value.as_str() == Some("")
        || value.as_array().is_some_and(Vec::is_empty)
        || value.as_object().is_some_and(Map::is_empty)
}

fn object_or_empty(value: Option<&Value>) -> Result<Map<String, Value>, String> {
    match value {
        None => Ok(Map::new()),
        Some(Value::Object(value)) => Ok(value.clone()),
        Some(_) => Err("worklist_contract_provenance_not_object".to_owned()),
    }
}

fn python_string(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::String(value) => value.clone(),
        Value::Number(value) => python_number_string(value),
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(python_repr)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(values) => format!(
            "{{{}}}",
            values
                .iter()
                .map(|(key, value)| format!(
                    "{}: {}",
                    python_repr(&Value::String(key.clone())),
                    python_repr(value)
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn python_repr(value: &Value) -> String {
    match value {
        Value::String(value) => format!("'{}'", value.replace('\\', "\\\\").replace('\'', "\\'")),
        Value::Null => "None".to_owned(),
        Value::Bool(value) => if *value { "True" } else { "False" }.to_owned(),
        Value::Number(value) => python_number_string(value),
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(python_repr)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(values) => format!(
            "{{{}}}",
            values
                .iter()
                .map(|(key, value)| format!(
                    "{}: {}",
                    python_repr(&Value::String(key.clone())),
                    python_repr(value)
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn python_number_string(value: &Number) -> String {
    value.to_string()
}

fn parse_decimal_i128(text: &str) -> Option<i128> {
    let trimmed = text.trim();
    let (sign, digits) = match trimmed.as_bytes().first() {
        Some(b'+') => ("+", &trimmed[1..]),
        Some(b'-') => ("-", &trimmed[1..]),
        _ => ("", trimmed),
    };
    if digits.is_empty() {
        return None;
    }
    let characters = digits.chars().collect::<Vec<_>>();
    let mut normalized = String::with_capacity(trimmed.len());
    normalized.push_str(sign);
    for (index, character) in characters.iter().copied().enumerate() {
        if character == '_' {
            if index == 0
                || index + 1 == characters.len()
                || !characters[index - 1].is_ascii_digit()
                || !characters[index + 1].is_ascii_digit()
            {
                return None;
            }
            continue;
        }
        if !character.is_ascii_digit() {
            return None;
        }
        normalized.push(character);
    }
    normalized.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::{
        build_execution_contract, normalize_load_worklist, parse_command_tip_mask,
        parse_selected_tip_indexes,
    };
    use serde_json::json;

    #[test]
    fn parses_command_masks_and_zero_based_selected_indexes() {
        assert_eq!(
            parse_command_tip_mask(&json!("5"), 8),
            super::TipMaskParse {
                value: Some(5),
                status: "valid",
                channels: vec![1, 3],
            }
        );
        assert_eq!(
            parse_selected_tip_indexes(&json!([0, 2, 2]), 8),
            super::TipMaskParse {
                value: Some(5),
                status: "valid",
                channels: vec![1, 3],
            }
        );
        assert_eq!(
            parse_command_tip_mask(&json!("3"), 1).status,
            "out_of_range"
        );
        assert_eq!(
            parse_selected_tip_indexes(&json!([-1]), 8).status,
            "out_of_range"
        );
        assert_eq!(
            parse_command_tip_mask(&json!("not-a-mask"), 8).status,
            "malformed"
        );
    }

    #[test]
    fn normalizes_known_aliases_and_retains_unknown_fields() {
        let contract = normalize_load_worklist(
            &json!({
                "WorklistName": "Transfers.gwl",
                "SelectedTipsIndexes": "5",
                "TipSystem": "Fixed Tips",
                "NewVendorField": "preserve-me",
                "options": { "retry": false },
            }),
            "LoadWorklistStatementDataV4",
            "v4",
            Some(&json!({ "source": "synthetic" })),
        )
        .unwrap();
        assert_eq!(
            contract,
            json!({
                "schema_version": "tecan.worklist.contract.v2",
                "kind": "load_worklist",
                "worklist": "Transfers.gwl",
                "selected_tip_mask": "5",
                "selected_arm": null,
                "default_liquid_class": null,
                "tip_system": "fixed_tip",
                "selected_tip_mask_value": 5,
                "selected_tip_mask_status": "valid",
                "selected_tip_channels": [1, 3],
                "options": { "retry": false },
                "extra_fields": { "NewVendorField": "preserve-me" },
                "command_id": "LoadWorklistStatementDataV4",
                "command_version": "v4",
                "provenance": { "source": "synthetic" },
            })
        );
    }

    #[test]
    fn execution_contract_preserves_load_order_and_execute_boundaries() {
        let commands = json!([
            { "command_id": "LoadWorklistStatementDataV4", "parameters": { "WorklistName": "first.gwl" } },
            { "command_id": "LoadWorklistStatementDataV4", "parameters": { "WorklistName": "second.gwl" } },
            { "command_id": "ExecuteWorklistStatementDataV1", "parameters": { "Unknown": "kept", "line_number": 12 } },
            { "command_id": "LoadWorklist", "parameters": { "WorklistName": "third.gwl" } },
            { "command_id": "Other", "parameters": { "untouched": true } },
        ]);
        let result = build_execution_contract(commands.as_array().unwrap()).unwrap();
        assert_eq!(
            result,
            json!([
                {
                    "schema_version": "tecan.worklist.contract.v2",
                    "kind": "load_worklist",
                    "worklist": "first.gwl",
                    "selected_tip_mask": null,
                    "selected_arm": null,
                    "default_liquid_class": null,
                    "tip_system": "unknown",
                    "selected_tip_mask_value": null,
                    "selected_tip_mask_status": "unavailable",
                    "selected_tip_channels": [],
                    "options": {},
                    "extra_fields": {},
                    "command_id": "LoadWorklistStatementDataV4",
                    "command_version": "",
                    "provenance": {},
                },
                {
                    "schema_version": "tecan.worklist.contract.v2",
                    "kind": "load_worklist",
                    "worklist": "second.gwl",
                    "selected_tip_mask": null,
                    "selected_arm": null,
                    "default_liquid_class": null,
                    "tip_system": "unknown",
                    "selected_tip_mask_value": null,
                    "selected_tip_mask_status": "unavailable",
                    "selected_tip_channels": [],
                    "options": {},
                    "extra_fields": {},
                    "command_id": "LoadWorklistStatementDataV4",
                    "command_version": "",
                    "provenance": {},
                },
                {
                    "schema_version": "tecan.worklist.contract.v2",
                    "kind": "execute_worklist",
                    "loads_before_execute": ["first.gwl", "second.gwl"],
                    "command_id": "ExecuteWorklistStatementDataV1",
                    "extra_fields": { "Unknown": "kept" },
                    "provenance": {},
                },
                {
                    "schema_version": "tecan.worklist.contract.v2",
                    "kind": "load_worklist",
                    "worklist": "third.gwl",
                    "selected_tip_mask": null,
                    "selected_arm": null,
                    "default_liquid_class": null,
                    "tip_system": "unknown",
                    "selected_tip_mask_value": null,
                    "selected_tip_mask_status": "unavailable",
                    "selected_tip_channels": [],
                    "options": {},
                    "extra_fields": {},
                    "command_id": "LoadWorklist",
                    "command_version": "",
                    "provenance": {},
                },
                { "command_id": "Other", "parameters": { "untouched": true } },
            ])
        );
    }
}
