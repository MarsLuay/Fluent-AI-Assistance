use csv::ReaderBuilder;
use pyo3::exceptions::{PyOSError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyModule, PySet, PyString, PyTuple,
};
use quick_xml::events::{BytesStart, Event};
use quick_xml::name::ResolveResult;
use quick_xml::reader::NsReader;
use rayon::prelude::*;
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs::{self, File};
use std::io::Read;
use std::path::{Component, Path, PathBuf};

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

fn is_exact_deepcopy_key(py: Python<'_>, value: &Bound<'_, PyAny>, depth: usize) -> PyResult<bool> {
    if value.is_none() {
        return Ok(true);
    }
    let value_type = value.get_type();
    if value_type.is(py.get_type::<PyBool>())
        || value_type.is(py.get_type::<PyInt>())
        || value_type.is(py.get_type::<PyFloat>())
        || value_type.is(py.get_type::<PyString>())
        || value_type.is(py.get_type::<PyBytes>())
    {
        return Ok(true);
    }
    if depth >= 1024 || !value_type.is(py.get_type::<PyTuple>()) {
        return Ok(false);
    }
    for item in value.cast::<PyTuple>()?.iter() {
        if !is_exact_deepcopy_key(py, &item, depth + 1)? {
            return Ok(false);
        }
    }
    Ok(true)
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
    let value_type = value.get_type();
    if value.is_none()
        || value_type.is(py.get_type::<PyBool>())
        || value_type.is(py.get_type::<PyInt>())
        || value_type.is(py.get_type::<PyFloat>())
        || value_type.is(py.get_type::<PyString>())
        || value_type.is(py.get_type::<PyBytes>())
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

    if value_type.is(py.get_type::<PyList>()) {
        let items = value.cast::<PyList>()?;
        let output = PyList::empty(py);
        memo.insert(pointer, output.clone().into_any().unbind());
        active.insert(pointer);
        for item in items.iter() {
            output.append(clone_json_value(py, &item, memo, active, depth + 1)?)?;
        }
        active.remove(&pointer);
        return Ok(output.into_any());
    }
    if value_type.is(py.get_type::<PyDict>()) {
        let items = value.cast::<PyDict>()?;
        let output = PyDict::new(py);
        memo.insert(pointer, output.clone().into_any().unbind());
        active.insert(pointer);
        for (key, item) in items.iter() {
            if !is_exact_deepcopy_key(py, &key, 0)? {
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
    if value_type.is(py.get_type::<PyTuple>()) {
        let items = value.cast::<PyTuple>()?;
        active.insert(pointer);
        let mut copied_items = Vec::with_capacity(items.len());
        let mut unchanged = true;
        for item in items.iter() {
            let copied = clone_json_value(py, &item, memo, active, depth + 1)?;
            unchanged &= copied.is(&item);
            copied_items.push(copied);
        }
        active.remove(&pointer);
        if unchanged {
            return Ok(value.clone());
        }
        let output = PyTuple::new(py, copied_items)?;
        memo.insert(pointer, output.clone().into_any().unbind());
        return Ok(output.into_any());
    }
    Err(PyTypeError::new_err(format!(
        "unsupported data for native copy: {}",
        value.get_type().name()?
    )))
}

fn is_safe_json_key(py: Python<'_>, value: &Bound<'_, PyAny>, depth: usize) -> PyResult<bool> {
    if value.is_none() {
        return Ok(true);
    }
    let value_type = value.get_type();
    if value_type.is(py.get_type::<PyBool>())
        || value_type.is(py.get_type::<PyInt>())
        || value_type.is(py.get_type::<PyFloat>())
        || value_type.is(py.get_type::<PyString>())
        || value_type.is(py.get_type::<PyBytes>())
    {
        return Ok(true);
    }
    if depth >= 64 || !value_type.is(py.get_type::<PyTuple>()) {
        return Ok(false);
    }
    for item in value.cast::<PyTuple>()?.iter() {
        if !is_safe_json_key(py, &item, depth + 1)? {
            return Ok(false);
        }
    }
    Ok(true)
}

fn sorted_builtin_set<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let items = value.cast::<PySet>()?;
    let mut values: Vec<Bound<'py, PyAny>> = items.iter().collect();
    let mut kind = None;
    for item in &values {
        let value_type = item.get_type();
        let item_kind = if value_type.is(py.get_type::<PyBool>())
            || value_type.is(py.get_type::<PyInt>())
            || value_type.is(py.get_type::<PyFloat>())
        {
            1
        } else if value_type.is(py.get_type::<PyString>()) {
            2
        } else if value_type.is(py.get_type::<PyBytes>()) {
            3
        } else {
            return Ok(None);
        };
        if kind.is_some_and(|kind| kind != item_kind) {
            return Ok(None);
        }
        kind = Some(item_kind);
    }

    for index in 1..values.len() {
        let mut cursor = index;
        while cursor > 0 {
            match values[cursor].compare(&values[cursor - 1]) {
                Ok(Ordering::Less) => values.swap(cursor, cursor - 1),
                Ok(_) => break,
                Err(_) => return Ok(None),
            }
            cursor -= 1;
        }
    }

    let output = PyList::empty(py);
    for item in values {
        output.append(item)?;
    }
    Ok(Some(output.into_any()))
}

#[allow(clippy::too_many_arguments)] // Exact Python types and recursion state stay explicit.
fn try_to_jsonable_containers<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    counter_type: &Bound<'py, PyAny>,
    path_type: &Bound<'py, PyAny>,
    defaultdict_type: &Bound<'py, PyAny>,
    ordered_dict_type: &Bound<'py, PyAny>,
    active: &mut HashSet<usize>,
    depth: usize,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if depth > 256 {
        return Ok(None);
    }
    let value_type = value.get_type();
    if value_type.is(counter_type) {
        let items = value.cast::<PyDict>()?;
        let output = PyDict::new(py);
        for (key, item) in items.iter() {
            output.set_item(key, item)?;
        }
        return Ok(Some(output.into_any()));
    }
    if value_type.is(path_type) {
        return Ok(Some(value.str()?.into_any()));
    }
    if value.is_none()
        || value_type.is(py.get_type::<PyBool>())
        || value_type.is(py.get_type::<PyInt>())
        || value_type.is(py.get_type::<PyFloat>())
        || value_type.is(py.get_type::<PyString>())
        || value_type.is(py.get_type::<PyBytes>())
    {
        return Ok(Some(value.clone()));
    }

    if value_type.is(py.get_type::<PySet>()) {
        return sorted_builtin_set(py, value);
    }

    if value_type.is(py.get_type::<PyTuple>()) {
        let items = value.cast::<PyTuple>()?;
        let output = PyList::empty(py);
        for item in items.iter() {
            output.append(item)?;
        }
        return Ok(Some(output.into_any()));
    }

    let pointer = value.as_ptr() as usize;
    if value_type.is(py.get_type::<PyList>()) {
        if !active.insert(pointer) {
            return Ok(None);
        }
        let items = value.cast::<PyList>()?;
        let output = PyList::empty(py);
        for item in items.iter() {
            let Some(converted) = try_to_jsonable_containers(
                py,
                &item,
                counter_type,
                path_type,
                defaultdict_type,
                ordered_dict_type,
                active,
                depth + 1,
            )?
            else {
                active.remove(&pointer);
                return Ok(None);
            };
            output.append(converted)?;
        }
        active.remove(&pointer);
        return Ok(Some(output.into_any()));
    }

    if value_type.is(py.get_type::<PyDict>())
        || value_type.is(defaultdict_type)
        || value_type.is(ordered_dict_type)
    {
        if !active.insert(pointer) {
            return Ok(None);
        }
        let items = value.cast::<PyDict>()?;
        let output = PyDict::new(py);
        for (key, item) in items.iter() {
            if !is_safe_json_key(py, &key, 0)? {
                active.remove(&pointer);
                return Ok(None);
            }
            let string_key = key.str()?;
            let Some(converted) = try_to_jsonable_containers(
                py,
                &item,
                counter_type,
                path_type,
                defaultdict_type,
                ordered_dict_type,
                active,
                depth + 1,
            )?
            else {
                active.remove(&pointer);
                return Ok(None);
            };
            output.set_item(string_key, converted)?;
        }
        active.remove(&pointer);
        return Ok(Some(output.into_any()));
    }

    Ok(None)
}

#[pyfunction]
fn to_jsonable_containers(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    counter_type: &Bound<'_, PyAny>,
    path_type: &Bound<'_, PyAny>,
    defaultdict_type: &Bound<'_, PyAny>,
    ordered_dict_type: &Bound<'_, PyAny>,
) -> PyResult<(bool, Py<PyAny>)> {
    match try_to_jsonable_containers(
        py,
        value,
        counter_type,
        path_type,
        defaultdict_type,
        ordered_dict_type,
        &mut HashSet::new(),
        0,
    )? {
        Some(converted) => Ok((true, converted.unbind())),
        None => Ok((false, py.None())),
    }
}

#[pyfunction]
fn deepcopy_json<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    clone_json_value(py, value, &mut HashMap::new(), &mut HashSet::new(), 0)
}

fn py_to_json(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Value> {
    if value.is_none() {
        return Ok(Value::Null);
    }
    let value_type = value.get_type();
    if value_type.is(py.get_type::<PyBool>()) {
        return Ok(Value::Bool(value.extract::<bool>()?));
    }
    if value_type.is(py.get_type::<PyInt>()) {
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
    if value_type.is(py.get_type::<PyFloat>()) {
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
    if value_type.is(py.get_type::<PyString>()) {
        return Ok(Value::String(value.extract::<String>()?));
    }
    if value_type.is(py.get_type::<PyList>()) {
        let items = value.cast::<PyList>()?;
        return items
            .iter()
            .map(|item| py_to_json(py, &item))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if value_type.is(py.get_type::<PyTuple>()) {
        let items = value.cast::<PyTuple>()?;
        return items
            .iter()
            .map(|item| py_to_json(py, &item))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if value_type.is(py.get_type::<PyDict>()) {
        let items = value.cast::<PyDict>()?;
        let mut result = Map::new();
        for (key, item) in items.iter() {
            if !key.get_type().is(py.get_type::<PyString>()) {
                return Err(PyTypeError::new_err(
                    "non-string dictionary keys require Python JSON fallback",
                ));
            }
            let key = key.extract::<String>()?;
            result.insert(key, py_to_json(py, &item)?);
        }
        return Ok(Value::Object(result));
    }
    Err(PyTypeError::new_err(format!(
        "unsupported value for native JSON serialization: {}",
        value_type.name()?
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

fn decode_utf16_bytes(bytes: &[u8], little_endian: bool) -> Option<String> {
    if bytes.len() % 2 != 0 {
        return None;
    }
    let units = bytes.chunks_exact(2).map(|pair| {
        let pair = [pair[0], pair[1]];
        if little_endian {
            u16::from_le_bytes(pair)
        } else {
            u16::from_be_bytes(pair)
        }
    });
    char::decode_utf16(units)
        .collect::<Result<String, _>>()
        .ok()
}

fn decode_text_bytes_data(bytes: &[u8]) -> String {
    let utf8_payload = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(bytes);
    if let Ok(text) = std::str::from_utf8(utf8_payload) {
        return text.to_owned();
    }
    if let Ok(text) = std::str::from_utf8(bytes) {
        return text.to_owned();
    }

    let decoded_utf16 = if let Some(payload) = bytes.strip_prefix(&[0xff, 0xfe]) {
        decode_utf16_bytes(payload, true)
    } else if let Some(payload) = bytes.strip_prefix(&[0xfe, 0xff]) {
        decode_utf16_bytes(payload, false)
    } else {
        decode_utf16_bytes(bytes, cfg!(target_endian = "little"))
    };
    if let Some(text) = decoded_utf16 {
        return text;
    }
    bytes.iter().map(|byte| char::from(*byte)).collect()
}

#[pyfunction]
fn decode_text_bytes(data: &Bound<'_, PyBytes>) -> String {
    decode_text_bytes_data(data.as_bytes())
}

#[pyfunction]
fn xml_tag_local_name(tag: &str) -> String {
    tag.rsplit('}').next().unwrap_or(tag).to_owned()
}

#[pyfunction]
fn xml_clean_text(text: &str) -> String {
    trim_python_whitespace(text).to_owned()
}

#[pyfunction]
fn xml_container_child_texts<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    container_name: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    for container in root.try_iter()? {
        let container = container?;
        if !container.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = container.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        if tag.rsplit('}').next().unwrap_or(tag.as_str()) != container_name {
            continue;
        }
        for child in container.try_iter()? {
            let child = child?;
            if !child.get_type().is(element_type) {
                return Ok(None);
            }
            let text = child.getattr("text")?;
            if text.is_none() {
                continue;
            }
            if !text.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let text = text.extract::<String>()?;
            let cleaned = trim_python_whitespace(&text);
            if !cleaned.is_empty() {
                output.append(cleaned)?;
            }
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_unique_texts<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    names: Vec<String>,
    limit: i64,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let wanted = names.into_iter().collect::<HashSet<_>>();
    let mut seen = HashSet::new();
    let output = PyList::empty(py);
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if !wanted.contains(local) {
            continue;
        }
        let text = element.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text_value = text.extract::<String>()?;
        let cleaned = trim_python_whitespace(&text_value);
        if cleaned.is_empty() || !seen.insert(cleaned.to_owned()) {
            continue;
        }
        if cleaned == text_value {
            output.append(text)?;
        } else {
            output.append(cleaned)?;
        }
        if output.len() as i128 >= limit as i128 {
            break;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_texts_by_name<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    names: Vec<String>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let positions = names
        .into_iter()
        .enumerate()
        .map(|(index, name)| (name, index))
        .collect::<HashMap<_, _>>();
    let groups = (0..positions.len())
        .map(|_| PyList::empty(py))
        .collect::<Vec<_>>();
    let mut seen = HashSet::new();
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        let Some(index) = positions.get(local).copied() else {
            continue;
        };
        let text = element.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text_value = text.extract::<String>()?;
        let cleaned = trim_python_whitespace(&text_value);
        if cleaned.is_empty() || !seen.insert((index, cleaned.to_owned())) {
            continue;
        }
        if cleaned == text_value {
            groups[index].append(text)?;
        } else {
            groups[index].append(cleaned)?;
        }
    }
    let output = PyList::empty(py);
    for group in groups {
        output.append(group)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_find_text<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    name: &str,
    direct: bool,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let iterator = if direct {
        root.try_iter()?
    } else {
        root.call_method0("iter")?.try_iter()?
    };
    for element in iterator {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local != name {
            continue;
        }
        let text = element.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text_value = text.extract::<String>()?;
        let cleaned = trim_python_whitespace(&text_value);
        if cleaned.is_empty() {
            continue;
        }
        let output = PyList::empty(py);
        output.append(true)?;
        if cleaned == text_value {
            output.append(text)?;
        } else {
            output.append(cleaned)?;
        }
        return Ok(Some(output.into_any()));
    }
    let output = PyList::empty(py);
    output.append(false)?;
    output.append(py.None().bind(py))?;
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_find_attribute<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    name: &str,
    attribute: &str,
    default: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local != name {
            continue;
        }
        let attributes = element.getattr("attrib")?;
        if !attributes.get_type().is(py.get_type::<PyDict>()) {
            return Ok(None);
        }
        let attributes = attributes.cast::<PyDict>()?;
        let Some(value) = attributes.get_item(attribute)? else {
            return Ok(Some(PyString::new(py, default).into_any()));
        };
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        return Ok(Some(value));
    }
    Ok(Some(PyString::new(py, default).into_any()))
}

#[pyfunction]
fn xml_all_unique_texts<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    let mut seen = HashSet::new();
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let text = element.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text_value = text.extract::<String>()?;
        let cleaned = trim_python_whitespace(&text_value);
        if cleaned.is_empty() || !seen.insert(cleaned.to_owned()) {
            continue;
        }
        output.append(cleaned)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xscr_command_records<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    field_names: Vec<String>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let field_names = field_names.into_iter().collect::<HashSet<_>>();
    let output = PyList::empty(py);
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        if tag.rsplit('}').next().unwrap_or(tag.as_str()) != "Object" {
            continue;
        }
        let attributes = element.getattr("attrib")?;
        if !attributes.get_type().is(py.get_type::<PyDict>()) {
            return Ok(None);
        }
        let attributes = attributes.cast::<PyDict>()?;
        let Some(type_name) = attributes.get_item("Type")? else {
            continue;
        };
        if !type_name.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let type_name = type_name.extract::<String>()?;
        let mut fields: Vec<(String, String)> = Vec::new();
        let children = element.call_method0("iter")?;
        for child in children.try_iter()? {
            let child = child?;
            if !child.get_type().is(element_type) {
                return Ok(None);
            }
            let child_tag = child.getattr("tag")?;
            if !child_tag.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let child_tag = child_tag.extract::<String>()?;
            let local = child_tag.rsplit('}').next().unwrap_or(child_tag.as_str());
            if !field_names.contains(local) {
                continue;
            }
            let text = child.getattr("text")?;
            if text.is_none() {
                continue;
            }
            if !text.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let text = text.extract::<String>()?;
            let value = trim_python_whitespace(&text);
            if value.is_empty() {
                continue;
            }
            if let Some((_, previous)) = fields.iter_mut().find(|(name, _)| name == local) {
                if !previous.split(" | ").any(|part| part == value) {
                    previous.push_str(" | ");
                    previous.push_str(value);
                }
            } else {
                fields.push((local.to_owned(), value.to_owned()));
            }
        }
        let row = PyList::empty(py);
        row.append(type_name)?;
        let field_rows = PyList::empty(py);
        for (name, value) in fields {
            field_rows.append((name, value))?;
        }
        row.append(field_rows)?;
        output.append(row)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn delimited_row_fields<'py>(
    py: Python<'py>,
    row: &Bound<'py, PyAny>,
    header: &Bound<'py, PyAny>,
    has_header: bool,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !row.get_type().is(py.get_type::<PyList>()) || !header.get_type().is(py.get_type::<PyList>())
    {
        return Ok(None);
    }
    let mut row_values = Vec::new();
    for value in row.try_iter()? {
        let value = value?;
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let value = value.extract::<String>()?;
        row_values.push(value);
    }
    let mut header_values = Vec::new();
    for value in header.try_iter()? {
        let value = value?;
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let value = value.extract::<String>()?;
        header_values.push(value);
    }
    let output = PyDict::new(py);
    for (column, value) in row_values.iter().enumerate() {
        let value = trim_python_whitespace(value);
        if value.is_empty() {
            continue;
        }
        let key = if has_header && column < header_values.len() {
            header_values[column].clone()
        } else {
            format!("column_{}", column + 1)
        };
        output.set_item(key, value)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn delimited_text_rows<'py>(
    py: Python<'py>,
    text: &str,
    delimiter: char,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let bytes = text.as_bytes();
    if !matches!(delimiter, ',' | '\t')
        || text.contains('"')
        || bytes
            .iter()
            .enumerate()
            .any(|(index, byte)| *byte == b'\r' && bytes.get(index + 1) != Some(&b'\n'))
    {
        return Ok(None);
    }
    let mut reader = ReaderBuilder::new()
        .delimiter(delimiter as u8)
        .has_headers(false)
        .flexible(true)
        .from_reader(text.as_bytes());
    let output = PyList::empty(py);
    for record in reader.records() {
        let Ok(record) = record else {
            return Ok(None);
        };
        if !record
            .iter()
            .any(|value| !trim_python_whitespace(value).is_empty())
        {
            continue;
        }
        let row = PyList::empty(py);
        for value in record.iter() {
            row.append(value)?;
        }
        output.append(row)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn unique_ascii_tuple_indices<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !rows.get_type().is(py.get_type::<PyList>()) {
        return Ok(None);
    }
    let mut seen: HashSet<(String, String, String)> = HashSet::new();
    let output = PyList::empty(py);
    for (index, row) in rows.try_iter()?.enumerate() {
        let row = row?;
        if !row.get_type().is(py.get_type::<PyTuple>()) || row.len()? != 3 {
            return Ok(None);
        }
        let mut fields = Vec::with_capacity(3);
        for value in row.try_iter()? {
            let value = value?;
            if !value.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let value = value.extract::<String>()?;
            fields.push(value);
        }
        let key = (fields.remove(0), fields.remove(0), fields.remove(0));
        if seen.insert(key) {
            output.append(index)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn ascii_casefold_equals(value: &str, expected: &str) -> Option<bool> {
    if value.is_ascii() && expected.is_ascii() {
        Some(value.eq_ignore_ascii_case(expected))
    } else {
        None
    }
}

fn ascii_case_insensitive_substring_match_groups(
    values: &[String],
    groups: &[Vec<String>],
) -> Option<Vec<bool>> {
    if values
        .iter()
        .chain(groups.iter().flatten())
        .any(|value| !value.is_ascii())
    {
        return None;
    }
    Some(
        groups
            .iter()
            .map(|needles| {
                values.iter().any(|value| {
                    needles.iter().any(|needle| {
                        needle.is_empty()
                            || value
                                .as_bytes()
                                .windows(needle.len())
                                .any(|window| window.eq_ignore_ascii_case(needle.as_bytes()))
                    })
                })
            })
            .collect(),
    )
}

#[pyfunction]
fn ascii_case_insensitive_substring_match_values<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
    needle: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !values.get_type().is(py.get_type::<PyList>())
        || !needle.get_type().is(py.get_type::<PyString>())
    {
        return Ok(None);
    }
    let needle = needle.extract::<String>()?;
    if !needle.is_ascii() {
        return Ok(None);
    }
    let needle_bytes = needle.as_bytes();
    let output = PyList::empty(py);
    for value in values.try_iter()? {
        let value = value?;
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let value = value.extract::<String>()?;
        if !value.is_ascii() {
            return Ok(None);
        }
        let matched = needle_bytes.is_empty()
            || value
                .as_bytes()
                .windows(needle_bytes.len())
                .any(|window| window.eq_ignore_ascii_case(needle_bytes));
        output.append(matched)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction(name = "ascii_case_insensitive_substring_match_groups")]
fn ascii_case_insensitive_substring_match_groups_py<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
    groups: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !values.get_type().is(py.get_type::<PyList>())
        || !groups.get_type().is(py.get_type::<PyList>())
    {
        return Ok(None);
    }
    let mut strings = Vec::new();
    for value in values.try_iter()? {
        let value = value?;
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        strings.push(value.extract::<String>()?);
    }
    let mut patterns = Vec::new();
    for group in groups.try_iter()? {
        let group = group?;
        if !group.get_type().is(py.get_type::<PyList>()) {
            return Ok(None);
        }
        let mut needles = Vec::new();
        for needle in group.try_iter()? {
            let needle = needle?;
            if !needle.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            needles.push(needle.extract::<String>()?);
        }
        patterns.push(needles);
    }
    let Some(matches) = ascii_case_insensitive_substring_match_groups(&strings, &patterns) else {
        return Ok(None);
    };
    let output = PyList::empty(py);
    for matched in matches {
        output.append(matched)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn sorted_unique_strings<'py>(
    py: Python<'py>,
    groups: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !groups.get_type().is(py.get_type::<PyList>()) {
        return Ok(None);
    }
    let mut values = BTreeSet::new();
    for group in groups.try_iter()? {
        let group = group?;
        if !group.get_type().is(py.get_type::<PyList>()) {
            return Ok(None);
        }
        for value in group.try_iter()? {
            let value = value?;
            if !value.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let value = value.extract::<String>()?;
            values.insert(value);
        }
    }
    let output = PyList::empty(py);
    for value in values {
        output.append(value)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn most_common_string_counts<'py>(
    py: Python<'py>,
    mapping: &Bound<'py, PyAny>,
    limit: Option<i64>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !mapping.get_type().is(py.get_type::<PyDict>()) || limit.is_some_and(|value| value < 0) {
        return Ok(None);
    }
    let mut counts = Vec::new();
    for (index, item) in mapping.call_method0("items")?.try_iter()?.enumerate() {
        let item = item?;
        let key = item.get_item(0)?;
        let value = item.get_item(1)?;
        if !key.get_type().is(py.get_type::<PyString>())
            || !value.get_type().is(py.get_type::<PyInt>())
        {
            return Ok(None);
        }
        let key = key.extract::<String>()?;
        let Ok(value) = value.extract::<i64>() else {
            return Ok(None);
        };
        counts.push((key, value, index));
    }
    counts.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.2.cmp(&right.2)));
    if let Some(limit) = limit {
        counts.truncate(usize::try_from(limit).unwrap_or(usize::MAX));
    }
    let output = PyDict::new(py);
    for (key, value, _) in counts {
        output.set_item(key, value)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn ordered_string_counts<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !values.get_type().is(py.get_type::<PyList>()) {
        return Ok(None);
    }
    let mut counts: HashMap<String, (usize, usize)> = HashMap::new();
    for (index, value) in values.try_iter()?.enumerate() {
        let value = value?;
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let value = value.extract::<String>()?;
        let entry = counts.entry(value).or_insert((index, 0));
        entry.1 += 1;
    }
    let mut ordered = counts.into_iter().collect::<Vec<_>>();
    ordered.sort_by(|left, right| {
        right
            .1
            .1
            .cmp(&left.1.1)
            .then_with(|| left.1.0.cmp(&right.1.0))
    });
    let output = PyDict::new(py);
    for (value, (_, count)) in ordered {
        output.set_item(value, count)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn any_ascii_casefold_in(values: Vec<String>, candidates: Vec<String>) -> Option<bool> {
    if values
        .iter()
        .chain(&candidates)
        .any(|value| !value.is_ascii())
    {
        return None;
    }
    let candidates = candidates
        .into_iter()
        .map(|value| value.to_ascii_lowercase())
        .collect::<HashSet<_>>();
    Some(
        values
            .iter()
            .any(|value| candidates.contains(&value.to_ascii_lowercase())),
    )
}

#[pyfunction]
fn sort_string_keys<'py>(
    py: Python<'py>,
    mapping: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !mapping.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let mut items = Vec::new();
    for item in mapping.call_method0("items")?.try_iter()? {
        let item = item?;
        let key = item.get_item(0)?;
        let value = item.get_item(1)?;
        if !key.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let key = key.extract::<String>()?;
        items.push((key, value));
    }
    items.sort_by(|left, right| left.0.cmp(&right.0));
    let output = PyDict::new(py);
    for (key, value) in items {
        output.set_item(key, value)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn merge_unique_string_groups<'py>(
    py: Python<'py>,
    group_mappings: &Bound<'py, PyAny>,
    limit: i64,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if limit < 0 {
        return Ok(None);
    }
    let limit = usize::try_from(limit).unwrap_or(usize::MAX);
    let mut groups: BTreeMap<String, (Vec<String>, HashSet<String>)> = BTreeMap::new();
    for mapping in group_mappings.try_iter()? {
        let mapping = mapping?;
        if !mapping.get_type().is(py.get_type::<PyDict>()) {
            return Ok(None);
        }
        for item in mapping.call_method0("items")?.try_iter()? {
            let item = item?;
            let key = item.get_item(0)?;
            let values = item.get_item(1)?;
            if !key.get_type().is(py.get_type::<PyString>())
                || !values.get_type().is(py.get_type::<PyList>())
            {
                return Ok(None);
            }
            let key = key.extract::<String>()?;
            let bucket = groups.entry(key).or_default();
            for value in values.try_iter()? {
                let value = value?;
                if !value.get_type().is(py.get_type::<PyString>()) {
                    return Ok(None);
                }
                let value = value.extract::<String>()?;
                if bucket.0.len() < limit && bucket.1.insert(value.clone()) {
                    bucket.0.push(value);
                }
            }
        }
    }
    let output = PyDict::new(py);
    for (key, (values, _)) in groups {
        let bucket = PyList::empty(py);
        for value in values {
            bucket.append(value)?;
        }
        output.set_item(key, bucket)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn casefold_alias_values<'py>(
    py: Python<'py>,
    mapping: &Bound<'py, PyAny>,
    alias_groups: Vec<Vec<String>>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !mapping.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let items = mapping.call_method0("items")?;
    let folded = PyDict::new(py);
    for item in items.try_iter()? {
        let item = item?;
        let key = item.get_item(0)?;
        let value = item.get_item(1)?;
        if !key.get_type().is(py.get_type::<PyString>())
            || !(value.get_type().is(py.get_type::<PyString>())
                || value.get_type().is(py.get_type::<PyDict>()))
        {
            return Ok(None);
        }
        let key = key.extract::<String>()?;
        let key = if key.is_ascii() {
            key.to_ascii_lowercase()
        } else {
            PyString::new(py, &key)
                .call_method0("casefold")?
                .extract::<String>()?
        };
        folded.set_item(key, value)?;
    }
    let output = PyList::empty(py);
    for aliases in alias_groups {
        let mut selected = None;
        for alias in aliases {
            if !alias.is_ascii() {
                return Ok(None);
            }
            let Some(value) = folded.get_item(alias)? else {
                continue;
            };
            if value.is_none() {
                continue;
            }
            if value.get_type().is(py.get_type::<PyString>())
                && value.extract::<String>()?.is_empty()
            {
                continue;
            }
            selected = Some(value);
            break;
        }
        if let Some(value) = selected {
            output.append(value)?;
        } else {
            output.append(py.None())?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_variable_definition_elements<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        let attributes = element.getattr("attrib")?;
        if !attributes.get_type().is(py.get_type::<PyDict>()) {
            return Ok(None);
        }
        let attribute_values = attributes.call_method0("values")?;
        let mut type_hint = String::new();
        for value in attribute_values.try_iter()? {
            let value = value?;
            if !value.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let value = value.extract::<String>()?;
            if !type_hint.is_empty() {
                type_hint.push(' ');
            }
            type_hint.push_str(&value);
        }
        let is_variable_helper = matches!(local, "anyType" | "VariableDefinitionHelper")
            || type_hint.contains("VariableDefinitionHelper");
        if !is_variable_helper {
            continue;
        }
        let mut has_name = false;
        let mut has_type_name = false;
        for child in element.try_iter()? {
            let child = child?;
            if !child.get_type().is(element_type) {
                return Ok(None);
            }
            let child_tag = child.getattr("tag")?;
            if !child_tag.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let child_tag = child_tag.extract::<String>()?;
            let child_local = child_tag.rsplit('}').next().unwrap_or(child_tag.as_str());
            has_name |= child_local == "Name";
            has_type_name |= child_local == "TypeName";
        }
        if has_name && has_type_name {
            output.append(element)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_elements_by_folded_name_with_children<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    names: Vec<String>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) || names.iter().any(|name| !name.is_ascii()) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        let folded = if local.is_ascii() {
            local.to_ascii_lowercase()
        } else {
            PyString::new(py, local)
                .call_method0("casefold")?
                .extract::<String>()?
        };
        if names.iter().any(|name| name.eq_ignore_ascii_case(&folded))
            && element.call_method0("__len__")?.extract::<usize>()? > 0
        {
            output.append(element)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_elements_by_name<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    names: Vec<String>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let names = names.into_iter().collect::<HashSet<_>>();
    let output = PyList::empty(py);
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if names.contains(local) {
            output.append(element)?;
        }
    }
    Ok(Some(output.into_any()))
}

fn is_python_whitespace(character: char) -> bool {
    character.is_whitespace() || ('\u{001c}'..='\u{001f}').contains(&character)
}

fn trim_python_whitespace(value: &str) -> &str {
    value.trim_matches(is_python_whitespace)
}

#[pyfunction]
fn xml_grouped_texts_excluding<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    excluded_names: Vec<String>,
    limit: i64,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) || limit < 0 {
        return Ok(None);
    }
    let excluded = excluded_names.into_iter().collect::<HashSet<_>>();
    let mut groups: BTreeMap<String, (Vec<String>, HashSet<String>)> = BTreeMap::new();
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        let text = element.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text_value = text.extract::<String>()?;
        if excluded.contains(local) {
            continue;
        }
        let cleaned = trim_python_whitespace(&text_value);
        if cleaned.is_empty() {
            continue;
        }
        let (values, seen) = groups.entry(local.to_owned()).or_default();
        if values.len() < limit as usize && seen.insert(cleaned.to_owned()) {
            values.push(cleaned.to_owned());
        }
    }
    let output = PyList::empty(py);
    for (name, (values, _)) in groups {
        let row = PyList::empty(py);
        row.append(name)?;
        row.append(PyList::new(py, values)?)?;
        output.append(row)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_records_by_name<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    record_name: &str,
    field_names: Vec<String>,
    direct: bool,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let records = PyList::empty(py);
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        if tag.rsplit('}').next().unwrap_or(tag.as_str()) != record_name {
            continue;
        }
        let row = PyList::empty(py);
        for field_name in &field_names {
            let mut value = String::new();
            let children = if direct {
                element.try_iter()?
            } else {
                element.call_method0("iter")?.try_iter()?
            };
            for child in children {
                let child = child?;
                if !child.get_type().is(element_type) {
                    return Ok(None);
                }
                let child_tag = child.getattr("tag")?;
                if !child_tag.get_type().is(py.get_type::<PyString>()) {
                    return Ok(None);
                }
                let child_tag = child_tag.extract::<String>()?;
                if child_tag.rsplit('}').next().unwrap_or(child_tag.as_str()) != field_name {
                    continue;
                }
                let text = child.getattr("text")?;
                if text.is_none() {
                    continue;
                }
                if !text.get_type().is(py.get_type::<PyString>()) {
                    return Ok(None);
                }
                let text = text.extract::<String>()?;
                let cleaned = trim_python_whitespace(&text);
                if !cleaned.is_empty() {
                    value = cleaned.to_owned();
                    break;
                }
            }
            row.append(value)?;
        }
        records.append(row)?;
    }
    Ok(Some(records.into_any()))
}

#[pyfunction]
fn render_append_list(title: &str, values: Vec<String>, limit: i64) -> Vec<String> {
    if values.is_empty() {
        return Vec::new();
    }
    let length = values.len() as i128;
    let limit = limit as i128;
    let slice_end = if limit < 0 {
        (length + limit).clamp(0, length) as usize
    } else {
        limit.min(length) as usize
    };
    let mut output = vec![String::new(), format!("## {title}")];
    output.extend(values[..slice_end].iter().map(|value| format!("- {value}")));
    if length > limit {
        output.push(format!("- ... {} more", length - limit));
    }
    output
}

#[pyfunction]
fn render_append_dict(title: &str, entries: Vec<(String, String)>, limit: i64) -> Vec<String> {
    if entries.is_empty() {
        return Vec::new();
    }
    let length = entries.len() as i128;
    let limit = limit as i128;
    let mut output = vec![String::new(), format!("## {title}")];
    for (index, (key, value)) in entries.iter().enumerate() {
        if index as i128 >= limit {
            output.push(format!("- ... {} more", length - limit));
            break;
        }
        output.push(format!("- `{key}`: {value}"));
    }
    output
}

#[allow(clippy::too_many_arguments)] // Keep the report inputs aligned with its compatibility wrapper.
#[pyfunction]
fn render_compare_report(
    left: &str,
    right: &str,
    left_entry_count: &str,
    right_entry_count: &str,
    common_script_count: &str,
    scripts_added: Vec<String>,
    scripts_removed: Vec<String>,
    left_families: Vec<(String, String)>,
    right_families: Vec<(String, String)>,
) -> String {
    let mut lines = vec![
        "# ZEIA Compare Report".to_string(),
        String::new(),
        format!("- Left: `{left}`"),
        format!("- Right: `{right}`"),
        format!("- Entries: `{left_entry_count}` -> `{right_entry_count}`"),
        format!("- Common script names: `{common_script_count}`"),
    ];
    lines.extend(render_append_list("Scripts Added", scripts_added, 100));
    lines.extend(render_append_list("Scripts Removed", scripts_removed, 100));
    lines.extend(render_append_dict(
        "Left Command Families",
        left_families,
        20,
    ));
    lines.extend(render_append_dict(
        "Right Command Families",
        right_families,
        20,
    ));
    format!("{}\n", lines.join("\n"))
}

#[pyfunction]
fn render_gwl_report(
    source: &str,
    line_count: &str,
    transfer_pairs_estimate: &str,
    record_counts: Vec<(String, String)>,
    examples: Vec<Vec<String>>,
) -> Option<String> {
    if examples.iter().any(|example| example.len() != 6) {
        return None;
    }
    let mut lines = vec![
        format!("# GWL Report: {source}"),
        String::new(),
        format!("- Lines: `{line_count}`"),
        format!("- Transfer pairs estimate: `{transfer_pairs_estimate}`"),
    ];
    lines.extend(render_append_dict("Record Counts", record_counts, 20));
    if !examples.is_empty() {
        lines.push(String::new());
        lines.push("## Pipette Examples".to_string());
        lines.extend(examples.iter().map(|example| {
            format!(
                "- line {}: `{}` {} pos {} vol {} lc `{}`",
                example[0], example[1], example[2], example[3], example[4], example[5]
            )
        }));
    }
    Some(format!("{}\n", lines.join("\n")))
}

#[allow(clippy::too_many_arguments)] // Keep the XSCR report adapter's inputs explicit.
#[pyfunction]
fn render_xscr_report(
    header: Vec<String>,
    references: Vec<Vec<String>>,
    family_counts: Vec<(String, String)>,
    command_counts: Vec<(String, String)>,
    variables: Vec<String>,
    query_prompts: Vec<String>,
    dependencies_present: bool,
    dependencies: Vec<Vec<String>>,
    comments: Vec<String>,
    warnings: Vec<String>,
    commands: Vec<Vec<String>>,
) -> Option<String> {
    if header.len() != 4
        || references.iter().any(|reference| reference.len() != 3)
        || dependencies.iter().any(|dependency| dependency.len() != 2)
        || commands.iter().any(|command| command.len() != 5)
    {
        return None;
    }
    let mut lines = vec![
        format!("# XSCR Report: {}", header[0]),
        String::new(),
        format!("- Source: `{}`", header[1]),
        format!("- Script version: `{}`", header[2]),
        format!("- Command count: `{}`", header[3]),
    ];
    if !references.is_empty() {
        lines.push(String::new());
        lines.push("## References".to_string());
        for reference in references.iter().take(20) {
            lines.push(format!(
                "- {}: `{}` (`{}`)",
                reference[0], reference[1], reference[2]
            ));
        }
    }
    lines.extend(render_append_dict("Command Families", family_counts, 20));
    lines.extend(render_append_dict("Command Types", command_counts, 30));
    lines.extend(render_append_list("Variables", variables, 20));
    lines.extend(render_append_list("Query Prompts", query_prompts, 20));
    if dependencies_present {
        lines.push(String::new());
        lines.push("## Dependencies And Mentions".to_string());
        for dependency in dependencies {
            lines.push(format!("- {}: {}", dependency[0], dependency[1]));
        }
    }
    lines.extend(render_append_list("Comments", comments, 12));
    lines.extend(render_append_list("Warnings", warnings, 20));
    if !commands.is_empty() {
        lines.push(String::new());
        lines.push("## First Commands".to_string());
        for command in commands.iter().take(40) {
            lines.push(format!(
                "- #{} line {}: `{}` ({}) {}",
                command[0], command[1], command[2], command[3], command[4]
            ));
        }
    }
    Some(format!("{}\n", lines.join("\n")))
}

#[pyfunction]
fn render_zeia_report(
    header: Vec<String>,
    count_sections: Vec<Vec<(String, String)>>,
    script_names: Vec<String>,
    scripts: Vec<Vec<String>>,
    errors: Vec<Vec<String>>,
) -> Option<String> {
    if header.len() != 6
        || count_sections.len() != 4
        || scripts
            .iter()
            .any(|script| script.len() < 2 || (script.len() - 2) % 2 != 0)
        || errors.iter().any(|error| error.len() != 2)
    {
        return None;
    }
    let mut count_sections = count_sections.into_iter();
    let mut lines = vec![
        format!("# ZEIA Report: {}", header[0]),
        String::new(),
        format!("- Entries: `{}`", header[1]),
        format!(
            "- Scripts: `{}` total, `{}` summarized",
            header[2], header[3]
        ),
        format!("- XML objects summarized: `{}`", header[4]),
        format!("- GWL files summarized: `{}`", header[5]),
    ];
    lines.extend(render_append_dict(
        "File Extensions",
        count_sections.next().unwrap_or_default(),
        40,
    ));
    lines.extend(render_append_dict(
        "Command Families",
        count_sections.next().unwrap_or_default(),
        30,
    ));
    lines.extend(render_append_dict(
        "Command Types",
        count_sections.next().unwrap_or_default(),
        30,
    ));
    lines.extend(render_append_dict(
        "Warning Counts",
        count_sections.next().unwrap_or_default(),
        20,
    ));
    lines.extend(render_append_list("Script Names", script_names, 80));
    if !scripts.is_empty() {
        lines.push(String::new());
        lines.push("## Script Summaries".to_string());
        for script in scripts.iter().take(40) {
            let families = script[2..]
                .chunks_exact(2)
                .take(5)
                .map(|pair| format!("{}={}", pair[0], pair[1]))
                .collect::<Vec<_>>()
                .join(", ");
            lines.push(format!(
                "- `{}`: {} commands ({families})",
                script[0], script[1]
            ));
        }
    }
    if !errors.is_empty() {
        lines.push(String::new());
        lines.push("## Parse Errors".to_string());
        for error in errors.iter().take(20) {
            lines.push(format!("- `{}`: {}", error[0], error[1]));
        }
    }
    Some(format!("{}\n", lines.join("\n")))
}

#[allow(clippy::too_many_arguments)] // Keep report sections explicit for the Python adapter.
#[pyfunction]
fn render_directory_report(
    header: Vec<String>,
    extension_counts: Vec<(String, String)>,
    family_counts: Vec<(String, String)>,
    command_counts: Vec<(String, String)>,
    warning_counts: Vec<(String, String)>,
    script_names: Vec<String>,
    scripts: Vec<Vec<String>>,
    file_sources: Vec<String>,
) -> Option<String> {
    if header.len() != 2
        || scripts
            .iter()
            .any(|script| script.len() < 2 || (script.len() - 2) % 2 != 0)
    {
        return None;
    }
    let mut lines = vec![
        format!("# Directory Report: {}", header[0]),
        String::new(),
        format!("- Files found: `{}`", header[1]),
    ];
    lines.extend(render_append_dict("File Extensions", extension_counts, 20));
    lines.extend(render_append_dict("Command Families", family_counts, 30));
    lines.extend(render_append_dict("Command Types", command_counts, 30));
    lines.extend(render_append_dict("Warning Counts", warning_counts, 20));
    lines.extend(render_append_list("Script Names", script_names, 100));
    if !scripts.is_empty() {
        lines.push(String::new());
        lines.push("## Script Summaries".to_string());
        for script in scripts.iter().take(80) {
            let families = script[2..]
                .chunks_exact(2)
                .take(5)
                .map(|pair| format!("{}={}", pair[0], pair[1]))
                .collect::<Vec<_>>()
                .join(", ");
            lines.push(format!(
                "- `{}`: {} commands ({families})",
                script[0], script[1]
            ));
        }
    }
    lines.extend(render_append_list("Files", file_sources, 100));
    Some(format!("{}\n", lines.join("\n")))
}

#[pyfunction]
fn render_project_index_summary(
    header: Vec<String>,
    indexed_files: Vec<String>,
    entity_counts: Vec<(String, String)>,
    family_counts: Vec<(String, String)>,
    file_rows: Vec<Vec<String>>,
    total_files: usize,
) -> Option<String> {
    if header.len() != 9
        || file_rows.len() != total_files.min(100)
        || file_rows.iter().any(|file| file.len() != 5)
    {
        return None;
    }
    let mut lines = vec![
        format!("# {}", header[0]),
        String::new(),
        format!("- Database: `{}`", header[1]),
        format!("- Schema version: `{}`", header[2]),
        format!("- ZEIA files: `{}`", header[3]),
        format!("- Scripts: `{}`", header[4]),
        format!("- Commands: `{}`", header[5]),
        format!("- Command sequences: `{}`", header[6]),
        format!("- Catalog objects: `{}`", header[7]),
        format!("- Worklists: `{}`", header[8]),
    ];
    lines.extend(render_append_list("Indexed Files", indexed_files, 100));
    lines.extend(render_append_dict("Entity Counts", entity_counts, 40));
    lines.extend(render_append_dict(
        "Command Family Counts",
        family_counts,
        30,
    ));
    if total_files > 0 {
        lines.push(String::new());
        lines.push("## ZEIA Files".to_string());
        for file in &file_rows {
            lines.push(format!(
                "- `{}`: {} scripts, {} objects, {} worklists",
                file[0], file[1], file[2], file[3]
            ));
            lines.push(format!("  - Path: `{}`", file[4]));
        }
        if total_files > 100 {
            lines.push(format!("- ... {} more", total_files - 100));
        }
    }
    Some(format!("{}\n", lines.join("\n")))
}

#[pyfunction]
fn render_project_index_search(
    database: &str,
    query: &str,
    kind_filter: &str,
    result_count: &str,
    results: Vec<Vec<String>>,
) -> Option<String> {
    if results.iter().any(|result| result.len() != 8) {
        return None;
    }
    let mut lines = vec![
        "# Tecan Project Index Search".to_string(),
        String::new(),
        format!("- Database: `{database}`"),
        format!("- Query: `{query}`"),
        format!("- Kind filter: `{kind_filter}`"),
        format!("- Results: `{result_count}`"),
    ];
    if !results.is_empty() {
        lines.push(String::new());
        lines.push("## Results".to_string());
        for (index, result) in results.iter().enumerate() {
            lines.push(format!("{}. `{}` `{}`", index + 1, result[0], result[1]));
            if !result[2].is_empty() {
                lines.push(format!("   - Detail: `{}`", result[2]));
            }
            if !result[3].is_empty() {
                lines.push(format!("   - Script: `{}`", result[3]));
            }
            if result[7] == "1" {
                lines.push(format!("   - Command: `{}`", result[4]));
            }
            lines.push(format!("   - ZEIA: `{}`", result[5]));
            lines.push(format!("   - Path: `{}`", result[6]));
        }
    }
    Some(format!("{}\n", lines.join("\n")))
}

#[allow(clippy::too_many_arguments)] // Keep the summary contract explicit for the Python adapter.
#[pyfunction]
fn render_script_pattern_summary(
    title: &str,
    database: &str,
    schema_version: &str,
    pattern_count: &str,
    step_count: &str,
    is_mining: bool,
    mined_pattern_count: &str,
    replaced_existing: &str,
    pattern_counts: Vec<(String, String)>,
    sources: Vec<Vec<String>>,
) -> Option<String> {
    if sources.iter().any(|source| source.len() != 3) {
        return None;
    }
    let mut lines = vec![
        format!("# {title}"),
        String::new(),
        format!("- Database: `{database}`"),
        format!("- Pattern schema version: `{schema_version}`"),
        format!("- Patterns: `{pattern_count}`"),
        format!("- Steps: `{step_count}`"),
    ];
    if is_mining {
        lines.push(format!("- Mined this run: `{mined_pattern_count}`"));
        lines.push(format!("- Replaced existing: `{replaced_existing}`"));
    }
    lines.extend(render_append_dict(
        "Pattern Type Counts",
        pattern_counts,
        40,
    ));
    if !sources.is_empty() {
        lines.push(String::new());
        lines.push("## Top Source Scripts".to_string());
        for source in sources {
            lines.push(format!("- `{}`: {} patterns", source[0], source[1]));
            lines.push(format!("  - ZEIA: `{}`", source[2]));
        }
    }
    Some(format!("{}\n", lines.join("\n")))
}

fn trim_python_ascii_whitespace(value: &str) -> &str {
    value.trim_matches(|character: char| {
        matches!(
            character,
            '\u{0009}'..='\u{000d}' | ' ' | '\u{001c}'..='\u{001f}'
        )
    })
}

#[pyfunction]
fn append_unique_ascii_string<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Option<bool>> {
    if !values.get_type().is(py.get_type::<PyList>()) {
        return Ok(None);
    }
    if value.is_none() {
        return Ok(Some(false));
    }
    if !value.get_type().is(py.get_type::<PyString>()) {
        return Ok(None);
    }
    let value = value.extract::<String>()?;
    if !value.is_ascii() {
        return Ok(None);
    }
    let normalized = trim_python_ascii_whitespace(&value);
    if normalized.is_empty() {
        return Ok(Some(false));
    }
    let values = values.cast::<PyList>()?;
    for existing in values.iter() {
        if !existing.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let existing = existing.extract::<String>()?;
        if !existing.is_ascii() {
            return Ok(None);
        }
        if existing == normalized {
            return Ok(Some(false));
        }
    }
    values.append(normalized)?;
    Ok(Some(true))
}

#[pyfunction]
fn pattern_specification_groups<'py>(
    py: Python<'py>,
    mapping: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !mapping.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let output = PyDict::new(py);
    for (key, values) in mapping.cast::<PyDict>()?.iter() {
        if !key.get_type().is(py.get_type::<PyString>())
            || !values.get_type().is(py.get_type::<PyList>())
        {
            return Ok(None);
        }
        let key_text = key.extract::<String>()?;
        if !key_text.is_ascii() {
            return Ok(None);
        }
        let values = values.cast::<PyList>()?;
        let output_values = PyList::empty(py);
        let mut seen = HashSet::new();
        for value in values.iter() {
            if value.is_none() {
                continue;
            }
            if !value.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let value = value.extract::<String>()?;
            if !value.is_ascii() {
                return Ok(None);
            }
            let normalized = trim_python_ascii_whitespace(&value);
            if !normalized.is_empty() && seen.insert(normalized.to_string()) {
                output_values.append(normalized)?;
            }
        }
        output.set_item(key_text, output_values)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn pattern_join_summary<'py>(
    py: Python<'py>,
    parts: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !parts.get_type().is(py.get_type::<PyList>()) {
        return Ok(None);
    }
    let mut values = Vec::new();
    for part in parts.try_iter()? {
        let part = part?;
        if !part.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let part = part.extract::<String>()?;
        if !part.is_ascii() {
            return Ok(None);
        }
        let part = trim_python_ascii_whitespace(&part);
        if !part.is_empty() {
            values.push(part.to_string());
        }
    }
    let output = if values.is_empty() {
        "Command".to_string()
    } else {
        values.join(" ")
    };
    Ok(Some(PyString::new(py, &output).into_any()))
}

#[pyfunction]
fn pattern_liquid_step<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !values.get_type().is(py.get_type::<PyList>()) {
        return Ok(None);
    }
    let values = values.cast::<PyList>()?;
    if values.len() != 4 {
        return Ok(None);
    }
    let mut parts = Vec::with_capacity(4);
    for value in values.iter() {
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let value = value.extract::<String>()?;
        if !value.is_ascii() {
            return Ok(None);
        }
        parts.push(value);
    }
    let mut output = parts[0].clone();
    if !parts[2].is_empty() {
        output.push(' ');
        output.push_str(&parts[2]);
    }
    if !parts[1].is_empty() {
        output.push_str(" at ");
        output.push_str(&parts[1]);
    }
    if !parts[3].is_empty() {
        output.push_str(" using ");
        output.push_str(&parts[3]);
    }
    Ok(Some(PyString::new(py, &output).into_any()))
}

#[pyfunction]
fn render_pattern_types_report(rows: Vec<Vec<String>>) -> Option<String> {
    if rows.iter().any(|row| row.len() != 3) {
        return None;
    }
    let mut lines = vec!["# Tecan Script Pattern Types".to_string(), String::new()];
    for row in rows {
        lines.push(format!("- `{}`: {}", row[0], row[1]));
        lines.push(format!("  - {}", row[2]));
    }
    Some(format!("{}\n", lines.join("\n")))
}

type PatternSearchEntry = (
    Vec<String>,
    Vec<(String, Vec<String>)>,
    Vec<String>,
    Vec<Vec<String>>,
);

#[pyfunction]
fn render_script_pattern_search(
    header: Vec<String>,
    patterns: Vec<PatternSearchEntry>,
) -> Option<String> {
    if header.len() != 5
        || patterns.iter().any(|(fields, _, _, steps)| {
            fields.len() != 8 || steps.iter().any(|step| step.len() != 4)
        })
    {
        return None;
    }
    let mut lines = vec![
        "# Tecan Script Pattern Search".to_string(),
        String::new(),
        format!("- Database: `{}`", header[0]),
        format!("- Query: `{}`", header[1]),
        format!("- Pattern type filter: `{}`", header[2]),
        format!("- Source script filter: `{}`", header[3]),
        format!("- Results: `{}`", header[4]),
    ];
    if !patterns.is_empty() {
        lines.push(String::new());
        lines.push("## Patterns".to_string());
        for (index, (fields, specifications, safety_notes, steps)) in
            patterns.into_iter().enumerate()
        {
            lines.push(format!("{}. Pattern: `{}`", index + 1, fields[0]));
            lines.push(format!("   - Type: `{}` ({})", fields[1], fields[2]));
            lines.push(format!("   - Source script: `{}`", fields[3]));
            lines.push(format!("   - Commands: `{}` to `{}`", fields[4], fields[5]));
            lines.push(format!("   - ZEIA: `{}`", fields[6]));
            lines.push(format!("   - Path: `{}`", fields[7]));
            lines.extend(render_pattern_specs(specifications));
            if !safety_notes.is_empty() {
                lines.push("   - Safety notes:".to_string());
                lines.extend(safety_notes.iter().map(|note| format!("     - {note}")));
            }
            if !steps.is_empty() {
                lines.push("   - Steps:".to_string());
                for step in steps {
                    lines.push(format!(
                        "     {}. {} (`{}`, command `{}`)",
                        step[0], step[1], step[2], step[3]
                    ));
                    lines.push(format!("        - Path: `{}`", fields[7]));
                }
            }
        }
    }
    Some(format!("{}\n", lines.join("\n")))
}

#[pyfunction]
fn render_pattern_specs(entries: Vec<(String, Vec<String>)>) -> Vec<String> {
    if entries.is_empty() {
        return Vec::new();
    }
    let mut output = vec!["   - Specifications:".to_string()];
    for (key, values) in entries {
        if values.is_empty() {
            continue;
        }
        let mut rendered = values
            .iter()
            .take(12)
            .map(|value| format!("`{value}`"))
            .collect::<Vec<_>>()
            .join(", ");
        if values.len() > 12 {
            rendered.push_str(&format!(", ... {} more", values.len() - 12));
        }
        output.push(format!("     - {key}: {rendered}"));
    }
    output
}

#[pyfunction]
fn render_join_lines(lines: Vec<String>) -> String {
    format!("{}\n", lines.join("\n"))
}

#[pyfunction]
fn render_object_report(
    title: &str,
    fields: Vec<String>,
    names: Vec<String>,
    guids: Vec<String>,
) -> Option<String> {
    let labels = ["Kind", "Source", "Type ID", "Functional group", "Footprint"];
    if fields.len() != labels.len() {
        return None;
    }
    let mut lines = vec![format!("# XML Object Report: {title}"), String::new()];
    lines.extend(
        labels
            .iter()
            .zip(fields.iter())
            .map(|(label, value)| format!("- {label}: `{value}`")),
    );
    lines.extend(render_append_list("Names", names, 20));
    lines.extend(render_append_list("GUIDs", guids, 20));
    Some(format!("{}\n", lines.join("\n")))
}

#[pyfunction]
fn read_text_file(path: &str) -> Option<String> {
    std::fs::read(path)
        .ok()
        .map(|bytes| decode_text_bytes_data(&bytes))
}

#[pyfunction]
fn read_utf8_sig_file(path: &str) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    let text = std::str::from_utf8(&bytes).ok()?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    Some(text.replace("\r\n", "\n").replace('\r', "\n"))
}

#[pyfunction]
fn canonical_json(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
    let json = py_to_json(py, value)?;
    let compact =
        serde_json::to_string(&json).map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(python_json_spacing(&compact))
}

#[pyfunction]
fn canonical_json_compact(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
    let json = py_to_json(py, value)?;
    serde_json::to_string(&json).map_err(|error| PyValueError::new_err(error.to_string()))
}

#[pyfunction]
fn diagnostic_sort_indices(keys_json: &str) -> PyResult<Option<String>> {
    let Ok(keys) = serde_json::from_str::<Vec<Vec<String>>>(keys_json) else {
        return Ok(None);
    };
    if keys.iter().any(|key| key.len() != 5) {
        return Ok(None);
    }
    let mut indices = (0..keys.len()).collect::<Vec<_>>();
    indices.sort_by(|left, right| keys[*left].cmp(&keys[*right]));
    Ok(serde_json::to_string(&indices).ok())
}

#[pyfunction]
fn diagnostic_message(message: Option<String>, code: Option<String>) -> String {
    message
        .filter(|value| !value.is_empty())
        .or_else(|| code.filter(|value| !value.is_empty()))
        .unwrap_or_else(|| "ingestion diagnostic".to_owned())
}

#[pyfunction]
fn xml_kind_from_suffix(suffix: &str) -> Option<String> {
    if !suffix.is_ascii() {
        return None;
    }
    let normalized = suffix.to_ascii_lowercase();
    let kind = match normalized.as_str() {
        ".xcmp" => "component".to_owned(),
        ".xwsp" => "workspace".to_owned(),
        ".xlqc" => "liquid_class".to_owned(),
        ".xlcp" => "liquid_class_map".to_owned(),
        ".xsit" => "site".to_owned(),
        ".xcon" => "connector".to_owned(),
        ".xml" => "xml".to_owned(),
        _ => normalized.trim_start_matches('.').to_owned(),
    };
    Some(if kind.is_empty() {
        "xml".to_owned()
    } else {
        kind
    })
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
fn entry_object_guid(source_name: &str) -> String {
    let normalized = source_name.replace('\\', "/");
    let Some(name) = normalized
        .split('/')
        .rfind(|part| !part.is_empty() && *part != ".")
    else {
        return String::new();
    };
    let stem = match name.rfind('.') {
        Some(index) if index > 0 => &name[..index],
        _ => name,
    };
    let bytes = stem.as_bytes();
    if bytes.len() != 36
        || [8, 13, 18, 23].iter().any(|index| bytes[*index] != b'-')
        || bytes
            .iter()
            .enumerate()
            .any(|(index, byte)| ![8, 13, 18, 23].contains(&index) && !byte.is_ascii_hexdigit())
    {
        return String::new();
    }
    let guid = stem.to_ascii_lowercase();
    if guid == "00000000-0000-0000-0000-000000000000" {
        String::new()
    } else {
        guid
    }
}

fn has_supported_asset_extension(value: &str) -> bool {
    let bytes = value.as_bytes();
    let extensions: [&[u8]; 7] = [b"bmp", b"gif", b"jpg", b"jpeg", b"png", b"tif", b"tiff"];
    for (index, byte) in bytes.iter().enumerate() {
        if *byte != b'.' {
            continue;
        }
        for extension in extensions {
            let start = index + 1;
            let end = start + extension.len();
            if end > bytes.len() || !bytes[start..end].eq_ignore_ascii_case(extension) {
                continue;
            }
            if end == bytes.len() || matches!(bytes[end], b'\\' | b's' | b'\'' | b'"') {
                return true;
            }
        }
    }
    false
}

fn ascii_word_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

fn ascii_pin_references(value: &str, output: &mut BTreeSet<String>) {
    let bytes = value.as_bytes();
    for start in 0..bytes.len() {
        if start > 0 && ascii_word_byte(bytes[start - 1]) {
            continue;
        }
        if value[start..].starts_with("GIO") {
            let mut position = start + 3;
            let digits_start = position;
            while position < bytes.len() && bytes[position].is_ascii_digit() {
                position += 1;
            }
            if position > digits_start && value[position..].starts_with("_Pin") {
                position += 4;
                let pin_digits_start = position;
                while position < bytes.len() && bytes[position].is_ascii_digit() {
                    position += 1;
                }
                if position > pin_digits_start
                    && (position == bytes.len() || !ascii_word_byte(bytes[position]))
                {
                    output.insert(value[start..position].to_owned());
                }
            }
        }
        for prefix in ["Worktable_", "WorktablePin_"] {
            if !value[start..].starts_with(prefix) {
                continue;
            }
            let body_start = start + prefix.len();
            let mut end = body_start;
            while end < bytes.len() && ascii_word_byte(bytes[end]) {
                end += 1;
            }
            if end == body_start || end < bytes.len() && ascii_word_byte(bytes[end]) {
                continue;
            }
            let body = &value[body_start..end];
            let matched = if prefix == "Worktable_" {
                body.contains("Pin")
            } else {
                true
            };
            if matched && (end == bytes.len() || !ascii_word_byte(bytes[end])) {
                output.insert(value[start..end].to_owned());
            }
        }
    }
}

#[pyfunction]
fn pin_reference_candidates(values: Vec<String>) -> Option<Vec<String>> {
    if values.iter().any(|value| !value.is_ascii()) {
        return None;
    }
    let mut output = BTreeSet::new();
    for value in &values {
        ascii_pin_references(value, &mut output);
    }
    Some(output.into_iter().collect())
}

fn path_basename(value: &str) -> Option<String> {
    let normalized = value.replace('\\', "/");
    let component = Path::new(&normalized).components().next_back()?;
    match component {
        Component::ParentDir => Some("..".to_string()),
        Component::Normal(name) => name.to_str().map(str::to_owned),
        _ => None,
    }
}

#[pyfunction]
fn asset_reference_names(values: Vec<String>) -> Option<Vec<String>> {
    if values.iter().any(|value| !value.is_ascii()) {
        return None;
    }
    let mut output = Vec::new();
    for value in values {
        let Some(name) = path_basename(&value) else {
            continue;
        };
        if !name.is_empty() && !output.contains(&name) {
            output.push(name);
        }
    }
    Some(output)
}

fn asset_extension_end(bytes: &[u8], start: usize) -> Option<usize> {
    if bytes.get(start) != Some(&b'.') {
        return None;
    }
    for extension in [
        b"bmp".as_slice(),
        b"gif".as_slice(),
        b"jpg".as_slice(),
        b"jpeg".as_slice(),
        b"png".as_slice(),
        b"tiff".as_slice(),
        b"tif".as_slice(),
    ] {
        let end = start + 1 + extension.len();
        if end <= bytes.len() && bytes[start + 1..end].eq_ignore_ascii_case(extension) {
            return Some(end);
        }
    }
    None
}

#[pyfunction]
fn collapse_ascii_whitespace(text: &str) -> Option<String> {
    let mut output = String::with_capacity(text.len());
    let mut pending_space = false;
    for character in text.chars() {
        if is_python_whitespace(character) {
            pending_space = !output.is_empty();
        } else {
            if pending_space {
                output.push(' ');
            }
            output.push(character);
            pending_space = false;
        }
    }
    Some(output)
}

fn python_ascii_whitespace(byte: u8) -> bool {
    matches!(byte, b'\t'..=b'\r' | 0x1c..=0x20)
}

#[pyfunction]
fn xml_asset_reference_names(text: &str) -> Option<Vec<String>> {
    if !text.is_ascii() {
        return None;
    }
    let bytes = text.as_bytes();
    let mut references = BTreeSet::new();
    let mut start = 0;
    while start < bytes.len() {
        if matches!(bytes[start], b'<' | b'>' | b'"') {
            start += 1;
            continue;
        }
        let mut run_end = start;
        while run_end < bytes.len() && !matches!(bytes[run_end], b'<' | b'>' | b'"') {
            run_end += 1;
        }
        let mut last_extension_end = None;
        for position in (start + 1)..run_end {
            if let Some(end) = asset_extension_end(bytes, position)
                && end <= run_end
            {
                last_extension_end = Some(end);
            }
        }
        if let Some(end) = last_extension_end {
            let mut left = start;
            let mut right = end;
            while left < right && python_ascii_whitespace(bytes[left]) {
                left += 1;
            }
            while right > left && python_ascii_whitespace(bytes[right - 1]) {
                right -= 1;
            }
            if left < right
                && let Some(name) = path_basename(&text[left..right])
                && !name.is_empty()
            {
                references.insert(name);
            }
        }
        start = run_end;
    }
    Some(references.into_iter().collect())
}

#[pyfunction]
fn asset_reference_candidates(values: Vec<String>) -> Option<Vec<String>> {
    if values.iter().any(|value| !value.is_ascii()) {
        return None;
    }
    Some(
        values
            .into_iter()
            .filter(|value| has_supported_asset_extension(value))
            .collect(),
    )
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
fn path_suffixes(paths: Vec<String>) -> Option<Vec<String>> {
    if paths.iter().any(|path| !path.is_ascii()) {
        return None;
    }
    Some(
        paths
            .into_iter()
            .map(|path| {
                Path::new(&path)
                    .extension()
                    .and_then(|extension| extension.to_str())
                    .filter(|extension| !extension.is_empty())
                    .map(|extension| format!(".{}", extension.to_ascii_lowercase()))
                    .unwrap_or_else(|| "<none>".to_string())
            })
            .collect(),
    )
}

#[pyfunction]
fn extension_counts(suffixes: Vec<String>) -> Vec<(String, usize)> {
    let mut counts = BTreeMap::new();
    for suffix in suffixes {
        *counts.entry(suffix).or_insert(0) += 1;
    }
    counts.into_iter().collect()
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
fn worklist_parse_bool(value: &str) -> Option<bool> {
    if !value.is_ascii() {
        return None;
    }
    let normalized = value
        .trim_matches(|character: char| matches!(character as u32, 0x09..=0x0d | 0x1c..=0x20))
        .to_ascii_lowercase();
    match normalized.as_str() {
        "1" | "true" | "yes" | "y" => Some(true),
        "0" | "false" | "no" | "n" => Some(false),
        _ => None,
    }
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
fn worklist_inspect_gwl_lines(lines_json: &str) -> PyResult<Option<String>> {
    let Ok(lines) = serde_json::from_str::<Vec<String>>(lines_json) else {
        return Ok(None);
    };
    if lines.iter().any(|line| {
        !line.is_ascii()
            || line
                .chars()
                .any(|character| (0x1c..=0x1f).contains(&(character as u32)))
    }) {
        return Ok(None);
    }
    let Some(records) = tecan_worklist_builder::parse_gwl_records_for_python(&lines, true) else {
        return Ok(None);
    };

    let mut record_counts = Vec::<(String, usize)>::new();
    let mut count_positions = HashMap::<String, usize>::new();
    let mut pipette_examples = Vec::<Vec<String>>::new();
    let mut aspirates = 0usize;
    let mut dispenses = 0usize;
    let mut record_index = 0usize;
    for (line_index, line) in lines.iter().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let Some(record) = records.get(record_index) else {
            return Ok(None);
        };
        record_index += 1;
        let Some(kind) = record.get("kind").and_then(Value::as_str) else {
            return Ok(None);
        };
        let record_type = match kind {
            "pipette" => {
                let Some(operation) = record.get("operation").and_then(Value::as_str) else {
                    return Ok(None);
                };
                match operation {
                    "A" => aspirates += 1,
                    "D" => dispenses += 1,
                    _ => return Ok(None),
                }
                if pipette_examples.len() < 12 {
                    let Some(rack_label) = record.get("rack_label").and_then(Value::as_str) else {
                        return Ok(None);
                    };
                    let Some(rack_type) = record.get("rack_type").and_then(Value::as_str) else {
                        return Ok(None);
                    };
                    let Some(position) = record.get("position").and_then(Value::as_str) else {
                        return Ok(None);
                    };
                    let Some(volume) = record.get("volume").and_then(Value::as_str) else {
                        return Ok(None);
                    };
                    let Some(liquid_class) = record.get("liquid_class").and_then(Value::as_str)
                    else {
                        return Ok(None);
                    };
                    pipette_examples.push(vec![
                        (line_index + 1).to_string(),
                        operation.to_owned(),
                        rack_label.to_owned(),
                        rack_type.to_owned(),
                        position.to_owned(),
                        volume.to_owned(),
                        liquid_class.to_owned(),
                    ]);
                }
                operation.to_owned()
            }
            "wash" => match record.get("scheme") {
                Some(Value::Number(number)) => match number.as_u64() {
                    Some(scheme @ 1..=4) => format!("W{scheme}"),
                    _ => return Ok(None),
                },
                Some(Value::Null) | None => "W".to_owned(),
                _ => return Ok(None),
            },
            "break" => "B".to_owned(),
            "comment" => "C".to_owned(),
            "flush" => "F".to_owned(),
            "set_diti_type" => "S".to_owned(),
            "raw" => {
                let Some(type_character) = record.get("type_character").and_then(Value::as_str)
                else {
                    return Ok(None);
                };
                type_character.to_owned()
            }
            _ => return Ok(None),
        };
        let count_index = if let Some(index) = count_positions.get(&record_type) {
            *index
        } else {
            let index = record_counts.len();
            count_positions.insert(record_type.clone(), index);
            record_counts.push((record_type, 0));
            index
        };
        record_counts[count_index].1 += 1;
    }
    if record_index != records.len() {
        return Ok(None);
    }
    let payload = serde_json::json!({
        "record_counts": record_counts,
        "line_count": lines.len(),
        "transfer_pairs_estimate": aspirates.min(dispenses),
        "pipette_examples": pipette_examples,
    });
    Ok(serde_json::to_string(&payload).ok())
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
fn worklist_read_gwl_records(path: &str, permissive: bool) -> PyResult<Option<String>> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return Ok(None);
    };
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let Some(records) =
        tecan_worklist_builder::parse_gwl_text_records_for_python(&normalized, permissive)
    else {
        return Ok(None);
    };
    Ok(serde_json::to_string(&records).ok())
}

fn is_ascii_float_literal(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().any(|byte| byte.is_ascii_digit())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'+' | b'-' | b'.' | b'e' | b'E'))
}

#[pyfunction]
fn worklist_format_volume(value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_instance_of::<PyBool>() {
        return Ok(None);
    }
    let number = if value.is_instance_of::<PyString>() {
        let text = value.extract::<String>()?;
        if !text.is_ascii()
            || text
                .chars()
                .any(|character| (0x1c..=0x1f).contains(&(character as u32)))
        {
            return Ok(None);
        }
        let stripped = text.trim();
        if !is_ascii_float_literal(stripped) {
            return Ok(None);
        }
        let Ok(number) = stripped.parse::<f64>() else {
            return Ok(None);
        };
        number
    } else if value.is_instance_of::<PyInt>() || value.is_instance_of::<PyFloat>() {
        let Ok(number) = value.extract::<f64>() else {
            return Ok(None);
        };
        number
    } else {
        return Ok(None);
    };
    Ok(Some(tecan_worklist_builder::format_volume(number)))
}

#[pyfunction]
fn worklist_clean_field(value: Option<&str>) -> String {
    value.unwrap_or_default().trim().to_owned()
}

#[pyfunction]
fn worklist_ascii_uppercase(value: &str) -> Option<String> {
    value.is_ascii().then(|| value.to_ascii_uppercase())
}

#[pyfunction]
fn worklist_wash_type(py: Python<'_>, scheme: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if scheme.is_none() {
        return Ok(Some("W".to_owned()));
    }
    if !scheme.get_type().is(py.get_type::<PyInt>()) {
        return Ok(None);
    }
    let Ok(scheme) = scheme.extract::<i64>() else {
        return Ok(None);
    };
    Ok((1..=4).contains(&scheme).then(|| format!("W{scheme}")))
}

#[pyfunction]
fn worklist_comment_line(text: &str) -> String {
    format!("C;{}", text.replace('\n', "\\n"))
}

#[pyfunction]
fn worklist_record_type_line(type_character: &str) -> String {
    format!("{type_character};")
}

#[pyfunction]
fn worklist_join_gwl_fields(fields_json: &str) -> PyResult<Option<String>> {
    let Ok(fields) = serde_json::from_str::<Vec<String>>(fields_json) else {
        return Ok(None);
    };
    Ok(Some(fields.join(";")))
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
                        maps.texts
                            .set_item(&local_name, trim_python_whitespace(&value))?;
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
                    maps.attributes
                        .set_item(key, trim_python_whitespace(&value))?;
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
    module.add_function(wrap_pyfunction!(xml_tag_local_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_clean_text, module)?)?;
    module.add_function(wrap_pyfunction!(xml_container_child_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_unique_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_texts_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_find_text, module)?)?;
    module.add_function(wrap_pyfunction!(xml_find_attribute, module)?)?;
    module.add_function(wrap_pyfunction!(xml_records_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_all_unique_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_grouped_texts_excluding, module)?)?;
    module.add_function(wrap_pyfunction!(xml_elements_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(
        xml_elements_by_folded_name_with_children,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(xml_variable_definition_elements, module)?)?;
    module.add_function(wrap_pyfunction!(casefold_alias_values, module)?)?;
    module.add_function(wrap_pyfunction!(merge_unique_string_groups, module)?)?;
    module.add_function(wrap_pyfunction!(sort_string_keys, module)?)?;
    module.add_function(wrap_pyfunction!(any_ascii_casefold_in, module)?)?;
    module.add_function(wrap_pyfunction!(ordered_string_counts, module)?)?;
    module.add_function(wrap_pyfunction!(most_common_string_counts, module)?)?;
    module.add_function(wrap_pyfunction!(sorted_unique_strings, module)?)?;
    module.add_function(wrap_pyfunction!(ascii_casefold_equals, module)?)?;
    module.add_function(wrap_pyfunction!(
        ascii_case_insensitive_substring_match_values,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        ascii_case_insensitive_substring_match_groups_py,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(unique_ascii_tuple_indices, module)?)?;
    module.add_function(wrap_pyfunction!(delimited_row_fields, module)?)?;
    module.add_function(wrap_pyfunction!(delimited_text_rows, module)?)?;
    module.add_function(wrap_pyfunction!(xscr_command_records, module)?)?;
    module.add_function(wrap_pyfunction!(render_append_list, module)?)?;
    module.add_function(wrap_pyfunction!(render_append_dict, module)?)?;
    module.add_function(wrap_pyfunction!(render_object_report, module)?)?;
    module.add_function(wrap_pyfunction!(render_gwl_report, module)?)?;
    module.add_function(wrap_pyfunction!(render_compare_report, module)?)?;
    module.add_function(wrap_pyfunction!(render_pattern_specs, module)?)?;
    module.add_function(wrap_pyfunction!(append_unique_ascii_string, module)?)?;
    module.add_function(wrap_pyfunction!(pattern_specification_groups, module)?)?;
    module.add_function(wrap_pyfunction!(pattern_join_summary, module)?)?;
    module.add_function(wrap_pyfunction!(pattern_liquid_step, module)?)?;
    module.add_function(wrap_pyfunction!(render_pattern_types_report, module)?)?;
    module.add_function(wrap_pyfunction!(render_script_pattern_search, module)?)?;
    module.add_function(wrap_pyfunction!(render_script_pattern_summary, module)?)?;
    module.add_function(wrap_pyfunction!(render_project_index_search, module)?)?;
    module.add_function(wrap_pyfunction!(render_project_index_summary, module)?)?;
    module.add_function(wrap_pyfunction!(render_directory_report, module)?)?;
    module.add_function(wrap_pyfunction!(render_zeia_report, module)?)?;
    module.add_function(wrap_pyfunction!(render_xscr_report, module)?)?;
    module.add_function(wrap_pyfunction!(render_join_lines, module)?)?;
    module.add_function(wrap_pyfunction!(decode_text_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(read_text_file, module)?)?;
    module.add_function(wrap_pyfunction!(read_utf8_sig_file, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_source_paths, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_stat_fingerprint, module)?)?;
    module.add_function(wrap_pyfunction!(source_content_fingerprint, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_content_fingerprints, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json_compact, module)?)?;
    module.add_function(wrap_pyfunction!(diagnostic_sort_indices, module)?)?;
    module.add_function(wrap_pyfunction!(diagnostic_message, module)?)?;
    module.add_function(wrap_pyfunction!(xml_kind_from_suffix, module)?)?;
    module.add_function(wrap_pyfunction!(command_short_name, module)?)?;
    module.add_function(wrap_pyfunction!(entry_object_guid, module)?)?;
    module.add_function(wrap_pyfunction!(asset_reference_candidates, module)?)?;
    module.add_function(wrap_pyfunction!(asset_reference_names, module)?)?;
    module.add_function(wrap_pyfunction!(xml_asset_reference_names, module)?)?;
    module.add_function(wrap_pyfunction!(collapse_ascii_whitespace, module)?)?;
    module.add_function(wrap_pyfunction!(pin_reference_candidates, module)?)?;
    module.add_function(wrap_pyfunction!(compact_list, module)?)?;
    module.add_function(wrap_pyfunction!(extension_counts, module)?)?;
    module.add_function(wrap_pyfunction!(path_suffixes, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_execution_contract, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_load_contract, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_validate_transfers, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_load_transfers, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_bool, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_well_to_position, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_tip_selection, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_enabled_tip_mask, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_build_transfers, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_build_schedule, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_gwl, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_gwl_records, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_inspect_gwl_lines, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_gwl_text_records, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_read_gwl_records, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_format_volume, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_clean_field, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_ascii_uppercase, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_wash_type, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_comment_line, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_record_type_line, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_join_gwl_fields, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_serialize_gwl_lines, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_count_record_types, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_analyze_lines, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_optimize_lines, module)?)?;
    module.add_function(wrap_pyfunction!(deepcopy_json, module)?)?;
    module.add_function(wrap_pyfunction!(to_jsonable_containers, module)?)?;
    module.add_function(wrap_pyfunction!(xscr_object_fields, module)?)?;
    module.add_function(wrap_pyfunction!(pattern_windows, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ascii_case_insensitive_substring_groups_match_python_for_supported_ascii() {
        let values = vec!["Vendor.SubRoutine".into(), "ExecuteVBScript".into()];
        let groups = vec![
            vec!["subroutine".into()],
            vec!["executevbscript".into(), "executeapplication".into()],
            vec!["worklist".into()],
            vec!["".into()],
        ];
        assert_eq!(
            ascii_case_insensitive_substring_match_groups(&values, &groups),
            Some(vec![true, true, false, true])
        );
        assert_eq!(
            ascii_case_insensitive_substring_match_groups(&["é".into()], &groups),
            None
        );
    }

    #[test]
    fn markdown_join_preserves_embedded_and_final_newlines() {
        assert_eq!(
            render_join_lines(vec!["α".into(), "line\nbreak".into()]),
            "α\nline\nbreak\n"
        );
        assert_eq!(render_join_lines(Vec::new()), "\n");
    }

    #[test]
    fn gwl_report_render_preserves_sections_and_order() {
        let report = render_gwl_report(
            "source-x",
            "line-count-x",
            "estimate-x",
            vec![("record-x".into(), "count-x".into())],
            vec![vec![
                "line-x".into(),
                "operation-x".into(),
                "rack-x".into(),
                "position-x".into(),
                "volume-x".into(),
                "class-x".into(),
            ]],
        );
        assert_eq!(
            report.as_deref(),
            Some(
                "# GWL Report: source-x\n\n- Lines: `line-count-x`\n- Transfer pairs estimate: `estimate-x`\n\n## Record Counts\n- `record-x`: count-x\n\n## Pipette Examples\n- line line-x: `operation-x` rack-x pos position-x vol volume-x lc `class-x`\n"
            )
        );
        assert_eq!(
            render_gwl_report("source", "0", "0", Vec::new(), Vec::new()).as_deref(),
            Some("# GWL Report: source\n\n- Lines: `0`\n- Transfer pairs estimate: `0`\n")
        );
        assert!(render_gwl_report("", "", "", Vec::new(), vec![vec!["short".into()]]).is_none());
    }

    #[test]
    fn compare_report_render_preserves_section_order_and_empty_sections() {
        assert_eq!(
            render_compare_report(
                "left-x",
                "right-x",
                "2",
                "3",
                "1",
                vec!["added-x".into()],
                Vec::new(),
                vec![("family-x".into(), "2".into())],
                Vec::new(),
            ),
            "# ZEIA Compare Report\n\n- Left: `left-x`\n- Right: `right-x`\n- Entries: `2` -> `3`\n- Common script names: `1`\n\n## Scripts Added\n- added-x\n\n## Left Command Families\n- `family-x`: 2\n"
        );
    }

    #[test]
    fn xscr_report_preserves_section_order_and_command_formatting() {
        assert_eq!(
            render_xscr_report(
                vec![
                    "title-x".into(),
                    "source-x".into(),
                    "unknown".into(),
                    "4".into(),
                ],
                vec![vec!["Reference".into(), "object-x".into(), "guid-x".into(),]],
                vec![("family-x".into(), "2".into())],
                vec![("operation-x".into(), "1".into())],
                vec!["variable-x (unknown)".into()],
                vec!["prompt-x: question-x [1..2]".into()],
                true,
                vec![vec![
                    "dependency-x".into(),
                    "`mention-a`, `mention-b`".into(),
                ]],
                vec!["comment-x".into()],
                vec!["warning-x".into()],
                vec![vec![
                    "1".into(),
                    "?".into(),
                    "operation-x".into(),
                    "family-x".into(),
                    "label-x".into(),
                ]],
            )
            .as_deref(),
            Some(
                "# XSCR Report: title-x\n\n- Source: `source-x`\n- Script version: `unknown`\n- Command count: `4`\n\n## References\n- Reference: `object-x` (`guid-x`)\n\n## Command Families\n- `family-x`: 2\n\n## Command Types\n- `operation-x`: 1\n\n## Variables\n- variable-x (unknown)\n\n## Query Prompts\n- prompt-x: question-x [1..2]\n\n## Dependencies And Mentions\n- dependency-x: `mention-a`, `mention-b`\n\n## Comments\n- comment-x\n\n## Warnings\n- warning-x\n\n## First Commands\n- #1 line ?: `operation-x` (family-x) label-x\n"
            )
        );
    }

    #[test]
    fn zeia_report_preserves_sections_and_nested_summary_order() {
        assert_eq!(
            render_zeia_report(
                vec![
                    "source-x".into(),
                    "entries-x".into(),
                    "scripts-x".into(),
                    "summarized-x".into(),
                    "objects-x".into(),
                    "worklists-x".into(),
                ],
                vec![
                    vec![("extension-x".into(), "count-x".into())],
                    vec![("family-x".into(), "count-y".into())],
                    Vec::new(),
                    vec![("warning-x".into(), "count-z".into())],
                ],
                vec!["script-name-x".into()],
                vec![vec![
                    "object-x".into(),
                    "commands-x".into(),
                    "key-x".into(),
                    "value-x".into(),
                ]],
                vec![vec!["entry-x".into(), "error-x".into()]],
            )
            .as_deref(),
            Some(
                "# ZEIA Report: source-x\n\n- Entries: `entries-x`\n- Scripts: `scripts-x` total, `summarized-x` summarized\n- XML objects summarized: `objects-x`\n- GWL files summarized: `worklists-x`\n\n## File Extensions\n- `extension-x`: count-x\n\n## Command Families\n- `family-x`: count-y\n\n## Warning Counts\n- `warning-x`: count-z\n\n## Script Names\n- script-name-x\n\n## Script Summaries\n- `object-x`: commands-x commands (key-x=value-x)\n\n## Parse Errors\n- `entry-x`: error-x\n"
            )
        );
    }

    #[test]
    fn script_pattern_search_preserves_nested_sections_and_result_order() {
        assert_eq!(
            render_script_pattern_search(
                vec![
                    "database-x".into(),
                    "query-x".into(),
                    "any".into(),
                    "source-x".into(),
                    "1".into(),
                ],
                vec![(
                    vec![
                        "pattern-x".into(),
                        "type-x".into(),
                        "label-x".into(),
                        "script-x".into(),
                        "1".into(),
                        "2".into(),
                        "zeia-x".into(),
                        "path-x".into(),
                    ],
                    vec![("key-x".into(), vec!["value-x".into()])],
                    vec!["note-x".into()],
                    vec![vec![
                        "1".into(),
                        "summary-x".into(),
                        "command-x".into(),
                        "3".into(),
                    ]],
                )],
            )
            .as_deref(),
            Some(
                "# Tecan Script Pattern Search\n\n- Database: `database-x`\n- Query: `query-x`\n- Pattern type filter: `any`\n- Source script filter: `source-x`\n- Results: `1`\n\n## Patterns\n1. Pattern: `pattern-x`\n   - Type: `type-x` (label-x)\n   - Source script: `script-x`\n   - Commands: `1` to `2`\n   - ZEIA: `zeia-x`\n   - Path: `path-x`\n   - Specifications:\n     - key-x: `value-x`\n   - Safety notes:\n     - note-x\n   - Steps:\n     1. summary-x (`command-x`, command `3`)\n        - Path: `path-x`\n"
            )
        );
    }

    #[test]
    fn directory_report_preserves_sections_and_script_family_order() {
        assert_eq!(
            render_directory_report(
                vec!["source-x".into(), "file-count-x".into()],
                vec![("extension-x".into(), "1".into())],
                vec![("family-x".into(), "2".into())],
                Vec::new(),
                Vec::new(),
                vec!["script-name-x".into()],
                vec![vec![
                    "object-x".into(),
                    "command-count-x".into(),
                    "key-a".into(),
                    "value-a".into(),
                    "key-b".into(),
                    "value-b".into(),
                ]],
                vec!["file-source-x".into()],
            )
            .as_deref(),
            Some(
                "# Directory Report: source-x\n\n- Files found: `file-count-x`\n\n## File Extensions\n- `extension-x`: 1\n\n## Command Families\n- `family-x`: 2\n\n## Script Names\n- script-name-x\n\n## Script Summaries\n- `object-x`: command-count-x commands (key-a=value-a, key-b=value-b)\n\n## Files\n- file-source-x\n"
            )
        );
        assert!(
            render_directory_report(
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                vec![vec!["short".into()]],
                Vec::new()
            )
            .is_none()
        );
    }

    #[test]
    fn project_index_summary_preserves_sections_and_file_rows() {
        assert_eq!(
            render_project_index_summary(
                vec![
                    "Tecan Project Index Build".into(),
                    "database-x".into(),
                    "schema-x".into(),
                    "1".into(),
                    "2".into(),
                    "3".into(),
                    "4".into(),
                    "5".into(),
                    "6".into(),
                ],
                vec!["indexed-x".into()],
                vec![("entity-x".into(), "count-x".into())],
                vec![("key-x".into(), "count-y".into())],
                vec![vec![
                    "file-x".into(),
                    "script-x".into(),
                    "object-x".into(),
                    "worklist-x".into(),
                    "path-x".into(),
                ]],
                1,
            )
            .as_deref(),
            Some(
                "# Tecan Project Index Build\n\n- Database: `database-x`\n- Schema version: `schema-x`\n- ZEIA files: `1`\n- Scripts: `2`\n- Commands: `3`\n- Command sequences: `4`\n- Catalog objects: `5`\n- Worklists: `6`\n\n## Indexed Files\n- indexed-x\n\n## Entity Counts\n- `entity-x`: count-x\n\n## Command Family Counts\n- `key-x`: count-y\n\n## ZEIA Files\n- `file-x`: script-x scripts, object-x objects, worklist-x worklists\n  - Path: `path-x`\n"
            )
        );
        assert!(
            render_project_index_summary(
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                0
            )
            .is_none()
        );
    }

    #[test]
    fn project_index_search_preserves_optional_result_fields() {
        assert_eq!(
            render_project_index_search(
                "database-x",
                "query-β",
                "any",
                "1",
                vec![vec![
                    "kind-x".into(),
                    "title-x".into(),
                    "value-x".into(),
                    "script-x".into(),
                    "0".into(),
                    "file-x".into(),
                    "path-x".into(),
                    "1".into(),
                ]],
            )
            .as_deref(),
            Some(
                "# Tecan Project Index Search\n\n- Database: `database-x`\n- Query: `query-β`\n- Kind filter: `any`\n- Results: `1`\n\n## Results\n1. `kind-x` `title-x`\n   - Detail: `value-x`\n   - Script: `script-x`\n   - Command: `0`\n   - ZEIA: `file-x`\n   - Path: `path-x`\n"
            )
        );
        assert_eq!(
            render_project_index_search("", "", "any", "0", Vec::new()).as_deref(),
            Some(
                "# Tecan Project Index Search\n\n- Database: ``\n- Query: ``\n- Kind filter: `any`\n- Results: `0`\n"
            )
        );
    }

    #[test]
    fn script_pattern_summary_preserves_modes_counts_and_sources() {
        assert_eq!(
            render_script_pattern_summary(
                "Tecan Script Pattern Mining",
                "database-x",
                "schema-x",
                "pattern-count-x",
                "step-count-x",
                true,
                "mined-x",
                "replaced-x",
                vec![("key-x".into(), "count-x".into())],
                vec![vec!["source-x".into(), "count-y".into(), "file-x".into()]],
            )
            .as_deref(),
            Some(
                "# Tecan Script Pattern Mining\n\n- Database: `database-x`\n- Pattern schema version: `schema-x`\n- Patterns: `pattern-count-x`\n- Steps: `step-count-x`\n- Mined this run: `mined-x`\n- Replaced existing: `replaced-x`\n\n## Pattern Type Counts\n- `key-x`: count-x\n\n## Top Source Scripts\n- `source-x`: count-y patterns\n  - ZEIA: `file-x`\n"
            )
        );
        assert_eq!(
            render_script_pattern_summary(
                "Tecan Script Patterns",
                "",
                "",
                "",
                "",
                false,
                "",
                "",
                Vec::new(),
                Vec::new(),
            )
            .as_deref(),
            Some(
                "# Tecan Script Patterns\n\n- Database: ``\n- Pattern schema version: ``\n- Patterns: ``\n- Steps: ``\n"
            )
        );
    }

    #[test]
    fn pattern_types_report_preserves_rows_and_empty_report() {
        assert_eq!(
            render_pattern_types_report(vec![
                vec!["kind-x".into(), "label-x".into(), "description-x".into()],
                vec!["None".into(), "None".into(), "β".into()],
            ])
            .as_deref(),
            Some(
                "# Tecan Script Pattern Types\n\n- `kind-x`: label-x\n  - description-x\n- `None`: None\n  - β\n"
            )
        );
        assert_eq!(
            render_pattern_types_report(Vec::new()).as_deref(),
            Some("# Tecan Script Pattern Types\n\n")
        );
        assert!(render_pattern_types_report(vec![vec!["short".into()]]).is_none());
    }

    #[test]
    fn pattern_spec_render_preserves_order_empty_values_and_limit() {
        let entries = vec![
            ("empty".into(), Vec::new()),
            (
                "first".into(),
                (0..14).map(|index| format!("value-{index}")).collect(),
            ),
            ("unicode".into(), vec!["β".into()]),
        ];
        assert_eq!(
            render_pattern_specs(entries),
            vec![
                "   - Specifications:",
                "     - first: `value-0`, `value-1`, `value-2`, `value-3`, `value-4`, `value-5`, `value-6`, `value-7`, `value-8`, `value-9`, `value-10`, `value-11`, ... 2 more",
                "     - unicode: `β`",
            ]
        );
        assert!(render_pattern_specs(Vec::new()).is_empty());
    }

    #[test]
    fn report_list_render_matches_slice_and_overflow_rules() {
        assert_eq!(
            render_append_list("Items", vec!["a".into(), "β".into(), "c".into()], 2),
            vec!["", "## Items", "- a", "- β", "- ... 1 more"]
        );
        assert_eq!(
            render_append_list("Items", vec!["a".into(), "b".into(), "c".into()], 0),
            vec!["", "## Items", "- ... 3 more"]
        );
        assert_eq!(
            render_append_list("Items", vec!["a".into(), "b".into(), "c".into()], -1),
            vec!["", "## Items", "- a", "- b", "- ... 4 more"]
        );
        assert!(render_append_list("Empty", Vec::new(), 20).is_empty());
    }

    #[test]
    fn report_dict_render_preserves_order_and_python_limit_behavior() {
        let entries = vec![
            ("first".into(), "one".into()),
            ("second".into(), "2".into()),
        ];
        assert_eq!(
            render_append_dict("Counts", entries.clone(), 1),
            vec!["", "## Counts", "- `first`: one", "- ... 1 more"]
        );
        assert_eq!(
            render_append_dict("Counts", entries, -1),
            vec!["", "## Counts", "- ... 3 more"]
        );
        assert!(render_append_dict("Empty", Vec::new(), 20).is_empty());
    }

    #[test]
    fn xml_object_report_uses_expected_labels_and_line_endings() {
        let report = render_object_report(
            "Example",
            vec![
                "component".into(),
                "source".into(),
                "type".into(),
                "group".into(),
                "footprint".into(),
            ],
            vec!["name".into()],
            vec!["guid".into()],
        );
        assert_eq!(
            report.as_deref(),
            Some(
                "# XML Object Report: Example\n\n- Kind: `component`\n- Source: `source`\n- Type ID: `type`\n- Functional group: `group`\n- Footprint: `footprint`\n\n## Names\n- name\n\n## GUIDs\n- guid\n"
            )
        );
        assert!(render_object_report("Example", Vec::new(), Vec::new(), Vec::new()).is_none());
    }

    #[test]
    fn collapse_ascii_whitespace_matches_python_split_join_rules() {
        assert_eq!(
            collapse_ascii_whitespace(" \talpha\n\t beta\u{1c}gamma \r\n"),
            Some("alpha beta gamma".to_string())
        );
        assert_eq!(collapse_ascii_whitespace(" \u{1f} \t"), Some(String::new()));
        assert_eq!(
            collapse_ascii_whitespace("café\u{00a0}user"),
            Some("café user".to_string())
        );
        assert_eq!(
            collapse_ascii_whitespace("a\u{2003}\u{2003}b"),
            Some("a b".to_string())
        );
    }

    #[test]
    fn xml_asset_reference_scan_preserves_legacy_greedy_runs() {
        assert_eq!(
            xml_asset_reference_names("<A>a.png and b.jpg</A><B>x.tiff</B><C>.bmp</C><D>z.bmp</D>"),
            Some(vec![
                "a.png and b.jpg".to_string(),
                "x.tiff".to_string(),
                "z.bmp".to_string(),
            ])
        );
        assert_eq!(xml_asset_reference_names("café.png"), None);
    }

    #[test]
    fn pin_reference_matching_preserves_boundaries_and_sorting() {
        assert_eq!(
            pin_reference_candidates(vec![
                "prefixGIO1_Pin2".into(),
                "(GIO1_Pin2) GIO12_Pin3".into(),
                "xWorktable_Pin".into(),
                "Worktable_Pin Worktable_namePin_suffix".into(),
                "WorktablePin_extra WorktablePin_".into(),
                "GIO3_Pin4x".into(),
            ]),
            Some(vec![
                "GIO12_Pin3".to_string(),
                "GIO1_Pin2".to_string(),
                "WorktablePin_extra".to_string(),
                "Worktable_Pin".to_string(),
                "Worktable_namePin_suffix".to_string(),
            ])
        );
    }

    #[test]
    fn asset_reference_candidate_matching_preserves_supported_boundaries() {
        for value in [
            "folder/ICON.JPEG",
            "picture.pngsometext",
            "picture.png\\path",
            "picture.bmp'quote",
            "picture.tiff\"quote",
        ] {
            assert!(has_supported_asset_extension(value), "{value:?}");
        }
        for value in [
            "picture.bmpx",
            "picture.png ",
            "picture.gif\tquote",
            "picture.tif\u{1c}boundary",
            "picture.png/path",
            "picture.jp",
            "plain text",
        ] {
            assert!(!has_supported_asset_extension(value), "{value:?}");
        }
    }

    #[test]
    fn migrated_xml_string_helpers_cover_suffix_and_text_edges() {
        assert_eq!(xml_tag_local_name("{urn:test}name"), "name");
        assert_eq!(xml_tag_local_name("{first}{second}name"), "name");
        assert_eq!(xml_clean_text(" \tvalue\r\n"), "value");
        assert_eq!(xml_kind_from_suffix(".XWSP"), Some("workspace".into()));
        assert_eq!(xml_kind_from_suffix("...Custom"), Some("custom".into()));
    }
}
