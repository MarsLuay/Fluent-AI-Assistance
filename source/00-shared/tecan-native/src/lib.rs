use pyo3::exceptions::{PyOSError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyModule, PyString, PyTuple};
use quick_xml::events::{BytesStart, Event};
use quick_xml::name::ResolveResult;
use quick_xml::reader::NsReader;
use rayon::prelude::*;
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

struct ElementFrame {
    node: Py<PyAny>,
    last_child: Option<Py<PyAny>>,
}

fn py_error(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

fn is_xml10_char(character: char) -> bool {
    let code = character as u32;
    matches!(code, 0x9 | 0xA | 0xD)
        || (0x20..=0xD7FF).contains(&code)
        || (0xE000..=0xFFFD).contains(&code)
        || (0x10000..=0x10FFFF).contains(&code)
}

fn validate_xml10_chars(value: &str) -> Result<(), String> {
    if value.chars().all(is_xml10_char) {
        Ok(())
    } else {
        Err("XML content contains a character forbidden by XML 1.0".to_owned())
    }
}

fn utf8_name(bytes: &[u8]) -> Result<&str, String> {
    std::str::from_utf8(bytes).map_err(|error| format!("XML name is not UTF-8: {error}"))
}

fn expanded_name(resolve: ResolveResult<'_>, local: &[u8]) -> Result<String, String> {
    let local = utf8_name(local)?;
    match resolve {
        ResolveResult::Unbound => Ok(local.to_owned()),
        ResolveResult::Bound(namespace) => {
            let uri = utf8_name(namespace.as_ref())?;
            if uri.is_empty() {
                Ok(local.to_owned())
            } else {
                Ok(format!("{{{uri}}}{local}"))
            }
        }
        ResolveResult::Unknown(prefix) => Err(format!(
            "XML name uses an undeclared namespace prefix {:?}",
            String::from_utf8_lossy(&prefix)
        )),
    }
}

fn make_element<'py>(
    py: Python<'py>,
    factory: &Bound<'py, PyAny>,
    reader: &NsReader<&[u8]>,
    tag: String,
    start: &BytesStart<'_>,
) -> PyResult<Bound<'py, PyAny>> {
    let attributes = PyDict::new(py);
    for attribute in start.attributes().with_checks(true) {
        let attribute = attribute.map_err(|error| py_error(error.to_string()))?;
        let raw_name = attribute.key.as_ref();
        let decoded = reader
            .decoder()
            .decode(attribute.value.as_ref())
            .map_err(|error| py_error(error.to_string()))?;
        let normalized = decoded
            .replace("\r\n", "\n")
            .replace('\r', "\n")
            .replace(['\n', '\t'], " ");
        let value = quick_xml::escape::unescape(&normalized)
            .map_err(|error| py_error(error.to_string()))?;
        validate_xml10_chars(value.as_ref()).map_err(py_error)?;
        // ElementTree's default parser does not expose namespace declarations in attrib.
        if raw_name == b"xmlns" || raw_name.starts_with(b"xmlns:") {
            continue;
        }
        let (namespace, local) = reader.resolver().resolve_attribute(attribute.key);
        let key = expanded_name(namespace, local.as_ref()).map_err(py_error)?;
        attributes.set_item(key, value.as_ref())?;
    }
    factory.call1((tag, attributes))
}

fn append_text(py: Python<'_>, stack: &[ElementFrame], text: &str) -> PyResult<()> {
    validate_xml10_chars(text).map_err(py_error)?;
    if text.is_empty() {
        return Ok(());
    }
    let Some(frame) = stack.last() else {
        if text
            .chars()
            .all(|character| matches!(character, ' ' | '\t' | '\n' | '\r'))
        {
            return Ok(());
        }
        return Err(py_error(
            "character data is outside the XML document element",
        ));
    };
    let (target, field) = match &frame.last_child {
        Some(child) => (child.bind(py), "tail"),
        None => (frame.node.bind(py), "text"),
    };
    let previous = target.getattr(field)?;
    if previous.is_none() {
        target.setattr(field, text)?;
    } else {
        let mut combined = previous.extract::<String>()?;
        combined.push_str(text);
        target.setattr(field, combined)?;
    }
    Ok(())
}

fn create_child<'py>(
    py: Python<'py>,
    factory: &Bound<'py, PyAny>,
    reader: &NsReader<&[u8]>,
    tag: String,
    start: &BytesStart<'_>,
    stack: &mut [ElementFrame],
    root: &mut Option<Py<PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    let child = make_element(py, factory, reader, tag, start)?;
    if let Some(parent) = stack.last_mut() {
        parent
            .node
            .bind(py)
            .call_method1("append", (child.clone(),))?;
        parent.last_child = Some(child.clone().unbind());
    } else if root.is_none() {
        *root = Some(child.clone().unbind());
    } else {
        return Err(py_error("XML document has more than one document element"));
    }
    Ok(child)
}

fn append_reference(reference: &quick_xml::events::BytesRef<'_>) -> Result<String, String> {
    if let Some(character) = reference
        .resolve_char_ref()
        .map_err(|error| error.to_string())?
    {
        let code = character as u32;
        let legal = matches!(code, 0x9 | 0xA | 0xD)
            || (0x20..=0xD7FF).contains(&code)
            || (0xE000..=0xFFFD).contains(&code)
            || (0x10000..=0x10FFFF).contains(&code);
        if !legal {
            return Err("XML character reference is not legal in XML 1.0".to_owned());
        }
        return Ok(character.to_string());
    }
    match reference
        .decode()
        .map_err(|error| error.to_string())?
        .as_ref()
    {
        "amp" => Ok("&".to_owned()),
        "lt" => Ok("<".to_owned()),
        "gt" => Ok(">".to_owned()),
        "apos" => Ok("'".to_owned()),
        "quot" => Ok("\"".to_owned()),
        _ => Err("DTD and custom XML entity references are disabled".to_owned()),
    }
}

#[pyfunction]
fn parse_xml(
    py: Python<'_>,
    data: Vec<u8>,
    max_bytes: i64,
    max_depth: i64,
) -> PyResult<Bound<'_, PyAny>> {
    if max_bytes >= 0 && data.len() > max_bytes as usize {
        return Err(py_error(format!(
            "XML document exceeds the safe size limit of {max_bytes} bytes"
        )));
    }
    if max_depth <= 0 {
        return Err(py_error("XML nesting depth limit must be positive"));
    }

    let element_tree = py.import("xml.etree.ElementTree")?;
    let element_factory = element_tree.getattr("Element")?;
    let mut reader = NsReader::from_reader(data.as_slice());
    reader.config_mut().trim_text(false);
    let mut buffer = Vec::new();
    let mut stack: Vec<ElementFrame> = Vec::new();
    let mut root: Option<Py<PyAny>> = None;

    loop {
        let (resolved, event) = reader
            .read_resolved_event_into(&mut buffer)
            .map_err(|error| py_error(error.to_string()))?;
        match event {
            Event::Start(start) => {
                let tag = expanded_name(resolved, start.local_name().as_ref()).map_err(py_error)?;
                if stack.len() >= max_depth as usize {
                    return Err(py_error(format!(
                        "XML nesting depth exceeds the safe limit of {max_depth} levels"
                    )));
                }
                let child = create_child(
                    py,
                    &element_factory,
                    &reader,
                    tag,
                    &start,
                    &mut stack,
                    &mut root,
                )?;
                stack.push(ElementFrame {
                    node: child.unbind(),
                    last_child: None,
                });
            }
            Event::Empty(start) => {
                if stack.len() >= max_depth as usize {
                    return Err(py_error(format!(
                        "XML nesting depth exceeds the safe limit of {max_depth} levels"
                    )));
                }
                let tag = expanded_name(resolved, start.local_name().as_ref()).map_err(py_error)?;
                create_child(
                    py,
                    &element_factory,
                    &reader,
                    tag,
                    &start,
                    &mut stack,
                    &mut root,
                )?;
            }
            Event::End(_) => {
                if stack.pop().is_none() {
                    return Err(py_error("unexpected XML closing tag"));
                }
            }
            Event::Text(text) => {
                let value = text
                    .xml_content()
                    .map_err(|error| py_error(error.to_string()))?;
                append_text(py, &stack, value.as_ref())?;
            }
            Event::CData(text) => {
                let value = text
                    .xml_content()
                    .map_err(|error| py_error(error.to_string()))?;
                append_text(py, &stack, value.as_ref())?;
            }
            Event::GeneralRef(reference) => {
                let value = append_reference(&reference).map_err(py_error)?;
                append_text(py, &stack, &value)?;
            }
            Event::DocType(_) => {
                return Err(py_error("DTD declarations are disabled"));
            }
            Event::Eof => break,
            Event::Comment(comment) => {
                let content = comment
                    .xml_content()
                    .map_err(|error| py_error(error.to_string()))?;
                validate_xml10_chars(content.as_ref()).map_err(py_error)?;
            }
            Event::PI(_) => {
                return Err(py_error(
                    "processing instructions use the Python XML compatibility parser",
                ));
            }
            Event::Decl(_) => {}
        }
        buffer.clear();
    }

    if !stack.is_empty() {
        return Err(py_error(
            "XML document ended before all elements were closed",
        ));
    }
    root.map(|node| node.into_bound(py))
        .ok_or_else(|| py_error("XML document is empty"))
}

fn catalog_groups(include_all_connectors: bool) -> Vec<(&'static str, &'static str)> {
    let mut groups = vec![
        ("SystemSpecific/Worktable/Components", "xcmp"),
        ("SystemSpecific/Worktable/Workspaces", "xwsp"),
        ("SystemSpecific/Worktable/Sites", "xsit"),
        ("SystemSpecific/LiquidClasses", "xlqc"),
    ];
    if include_all_connectors {
        groups.push(("SystemSpecific/Worktable/Connectors", "xcon"));
    }
    groups
}

fn extension_matches(path: &Path, wanted: &str) -> bool {
    let Some(extension) = path.extension().and_then(|value| value.to_str()) else {
        return false;
    };
    #[cfg(windows)]
    {
        extension.eq_ignore_ascii_case(wanted)
    }
    #[cfg(not(windows))]
    {
        extension == wanted
    }
}

fn catalog_paths(install: &Path, include_all_connectors: bool) -> std::io::Result<Vec<String>> {
    let mut paths = Vec::new();
    for (relative_directory, extension) in catalog_groups(include_all_connectors) {
        let directory = install.join(relative_directory);
        if !directory.exists() {
            continue;
        }
        let mut group_paths = Vec::new();
        for entry in fs::read_dir(&directory)? {
            let entry = entry?;
            let path = entry.path();
            if extension_matches(&path, extension) {
                let name = entry.file_name().to_string_lossy().into_owned();
                group_paths.push(format!("{relative_directory}/{name}"));
            }
        }
        group_paths.sort();
        paths.extend(group_paths);
    }
    Ok(paths)
}

fn content_fingerprint(install: &Path, relative: &str) -> std::io::Result<String> {
    let path = install.join(relative);
    let size = fs::metadata(&path)?.len().to_string();
    let mut digest = Sha256::new();
    digest.update(b"fluentcoder.catalog.content.v1");
    digest.update(relative.as_bytes());
    digest.update(size.as_bytes());
    let mut file = File::open(path)?;
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut chunk)?;
        if count == 0 {
            break;
        }
        digest.update(&chunk[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

#[pyfunction]
fn catalog_source_paths(install_path: &str, include_all_connectors: bool) -> PyResult<Vec<String>> {
    catalog_paths(Path::new(install_path), include_all_connectors)
        .map_err(|error| PyOSError::new_err(error.to_string()))
}

#[pyfunction]
fn catalog_stat_fingerprint(install_path: &str, include_all_connectors: bool) -> PyResult<String> {
    let install = Path::new(install_path);
    let paths = catalog_paths(install, include_all_connectors)
        .map_err(|error| PyOSError::new_err(error.to_string()))?;
    let metadata: Vec<Option<u64>> = paths
        .par_iter()
        .map(|relative| {
            fs::metadata(install.join(relative))
                .ok()
                .map(|meta| meta.len())
        })
        .collect();

    let mut digest = Sha256::new();
    digest.update(b"fluentcoder.install.stat.v1");
    digest.update(if include_all_connectors {
        &b"connectors:all"[..]
    } else {
        &b"connectors:site"[..]
    });
    for (relative, size) in paths.iter().zip(metadata) {
        if let Some(size) = size {
            digest.update(relative.as_bytes());
            digest.update(size.to_string().as_bytes());
        }
    }
    Ok(format!("{:x}", digest.finalize()))
}

#[pyfunction]
fn source_content_fingerprint(path: &str, install_path: &str) -> PyResult<String> {
    let path = Path::new(path);
    let install = Path::new(install_path);
    let relative = path
        .strip_prefix(install)
        .map_err(|error| PyValueError::new_err(error.to_string()))?
        .to_string_lossy()
        .replace('\\', "/");
    content_fingerprint(install, &relative).map_err(|error| PyOSError::new_err(error.to_string()))
}

#[pyfunction]
fn catalog_content_fingerprints(
    install_path: &str,
    include_all_connectors: bool,
) -> PyResult<Vec<(String, String)>> {
    let install = PathBuf::from(install_path);
    let paths = catalog_paths(&install, include_all_connectors)
        .map_err(|error| PyOSError::new_err(error.to_string()))?;
    let fingerprints: std::io::Result<Vec<_>> = paths
        .par_iter()
        .map(|relative| {
            content_fingerprint(&install, relative)
                .map(|fingerprint| (relative.clone(), fingerprint))
        })
        .collect();
    fingerprints.map_err(|error| PyOSError::new_err(error.to_string()))
}

fn clone_json_value<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    memo: &mut HashMap<usize, Py<PyAny>>,
    active: &mut HashSet<usize>,
    depth: usize,
) -> PyResult<Bound<'py, PyAny>> {
    if depth > 1024 {
        return Err(PyValueError::new_err(
            "data nesting exceeds native copy limit",
        ));
    }
    if value.is_none()
        || value.is_instance_of::<PyBool>()
        || value.is_instance_of::<PyInt>()
        || value.is_instance_of::<PyFloat>()
        || value.is_instance_of::<PyString>()
        || value.is_instance_of::<PyBytes>()
    {
        return Ok(value.clone());
    }

    let pointer = value.as_ptr() as usize;
    if active.contains(&pointer) {
        return Err(PyValueError::new_err(
            "cyclic data requires Python deepcopy fallback",
        ));
    }
    if let Some(copied) = memo.get(&pointer) {
        return Ok(copied.clone_ref(py).into_bound(py));
    }

    if let Ok(items) = value.cast::<PyList>() {
        let output = PyList::empty(py);
        memo.insert(pointer, output.clone().into_any().unbind());
        active.insert(pointer);
        for item in items.iter() {
            output.append(clone_json_value(py, &item, memo, active, depth + 1)?)?;
        }
        active.remove(&pointer);
        return Ok(output.into_any());
    }
    if let Ok(items) = value.cast::<PyDict>() {
        let output = PyDict::new(py);
        memo.insert(pointer, output.clone().into_any().unbind());
        active.insert(pointer);
        for (key, item) in items.iter() {
            if !(key.is_none()
                || key.is_instance_of::<PyBool>()
                || key.is_instance_of::<PyInt>()
                || key.is_instance_of::<PyFloat>()
                || key.is_instance_of::<PyString>()
                || key.is_instance_of::<PyBytes>()
                || key.is_instance_of::<PyTuple>())
            {
                return Err(PyTypeError::new_err(
                    "custom dictionary keys require Python deepcopy fallback",
                ));
            }
            let copied = clone_json_value(py, &item, memo, active, depth + 1)?;
            output.set_item(key, copied)?;
        }
        active.remove(&pointer);
        return Ok(output.into_any());
    }
    if let Ok(items) = value.cast::<PyTuple>() {
        active.insert(pointer);
        let mut copied_items = Vec::with_capacity(items.len());
        for item in items.iter() {
            copied_items.push(clone_json_value(py, &item, memo, active, depth + 1)?);
        }
        active.remove(&pointer);
        let output = PyTuple::new(py, copied_items)?;
        memo.insert(pointer, output.clone().into_any().unbind());
        return Ok(output.into_any());
    }
    Err(PyTypeError::new_err(format!(
        "unsupported data for native copy: {}",
        value.get_type().name()?
    )))
}

#[pyfunction]
fn deepcopy_json<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    clone_json_value(py, value, &mut HashMap::new(), &mut HashSet::new(), 0)
}

fn py_to_json(value: &Bound<'_, PyAny>) -> PyResult<Value> {
    if value.is_none() {
        return Ok(Value::Null);
    }
    if value.is_instance_of::<PyBool>() {
        return Ok(Value::Bool(value.extract::<bool>()?));
    }
    if value.is_instance_of::<PyInt>() {
        if let Ok(integer) = value.extract::<i64>() {
            return Ok(Value::Number(Number::from(integer)));
        }
        if let Ok(integer) = value.extract::<u64>() {
            return Ok(Value::Number(Number::from(integer)));
        }
        return Err(PyTypeError::new_err(
            "integer is outside the JSON 64-bit range",
        ));
    }
    if value.is_instance_of::<PyFloat>() {
        let float = value.extract::<f64>()?;
        if float != 0.0 && (float.abs() < 1e-4 || float.abs() >= 1e16) {
            return Err(PyTypeError::new_err(
                "scientific floats require Python JSON formatting fallback",
            ));
        }
        let number = Number::from_f64(float).ok_or_else(|| {
            PyValueError::new_err("non-finite floats require Python JSON fallback")
        })?;
        return Ok(Value::Number(number));
    }
    if let Ok(text) = value.extract::<String>() {
        return Ok(Value::String(text));
    }
    if let Ok(items) = value.cast::<PyList>() {
        return items
            .iter()
            .map(|item| py_to_json(&item))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if let Ok(items) = value.cast::<PyTuple>() {
        return items
            .iter()
            .map(|item| py_to_json(&item))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if let Ok(items) = value.cast::<PyDict>() {
        let mut result = Map::new();
        for (key, item) in items.iter() {
            let key = key.extract::<String>()?;
            result.insert(key, py_to_json(&item)?);
        }
        return Ok(Value::Object(result));
    }
    Err(PyTypeError::new_err(format!(
        "unsupported value for native JSON serialization: {}",
        value.get_type().name()?
    )))
}

fn python_json_spacing(compact: &str) -> String {
    let mut output = String::with_capacity(compact.len() + compact.len() / 8);
    let mut in_string = false;
    let mut escaped = false;
    for character in compact.chars() {
        if in_string {
            output.push(character);
            if escaped {
                escaped = false;
            } else if character == '\\' {
                escaped = true;
            } else if character == '"' {
                in_string = false;
            }
            continue;
        }
        if character == '"' {
            in_string = true;
        }
        output.push(character);
        if character == ',' || character == ':' {
            output.push(' ');
        }
    }
    output
}

#[pyfunction]
fn canonical_json(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let json = py_to_json(value)?;
    let compact =
        serde_json::to_string(&json).map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(python_json_spacing(&compact))
}

#[pyfunction]
fn command_short_name(type_name: &str) -> Option<String> {
    if !type_name.is_ascii() {
        return None;
    }
    let short = type_name.rsplit('.').next().unwrap_or(type_name);
    let Some(suffix_start) = short.rfind("DataV") else {
        return Some(short.to_owned());
    };
    let digits = &short[suffix_start + "DataV".len()..];
    if !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit()) {
        Some(short[..suffix_start].to_owned())
    } else {
        Some(short.to_owned())
    }
}

#[pyfunction]
fn compact_list(values: Vec<String>, limit: i64) -> Vec<String> {
    let mut output = Vec::new();
    for value in values {
        if !value.is_empty() && !output.contains(&value) {
            output.push(value);
        }
        let reached_limit = usize::try_from(limit)
            .map(|bound| output.len() >= bound)
            .unwrap_or(false);
        if limit <= 0 || reached_limit {
            break;
        }
    }
    output
}

#[pyfunction]
fn worklist_execution_contract(commands_json: &str) -> PyResult<String> {
    let commands: Value = serde_json::from_str(commands_json)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let commands = commands
        .as_array()
        .ok_or_else(|| PyValueError::new_err("worklist commands must be a JSON array"))?;
    let contracts = tecan_worklist_builder::contracts::build_execution_contract(commands)
        .map_err(PyValueError::new_err)?;
    serde_json::to_string(&contracts).map_err(|error| PyValueError::new_err(error.to_string()))
}

#[pyfunction]
fn worklist_load_contract(
    fields_json: &str,
    command_id: &str,
    command_version: &str,
    provenance_json: &str,
) -> PyResult<String> {
    let fields: Value = serde_json::from_str(fields_json)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let provenance: Value = serde_json::from_str(provenance_json)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let provenance = (!provenance.is_null()).then_some(&provenance);
    let contract = tecan_worklist_builder::contracts::normalize_load_worklist(
        &fields,
        command_id,
        command_version,
        provenance,
    )
    .map_err(PyValueError::new_err)?;
    serde_json::to_string(&contract).map_err(|error| PyValueError::new_err(error.to_string()))
}

#[pyfunction]
fn worklist_parse_tip_selection(
    raw: &str,
    supported_channels: Option<usize>,
) -> PyResult<Option<String>> {
    let Some(selection) =
        tecan_worklist_builder::semantics::parse_tip_selection_json(raw, supported_channels)
    else {
        return Ok(None);
    };
    Ok(serde_json::to_string(&selection).ok())
}

#[pyfunction]
fn worklist_parse_enabled_tip_mask(
    value: &str,
    supported_channels: usize,
) -> PyResult<Option<String>> {
    let Ok(Some(mask)) =
        tecan_worklist_builder::semantics::parse_enabled_tip_mask(Some(value), supported_channels)
    else {
        return Ok(None);
    };
    Ok(Some(mask.to_string()))
}

#[pyfunction]
fn worklist_well_to_position(value: &str, rows: i64) -> PyResult<Option<u64>> {
    Ok(tecan_worklist_builder::well_to_position(value, rows).ok())
}

#[pyfunction]
fn worklist_load_transfers(path: &str, well_rows: i64) -> PyResult<Option<String>> {
    let Ok(transfers) = tecan_worklist_builder::load_transfers(path, well_rows) else {
        return Ok(None);
    };
    if transfers
        .iter()
        .any(|transfer| !transfer.volume_ul.is_finite())
    {
        return Ok(None);
    }
    Ok(serde_json::to_string(&transfers).ok())
}

#[pyfunction]
fn worklist_parse_gwl(path: &str) -> PyResult<Option<String>> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return Ok(None);
    };
    if text.chars().any(|character| {
        matches!(
            character,
            '\u{000b}'
                | '\u{000c}'
                | '\u{001c}'
                | '\u{001d}'
                | '\u{001e}'
                | '\u{0085}'
                | '\u{2028}'
                | '\u{2029}'
        )
    }) || text
        .as_bytes()
        .iter()
        .enumerate()
        .any(|(index, byte)| *byte == b'\r' && text.as_bytes().get(index + 1) != Some(&b'\n'))
    {
        return Ok(None);
    }
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let Ok(worklist) = tecan_worklist_builder::parse_gwl_text(text, "worklist") else {
        return Ok(None);
    };
    let payload = serde_json::json!({
        "counts": worklist.counts,
        "count_order": worklist.count_order,
    });
    Ok(serde_json::to_string(&payload).ok())
}

#[pyfunction]
fn worklist_parse_gwl_records(lines_json: &str, permissive: bool) -> PyResult<Option<String>> {
    let Ok(lines) = serde_json::from_str::<Vec<String>>(lines_json) else {
        return Ok(None);
    };
    let Some(records) = tecan_worklist_builder::parse_gwl_records_for_python(&lines, permissive)
    else {
        return Ok(None);
    };
    Ok(serde_json::to_string(&records).ok())
}

#[pyfunction]
fn worklist_parse_gwl_text_records(text: &str, permissive: bool) -> PyResult<Option<String>> {
    let Some(records) = tecan_worklist_builder::parse_gwl_text_records_for_python(text, permissive)
    else {
        return Ok(None);
    };
    Ok(serde_json::to_string(&records).ok())
}

#[pyfunction]
fn worklist_serialize_gwl_lines(lines_json: &str) -> PyResult<Option<String>> {
    let Ok(lines) = serde_json::from_str::<Vec<String>>(lines_json) else {
        return Ok(None);
    };
    Ok(Some(tecan_worklist_builder::serialize_gwl_lines(&lines)))
}

#[pyfunction]
fn worklist_count_record_types(types_json: &str) -> PyResult<Option<String>> {
    let Ok(types) = serde_json::from_str::<Vec<String>>(types_json) else {
        return Ok(None);
    };
    let counts = tecan_worklist_builder::count_record_types_in_order(&types);
    Ok(serde_json::to_string(&counts).ok())
}

#[pyfunction]
fn worklist_build_transfers(
    transfers_json: &str,
    name: &str,
    wash_policy: &str,
    batch_size: Option<usize>,
) -> PyResult<Option<String>> {
    let Ok(transfers) =
        serde_json::from_str::<Vec<tecan_worklist_builder::Transfer>>(transfers_json)
    else {
        return Ok(None);
    };
    if transfers
        .iter()
        .any(|transfer| !transfer.volume_ul.is_finite())
    {
        return Ok(None);
    }
    let Ok(wash_policy) = tecan_worklist_builder::WashPolicy::parse(wash_policy) else {
        return Ok(None);
    };
    let Ok(worklist) =
        tecan_worklist_builder::build_worklist(&transfers, name, wash_policy, batch_size)
    else {
        return Ok(None);
    };
    let payload = serde_json::json!({
        "text": worklist.to_text(),
        "counts": worklist.counts,
        "count_order": worklist.count_order,
    });
    Ok(serde_json::to_string(&payload).ok())
}

#[pyfunction]
fn worklist_build_schedule(
    transfers_json: &str,
    wash_policy: &str,
    batch_size: Option<usize>,
) -> PyResult<Option<String>> {
    let Ok(transfers) =
        serde_json::from_str::<Vec<tecan_worklist_builder::Transfer>>(transfers_json)
    else {
        return Ok(None);
    };
    let Ok(wash_policy) = tecan_worklist_builder::WashPolicy::parse(wash_policy) else {
        return Ok(None);
    };
    let Ok(schedule) =
        tecan_worklist_builder::build_worklist_schedule(&transfers, wash_policy, batch_size)
    else {
        return Ok(None);
    };
    Ok(serde_json::to_string(&schedule).ok())
}

#[pyfunction]
fn worklist_analyze_lines(
    lines_json: &str,
    enabled_tip_mask: Option<&str>,
    supported_channels: usize,
) -> PyResult<Option<String>> {
    let Ok(lines) = serde_json::from_str::<Vec<String>>(lines_json) else {
        return Ok(None);
    };
    let worklist = tecan_worklist_builder::Worklist {
        lines,
        ..tecan_worklist_builder::Worklist::default()
    };
    let Ok(analysis) = tecan_worklist_builder::semantics::analyze_worklist(
        &worklist,
        enabled_tip_mask,
        supported_channels,
    ) else {
        return Ok(None);
    };
    Ok(serde_json::to_string(&analysis).ok())
}

#[pyfunction]
fn worklist_optimize_lines(
    lines_json: &str,
    name: &str,
    enabled_tip_mask: Option<&str>,
    supported_channels: usize,
) -> PyResult<Option<String>> {
    let Ok(lines) = serde_json::from_str::<Vec<String>>(lines_json) else {
        return Ok(None);
    };
    let worklist = tecan_worklist_builder::Worklist {
        name: name.to_owned(),
        lines,
        ..tecan_worklist_builder::Worklist::default()
    };
    let Ok((optimized, mut payload)) = tecan_worklist_builder::semantics::optimize_worklist(
        &worklist,
        enabled_tip_mask,
        supported_channels,
    ) else {
        return Ok(None);
    };
    let Some(object) = payload.as_object_mut() else {
        return Ok(None);
    };
    object.insert("text".to_owned(), serde_json::json!(optimized.to_text()));
    Ok(serde_json::to_string(&payload).ok())
}

#[pyfunction]
fn worklist_validate_transfers(transfers_json: &str, strict: bool) -> PyResult<String> {
    let transfers: Vec<tecan_worklist_builder::Transfer> = serde_json::from_str(transfers_json)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let result = tecan_worklist_builder::validate_transfers(&transfers, strict);
    let payload = serde_json::json!({
        "ok": result.ok(),
        "errors": result.errors,
        "warnings": result.warnings,
    });
    serde_json::to_string(&payload).map_err(|error| PyValueError::new_err(error.to_string()))
}

fn window_has_pattern_type(
    classifications: &Bound<'_, PyList>,
    start: usize,
    end: usize,
    pattern_type: &str,
) -> PyResult<bool> {
    for index in start..=end {
        let class_list = classifications.get_item(index)?.cast_into::<PyList>()?;
        for class in class_list.iter() {
            if class.extract::<String>()? == pattern_type {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

struct XscrFieldMaps<'py> {
    elements: Bound<'py, PyDict>,
    texts: Bound<'py, PyDict>,
    attributes: Bound<'py, PyDict>,
}

fn xml_local_name(node: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    let tag = node.getattr("tag")?;
    if !tag.is_instance_of::<PyString>() {
        return Ok(None);
    }
    let tag = tag.extract::<String>()?;
    Ok(Some(
        tag.rsplit('}').next().unwrap_or(tag.as_str()).to_owned(),
    ))
}

fn scan_xscr_fields<'py>(
    py: Python<'py>,
    node: &Bound<'py, PyAny>,
    output: &Bound<'py, PyDict>,
    active: Option<&XscrFieldMaps<'py>>,
    depth: usize,
    node_count: &mut usize,
) -> PyResult<()> {
    *node_count += 1;
    if *node_count > 100_000 {
        return Err(PyValueError::new_err(
            "XSCR field indexing exceeds the safe node limit",
        ));
    }
    if depth > 256 {
        return Err(PyValueError::new_err(
            "XSCR field indexing exceeds the safe XML depth limit",
        ));
    }
    let Some(local_name) = xml_local_name(node)? else {
        return Ok(());
    };

    let owned_maps = if local_name == "Object" {
        if output.len() >= 10_000 {
            return Err(PyValueError::new_err(
                "XSCR field indexing exceeds the safe Object limit",
            ));
        }
        let maps = XscrFieldMaps {
            elements: PyDict::new(py),
            texts: PyDict::new(py),
            attributes: PyDict::new(py),
        };
        let entry = PyDict::new(py);
        entry.set_item("elements", maps.elements.clone())?;
        entry.set_item("texts", maps.texts.clone())?;
        entry.set_item("attributes", maps.attributes.clone())?;
        output.set_item(node.clone(), entry)?;
        Some(maps)
    } else {
        if let Some(maps) = active {
            if maps.elements.get_item(&local_name)?.is_none() {
                maps.elements.set_item(&local_name, node.clone())?;
            }
            let text = node.getattr("text")?;
            if !text.is_none() {
                if let Ok(value) = text.extract::<String>() {
                    if maps.texts.get_item(&local_name)?.is_none() {
                        maps.texts.set_item(&local_name, value.trim())?;
                    }
                }
            }
            let attributes = node.getattr("attrib")?;
            let items = attributes.call_method0("items")?;
            for pair in items.try_iter()? {
                let pair = pair?.cast_into::<PyTuple>()?;
                let key = pair.get_item(0)?.extract::<String>()?;
                if maps.attributes.get_item(&key)?.is_none() {
                    let value = pair.get_item(1)?.extract::<String>()?;
                    maps.attributes.set_item(key, value.trim())?;
                }
            }
        }
        None
    };
    let current = owned_maps.as_ref().or(active);
    for child in node.try_iter()? {
        let child = child?;
        scan_xscr_fields(py, &child, output, current, depth + 1, node_count)?;
    }
    Ok(())
}

#[pyfunction]
fn xscr_object_fields<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let output = PyDict::new(py);
    let mut node_count = 0;
    scan_xscr_fields(py, root, &output, None, 0, &mut node_count)?;
    Ok(output)
}

#[pyfunction]
fn pattern_windows<'py>(
    py: Python<'py>,
    scripts: &Bound<'py, PyList>,
    classifications: &Bound<'py, PyList>,
    context_before: i64,
    context_after: i64,
) -> PyResult<Bound<'py, PyList>> {
    if scripts.len() != classifications.len() {
        return Err(PyValueError::new_err(
            "script/classification batch lengths do not match",
        ));
    }
    let before = context_before.max(0) as usize;
    let after = context_after.max(0) as usize;
    let result = PyList::empty(py);
    let mut seen: HashSet<(i64, String, i64, i64, String)> = HashSet::new();

    for script_index in 0..scripts.len() {
        let script = scripts.get_item(script_index)?.cast_into::<PyDict>()?;
        let commands = script
            .get_item("commands")?
            .ok_or_else(|| PyValueError::new_err("script is missing commands"))?
            .cast_into::<PyList>()?;
        let classes = classifications
            .get_item(script_index)?
            .cast_into::<PyList>()?;
        if commands.len() != classes.len() {
            return Err(PyValueError::new_err(
                "command/classification counts do not match",
            ));
        }
        let script_id = script
            .get_item("script_id")?
            .ok_or_else(|| PyValueError::new_err("script is missing script_id"))?
            .extract::<i64>()?;

        for anchor_position in 0..commands.len() {
            let anchor_classes = classes.get_item(anchor_position)?.cast_into::<PyList>()?;
            for pattern_value in anchor_classes.iter() {
                let pattern_type = pattern_value.extract::<String>()?;
                let start = anchor_position.saturating_sub(before);
                let end = commands
                    .len()
                    .saturating_sub(1)
                    .min(anchor_position.saturating_add(after));
                let mut signature_parts = Vec::with_capacity(end - start + 1);
                for position in start..=end {
                    let command = commands.get_item(position)?.cast_into::<PyDict>()?;
                    let command_type = command
                        .get_item("command_type")?
                        .map(|value| value.extract::<String>())
                        .transpose()?
                        .unwrap_or_default();
                    signature_parts.push(command_type);
                }
                let signature = signature_parts.join(" > ");
                let first = commands.get_item(start)?.cast_into::<PyDict>()?;
                let last = commands.get_item(end)?.cast_into::<PyDict>()?;
                let first_index = first
                    .get_item("command_index")?
                    .ok_or_else(|| PyValueError::new_err("command is missing command_index"))?
                    .extract::<i64>()?;
                let last_index = last
                    .get_item("command_index")?
                    .ok_or_else(|| PyValueError::new_err("command is missing command_index"))?
                    .extract::<i64>()?;
                let key = (
                    script_id,
                    pattern_type.clone(),
                    first_index,
                    last_index,
                    signature.clone(),
                );
                if !seen.insert(key) {
                    continue;
                }

                let confidence = if window_has_pattern_type(&classes, start, end, &pattern_type)? {
                    1.0_f64
                } else {
                    0.75_f64
                };
                let candidate = PyDict::new(py);
                candidate.set_item("script_index", script_index)?;
                candidate.set_item("pattern_type", pattern_type)?;
                candidate.set_item("start_pos", start)?;
                candidate.set_item("end_pos", end)?;
                candidate.set_item("signature", signature)?;
                candidate.set_item("confidence", confidence)?;
                result.append(candidate)?;
            }
        }
    }
    Ok(result)
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse_xml, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_source_paths, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_stat_fingerprint, module)?)?;
    module.add_function(wrap_pyfunction!(source_content_fingerprint, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_content_fingerprints, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(command_short_name, module)?)?;
    module.add_function(wrap_pyfunction!(compact_list, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_execution_contract, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_load_contract, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_validate_transfers, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_load_transfers, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_well_to_position, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_tip_selection, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_enabled_tip_mask, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_build_transfers, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_build_schedule, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_gwl, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_gwl_records, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_gwl_text_records, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_serialize_gwl_lines, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_count_record_types, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_analyze_lines, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_optimize_lines, module)?)?;
    module.add_function(wrap_pyfunction!(deepcopy_json, module)?)?;
    module.add_function(wrap_pyfunction!(xscr_object_fields, module)?)?;
    module.add_function(wrap_pyfunction!(pattern_windows, module)?)?;
    Ok(())
}
