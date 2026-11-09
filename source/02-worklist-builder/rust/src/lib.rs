pub mod contracts;
pub mod semantics;

use std::collections::{BTreeMap, HashMap, HashSet};
use std::io::Read;
use std::path::Path;

use serde_json::{Value, json};

const REQUIRED_COLUMNS: [&str; 7] = [
    "source_label",
    "source_type",
    "source_position",
    "dest_label",
    "dest_type",
    "dest_position",
    "volume_ul",
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WashPolicy {
    Each,
    None,
}

impl WashPolicy {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "each" => Ok(Self::Each),
            "none" => Ok(Self::None),
            _ => Err("wash_policy must be 'each' or 'none'.".to_owned()),
        }
    }
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct Transfer {
    pub source_label: String,
    pub source_type: String,
    pub source_position: u64,
    pub dest_label: String,
    pub dest_type: String,
    pub dest_position: u64,
    pub volume_ul: f64,
    pub liquid_class: String,
    pub source_id: String,
    pub dest_id: String,
    pub source_tube_id: String,
    pub dest_tube_id: String,
    pub tip_mask: String,
    pub forced_source_type: String,
    pub forced_dest_type: String,
    pub comment: String,
    pub wash_after: Option<bool>,
    pub break_after: bool,
    pub row_number: usize,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct ValidationResult {
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
}

impl ValidationResult {
    pub fn ok(&self) -> bool {
        self.errors.is_empty()
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Worklist {
    pub name: String,
    pub lines: Vec<String>,
    pub counts: BTreeMap<String, usize>,
    pub count_order: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct WorklistOperation {
    pub kind: &'static str,
    pub transfer_index: usize,
}

pub fn serialize_gwl_lines(lines: &[String]) -> String {
    let mut text = lines.join("\n");
    text.push('\n');
    text
}

pub fn count_record_types_in_order(types: &[String]) -> Vec<(String, usize)> {
    let mut positions = HashMap::new();
    let mut counts = Vec::new();
    for record_type in types {
        let index = if let Some(index) = positions.get(record_type.as_str()) {
            *index
        } else {
            let index = counts.len();
            positions.insert(record_type.as_str(), index);
            counts.push((record_type.clone(), 0));
            index
        };
        counts[index].1 += 1;
    }
    counts
}

impl Worklist {
    pub fn to_text(&self) -> String {
        serialize_gwl_lines(&self.lines)
    }

    pub fn counts_repr(&self) -> String {
        let entries = self
            .count_order
            .iter()
            .filter_map(|record_type| {
                self.counts
                    .get(record_type)
                    .map(|count| format!("{}: {count}", python_repr(record_type)))
            })
            .collect::<Vec<_>>();
        format!("{{{}}}", entries.join(", "))
    }
}

pub fn parse_gwl_file(path: impl AsRef<Path>) -> Result<Worklist, String> {
    parse_gwl_file_mode(path.as_ref(), false)
}

pub fn parse_gwl_file_permissive(path: impl AsRef<Path>) -> Result<Worklist, String> {
    parse_gwl_file_mode(path.as_ref(), true)
}

fn parse_gwl_file_mode(path: &Path, permissive: bool) -> Result<Worklist, String> {
    let mut text = String::new();
    std::fs::File::open(path)
        .and_then(|mut file| file.read_to_string(&mut text))
        .map_err(|error| format!("could not read GWL file: {error}"))?;
    if let Some(without_bom) = text.strip_prefix('\u{feff}') {
        text = without_bom.to_owned();
    }
    let name = path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .unwrap_or("worklist");
    parse_gwl_text_mode(&text, name, permissive)
}

pub fn parse_gwl_text(text: &str, name: &str) -> Result<Worklist, String> {
    parse_gwl_text_mode(text, name, false)
}

pub fn parse_gwl_text_permissive(text: &str, name: &str) -> Result<Worklist, String> {
    parse_gwl_text_mode(text, name, true)
}

fn parse_gwl_text_mode(text: &str, name: &str, permissive: bool) -> Result<Worklist, String> {
    let mut worklist = Worklist {
        name: name.to_owned(),
        ..Worklist::default()
    };
    for (line_index, raw_line) in text.lines().enumerate() {
        let line = raw_line.trim_end_matches(['\r', '\n']);
        if line.trim().is_empty() {
            continue;
        }
        let parts = line.split(';').collect::<Vec<_>>();
        let operation = parts[0].trim().to_ascii_uppercase();
        let line_number = line_index + 1;
        let prefix = format!("Line {line_number}: ");
        match operation.as_str() {
            "A" | "D" => {
                if !permissive && parts.len() != 11 {
                    return Err(format!(
                        "{prefix}pipette record has {} fields, expected 11.",
                        parts.len()
                    ));
                }
            }
            "W" | "W1" | "W2" | "W3" | "W4" | "B" => {
                if !permissive && parts.len() != 2 {
                    let record_name = if operation.starts_with('W') {
                        "wash"
                    } else {
                        "break"
                    };
                    return Err(format!(
                        "{prefix}{record_name} record has {} fields, expected 2.",
                        parts.len()
                    ));
                }
            }
            "C" => {
                if !permissive && parts.len() < 2 {
                    return Err(format!(
                        "{prefix}comment record has {} fields, expected at least 2.",
                        parts.len()
                    ));
                }
            }
            "F" | "S" => {}
            _ if permissive => {}
            _ => {
                return Err(format!(
                    "{prefix}unsupported record type {}.",
                    python_repr(&operation)
                ));
            }
        }
        push_record(&mut worklist, line.to_owned(), &operation);
    }
    Ok(worklist)
}

pub fn parse_gwl_records_for_python(lines: &[String], permissive: bool) -> Option<Vec<Value>> {
    let mut records = Vec::new();
    for raw_line in lines {
        if raw_line
            .chars()
            .any(|character| ('\u{001c}'..='\u{001f}').contains(&character))
        {
            return None;
        }
        let line = raw_line.trim_end_matches(['\r', '\n']);
        if line.trim().is_empty() {
            continue;
        }
        let parts = line.split(';').collect::<Vec<_>>();
        let raw_operation = parts[0].trim();
        if !raw_operation.is_ascii() {
            return None;
        }
        let operation = raw_operation.to_ascii_uppercase();
        match operation.as_str() {
            "A" | "D" => {
                if !permissive && parts.len() != 11 {
                    return None;
                }
                let mut fields = parts.clone();
                fields.resize(11, "");
                fields.truncate(11);
                records.push(json!({
                    "kind": "pipette",
                    "operation": operation,
                    "rack_label": fields[1],
                    "rack_id": fields[2],
                    "rack_type": fields[3],
                    "position": fields[4],
                    "tube_id": fields[5],
                    "volume": fields[6],
                    "liquid_class": fields[7],
                    "tip_mask": fields[9],
                    "forced_rack_type": fields[10],
                }));
            }
            "W" | "W1" | "W2" | "W3" | "W4" => {
                if !permissive && parts.len() != 2 {
                    return None;
                }
                let scheme = operation
                    .strip_prefix('W')
                    .filter(|suffix| !suffix.is_empty())
                    .and_then(|suffix| suffix.parse::<u8>().ok());
                records.push(json!({"kind": "wash", "scheme": scheme}));
            }
            "B" => {
                if !permissive && parts.len() != 2 {
                    return None;
                }
                records.push(json!({"kind": "break"}));
            }
            "C" => {
                if !permissive && parts.len() < 2 {
                    return None;
                }
                records.push(json!({
                    "kind": "comment",
                    "text": parts[1..].join(";"),
                }));
            }
            "F" | "S" => records.push(json!({
                "kind": if operation == "F" { "flush" } else { "set_diti_type" },
                "type_character": operation,
                "parameters": parts[1..].to_vec(),
                "raw_line": line,
            })),
            _ if permissive => records.push(json!({
                "kind": "raw",
                "type_character": operation,
                "raw_line": line,
            })),
            _ => return None,
        }
    }
    Some(records)
}

pub fn parse_gwl_text_records_for_python(text: &str, permissive: bool) -> Option<Vec<Value>> {
    if text.chars().any(|character| {
        matches!(
            character,
            '\u{000b}' | '\u{000c}' | '\u{001c}'
                ..='\u{001f}' | '\u{0085}' | '\u{2028}' | '\u{2029}'
        )
    }) || text
        .as_bytes()
        .iter()
        .enumerate()
        .any(|(index, byte)| *byte == b'\r' && text.as_bytes().get(index + 1) != Some(&b'\n'))
    {
        return None;
    }
    let lines = text.lines().map(str::to_owned).collect::<Vec<_>>();
    parse_gwl_records_for_python(&lines, permissive)
}

pub fn load_transfers(path: impl AsRef<Path>, well_rows: i64) -> Result<Vec<Transfer>, String> {
    let path = path.as_ref();
    let file = std::fs::File::open(path)
        .map_err(|error| format!("could not open transfer CSV: {error}"))?;
    parse_transfers(file, well_rows)
}

pub fn parse_transfers(reader: impl Read, well_rows: i64) -> Result<Vec<Transfer>, String> {
    let mut csv = csv::ReaderBuilder::new().flexible(true).from_reader(reader);
    let raw_headers = csv
        .headers()
        .map_err(|error| format!("could not read CSV header: {error}"))?
        .iter()
        .enumerate()
        .map(|(index, value)| {
            if index == 0 {
                value.trim_start_matches('\u{feff}').to_owned()
            } else {
                value.to_owned()
            }
        })
        .collect::<Vec<_>>();
    if raw_headers.is_empty() {
        return Err("CSV is empty or missing a header row.".to_owned());
    }

    let trimmed_headers = raw_headers
        .iter()
        .map(|name| name.trim().to_owned())
        .collect::<HashSet<_>>();
    let mut missing = REQUIRED_COLUMNS
        .iter()
        .filter(|name| !trimmed_headers.contains(**name))
        .copied()
        .collect::<Vec<_>>();
    missing.sort_unstable();
    if !missing.is_empty() {
        return Err(format!(
            "CSV missing required columns: {}",
            missing.join(", ")
        ));
    }

    // DictReader keeps the literal header spelling for row lookup; duplicate
    // headers resolve to the last field, matching Python's dict construction.
    let mut header_index = HashMap::new();
    for (index, name) in raw_headers.iter().enumerate() {
        header_index.insert(name.as_str(), index);
    }
    let mut transfers = Vec::new();
    for (row_index, row) in csv.records().enumerate() {
        let row =
            row.map_err(|error| format!("could not read CSV row {}: {error}", row_index + 2))?;
        let row_number = row_index + 2;
        let get = |name: &str| -> String {
            header_index
                .get(name)
                .and_then(|index| row.get(*index))
                .unwrap_or("")
                .trim()
                .to_owned()
        };
        let source_position = well_to_position(&get("source_position"), well_rows)
            .map_err(|error| format!("CSV row {row_number}: {error}"))?;
        let dest_position = well_to_position(&get("dest_position"), well_rows)
            .map_err(|error| format!("CSV row {row_number}: {error}"))?;
        let volume_text = get("volume_ul");
        let volume_ul = volume_text
            .parse::<f64>()
            .map_err(|_| format!("CSV row {row_number}: volume_ul must be numeric."))?;

        transfers.push(Transfer {
            source_label: get("source_label"),
            source_type: get("source_type"),
            source_position,
            dest_label: get("dest_label"),
            dest_type: get("dest_type"),
            dest_position,
            volume_ul,
            liquid_class: get("liquid_class"),
            source_id: get("source_id"),
            dest_id: get("dest_id"),
            source_tube_id: get("source_tube_id"),
            dest_tube_id: get("dest_tube_id"),
            tip_mask: get("tip_mask"),
            forced_source_type: get("forced_source_type"),
            forced_dest_type: get("forced_dest_type"),
            comment: get("comment"),
            wash_after: parse_optional_bool(&get("wash_after"))?,
            break_after: parse_bool(&get("break_after"))?,
            row_number,
        });
    }
    Ok(transfers)
}

pub fn well_to_position(value: &str, rows: i64) -> Result<u64, String> {
    let stripped = value.trim();
    if !stripped.is_empty() && stripped.chars().all(|character| character.is_ascii_digit()) {
        return stripped
            .parse::<u64>()
            .map_err(|_| format!("Invalid well/position value: {value:?}"));
    }

    let bytes = stripped.as_bytes();
    let mut split = 0;
    while split < bytes.len() && bytes[split].is_ascii_alphabetic() {
        split += 1;
    }
    if split == 0 || split == bytes.len() {
        return Err(format!("Invalid well/position value: {value:?}"));
    }
    let row_name = &stripped[..split];
    let column_text = &stripped[split..];
    if !column_text.as_bytes()[0].is_ascii_digit()
        || column_text.as_bytes()[0] == b'0'
        || !column_text.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(format!("Invalid well/position value: {value:?}"));
    }
    let mut row_index: u64 = 0;
    for character in row_name.bytes().map(|byte| byte.to_ascii_uppercase()) {
        if !character.is_ascii_uppercase() {
            return Err(format!("Invalid well/position value: {value:?}"));
        }
        row_index = row_index
            .checked_mul(26)
            .and_then(|index| index.checked_add(u64::from(character - b'A' + 1)))
            .ok_or_else(|| format!("Invalid well/position value: {value:?}"))?;
    }
    row_index -= 1;
    let column = column_text
        .parse::<u64>()
        .map_err(|_| format!("Invalid well/position value: {value:?}"))?;
    let inferred_rows = u64::try_from(rows.max(0))
        .unwrap_or(0)
        .max(row_index.saturating_add(1));
    column
        .checked_sub(1)
        .and_then(|value| value.checked_mul(inferred_rows))
        .and_then(|value| value.checked_add(row_index + 1))
        .ok_or_else(|| format!("Invalid well/position value: {value:?}"))
}

pub fn validate_transfers(transfers: &[Transfer], strict: bool) -> ValidationResult {
    let mut result = ValidationResult::default();
    if transfers.is_empty() {
        result.errors.push("No transfers found.".to_owned());
    }
    for transfer in transfers {
        let prefix = if transfer.row_number > 0 {
            format!("CSV row {}", transfer.row_number)
        } else {
            "Transfer".to_owned()
        };
        for (field, value) in [
            ("source_label", transfer.source_label.as_str()),
            ("source_type", transfer.source_type.as_str()),
            ("dest_label", transfer.dest_label.as_str()),
            ("dest_type", transfer.dest_type.as_str()),
        ] {
            if value.is_empty() {
                result
                    .errors
                    .push(format!("{prefix}: {field} is required."));
            }
        }
        if transfer.volume_ul <= 0.0 {
            result
                .errors
                .push(format!("{prefix}: volume_ul must be greater than zero."));
        }
        for (field, position) in [
            ("source_position", transfer.source_position),
            ("dest_position", transfer.dest_position),
        ] {
            if position < 1 {
                result
                    .errors
                    .push(format!("{prefix}: {field} must be >= 1."));
            }
        }
        for (field, value) in [
            ("source_label", transfer.source_label.as_str()),
            ("source_type", transfer.source_type.as_str()),
            ("dest_label", transfer.dest_label.as_str()),
            ("dest_type", transfer.dest_type.as_str()),
            ("liquid_class", transfer.liquid_class.as_str()),
        ] {
            if !value.is_empty() && value.chars().count() > 32 {
                result.warnings.push(format!(
                    "{prefix}: {field} is longer than 32 characters: {}.",
                    python_repr(value)
                ));
            }
        }
        if transfer.volume_ul > 1000.0 {
            result.warnings.push(format!(
                "{prefix}: volume_ul is high for a single transfer: {}.",
                format_general(transfer.volume_ul)
            ));
        }
        if let Some(code) = tip_selection_error(&transfer.tip_mask) {
            result.errors.push(format!(
                "{prefix}: [{code}] invalid record-level tip_mask {}.",
                python_repr(&transfer.tip_mask)
            ));
        }
    }
    if strict && !result.warnings.is_empty() {
        result.errors.extend(
            result
                .warnings
                .iter()
                .map(|warning| format!("Strict warning: {warning}")),
        );
    }
    result
}

pub fn build_worklist_schedule(
    transfers: &[Transfer],
    wash_policy: WashPolicy,
    batch_size: Option<usize>,
) -> Result<Vec<WorklistOperation>, String> {
    if batch_size == Some(0) {
        return Err("batch_size must be positive.".to_owned());
    }
    let mut schedule = Vec::new();
    for (index, transfer) in transfers.iter().enumerate() {
        if !transfer.comment.is_empty() {
            schedule.push(WorklistOperation {
                kind: "comment",
                transfer_index: index,
            });
        }
        schedule.push(WorklistOperation {
            kind: "aspirate",
            transfer_index: index,
        });
        schedule.push(WorklistOperation {
            kind: "dispense",
            transfer_index: index,
        });
        if transfer
            .wash_after
            .unwrap_or(wash_policy == WashPolicy::Each)
        {
            schedule.push(WorklistOperation {
                kind: "wash",
                transfer_index: index,
            });
        }
        if transfer.break_after || batch_size.is_some_and(|size| (index + 1) % size == 0) {
            schedule.push(WorklistOperation {
                kind: "break",
                transfer_index: index,
            });
        }
    }
    Ok(schedule)
}

pub fn build_worklist(
    transfers: &[Transfer],
    name: &str,
    wash_policy: WashPolicy,
    batch_size: Option<usize>,
) -> Result<Worklist, String> {
    let schedule = build_worklist_schedule(transfers, wash_policy, batch_size)?;
    let mut worklist = Worklist {
        name: name.to_owned(),
        ..Worklist::default()
    };
    for operation in schedule {
        let transfer = transfers
            .get(operation.transfer_index)
            .ok_or_else(|| "worklist_schedule_transfer_index_invalid".to_owned())?;
        match operation.kind {
            "comment" => push_record(
                &mut worklist,
                format!("C;{}", transfer.comment.replace('\n', "\\n")),
                "C",
            ),
            "aspirate" => push_record(
                &mut worklist,
                PipetteRecord {
                    operation: 'A',
                    rack_label: &transfer.source_label,
                    rack_id: &transfer.source_id,
                    rack_type: &transfer.source_type,
                    position: transfer.source_position,
                    tube_id: &transfer.source_tube_id,
                    volume: transfer.volume_ul,
                    liquid_class: &transfer.liquid_class,
                    tip_mask: &transfer.tip_mask,
                    forced_rack_type: &transfer.forced_source_type,
                }
                .to_line(),
                "A",
            ),
            "dispense" => push_record(
                &mut worklist,
                PipetteRecord {
                    operation: 'D',
                    rack_label: &transfer.dest_label,
                    rack_id: &transfer.dest_id,
                    rack_type: &transfer.dest_type,
                    position: transfer.dest_position,
                    tube_id: &transfer.dest_tube_id,
                    volume: transfer.volume_ul,
                    liquid_class: &transfer.liquid_class,
                    tip_mask: &transfer.tip_mask,
                    forced_rack_type: &transfer.forced_dest_type,
                }
                .to_line(),
                "D",
            ),
            "wash" => push_record(&mut worklist, "W;".to_owned(), "W"),
            "break" => push_record(&mut worklist, "B;".to_owned(), "B"),
            _ => return Err("worklist_schedule_operation_invalid".to_owned()),
        }
    }
    Ok(worklist)
}

pub fn total_volume(transfers: &[Transfer]) -> f64 {
    transfers.iter().map(|transfer| transfer.volume_ul).sum()
}

fn push_record(worklist: &mut Worklist, line: String, record_type: &str) {
    worklist.lines.push(line);
    if !worklist.counts.contains_key(record_type) {
        worklist.count_order.push(record_type.to_owned());
    }
    *worklist.counts.entry(record_type.to_owned()).or_default() += 1;
}

struct PipetteRecord<'a> {
    operation: char,
    rack_label: &'a str,
    rack_id: &'a str,
    rack_type: &'a str,
    position: u64,
    tube_id: &'a str,
    volume: f64,
    liquid_class: &'a str,
    tip_mask: &'a str,
    forced_rack_type: &'a str,
}

impl PipetteRecord<'_> {
    fn to_line(&self) -> String {
        format!(
            "{};{};{};{};{};{};{};{};;{};{}",
            self.operation,
            self.rack_label,
            self.rack_id,
            self.rack_type,
            self.position,
            self.tube_id,
            format_volume(self.volume),
            self.liquid_class,
            self.tip_mask,
            self.forced_rack_type,
        )
    }
}

pub fn format_volume(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_owned();
    }
    if value == f64::INFINITY {
        return "inf".to_owned();
    }
    if value == f64::NEG_INFINITY {
        return "-inf".to_owned();
    }
    if value == 0.0 {
        return "0".to_owned();
    }
    if value.fract() == 0.0 {
        return format!("{value:.0}");
    }
    let mut text = format!("{value:.6}");
    while text.contains('.') && text.ends_with('0') {
        text.pop();
    }
    if text.ends_with('.') {
        text.pop();
    }
    text
}

fn format_general(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_owned();
    }
    if value == f64::INFINITY {
        return "inf".to_owned();
    }
    if value == f64::NEG_INFINITY {
        return "-inf".to_owned();
    }
    if value == 0.0 {
        return "0".to_owned();
    }
    let exponent = value.abs().log10().floor() as i32;
    if !(-4..6).contains(&exponent) {
        let scientific = format!("{value:.5e}");
        let (mantissa, exponent_text) = scientific.split_once('e').unwrap_or((&scientific, "0"));
        let exponent_value = exponent_text.parse::<i32>().unwrap_or(0);
        format!(
            "{}e{exponent_value:+03}",
            trim_decimal_zeros(mantissa.to_owned())
        )
    } else {
        let decimals = (5 - exponent).max(0) as usize;
        trim_decimal_zeros(format!("{value:.decimals$}"))
    }
}

fn trim_decimal_zeros(mut value: String) -> String {
    while value.contains('.') && value.ends_with('0') {
        value.pop();
    }
    if value.ends_with('.') {
        value.pop();
    }
    value
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

fn parse_optional_bool(value: &str) -> Result<Option<bool>, String> {
    if value.is_empty() {
        return Ok(None);
    }
    parse_bool(value).map(Some)
}

fn parse_bool(value: &str) -> Result<bool, String> {
    if value.is_empty() {
        return Ok(false);
    }
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "y" => Ok(true),
        "0" | "false" | "no" | "n" => Ok(false),
        _ => Err(format!("Invalid boolean value: {value:?}")),
    }
}

fn tip_selection_error(raw: &str) -> Option<&'static str> {
    let text = raw.trim();
    if text.is_empty() {
        return None;
    }
    let value = match text.parse::<u64>() {
        Ok(value) => value,
        Err(_) => return Some("tip_mask_not_integer"),
    };
    if value == 0 {
        return Some("tip_mask_non_positive");
    }
    if value.count_ones() != 1 {
        return Some("tip_mask_not_one_hot");
    }
    if value.trailing_zeros() >= 8 {
        return Some("tip_mask_channel_out_of_range");
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn well_positions_match_96_well_column_major_layout() {
        assert_eq!(well_to_position("A1", 8).unwrap(), 1);
        assert_eq!(well_to_position("B1", 8).unwrap(), 2);
        assert_eq!(well_to_position("H1", 8).unwrap(), 8);
        assert_eq!(well_to_position("A2", 8).unwrap(), 9);
        assert_eq!(well_to_position("H12", 8).unwrap(), 96);
        assert_eq!(well_to_position("A2", 16).unwrap(), 17);
    }

    #[test]
    fn strict_gwl_parser_counts_supported_records_in_source_order() {
        let text = "A;src;;plate;1;;20;lc;;1;\nD;dst;;plate;2;;20;lc;;1;\nW;\nC;note;detail\nB;\nF;opaque\nS;setting;value\n";
        let worklist = parse_gwl_text(text, "sample").unwrap();
        assert_eq!(worklist.counts.get("A"), Some(&1));
        assert_eq!(worklist.counts.get("D"), Some(&1));
        assert_eq!(worklist.counts.get("W"), Some(&1));
        assert_eq!(worklist.counts.get("C"), Some(&1));
        assert_eq!(worklist.counts.get("B"), Some(&1));
        assert_eq!(worklist.count_order, ["A", "D", "W", "C", "B", "F", "S"]);
        assert_eq!(
            worklist.counts_repr(),
            "{'A': 1, 'D': 1, 'W': 1, 'C': 1, 'B': 1, 'F': 1, 'S': 1}"
        );
    }

    #[test]
    fn native_gwl_text_record_parser_matches_supported_line_boundaries() {
        let records =
            parse_gwl_text_records_for_python("C;first\r\nC;second\nW;\n", false).unwrap();
        assert_eq!(records.len(), 3);
        assert_eq!(records[0]["text"], "first");
        assert_eq!(records[1]["text"], "second");
        assert_eq!(records[2]["kind"], "wash");
        assert!(
            parse_gwl_text_records_for_python("", false)
                .unwrap()
                .is_empty()
        );
        assert!(parse_gwl_text_records_for_python("C;first\u{000b}C;second", false).is_none());
        assert!(parse_gwl_text_records_for_python("C;first\rC;second", false).is_none());
        assert!(parse_gwl_text_records_for_python("C;first\u{2028}C;second", false).is_none());
        assert_eq!(
            parse_gwl_text_records_for_python("C;naïve", false).unwrap()[0]["text"],
            "naïve"
        );
        assert!(parse_gwl_text_records_for_python("ſ;opaque", true).is_none());
    }

    #[test]
    fn strict_gwl_parser_reports_record_shape_and_unknown_types() {
        assert_eq!(
            parse_gwl_text("A;src", "sample").unwrap_err(),
            "Line 1: pipette record has 2 fields, expected 11."
        );
        assert_eq!(
            parse_gwl_text("  \nW5;", "sample").unwrap_err(),
            "Line 2: unsupported record type 'W5'."
        );
    }

    #[test]
    fn parses_csv_and_generates_expected_gwl_records() {
        let input = "source_label,source_type,source_position,dest_label,dest_type,dest_position,volume_ul,liquid_class,comment\nsource,plate,A1,destination,plate,B1,20,synthetic-liquid,synthetic transfer\n";
        let transfers = parse_transfers(Cursor::new(input), 8).unwrap();
        let validation = validate_transfers(&transfers, false);
        assert!(validation.ok(), "{:?}", validation.errors);
        let worklist = build_worklist(&transfers, "output", WashPolicy::Each, Some(1)).unwrap();
        assert_eq!(worklist.counts.get("A"), Some(&1));
        assert_eq!(worklist.counts.get("D"), Some(&1));
        assert_eq!(worklist.counts.get("W"), Some(&1));
        assert_eq!(worklist.counts.get("B"), Some(&1));
        assert_eq!(
            worklist.to_text(),
            "C;synthetic transfer\nA;source;;plate;1;;20;synthetic-liquid;;;\nD;destination;;plate;2;;20;synthetic-liquid;;;\nW;\nB;\n"
        );
    }

    #[test]
    fn csv_bom_quoted_fields_and_optional_boolean_match_contract() {
        let input = "\u{feff}source_label,source_type,source_position,dest_label,dest_type,dest_position,volume_ul,comment,wash_after\n\"Source, A\",Plate,A1,Dest,Plate,B1,10,\"quoted, comment\",no\n";
        let transfers = parse_transfers(Cursor::new(input), 8).unwrap();
        assert_eq!(transfers[0].source_label, "Source, A");
        assert_eq!(transfers[0].comment, "quoted, comment");
        assert_eq!(transfers[0].wash_after, Some(false));
        assert_eq!(transfers[0].source_position, 1);
    }

    #[test]
    fn worklist_volume_format_matches_python_fixed_precision_rules() {
        assert_eq!(format_volume(-0.0), "0");
        assert_eq!(format_volume(f64::NAN), "nan");
        assert_eq!(format_volume(f64::INFINITY), "inf");
        assert_eq!(format_volume(f64::NEG_INFINITY), "-inf");
        assert_eq!(format_volume(1.2345675), "1.234568");
        assert_eq!(format_volume(-0.0000001), "-0");
        assert_eq!(format_volume(1_000_000_000_000_000.0), "1000000000000000");
    }

    #[test]
    fn python_repr_and_general_float_match_cli_diagnostics() {
        assert_eq!(python_repr("plain"), "'plain'");
        assert_eq!(python_repr("has 'single'"), "\"has 'single'\"");
        assert_eq!(python_repr("line\nbreak"), "'line\\nbreak'");
        assert_eq!(format_general(1200.0), "1200");
        assert_eq!(format_general(1_200_000.0), "1.2e+06");
    }

    #[test]
    fn strict_mode_promotes_warnings_without_discarding_them() {
        let long_source_type = "P".repeat(33);
        let transfer = Transfer {
            source_label: "S".to_owned(),
            source_type: long_source_type.clone(),
            source_position: 1,
            dest_label: "D".to_owned(),
            dest_type: "Plate".to_owned(),
            dest_position: 1,
            volume_ul: 1200.0,
            liquid_class: String::new(),
            source_id: String::new(),
            dest_id: String::new(),
            source_tube_id: String::new(),
            dest_tube_id: String::new(),
            tip_mask: String::new(),
            forced_source_type: String::new(),
            forced_dest_type: String::new(),
            comment: String::new(),
            wash_after: None,
            break_after: false,
            row_number: 2,
        };
        let result = validate_transfers(&[transfer], true);
        assert_eq!(
            result.warnings,
            vec![
                format!(
                    "CSV row 2: source_type is longer than 32 characters: '{long_source_type}'."
                ),
                "CSV row 2: volume_ul is high for a single transfer: 1200.".to_owned(),
            ]
        );
        assert_eq!(
            result.errors,
            result
                .warnings
                .iter()
                .map(|warning| format!("Strict warning: {warning}"))
                .collect::<Vec<_>>()
        );
    }
}
