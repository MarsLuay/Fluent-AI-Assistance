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
use std::collections::{BTreeMap, HashMap, HashSet};
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

#[pyfunction]
fn decode_text_bytes(data: &Bound<'_, PyBytes>) -> String {
    let bytes = data.as_bytes();
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
fn canonical_json(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
    let json = py_to_json(py, value)?;
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
    module.add_function(wrap_pyfunction!(decode_text_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_source_paths, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_stat_fingerprint, module)?)?;
    module.add_function(wrap_pyfunction!(source_content_fingerprint, module)?)?;
    module.add_function(wrap_pyfunction!(catalog_content_fingerprints, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(command_short_name, module)?)?;
    module.add_function(wrap_pyfunction!(compact_list, module)?)?;
    module.add_function(wrap_pyfunction!(extension_counts, module)?)?;
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
