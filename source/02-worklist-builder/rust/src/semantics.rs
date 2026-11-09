use crate::{Worklist, parse_gwl_text_permissive};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashSet};

#[derive(Clone, Debug)]
struct Pipette {
    operation: String,
    rack_label: String,
    rack_id: String,
    rack_type: String,
    position: String,
    tube_id: String,
    volume: String,
    liquid_class: String,
    tip_mask: String,
    forced_rack_type: String,
}

#[derive(Clone, Debug)]
enum Record {
    Comment(String),
    Pipette(Pipette),
    Boundary {
        type_character: String,
        raw_line: String,
        reason: String,
    },
    Unknown {
        type_character: String,
        raw_line: String,
    },
}

impl Record {
    fn type_character(&self) -> &str {
        match self {
            Self::Comment(_) => "C",
            Self::Pipette(record) => &record.operation,
            Self::Boundary { type_character, .. } | Self::Unknown { type_character, .. } => {
                type_character
            }
        }
    }

    fn to_line(&self) -> String {
        match self {
            Self::Comment(text) => format!("C;{}", text.replace('\n', "\\n")),
            Self::Pipette(record) => [
                clean(&record.operation),
                clean(&record.rack_label),
                clean(&record.rack_id),
                clean(&record.rack_type),
                clean(&record.position),
                clean(&record.tube_id),
                format_volume(&record.volume),
                clean(&record.liquid_class),
                String::new(),
                record.tip_mask.clone(),
                clean(&record.forced_rack_type),
            ]
            .join(";"),
            Self::Boundary { raw_line, .. } | Self::Unknown { raw_line, .. } => raw_line.clone(),
        }
    }

    fn barrier_reason(&self) -> String {
        match self {
            Self::Boundary { reason, .. } => reason.clone(),
            Self::Unknown { .. } => "unsupported or state-changing record".to_owned(),
            Self::Comment(_) => "comment association".to_owned(),
            Self::Pipette(_) => "unsupported or state-changing record".to_owned(),
        }
    }
}

#[derive(Clone, Debug)]
struct TipSelection {
    raw: String,
    mode: &'static str,
    value: Option<i128>,
    channel: Option<usize>,
    status: &'static str,
    diagnostic_code: Option<&'static str>,
}

impl TipSelection {
    fn valid(&self) -> bool {
        self.status == "valid"
    }

    fn diagnostic_code(&self) -> Option<&'static str> {
        self.diagnostic_code
    }

    fn parse(raw: &str, supported_channels: usize) -> Self {
        let text = raw.trim();
        if text.is_empty() {
            return Self {
                raw: raw.to_owned(),
                mode: "automatic",
                value: None,
                channel: None,
                status: "valid",
                diagnostic_code: None,
            };
        }
        let value = match parse_decimal_i128(text) {
            Some(value) => value,
            None => {
                return Self {
                    raw: raw.to_owned(),
                    mode: "explicit",
                    value: None,
                    channel: None,
                    status: "malformed",
                    diagnostic_code: Some("tip_mask_not_integer"),
                };
            }
        };
        if value <= 0 {
            return Self {
                raw: raw.to_owned(),
                mode: "explicit",
                value: Some(value),
                channel: None,
                status: "invalid",
                diagnostic_code: Some("tip_mask_non_positive"),
            };
        }
        if value & (value - 1) != 0 {
            return Self {
                raw: raw.to_owned(),
                mode: "explicit",
                value: Some(value),
                channel: None,
                status: "non_one_hot",
                diagnostic_code: Some("tip_mask_not_one_hot"),
            };
        }
        let channel = value.trailing_zeros() as usize + 1;
        if channel > supported_channels {
            return Self {
                raw: raw.to_owned(),
                mode: "explicit",
                value: Some(value),
                channel: Some(channel),
                status: "out_of_range",
                diagnostic_code: Some("tip_mask_channel_out_of_range"),
            };
        }
        Self {
            raw: raw.to_owned(),
            mode: "explicit",
            value: Some(value),
            channel: Some(channel),
            status: "valid",
            diagnostic_code: None,
        }
    }
}

#[derive(Clone, Debug)]
struct Unit {
    unit_index: usize,
    record_indices: Vec<usize>,
    aspirate: Pipette,
    dispense: Pipette,
    tip_selection: TipSelection,
    source_key: Vec<String>,
    destination_key: Vec<String>,
}

impl Unit {
    fn signature(&self) -> Vec<String> {
        let aspirate = &self.aspirate;
        let dispense = &self.dispense;
        vec![
            aspirate.rack_label.clone(),
            aspirate.rack_id.clone(),
            aspirate.rack_type.clone(),
            aspirate.position.clone(),
            aspirate.tube_id.clone(),
            dispense.rack_label.clone(),
            dispense.rack_id.clone(),
            dispense.rack_type.clone(),
            dispense.position.clone(),
            dispense.tube_id.clone(),
            aspirate.volume.clone(),
            aspirate.liquid_class.clone(),
            aspirate.tip_mask.clone(),
            dispense.volume.clone(),
            dispense.liquid_class.clone(),
            dispense.tip_mask.clone(),
            aspirate.forced_rack_type.clone(),
            dispense.forced_rack_type.clone(),
        ]
    }
}

#[derive(Default)]
struct AnalysisState {
    units: Vec<Unit>,
    windows: Vec<Value>,
    barriers: Vec<Value>,
    diagnostics: Vec<Value>,
    current: Vec<usize>,
}

pub fn analyze_worklist(
    worklist: &Worklist,
    enabled_tip_mask: Option<&str>,
    supported_channels: usize,
) -> Result<Value, String> {
    let (_, payload) = analyze_state(worklist, enabled_tip_mask, supported_channels)?;
    Ok(payload)
}

fn analyze_state(
    worklist: &Worklist,
    enabled_tip_mask: Option<&str>,
    supported_channels: usize,
) -> Result<(AnalysisState, Value), String> {
    let enabled = parse_enabled_tip_mask(enabled_tip_mask, supported_channels)?;
    let records = worklist
        .lines
        .iter()
        .map(|line| parse_record(line))
        .collect::<Vec<_>>();
    let mut state = AnalysisState::default();
    let mut pending_comments = Vec::new();
    let mut index = 0;

    while index < records.len() {
        match &records[index] {
            Record::Comment(_) => {
                pending_comments.push(index);
                index += 1;
            }
            Record::Pipette(aspirate) if aspirate.operation == "A" => {
                let paired_dispense = records.get(index + 1).and_then(|record| match record {
                    Record::Pipette(dispense) if dispense.operation == "D" => Some(dispense),
                    _ => None,
                });
                let Some(dispense) = paired_dispense else {
                    state.diagnostics.push(diagnostic(
                        "unpaired_aspirate",
                        "A record is not immediately followed by a D record; scheduling stops at this point.",
                        &[index + 1],
                        "error",
                    ));
                    close_window(&mut state, "unpaired A record");
                    state.barriers.push(json!({
                        "record_index": index,
                        "line_number": index + 1,
                        "type": aspirate.operation,
                        "reason": "unpaired_aspirate",
                    }));
                    pending_comments.clear();
                    index += 1;
                    continue;
                };

                let selection_a = TipSelection::parse(&aspirate.tip_mask, supported_channels);
                let selection_d = TipSelection::parse(&dispense.tip_mask, supported_channels);
                append_tip_diagnostic(&mut state.diagnostics, &selection_a, index + 1);
                append_tip_diagnostic(&mut state.diagnostics, &selection_d, index + 2);
                if selection_a.valid()
                    && selection_d.valid()
                    && selection_a.value != selection_d.value
                {
                    state.diagnostics.push(diagnostic(
                        "record_tip_selection_mismatch",
                        "The A/D pair changes explicit TipMask selection.",
                        &[index + 1, index + 2],
                        "error",
                    ));
                }
                let selection = if selection_a.mode == "explicit" {
                    selection_a
                } else {
                    selection_d
                };
                if let (Some(mask), Some(enabled_mask)) = (selection.value, enabled) {
                    if mask & enabled_mask as i128 == 0 {
                        state.diagnostics.push(diagnostic(
                            "record_tip_not_enabled",
                            "The record-level selected tip is not enabled by the enclosing Load Worklist mask.",
                            &[index + 1, index + 2],
                            "error",
                        ));
                    }
                } else if selection.value.is_some() && enabled.is_none() {
                    state.diagnostics.push(diagnostic(
                        "worklist_tip_selection_unavailable",
                        "The enclosing Load Worklist tip-selection mask was not supplied; subset compatibility is unproven.",
                        &[index + 1, index + 2],
                        "warning",
                    ));
                }

                let mut record_indices = pending_comments.clone();
                record_indices.extend([index, index + 1]);
                let unit = Unit {
                    unit_index: state.units.len(),
                    record_indices,
                    aspirate: aspirate.clone(),
                    dispense: dispense.clone(),
                    source_key: location_key(aspirate),
                    destination_key: location_key(dispense),
                    tip_selection: selection,
                };
                state.current.push(state.units.len());
                state.units.push(unit);
                pending_comments.clear();
                index += 2;
            }
            Record::Pipette(dispense) if dispense.operation == "D" => {
                state.diagnostics.push(diagnostic(
                    "unpaired_dispense",
                    "A D record is not paired with a preceding A record.",
                    &[index + 1],
                    "error",
                ));
                pending_comments.clear();
                index += 1;
            }
            record => {
                close_window(
                    &mut state,
                    &format!("{} state boundary", record.type_character()),
                );
                state.barriers.push(json!({
                    "record_index": index,
                    "line_number": index + 1,
                    "type": record.type_character(),
                    "raw_line": record.to_line(),
                    "reason": record.barrier_reason(),
                }));
                if let Record::Unknown { type_character, .. } = record {
                    state.diagnostics.push(diagnostic(
                        "unknown_record_barrier",
                        &format!(
                            "Unknown record {} prevents safe reordering.",
                            python_repr(type_character)
                        ),
                        &[index + 1],
                        "warning",
                    ));
                }
                pending_comments.clear();
                index += 1;
            }
        }
    }
    close_window(&mut state, "end of worklist");
    if let Some(last_comment) = pending_comments.last() {
        state.barriers.push(json!({
            "record_index": last_comment,
            "line_number": last_comment + 1,
            "type": "C",
            "reason": "trailing_comment",
        }));
    }

    let explicit_assignments = explicit_assignments(&state.units);
    let automatic_tip_units = state
        .units
        .iter()
        .filter(|unit| unit.tip_selection.mode == "automatic")
        .count();
    let has_errors = state
        .diagnostics
        .iter()
        .any(|diagnostic| diagnostic["severity"] == "error");
    let payload = json!({
        "ok": !has_errors,
        "schema_version": "tecan.worklist.semantics.v1",
        "record_count": records.len(),
        "unit_count": state.units.len(),
        "explicit_tip_assignments": explicit_assignments,
        "automatic_tip_units": automatic_tip_units,
        "enabled_tip_mask": enabled,
        "barriers": state.barriers.clone(),
        "parallel_windows": state.windows.clone(),
        "diagnostics": state.diagnostics.clone(),
        "runtime_parallelism": "FluentControl-dependent; offline grouping is not a timing guarantee.",
    });
    Ok((state, payload))
}

pub fn optimize_worklist(
    worklist: &Worklist,
    enabled_tip_mask: Option<&str>,
    supported_channels: usize,
) -> Result<(Worklist, Value), String> {
    let (state, mut payload) = analyze_state(worklist, enabled_tip_mask, supported_channels)?;
    let has_errors = payload["ok"].as_bool() != Some(true);
    let mut reordered_units = Vec::<Vec<usize>>::new();
    let mut reasons = Vec::<String>::new();
    let mut changed = false;

    let optimized = if has_errors {
        reasons.push("optimization refused because the worklist has semantic errors".to_owned());
        worklist.clone()
    } else {
        let original_records = worklist
            .lines
            .iter()
            .map(|line| parse_record(line))
            .collect::<Vec<_>>();
        let mut records = original_records
            .iter()
            .cloned()
            .map(Some)
            .collect::<Vec<_>>();
        let mut replacements = BTreeMap::<usize, Vec<Record>>::new();

        for window in &state.windows {
            let window_index = window["index"].as_u64().unwrap_or(0) as usize;
            let unit_indices = window["unit_indices"]
                .as_array()
                .ok_or_else(|| "worklist_window_unit_indices_invalid".to_owned())?
                .iter()
                .map(|value| {
                    value
                        .as_u64()
                        .map(|index| index as usize)
                        .ok_or_else(|| "worklist_window_unit_index_invalid".to_owned())
                })
                .collect::<Result<Vec<_>, _>>()?;
            if unit_indices.len() < 2 {
                continue;
            }
            if window["independent"].as_bool() != Some(true) {
                reasons.push(format!(
                    "window {window_index}: shared source/destination state prevents reordering"
                ));
                continue;
            }
            if window["explicit_tips"].as_bool() != Some(true) {
                reasons.push(format!(
                    "window {window_index}: automatic tip selection is not rewritten"
                ));
                continue;
            }

            let mut grouped = BTreeMap::<usize, Vec<usize>>::new();
            for unit_index in &unit_indices {
                let unit = state
                    .units
                    .get(*unit_index)
                    .ok_or_else(|| "worklist_window_unit_not_found".to_owned())?;
                let channel = unit
                    .tip_selection
                    .channel
                    .ok_or_else(|| "worklist_explicit_tip_channel_missing".to_owned())?;
                grouped.entry(channel).or_default().push(*unit_index);
            }
            let channels = grouped.keys().copied().collect::<Vec<_>>();
            if channels.len() < 2 {
                reasons.push(format!(
                    "window {window_index}: only one explicit tip channel is available"
                ));
                continue;
            }
            let mut candidate = Vec::<usize>::new();
            let mut offset = 0;
            loop {
                let mut added = false;
                for channel in &channels {
                    if let Some(unit_index) =
                        grouped.get(channel).and_then(|group| group.get(offset))
                    {
                        candidate.push(*unit_index);
                        added = true;
                    }
                }
                if !added {
                    break;
                }
                offset += 1;
            }
            if candidate == unit_indices {
                reasons.push(format!(
                    "window {window_index}: order is already tip-interleaved"
                ));
                continue;
            }

            let first = unit_indices
                .iter()
                .flat_map(|unit_index| state.units[*unit_index].record_indices.iter().copied())
                .min()
                .ok_or_else(|| "worklist_window_records_missing".to_owned())?;
            let mut blocks = BTreeMap::<usize, Vec<Record>>::new();
            for unit_index in &unit_indices {
                let unit = &state.units[*unit_index];
                let block_start = unit
                    .record_indices
                    .iter()
                    .copied()
                    .min()
                    .ok_or_else(|| "worklist_unit_records_missing".to_owned())?;
                let block = unit
                    .record_indices
                    .iter()
                    .map(|record_index| {
                        original_records
                            .get(*record_index)
                            .cloned()
                            .ok_or_else(|| "worklist_unit_record_not_found".to_owned())
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                blocks.insert(block_start, block);
            }
            let replacement = candidate
                .iter()
                .flat_map(|unit_index| {
                    let block_start = state.units[*unit_index]
                        .record_indices
                        .iter()
                        .copied()
                        .min()
                        .expect("validated unit has record indices");
                    blocks[&block_start].clone()
                })
                .collect::<Vec<_>>();
            replacements.insert(first, replacement);
            for unit_index in &unit_indices {
                for record_index in &state.units[*unit_index].record_indices {
                    if *record_index != first {
                        records[*record_index] = None;
                    }
                }
            }
            reordered_units.push(
                candidate
                    .iter()
                    .map(|unit_index| state.units[*unit_index].unit_index)
                    .collect(),
            );
        }

        changed = !replacements.is_empty();
        let mut output_records = Vec::<Record>::new();
        for (index, record) in records.into_iter().enumerate() {
            if let Some(replacement) = replacements.remove(&index) {
                output_records.extend(replacement);
            } else if let Some(record) = record {
                output_records.push(record);
            }
        }
        let lines = output_records
            .iter()
            .map(Record::to_line)
            .collect::<Vec<_>>();
        let text = format!("{}\n", lines.join("\n"));
        let optimized = parse_gwl_text_permissive(&text, &worklist.name)?;
        if !semantic_equivalent(worklist, &optimized, enabled_tip_mask, supported_channels)? {
            payload["ok"] = json!(false);
            reasons.push("optimized output failed semantic-equivalence verification".to_owned());
        }
        optimized
    };

    payload["changed"] = json!(changed);
    payload["reordered_units"] = json!(reordered_units);
    payload["reasons"] = json!(reasons);
    Ok((optimized, payload))
}

pub fn semantic_equivalent(
    left: &Worklist,
    right: &Worklist,
    enabled_tip_mask: Option<&str>,
    supported_channels: usize,
) -> Result<bool, String> {
    let (left_state, left_payload) = analyze_state(left, enabled_tip_mask, supported_channels)?;
    let (right_state, right_payload) = analyze_state(right, enabled_tip_mask, supported_channels)?;
    if left_payload["ok"].as_bool() != Some(true) || right_payload["ok"].as_bool() != Some(true) {
        return Ok(false);
    }
    if barrier_signatures(&left_state) != barrier_signatures(&right_state) {
        return Ok(false);
    }
    if left_state.windows.len() != right_state.windows.len() {
        return Ok(false);
    }
    for (left_window, right_window) in left_state.windows.iter().zip(&right_state.windows) {
        if window_signatures(left_window, &left_state)?
            != window_signatures(right_window, &right_state)?
        {
            return Ok(false);
        }
    }
    Ok(true)
}

fn barrier_signatures(state: &AnalysisState) -> Vec<(Value, Value)> {
    state
        .barriers
        .iter()
        .map(|barrier| {
            (
                barrier.get("type").cloned().unwrap_or(Value::Null),
                barrier.get("raw_line").cloned().unwrap_or(Value::Null),
            )
        })
        .collect()
}

fn window_signatures(
    window: &Value,
    state: &AnalysisState,
) -> Result<BTreeMap<Vec<String>, usize>, String> {
    let unit_indices = window["unit_indices"]
        .as_array()
        .ok_or_else(|| "worklist_window_unit_indices_invalid".to_owned())?;
    let mut signatures = BTreeMap::new();
    for value in unit_indices {
        let index = value
            .as_u64()
            .map(|index| index as usize)
            .ok_or_else(|| "worklist_window_unit_index_invalid".to_owned())?;
        let signature = state
            .units
            .get(index)
            .ok_or_else(|| "worklist_window_unit_not_found".to_owned())?
            .signature();
        *signatures.entry(signature).or_insert(0) += 1;
    }
    Ok(signatures)
}

fn parse_record(line: &str) -> Record {
    let parts = line.split(';').collect::<Vec<_>>();
    let operation = parts[0].trim().to_ascii_uppercase();
    match operation.as_str() {
        "A" | "D" => {
            let mut normalized = parts
                .iter()
                .map(|part| (*part).to_owned())
                .collect::<Vec<_>>();
            normalized.resize(11, String::new());
            normalized.truncate(11);
            Record::Pipette(Pipette {
                operation,
                rack_label: normalized[1].clone(),
                rack_id: normalized[2].clone(),
                rack_type: normalized[3].clone(),
                position: normalized[4].clone(),
                tube_id: normalized[5].clone(),
                volume: normalized[6].clone(),
                liquid_class: normalized[7].clone(),
                tip_mask: normalized[9].clone(),
                forced_rack_type: normalized[10].clone(),
            })
        }
        "C" => Record::Comment(parts.iter().skip(1).copied().collect::<Vec<_>>().join(";")),
        "B" => Record::Boundary {
            type_character: "B".to_owned(),
            raw_line: "B;".to_owned(),
            reason: "explicit execution barrier".to_owned(),
        },
        "W" | "W1" | "W2" | "W3" | "W4" => Record::Boundary {
            type_character: operation.clone(),
            raw_line: format!("{operation};"),
            reason: "typed worklist state transition".to_owned(),
        },
        "F" | "S" => Record::Boundary {
            type_character: operation,
            raw_line: line.to_owned(),
            reason: "typed worklist state transition".to_owned(),
        },
        _ => Record::Unknown {
            type_character: operation,
            raw_line: line.to_owned(),
        },
    }
}

fn close_window(state: &mut AnalysisState, boundary_reason: &str) {
    if state.current.is_empty() {
        return;
    }
    let (independent, explicit_tips, channels, detail) =
        window_properties(&state.current, &state.units);
    let window_index = state.windows.len();
    state.windows.push(json!({
        "index": window_index,
        "unit_indices": state.current.iter().map(|index| state.units[*index].unit_index).collect::<Vec<_>>(),
        "independent": independent,
        "explicit_tips": explicit_tips,
        "candidate_channels": channels,
        "reason": if independent { detail } else { boundary_reason.to_owned() },
    }));
    state.current.clear();
}

fn window_properties(indices: &[usize], units: &[Unit]) -> (bool, bool, Vec<usize>, String) {
    let channels = indices
        .iter()
        .filter_map(|index| units[*index].tip_selection.channel)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    if indices
        .iter()
        .any(|index| !units[*index].tip_selection.valid())
    {
        return (
            false,
            false,
            channels,
            "invalid tip selection prevents reordering".to_owned(),
        );
    }
    let mut seen = HashSet::<Vec<String>>::new();
    for index in indices {
        let unit = &units[*index];
        if seen.contains(&unit.source_key) || seen.contains(&unit.destination_key) {
            let explicit = indices
                .iter()
                .all(|item| units[*item].tip_selection.mode == "explicit");
            return (
                false,
                explicit,
                channels,
                "shared source/destination state is order-sensitive".to_owned(),
            );
        }
        seen.insert(unit.source_key.clone());
        seen.insert(unit.destination_key.clone());
    }
    let explicit = indices
        .iter()
        .all(|index| units[*index].tip_selection.mode == "explicit");
    if !explicit {
        return (
            true,
            false,
            channels,
            "independent units may be analyzed, but automatic tips are not rewritten".to_owned(),
        );
    }
    (
        true,
        true,
        channels,
        "independent explicit-tip units may be interleaved deterministically".to_owned(),
    )
}

fn location_key(record: &Pipette) -> Vec<String> {
    vec![
        record.rack_label.clone(),
        record.rack_id.clone(),
        record.rack_type.clone(),
        record.position.clone(),
        record.tube_id.clone(),
    ]
}

fn append_tip_diagnostic(diagnostics: &mut Vec<Value>, selection: &TipSelection, line: usize) {
    if selection.valid() {
        return;
    }
    diagnostics.push(diagnostic(
        selection.diagnostic_code().unwrap_or("tip_mask_invalid"),
        &format!(
            "Record-level TipMask {} is not a supported one-hot tip selection.",
            python_repr(&selection.raw)
        ),
        &[line],
        "error",
    ));
}

fn diagnostic(code: &str, message: &str, line_numbers: &[usize], severity: &str) -> Value {
    json!({
        "code": code,
        "message": message,
        "line_numbers": line_numbers,
        "severity": severity,
    })
}

fn explicit_assignments(units: &[Unit]) -> BTreeMap<String, usize> {
    let mut assignments = BTreeMap::new();
    for channel in units.iter().filter_map(|unit| unit.tip_selection.channel) {
        *assignments.entry(channel.to_string()).or_insert(0) += 1;
    }
    assignments
}

fn parse_enabled_tip_mask(
    value: Option<&str>,
    supported_channels: usize,
) -> Result<Option<u128>, String> {
    let Some(value) = value else { return Ok(None) };
    if value.is_empty() {
        return Ok(None);
    }
    let mask = parse_decimal_i128(value)
        .ok_or_else(|| "worklist_enabled_tip_mask_not_integer".to_owned())?;
    if supported_channels == 0 || supported_channels > 127 {
        return Err("worklist_enabled_tip_mask_out_of_range".to_owned());
    }
    let maximum = (1u128 << supported_channels) - 1;
    if mask <= 0 || mask as u128 > maximum {
        return Err("worklist_enabled_tip_mask_out_of_range".to_owned());
    }
    Ok(Some(mask as u128))
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

fn clean(value: &str) -> String {
    value.trim().to_owned()
}

fn format_volume(value: &str) -> String {
    let stripped = value.trim();
    match stripped.parse::<f64>() {
        Ok(number) if number.is_finite() && number.fract() == 0.0 => format!("{number:.0}"),
        Ok(number) => {
            let mut text = format!("{number:.6}");
            while text.contains('.') && text.ends_with('0') {
                text.pop();
            }
            if text.ends_with('.') {
                text.pop();
            }
            text
        }
        Err(_) => stripped.to_owned(),
    }
}

fn python_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut output = String::with_capacity(value.len() + 2);
    output.push(quote);
    for character in value.chars() {
        match character {
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value == quote => {
                output.push('\\');
                output.push(value);
            }
            value if value.is_control() && (value as u32) <= 0xff => {
                output.push_str(&format!("\\x{:02x}", value as u32));
            }
            value if value.is_control() => {
                output.push_str(&format!("\\u{:04x}", value as u32));
            }
            value => output.push(value),
        }
    }
    output.push(quote);
    output
}
