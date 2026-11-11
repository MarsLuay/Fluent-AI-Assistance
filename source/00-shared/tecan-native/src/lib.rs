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

fn is_safe_clean_key(py: Python<'_>, key: &Bound<'_, PyAny>) -> bool {
    let key_type = key.get_type();
    key.is_none()
        || key_type.is(py.get_type::<PyBool>())
        || key_type.is(py.get_type::<PyInt>())
        || key_type.is(py.get_type::<PyFloat>())
        || key_type.is(py.get_type::<PyString>())
        || key_type.is(py.get_type::<PyBytes>())
}

fn is_clean_value_omitted(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    if value.is_none() {
        return Ok(true);
    }
    let py = value.py();
    let value_type = value.get_type();
    if value_type.is(py.get_type::<PyString>()) {
        return Ok(value.extract::<String>()?.is_empty());
    }
    if value_type.is(py.get_type::<PyList>()) {
        return Ok(value.cast::<PyList>()?.is_empty());
    }
    if value_type.is(py.get_type::<PyDict>()) {
        return Ok(value.cast::<PyDict>()?.is_empty());
    }
    Ok(false)
}

fn try_clean_empty_containers<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    active: &mut HashSet<usize>,
    depth: usize,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if depth > 256 {
        return Ok(None);
    }
    let value_type = value.get_type();
    if value.is_none()
        || value_type.is(py.get_type::<PyBool>())
        || value_type.is(py.get_type::<PyInt>())
        || value_type.is(py.get_type::<PyFloat>())
        || value_type.is(py.get_type::<PyString>())
        || value_type.is(py.get_type::<PyBytes>())
        || value_type.is(py.get_type::<PyTuple>())
    {
        return Ok(Some(value.clone()));
    }

    let pointer = value.as_ptr() as usize;
    if value_type.is(py.get_type::<PyList>()) {
        if !active.insert(pointer) {
            return Ok(None);
        }
        let items = value.cast::<PyList>()?;
        let output = PyList::empty(py);
        for item in items.iter() {
            let Some(cleaned) = try_clean_empty_containers(py, &item, active, depth + 1)? else {
                active.remove(&pointer);
                return Ok(None);
            };
            if !is_clean_value_omitted(&cleaned)? {
                output.append(cleaned)?;
            }
        }
        active.remove(&pointer);
        return Ok(Some(output.into_any()));
    }

    if value_type.is(py.get_type::<PyDict>()) {
        if !active.insert(pointer) {
            return Ok(None);
        }
        let items = value.cast::<PyDict>()?;
        let output = PyDict::new(py);
        for (key, item) in items.iter() {
            if !is_safe_clean_key(py, &key) {
                active.remove(&pointer);
                return Ok(None);
            }
            let Some(cleaned) = try_clean_empty_containers(py, &item, active, depth + 1)? else {
                active.remove(&pointer);
                return Ok(None);
            };
            if !is_clean_value_omitted(&cleaned)? {
                output.set_item(key, cleaned)?;
            }
        }
        active.remove(&pointer);
        return Ok(Some(output.into_any()));
    }

    Ok(None)
}

#[pyfunction]
fn clean_empty_containers(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<(bool, Py<PyAny>)> {
    match try_clean_empty_containers(py, value, &mut HashSet::new(), 0)? {
        Some(cleaned) => Ok((true, cleaned.unbind())),
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

fn is_python_ascii_whitespace(byte: u8) -> bool {
    matches!(byte, 0x09..=0x0d | 0x1c..=0x20)
}

fn is_python_regex_ascii_whitespace(byte: u8) -> bool {
    matches!(byte, 0x09..=0x0d | b' ')
}

fn has_gwl_record_prefix_ascii(text: &str) -> Option<bool> {
    if !text.is_ascii() {
        return None;
    }
    let bytes = text.as_bytes();
    let mut line_start = 0;
    loop {
        let mut index = line_start;
        while index < bytes.len() && is_python_ascii_whitespace(bytes[index]) {
            index += 1;
        }
        if index < bytes.len() {
            let command = bytes[index].to_ascii_uppercase();
            if matches!(command, b'A' | b'D' | b'W' | b'B') {
                index += 1;
                if index < bytes.len() && matches!(bytes[index], b'1'..=b'4') {
                    index += 1;
                }
                if index < bytes.len() && bytes[index] == b';' {
                    return Some(true);
                }
            }
        }
        let Some(next_line) = bytes[line_start..].iter().position(|byte| *byte == b'\n') else {
            break;
        };
        line_start += next_line + 1;
    }
    Some(false)
}

#[pyfunction]
fn has_gwl_record_prefix(text: &str) -> Option<bool> {
    has_gwl_record_prefix_ascii(text)
}

fn optional_ascii_xml_prefix_length(bytes: &[u8]) -> usize {
    let Some(first) = bytes.first().copied() else {
        return 0;
    };
    if !(first.is_ascii_alphabetic() || first == b'_') {
        return 0;
    }
    let mut index = 1;
    while index < bytes.len()
        && (is_ascii_word(bytes[index]) || matches!(bytes[index], b'.' | b'-'))
    {
        index += 1;
    }
    if index < bytes.len() && bytes[index] == b':' {
        index + 1
    } else {
        0
    }
}

fn xml_name_matches_at_ascii(text: &[u8], start: usize, name: &[u8]) -> bool {
    start + name.len() <= text.len() && text[start..start + name.len()].eq_ignore_ascii_case(name)
}

fn xml_opening_tag_value_end_ascii(text: &[u8], start: usize, name: &[u8]) -> Option<usize> {
    if text.get(start) != Some(&b'<') {
        return None;
    }
    let tag_start = start + 1;
    let name_start = tag_start + optional_ascii_xml_prefix_length(&text[tag_start..]);
    if !xml_name_matches_at_ascii(text, name_start, name) {
        return None;
    }
    let after_name = name_start + name.len();
    match text.get(after_name).copied()? {
        b'>' => Some(after_name + 1),
        byte if is_python_regex_ascii_whitespace(byte) => text[after_name + 1..]
            .iter()
            .position(|byte| *byte == b'>')
            .map(|relative_end| after_name + 1 + relative_end + 1),
        _ => None,
    }
}

fn xml_closing_tag_matches_ascii(text: &[u8], start: usize, name: &[u8]) -> Option<usize> {
    if text.get(start..start + 2)? != b"</" {
        return None;
    }
    let tag_start = start + 2;
    let name_start = tag_start + optional_ascii_xml_prefix_length(&text[tag_start..]);
    if !xml_name_matches_at_ascii(text, name_start, name) {
        return None;
    }
    let close = name_start + name.len();
    (text.get(close) == Some(&b'>')).then_some(close + 1)
}

fn collapse_python_regex_ascii_whitespace(value: &str) -> String {
    value
        .split(|character| matches!(character, '\t'..='\r' | ' '))
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn extract_line_number_ascii_value(text: &str) -> String {
    const OPEN: &[u8] = b"<LineNumber>";
    const CLOSE: &[u8] = b"</LineNumber>";
    let bytes = text.as_bytes();
    let mut search_start = 0;
    while search_start + OPEN.len() <= bytes.len() {
        let Some(relative_open) = bytes[search_start..]
            .windows(OPEN.len())
            .position(|window| window == OPEN)
        else {
            break;
        };
        let open = search_start + relative_open;
        let mut cursor = open + OPEN.len();
        while cursor < bytes.len() && is_python_ascii_whitespace(bytes[cursor]) {
            cursor += 1;
        }
        let digits_start = cursor;
        while cursor < bytes.len() && bytes[cursor].is_ascii_digit() {
            cursor += 1;
        }
        if cursor > digits_start {
            let digits_end = cursor;
            while cursor < bytes.len() && is_python_ascii_whitespace(bytes[cursor]) {
                cursor += 1;
            }
            if bytes[cursor..].starts_with(CLOSE) {
                return text[digits_start..digits_end].to_owned();
            }
        }
        search_start = open + 1;
    }
    String::new()
}

#[pyfunction]
fn extract_line_number_ascii(text: &str) -> Option<String> {
    if !text.is_ascii() {
        return None;
    }
    Some(extract_line_number_ascii_value(text))
}

fn normalize_line_number_tags_ascii_value(text: &str) -> Option<String> {
    const OPEN: &[u8] = b"<LineNumber>";
    const CLOSE: &[u8] = b"</LineNumber>";
    const REPLACEMENT: &str = "<LineNumber>0</LineNumber>";
    if !text.is_ascii() {
        return None;
    }
    let bytes = text.as_bytes();
    let mut output = String::with_capacity(text.len());
    let mut cursor = 0;
    let mut copied_through = 0;
    while cursor + OPEN.len() <= bytes.len() {
        let open_end = cursor + OPEN.len();
        if !bytes[cursor..open_end].eq_ignore_ascii_case(OPEN) {
            cursor += 1;
            continue;
        }
        let mut value_start = open_end;
        while value_start < bytes.len() && is_python_ascii_whitespace(bytes[value_start]) {
            value_start += 1;
        }
        let mut value_end = value_start;
        while value_end < bytes.len() && bytes[value_end].is_ascii_digit() {
            value_end += 1;
        }
        if value_start == value_end {
            cursor += 1;
            continue;
        }
        let mut close_start = value_end;
        while close_start < bytes.len() && is_python_ascii_whitespace(bytes[close_start]) {
            close_start += 1;
        }
        let Some(close_end) = close_start.checked_add(CLOSE.len()) else {
            cursor += 1;
            continue;
        };
        if bytes
            .get(close_start..close_end)
            .is_some_and(|closing| closing.eq_ignore_ascii_case(CLOSE))
        {
            output.push_str(&text[copied_through..cursor]);
            output.push_str(REPLACEMENT);
            cursor = close_end;
            copied_through = close_end;
        } else {
            cursor += 1;
        }
    }
    output.push_str(&text[copied_through..]);
    Some(output)
}

#[pyfunction]
fn xml_normalize_line_numbers_ascii(text: &str) -> Option<String> {
    normalize_line_number_tags_ascii_value(text)
}

fn normalize_xml_ascii_tail_value(text: &str) -> Option<String> {
    if !text.is_ascii() {
        return None;
    }
    let lowered: String = text
        .bytes()
        .filter(|byte| !is_python_ascii_whitespace(*byte))
        .map(|byte| byte.to_ascii_lowercase() as char)
        .collect();
    let bytes = lowered.as_bytes();
    let mut output = String::with_capacity(lowered.len());
    let mut cursor = 0;
    let mut copied_through = 0;
    while cursor < bytes.len() {
        if bytes[cursor] != b'<' {
            cursor += 1;
            continue;
        }
        let mut name_end = cursor + 1;
        while bytes.get(name_end).is_some_and(|byte| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || matches!(*byte, b'_' | b':' | b'.' | b'-')
        }) {
            name_end += 1;
        }
        if name_end == cursor + 1
            || !bytes
                .get(name_end..)
                .is_some_and(|suffix| suffix.starts_with(b"/>"))
        {
            cursor += 1;
            continue;
        }
        let name = &lowered[cursor + 1..name_end];
        output.push_str(&lowered[copied_through..cursor]);
        output.push('<');
        output.push_str(name);
        output.push_str("></");
        output.push_str(name);
        output.push('>');
        cursor = name_end + 2;
        copied_through = cursor;
    }
    output.push_str(&lowered[copied_through..]);
    Some(output)
}

#[pyfunction]
fn xml_normalize_ascii_tail(text: &str) -> Option<String> {
    normalize_xml_ascii_tail_value(text)
}

fn xml_comparison_tag_names_ascii(text: &str, allow_punctuation: bool) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut tags = Vec::new();
    let mut cursor = 0;
    while cursor < bytes.len() {
        if bytes[cursor] != b'<' {
            cursor += 1;
            continue;
        }
        let start = cursor + 1;
        let mut end = start;
        while bytes.get(end).is_some_and(|byte| {
            byte.is_ascii_alphanumeric()
                || matches!(*byte, b'_' | b':')
                || (allow_punctuation && matches!(*byte, b'.' | b'-'))
        }) {
            end += 1;
        }
        if end > start && bytes.get(end) == Some(&b'>') {
            tags.push(&text[start..end]);
            cursor = end + 1;
        } else {
            cursor += 1;
        }
    }
    tags
}

fn xml_compare_diff_hint_ascii_value(expected: &str, actual: &str) -> Option<String> {
    if !expected.is_ascii() || !actual.is_ascii() {
        return None;
    }
    let expected_tags = xml_comparison_tag_names_ascii(expected, true);
    let actual_tags = xml_comparison_tag_names_ascii(actual, false);
    for (index, (expected_tag, actual_tag)) in expected_tags.iter().zip(&actual_tags).enumerate() {
        if expected_tag != actual_tag {
            return Some(format!(
                "tag mismatch at index {index}: '{expected_tag}' vs '{actual_tag}'"
            ));
        }
    }
    if expected_tags.len() != actual_tags.len() {
        return Some(format!(
            "tag count mismatch: {} vs {}",
            expected_tags.len(),
            actual_tags.len()
        ));
    }
    Some("payload mismatch after normalization".to_owned())
}

#[pyfunction]
fn xml_compare_diff_hint_ascii(expected: &str, actual: &str) -> Option<String> {
    xml_compare_diff_hint_ascii_value(expected, actual)
}

fn xml_regex_first_value_ascii(text: &str, name: &str) -> Option<String> {
    if !text.is_ascii() || !name.is_ascii() || name.is_empty() || name.contains(':') {
        return None;
    }
    let bytes = text.as_bytes();
    let name = name.as_bytes();
    let mut search_start = 0;
    while let Some(relative_open) = bytes[search_start..].iter().position(|byte| *byte == b'<') {
        let open = search_start + relative_open;
        let Some(value_start) = xml_opening_tag_value_end_ascii(bytes, open, name) else {
            search_start = open + 1;
            continue;
        };
        let mut close_search = value_start;
        while let Some(relative_close) = bytes[close_search..].iter().position(|byte| *byte == b'<')
        {
            let close = close_search + relative_close;
            if xml_closing_tag_matches_ascii(bytes, close, name).is_some() {
                let captured = &text[value_start..close];
                return Some(collapse_python_regex_ascii_whitespace(captured));
            }
            close_search = close + 1;
        }
        search_start = open + 1;
    }
    Some(String::new())
}

#[pyfunction]
fn xml_regex_first_value(text: &str, name: &str) -> Option<String> {
    xml_regex_first_value_ascii(text, name)
}

fn xml_local_tag_name_ascii(text: &[u8], start: usize) -> Option<(String, usize)> {
    let first_start = start + optional_ascii_xml_prefix_length(&text[start..]);
    if first_start >= text.len()
        || !(text[first_start].is_ascii_alphabetic() || text[first_start] == b'_')
    {
        return None;
    }
    let mut end = first_start + 1;
    while end < text.len() && (is_ascii_word(text[end]) || matches!(text[end], b'.' | b'-')) {
        end += 1;
    }
    Some((
        String::from_utf8(text[first_start..end].to_vec()).ok()?,
        end,
    ))
}

fn xml_unknown_text_fields_ascii(
    text: &str,
    known_names: &[String],
    limit: usize,
) -> Option<Vec<(String, Vec<String>)>> {
    if !text.is_ascii() || known_names.iter().any(|name| !name.is_ascii()) {
        return None;
    }
    let bytes = text.as_bytes();
    let known = known_names
        .iter()
        .map(String::as_str)
        .collect::<HashSet<_>>();
    let mut values = BTreeMap::<String, Vec<String>>::new();
    let mut search_start = 0;
    while let Some(relative_open) = bytes[search_start..].iter().position(|byte| *byte == b'<') {
        let open = search_start + relative_open;
        let tag_start = open + 1;
        let Some((name, name_end)) = xml_local_tag_name_ascii(bytes, tag_start) else {
            search_start = open + 1;
            continue;
        };
        let value_start = match bytes.get(name_end).copied() {
            Some(b'>') => name_end + 1,
            Some(byte) if is_python_regex_ascii_whitespace(byte) => bytes[name_end + 1..]
                .iter()
                .position(|byte| *byte == b'>')
                .map(|relative_end| name_end + 1 + relative_end + 1)
                .unwrap_or_else(|| {
                    search_start = open + 1;
                    0
                }),
            _ => {
                search_start = open + 1;
                continue;
            }
        };
        if value_start == 0 {
            continue;
        }
        let mut close_search = value_start;
        let mut matched = None;
        while let Some(relative_close) = bytes[close_search..].iter().position(|byte| *byte == b'<')
        {
            let close = close_search + relative_close;
            if bytes.get(close..close + 2) == Some(b"</")
                && xml_name_matches_at_ascii(bytes, close + 2, name.as_bytes())
                && bytes.get(close + 2 + name.len()) == Some(&b'>')
            {
                matched = Some((close, close + 3 + name.len()));
                break;
            }
            close_search = close + 1;
        }
        let Some((value_end, match_end)) = matched else {
            search_start = open + 1;
            continue;
        };
        let value = collapse_python_regex_ascii_whitespace(&text[value_start..value_end]);
        if !value.is_empty() && !value.contains('<') && !known.contains(name.as_str()) {
            let bucket = values.entry(name.clone()).or_default();
            if !bucket.contains(&value) && bucket.len() < limit {
                bucket.push(value);
            }
        }
        search_start = match_end;
    }
    Some(values.into_iter().collect())
}

#[pyfunction]
fn xml_unknown_text_fields(
    text: &str,
    known_names: Vec<String>,
    limit: usize,
) -> Option<Vec<(String, Vec<String>)>> {
    xml_unknown_text_fields_ascii(text, &known_names, limit)
}

fn extract_data_store_versions_ascii(text: &str) -> Option<Vec<String>> {
    if !text.is_ascii() {
        return None;
    }
    let bytes = text.as_bytes();
    let needle = b"datastoreversion";
    let mut versions = Vec::new();
    let mut search_start = 0;
    while search_start + needle.len() <= bytes.len() {
        let Some(relative_start) = bytes[search_start..]
            .windows(needle.len())
            .position(|candidate| candidate.eq_ignore_ascii_case(needle))
        else {
            break;
        };
        let start = search_start + relative_start;
        let mut index = start + needle.len();
        while index < bytes.len() && is_python_ascii_whitespace(bytes[index]) {
            index += 1;
        }
        if index >= bytes.len() || bytes[index] != b'=' {
            search_start = start + 1;
            continue;
        }
        index += 1;
        while index < bytes.len() && is_python_ascii_whitespace(bytes[index]) {
            index += 1;
        }
        if index >= bytes.len() || !matches!(bytes[index], b'\'' | b'"') {
            search_start = start + 1;
            continue;
        }
        index += 1;
        let value_start = index;
        while index < bytes.len() && !matches!(bytes[index], b'\'' | b'"') {
            index += 1;
        }
        if index == value_start {
            search_start = start + 1;
            continue;
        }
        versions.push(text[value_start..index].to_owned());
        search_start = index;
    }
    Some(versions)
}

#[pyfunction]
fn extract_data_store_versions(text: &str) -> Option<Vec<String>> {
    extract_data_store_versions_ascii(text)
}

fn is_ascii_word(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

fn count_xscr_object_type_attributes_ascii(text: &str) -> Option<usize> {
    if !text.is_ascii() {
        return None;
    }
    let bytes = text.as_bytes();
    let object_tag = b"<object";
    let type_attribute = b"type=";
    let mut count = 0;
    let mut search_start = 0;
    while search_start + object_tag.len() <= bytes.len() {
        let Some(relative_start) = bytes[search_start..]
            .windows(object_tag.len())
            .position(|candidate| candidate.eq_ignore_ascii_case(object_tag))
        else {
            break;
        };
        let start = search_start + relative_start;
        let after_object = start + object_tag.len();
        if after_object < bytes.len() && is_ascii_word(bytes[after_object]) {
            search_start = start + 1;
            continue;
        }
        let tag_end = bytes[after_object..]
            .iter()
            .position(|byte| *byte == b'>')
            .map_or(bytes.len(), |relative_end| after_object + relative_end);
        let mut candidate_start = after_object;
        let mut captured_end = None;
        while candidate_start + type_attribute.len() <= tag_end {
            let has_boundary = candidate_start == 0 || !is_ascii_word(bytes[candidate_start - 1]);
            if has_boundary
                && bytes[candidate_start..candidate_start + type_attribute.len()]
                    .eq_ignore_ascii_case(type_attribute)
            {
                let quote_index = candidate_start + type_attribute.len();
                if quote_index < bytes.len() && matches!(bytes[quote_index], b'\'' | b'"') {
                    let value_start = quote_index + 1;
                    let value_end = bytes[value_start..]
                        .iter()
                        .position(|byte| matches!(byte, b'\'' | b'"'))
                        .map_or(bytes.len(), |relative_end| value_start + relative_end);
                    if value_end > value_start {
                        captured_end = Some(value_end);
                    }
                }
            }
            candidate_start += 1;
        }
        if let Some(match_end) = captured_end {
            count += 1;
            search_start = match_end;
        } else {
            search_start = start + 1;
        }
    }
    Some(count)
}

#[pyfunction]
fn count_xscr_object_type_attributes(text: &str) -> Option<usize> {
    count_xscr_object_type_attributes_ascii(text)
}

fn has_visionx_namespace_ascii(text: &str) -> Option<bool> {
    if !text.is_ascii() {
        return None;
    }
    let bytes = text.as_bytes();
    let url_marker = b"tecan.com/TSCC/VisionX";
    if bytes
        .windows(url_marker.len())
        .any(|candidate| candidate.eq_ignore_ascii_case(url_marker))
    {
        return Some(true);
    }
    let marker = b"VxData";
    let mut search_start = 0;
    while let Some(relative_open) = bytes[search_start..].iter().position(|byte| *byte == b'<') {
        let open = search_start + relative_open;
        let content_start = open + 1;
        let tag_end = bytes[content_start..]
            .iter()
            .position(|byte| *byte == b'>')
            .map_or(bytes.len(), |relative_end| content_start + relative_end);
        let mut candidate_start = content_start;
        while candidate_start + marker.len() <= tag_end {
            let next = candidate_start + marker.len();
            if bytes[candidate_start..next].eq_ignore_ascii_case(marker)
                && next < bytes.len()
                && (bytes[next] == b'>' || is_python_ascii_whitespace(bytes[next]))
            {
                return Some(true);
            }
            candidate_start += 1;
        }
        if tag_end == bytes.len() {
            break;
        }
        search_start = tag_end + 1;
    }
    Some(false)
}

#[pyfunction]
fn has_visionx_namespace(text: &str) -> Option<bool> {
    has_visionx_namespace_ascii(text)
}

fn object_name_opening_tag_matches_ascii(tag_body: &[u8]) -> bool {
    let marker = b"objectname";
    let mut search_start = 0;
    while search_start + marker.len() <= tag_body.len() {
        let Some(relative_start) = tag_body[search_start..]
            .windows(marker.len())
            .position(|candidate| candidate.eq_ignore_ascii_case(marker))
        else {
            break;
        };
        let end = search_start + relative_start + marker.len();
        if end == tag_body.len() || !is_ascii_word(tag_body[end]) {
            return true;
        }
        search_start = end;
    }
    false
}

fn object_name_closing_tag_matches_ascii(tag_body: &[u8]) -> bool {
    let marker = b"objectname";
    tag_body.len() > marker.len()
        && tag_body.first() == Some(&b'/')
        && tag_body[tag_body.len() - marker.len()..].eq_ignore_ascii_case(marker)
}

fn has_ascii_object_name_tag(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut search_start = 0;
    while let Some(relative_open) = bytes[search_start..].iter().position(|byte| *byte == b'<') {
        let body_start = search_start + relative_open + 1;
        let Some(relative_end) = bytes[body_start..].iter().position(|byte| *byte == b'>') else {
            return false;
        };
        let tag_end = body_start + relative_end;
        if object_name_opening_tag_matches_ascii(&bytes[body_start..tag_end]) {
            return true;
        }
        search_start = tag_end + 1;
    }
    false
}

fn has_ascii_object_name_structure(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut search_start = 0;
    while let Some(relative_open) = bytes[search_start..].iter().position(|byte| *byte == b'<') {
        let open = search_start + relative_open;
        let body_start = open + 1;
        let Some(relative_end) = bytes[body_start..].iter().position(|byte| *byte == b'>') else {
            return false;
        };
        let close = body_start + relative_end;
        if object_name_opening_tag_matches_ascii(&bytes[body_start..close]) {
            let content_start = close + 1;
            if let Some(relative_content_end) =
                bytes[content_start..].iter().position(|byte| *byte == b'<')
            {
                let content_end = content_start + relative_content_end;
                if content_end > content_start {
                    let closing_body_start = content_end + 1;
                    if closing_body_start < bytes.len() && bytes[content_end] == b'<' {
                        if let Some(relative_closing_end) = bytes[closing_body_start..]
                            .iter()
                            .position(|byte| *byte == b'>')
                        {
                            let closing_end = closing_body_start + relative_closing_end;
                            if object_name_closing_tag_matches_ascii(
                                &bytes[closing_body_start..closing_end],
                            ) {
                                return true;
                            }
                        }
                    }
                }
            }
        }
        search_start = close + 1;
    }
    false
}

fn has_ascii_script_tag(text: &str) -> bool {
    let bytes = text.as_bytes();
    let marker = b"script";
    let mut search_start = 0;
    while let Some(relative_open) = bytes[search_start..].iter().position(|byte| *byte == b'<') {
        let body_start = search_start + relative_open + 1;
        let tag_end = bytes[body_start..]
            .iter()
            .position(|byte| *byte == b'>')
            .map_or(bytes.len(), |relative_end| body_start + relative_end);
        let mut candidate_start = body_start;
        while candidate_start + marker.len() <= tag_end {
            let marker_end = candidate_start + marker.len();
            if bytes[candidate_start..marker_end].eq_ignore_ascii_case(marker)
                && marker_end < bytes.len()
                && (bytes[marker_end] == b'>' || is_python_ascii_whitespace(bytes[marker_end]))
            {
                return true;
            }
            candidate_start += 1;
        }
        if tag_end == bytes.len() {
            break;
        }
        search_start = tag_end + 1;
    }
    false
}

fn has_ascii_object_structure(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut search_start = 0;
    while let Some(relative_open) = bytes[search_start..].iter().position(|byte| *byte == b'<') {
        let body_start = search_start + relative_open + 1;
        let tag_end = bytes[body_start..]
            .iter()
            .position(|byte| *byte == b'>')
            .map_or(bytes.len(), |relative_end| body_start + relative_end);
        let body = &bytes[body_start..tag_end];
        let object = b"object";
        let type_attribute = b"type=";
        let mut object_search = 0;
        while object_search + object.len() <= body.len() {
            let Some(relative_object) = body[object_search..]
                .windows(object.len())
                .position(|candidate| candidate.eq_ignore_ascii_case(object))
            else {
                break;
            };
            let after_object = object_search + relative_object + object.len();
            if after_object < body.len() && is_python_ascii_whitespace(body[after_object]) {
                let tail = &body[after_object..];
                if tail
                    .windows(type_attribute.len())
                    .any(|candidate| candidate.eq_ignore_ascii_case(type_attribute))
                {
                    return true;
                }
            }
            object_search = after_object;
        }
        if tag_end == bytes.len() {
            break;
        }
        search_start = tag_end + 1;
    }
    false
}

fn zeia_xml_structural_flags_ascii(text: &str) -> Option<(bool, bool)> {
    if !text.is_ascii() {
        return None;
    }
    let has_object_name = has_ascii_object_name_structure(text);
    Some((
        has_object_name && has_ascii_script_tag(text),
        has_ascii_object_name_tag(text) || has_ascii_object_structure(text),
    ))
}

#[pyfunction]
fn zeia_xml_structural_flags(text: &str) -> Option<(bool, bool)> {
    zeia_xml_structural_flags_ascii(text)
}

#[pyfunction]
fn xml_clean_text(text: &str) -> String {
    trim_python_whitespace(text).to_owned()
}

fn normalize_script_folder_path_value(text: &str) -> String {
    trim_python_whitespace(text)
        .replace('/', "\\")
        .trim_matches('\\')
        .to_owned()
}

#[pyfunction]
fn normalize_script_folder_path(text: &str) -> String {
    normalize_script_folder_path_value(text)
}

fn format_script_display_name_value(folder: &str, object_name: &str) -> String {
    if !folder.is_empty() && !object_name.contains('\\') {
        format!("{folder}\\{object_name}")
    } else {
        object_name.to_owned()
    }
}

#[pyfunction]
fn format_script_display_name(folder: &str, object_name: &str) -> String {
    format_script_display_name_value(folder, object_name)
}

#[pyfunction]
fn xml_direct_child_texts<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    for child in root.try_iter()? {
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
    Ok(Some(output.into_any()))
}

fn collapse_python_whitespace(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for part in value
        .split(is_python_whitespace)
        .filter(|part| !part.is_empty())
    {
        if !output.is_empty() {
            output.push(' ');
        }
        output.push_str(part);
    }
    output
}

#[pyfunction]
fn xml_direct_leaf_name_texts<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local_name = tag.rsplit('}').next().unwrap_or(tag.as_str());
        let text = child.getattr("text")?;
        let text = if text.is_none() {
            String::new()
        } else {
            if !text.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let text = text.extract::<String>()?;
            collapse_python_whitespace(&text)
        };
        if !text.is_empty() && child.call_method0("__len__")?.extract::<usize>()? == 0 {
            let pair = PyTuple::new(
                py,
                [
                    PyString::new(py, local_name).into_any(),
                    PyString::new(py, &text).into_any(),
                ],
            )?;
            output.append(pair)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_direct_record_children<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local_name = tag.rsplit('}').next().unwrap_or(tag.as_str());
        let text = child.getattr("text")?;
        let text = if text.is_none() {
            String::new()
        } else {
            if !text.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let text = text.extract::<String>()?;
            collapse_python_whitespace(&text)
        };
        let has_children = child.call_method0("__len__")?.extract::<usize>()? > 0;
        let record = PyTuple::new(
            py,
            [
                PyString::new(py, local_name).into_any(),
                PyString::new(py, &text).into_any(),
                PyBool::new(py, has_children).as_any().clone(),
                child.clone(),
            ],
        )?;
        output.append(record)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_attribute_name_values<'py>(
    py: Python<'py>,
    element: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !element.get_type().is(element_type) {
        return Ok(None);
    }
    let attributes = element.getattr("attrib")?;
    if !attributes.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let attributes = attributes.cast::<PyDict>()?;
    let output = PyList::empty(py);
    for (name, value) in attributes.iter() {
        if !name.get_type().is(py.get_type::<PyString>())
            || !value.get_type().is(py.get_type::<PyString>())
        {
            return Ok(None);
        }
        let name = name.extract::<String>()?;
        let local_name = name.rsplit('}').next().unwrap_or(name.as_str());
        let value = value.extract::<String>()?;
        let pair = PyTuple::new(
            py,
            [
                PyString::new(py, local_name).into_any(),
                PyString::new(py, &value).into_any(),
            ],
        )?;
        output.append(pair)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_first_nonempty_texts<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let result = PyDict::new(py);
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
        let local_name = tag.rsplit('}').next().unwrap_or(tag.as_str());
        let text = element.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text = text.extract::<String>()?;
        let cleaned = trim_python_whitespace(&text);
        if cleaned.is_empty() || result.get_item(local_name)?.is_some() {
            continue;
        }
        result.set_item(local_name, cleaned)?;
    }
    Ok(Some(result.into_any()))
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
fn xml_element_is_nil<'py>(
    py: Python<'py>,
    element: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<bool>> {
    if !element.get_type().is(element_type) {
        return Ok(None);
    }
    let attributes = element.getattr("attrib")?;
    if !attributes.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let attributes = attributes.cast::<PyDict>()?;
    let mut has_nil_key = false;
    for (key, _) in attributes.iter() {
        if !key.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let key = key.extract::<String>()?;
        has_nil_key |= key.ends_with("}nil") || key == "i:nil";
    }
    if !has_nil_key {
        return Ok(Some(false));
    }
    for (_, value) in attributes.iter() {
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let value = value.extract::<String>()?;
        if !value.is_ascii() {
            return Ok(None);
        }
        if value.eq_ignore_ascii_case("true") {
            return Ok(Some(true));
        }
    }
    Ok(Some(false))
}

#[pyfunction]
fn parse_ascii_bool_text(value: &str) -> Option<bool> {
    if !value.is_ascii() {
        return None;
    }
    if value.eq_ignore_ascii_case("true") {
        Some(true)
    } else if value.eq_ignore_ascii_case("false") {
        Some(false)
    } else {
        None
    }
}

#[pyfunction]
fn ascii_casefold_equals(value: &str, expected: &str) -> Option<bool> {
    if value.is_ascii() && expected.is_ascii() {
        Some(value.eq_ignore_ascii_case(expected))
    } else {
        None
    }
}

#[pyfunction]
fn ascii_casefold_trim(value: &str) -> Option<String> {
    if !value.is_ascii() {
        return None;
    }
    Some(trim_python_whitespace(value).to_ascii_lowercase())
}

#[pyfunction]
fn ascii_lower_alnum_compact(value: &str) -> Option<String> {
    if !value.is_ascii() {
        return None;
    }
    Some(
        value
            .chars()
            .filter(|character| character.is_ascii_lowercase() || character.is_ascii_digit())
            .collect(),
    )
}

#[pyfunction]
fn ascii_profile_slug(value: &str) -> Option<String> {
    if !value.is_ascii() {
        return None;
    }
    let mut output = String::new();
    let mut separator_pending = false;
    for character in value.chars() {
        if character.is_ascii_alphanumeric() {
            if separator_pending && !output.is_empty() {
                output.push('_');
            }
            output.push(character.to_ascii_lowercase());
            separator_pending = false;
        } else if !output.is_empty() {
            separator_pending = true;
        }
    }
    Some(output)
}

#[pyfunction]
fn ascii_safe_filename(value: &str) -> Option<String> {
    if !value.is_ascii() {
        return None;
    }
    let mut output = String::new();
    let mut separator_pending = false;
    for character in value.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '_' | '.' | '-') {
            output.push(character);
            separator_pending = false;
        } else if !separator_pending {
            output.push('_');
            separator_pending = true;
        }
    }
    let trimmed = output.trim_matches(|character| character == '.' || character == '_');
    Some(trimmed.chars().take(120).collect())
}

#[pyfunction]
fn ascii_word_boundary_contains(value: &str, word: &str) -> Option<bool> {
    if !value.is_ascii() || !word.is_ascii() || word.is_empty() {
        return None;
    }
    let bytes = value.as_bytes();
    let needle = word.as_bytes();
    if needle.len() > bytes.len() {
        return Some(false);
    }
    for start in 0..=bytes.len() - needle.len() {
        if &bytes[start..start + needle.len()] != needle {
            continue;
        }
        let left_boundary = start == 0
            || !(bytes[start - 1].is_ascii_lowercase() || bytes[start - 1].is_ascii_digit());
        let end = start + needle.len();
        let right_boundary =
            end == bytes.len() || !(bytes[end].is_ascii_lowercase() || bytes[end].is_ascii_digit());
        if left_boundary && right_boundary {
            return Some(true);
        }
    }
    Some(false)
}

fn label_base_ascii(value: &str) -> Option<String> {
    if !value.is_ascii() {
        return None;
    }
    let bytes = value.as_bytes();
    let effective_len = if bytes.last() == Some(&b'\n') {
        bytes.len() - 1
    } else {
        bytes.len()
    };
    let opening = if effective_len > 0 && bytes[effective_len - 1] == b']' {
        let closing = effective_len - 1;
        let search_start = bytes[..closing]
            .iter()
            .rposition(|byte| *byte == b']')
            .map_or(0, |index| index + 1);
        bytes[search_start..closing]
            .iter()
            .position(|byte| *byte == b'[')
            .map(|relative| search_start + relative)
            .filter(|opening| opening + 1 < closing)
    } else {
        None
    };
    let base = opening.map_or(value, |opening| &value[..opening]);
    Some(trim_python_whitespace(base).to_owned())
}

#[pyfunction]
fn label_base_ascii_text(value: &str) -> Option<String> {
    label_base_ascii(value)
}

fn is_ascii_guid(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => *byte == b'-',
            _ => byte.is_ascii_hexdigit(),
        })
}

#[pyfunction]
fn is_guid_text(value: &str) -> bool {
    is_ascii_guid(value)
}

#[pyfunction]
fn non_guid_text(value: &str, zero_guid: &str) -> String {
    let value = trim_python_whitespace(value);
    if value.is_empty() || value == zero_guid || is_ascii_guid(value) {
        String::new()
    } else {
        value.to_owned()
    }
}

#[pyfunction]
fn format_site_path<'py>(py: Python<'py>, path: &Bound<'py, PyAny>) -> PyResult<Option<String>> {
    if !path.get_type().is(py.get_type::<PyTuple>()) {
        return Ok(None);
    }
    let path = path.cast::<PyTuple>()?;
    if path.len() > 256 {
        return Ok(None);
    }
    let mut labels = Vec::with_capacity(path.len());
    for index in path.iter() {
        if !index.get_type().is(py.get_type::<PyInt>()) {
            return Ok(None);
        }
        let Ok(index) = index.extract::<i64>() else {
            return Ok(None);
        };
        let Some(label) = index.checked_add(1) else {
            return Ok(None);
        };
        labels.push(label.to_string());
    }
    Ok(Some(labels.join(".")))
}

#[pyfunction]
fn site_index_sort_key(value: &str) -> Option<(i8, String)> {
    let parsed = value.parse::<i64>().ok()?;
    Some((0, format!("{parsed:012}")))
}

#[pyfunction]
fn parse_i64_text<'py>(py: Python<'py>, text: &Bound<'py, PyAny>) -> PyResult<Option<i64>> {
    if !text.get_type().is(py.get_type::<PyString>()) {
        return Ok(None);
    }
    let text = text.extract::<String>()?;
    if !text.is_ascii() {
        return Ok(None);
    }
    Ok(text.parse::<i64>().ok())
}

fn skip_python_whitespace(bytes: &[u8], position: &mut usize) {
    while bytes
        .get(*position)
        .is_some_and(|byte| is_python_whitespace(char::from(*byte)))
    {
        *position += 1;
    }
}

fn consume_ascii_keyword(bytes: &[u8], position: &mut usize, keyword: &[u8]) -> bool {
    let Some(candidate) = bytes.get(*position..*position + keyword.len()) else {
        return false;
    };
    if !candidate.eq_ignore_ascii_case(keyword) {
        return false;
    }
    *position += keyword.len();
    true
}

fn parse_orientation_number(bytes: &[u8], position: &mut usize) -> Option<f64> {
    let start = *position;
    if bytes.get(*position) == Some(&b'-') {
        *position += 1;
    }
    let integer_start = *position;
    while bytes.get(*position).is_some_and(u8::is_ascii_digit) {
        *position += 1;
    }
    if *position == integer_start {
        return None;
    }
    if bytes.get(*position) == Some(&b'.') {
        let fraction_start = *position + 1;
        let mut fraction_end = fraction_start;
        while bytes.get(fraction_end).is_some_and(u8::is_ascii_digit) {
            fraction_end += 1;
        }
        if fraction_end == fraction_start {
            return None;
        }
        *position = fraction_end;
    }
    let number = std::str::from_utf8(&bytes[start..*position])
        .ok()?
        .parse::<f64>()
        .ok()?;
    number.is_finite().then_some(number)
}

fn parse_orientation_candidate(bytes: &[u8], start: usize) -> Option<[f64; 3]> {
    let mut position = start + 3;
    skip_python_whitespace(bytes, &mut position);
    if bytes.get(position) != Some(&b'=') {
        return None;
    }
    position += 1;
    skip_python_whitespace(bytes, &mut position);
    let phi = parse_orientation_number(bytes, &mut position)?;
    skip_python_whitespace(bytes, &mut position);
    if bytes.get(position) != Some(&b',') {
        return None;
    }
    position += 1;
    skip_python_whitespace(bytes, &mut position);
    if !consume_ascii_keyword(bytes, &mut position, b"theta") {
        return None;
    }
    skip_python_whitespace(bytes, &mut position);
    if bytes.get(position) != Some(&b'=') {
        return None;
    }
    position += 1;
    skip_python_whitespace(bytes, &mut position);
    let theta = parse_orientation_number(bytes, &mut position)?;
    skip_python_whitespace(bytes, &mut position);
    if bytes.get(position) != Some(&b',') {
        return None;
    }
    position += 1;
    skip_python_whitespace(bytes, &mut position);
    if !consume_ascii_keyword(bytes, &mut position, b"psi") {
        return None;
    }
    skip_python_whitespace(bytes, &mut position);
    if bytes.get(position) != Some(&b'=') {
        return None;
    }
    position += 1;
    skip_python_whitespace(bytes, &mut position);
    let psi = parse_orientation_number(bytes, &mut position)?;
    Some([phi, theta, psi])
}

fn parse_orientation_ascii_text(text: &str) -> Option<[f64; 3]> {
    if !text.is_ascii() {
        return None;
    }
    let bytes = text.as_bytes();
    if bytes.len() < 3 {
        return None;
    }
    for start in 0..=bytes.len() - 3 {
        if bytes[start..start + 3].eq_ignore_ascii_case(b"phi") {
            if let Some(values) = parse_orientation_candidate(bytes, start) {
                return Some(values);
            }
        }
    }
    None
}

#[pyfunction]
fn parse_orientation_ascii(text: &str) -> Option<(f64, f64, f64)> {
    let [phi, theta, psi] = parse_orientation_ascii_text(text)?;
    Some((phi, theta, psi))
}

fn has_ascii_decimal_float_syntax(text: &str) -> bool {
    let bytes = text.as_bytes();
    if bytes.is_empty() {
        return false;
    }
    let mut index = usize::from(matches!(bytes[0], b'+' | b'-'));
    let mut mantissa_digits = 0;
    let mut decimal_point = false;
    while index < bytes.len() {
        match bytes[index] {
            b'0'..=b'9' => mantissa_digits += 1,
            b'.' if !decimal_point => decimal_point = true,
            b'e' | b'E' => break,
            _ => return false,
        }
        index += 1;
    }
    if mantissa_digits == 0 {
        return false;
    }
    if index == bytes.len() {
        return true;
    }

    index += 1;
    if index < bytes.len() && matches!(bytes[index], b'+' | b'-') {
        index += 1;
    }
    let exponent_start = index;
    while index < bytes.len() && bytes[index].is_ascii_digit() {
        index += 1;
    }
    index == bytes.len() && index > exponent_start
}

#[pyfunction]
fn parse_f64_decimal_text<'py>(py: Python<'py>, text: &Bound<'py, PyAny>) -> PyResult<Option<f64>> {
    if !text.get_type().is(py.get_type::<PyString>()) {
        return Ok(None);
    }
    let text = text.extract::<String>()?;
    if !has_ascii_decimal_float_syntax(&text) {
        return Ok(None);
    }
    Ok(text.parse::<f64>().ok())
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
fn xml_direct_child_local_names<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        output.append(local)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_runtime_variable_definition_candidates<'py>(
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
        if matches!(local, "anyType" | "VariableDefinitionHelper") {
            output.append(element)?;
            continue;
        }
        let mut has_name = false;
        let mut has_type_name = false;
        let mut has_query_on_startup = false;
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
            has_query_on_startup |= child_local == "QueryOnStartup";
        }
        if has_name && has_type_name && has_query_on_startup {
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
fn xml_indexed_elements_by_folded_name<'py>(
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
    for (index, element) in iterator.try_iter()?.enumerate() {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
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
        if names.iter().any(|name| name.eq_ignore_ascii_case(&folded)) {
            let pair = PyTuple::new(py, [PyInt::new(py, index).into_any(), element])?;
            output.append(pair)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_first_element_by_name<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    name: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<(bool, Option<Bound<'py, PyAny>>)> {
    if !root.get_type().is(element_type) {
        return Ok((false, None));
    }
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok((false, None));
        }
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, None));
        }
        let tag = tag.extract::<String>()?;
        if xml_tag_local_name(&tag) == name {
            return Ok((true, Some(element)));
        }
    }
    Ok((true, None))
}

#[pyfunction]
fn xml_first_direct_child_by_name<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    name: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<(bool, Option<Bound<'py, PyAny>>)> {
    if !root.get_type().is(element_type) {
        return Ok((false, None));
    }
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok((false, None));
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, None));
        }
        let tag = tag.extract::<String>()?;
        if xml_tag_local_name(&tag) == name {
            return Ok((true, Some(child)));
        }
    }
    Ok((true, None))
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

#[pyfunction]
fn xml_elements_ending_with<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    suffix: String,
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
        if tag.ends_with(&suffix) {
            output.append(element)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_has_unmodeled_local_name<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    known_names: Vec<String>,
    allowed_prefixes: Vec<String>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<bool>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let known_names = known_names.into_iter().collect::<HashSet<_>>();
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if !known_names.contains(local)
            && !allowed_prefixes
                .iter()
                .any(|prefix| local.starts_with(prefix))
        {
            return Ok(Some(true));
        }
    }
    Ok(Some(false))
}

fn xml_first_exact_local_text<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    target: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<(bool, Option<String>)> {
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok((false, None));
        }
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, None));
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local != target {
            continue;
        }
        let text = element.getattr("text")?;
        if text.is_none() {
            return Ok((true, Some(String::new())));
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, None));
        }
        return Ok((true, Some(text.extract::<String>()?)));
    }
    Ok((true, None))
}

fn xml_direct_exact_local_text<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    target: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<(bool, String)> {
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok((false, String::new()));
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, String::new()));
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local != target {
            continue;
        }
        let text = child.getattr("text")?;
        if text.is_none() {
            return Ok((true, String::new()));
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, String::new()));
        }
        let text = text.extract::<String>()?;
        return Ok((true, trim_python_whitespace(&text).to_owned()));
    }
    Ok((true, String::new()))
}

fn xml_first_exact_local_element<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    target: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<(bool, Option<Bound<'py, PyAny>>)> {
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok((false, None));
        }
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, None));
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local == target {
            return Ok((true, Some(element)));
        }
    }
    Ok((true, None))
}

#[pyfunction]
fn xml_vec_dict<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyDict::new(py);
    let mut axes = HashSet::new();
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if !local.is_ascii() {
            return Ok(None);
        }
        let axis = local.to_ascii_lowercase();
        if !matches!(axis.as_str(), "x" | "y" | "z") {
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
        let text = trim_python_whitespace(&text);
        if text.is_empty() {
            continue;
        }
        if !has_ascii_decimal_float_syntax(text) {
            return Ok(None);
        }
        let Ok(value) = text.parse::<f64>() else {
            return Ok(None);
        };
        let value = if value == 0.0 { 0.0 } else { value };
        axes.insert(axis.clone());
        output.set_item(axis, value)?;
    }
    if axes.len() == 3 {
        Ok(Some(output.into_any()))
    } else {
        Ok(Some(PyDict::new(py).into_any()))
    }
}

#[pyfunction]
fn xml_vec3_case_sensitive<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<(f64, f64, f64)>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let mut coordinates = [None, None, None];
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        let axis = match local {
            "X" => 0,
            "Y" => 1,
            "Z" => 2,
            _ => continue,
        };
        let text = child.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text = text.extract::<String>()?;
        if text.is_empty() {
            continue;
        }
        let text = trim_python_whitespace(&text);
        if text.is_empty() {
            continue;
        }
        if !has_ascii_decimal_float_syntax(text) {
            return Ok(None);
        }
        let Ok(value) = text.parse::<f64>() else {
            return Ok(None);
        };
        coordinates[axis] = Some(value);
    }
    let [Some(x), Some(y), Some(z)] = coordinates else {
        return Ok(None);
    };
    Ok(Some((x, y, z)))
}

#[pyfunction]
fn xml_indexed_values<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
    zero_guid: &str,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyDict::new(py);
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let (key_supported, key_text) =
            xml_first_exact_local_text(py, &child, "Key", element_type)?;
        if !key_supported {
            return Ok(None);
        }
        let Some(key_text) = key_text else {
            continue;
        };
        let key_text = trim_python_whitespace(&key_text);
        if key_text.is_empty() {
            continue;
        }
        let Ok(key) = key_text.parse::<i64>() else {
            return Ok(None);
        };
        let (value_supported, value_text) =
            xml_first_exact_local_text(py, &child, "Value", element_type)?;
        if !value_supported {
            return Ok(None);
        }
        let Some(value_text) = value_text else {
            continue;
        };
        let value = trim_python_whitespace(&value_text);
        if value.is_empty() || value == zero_guid {
            continue;
        }
        output.set_item(key.to_string(), value)?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_indexed_vectors<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyDict::new(py);
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let (key_supported, key_text) =
            xml_first_exact_local_text(py, &child, "Key", element_type)?;
        if !key_supported {
            return Ok(None);
        }
        let Some(key_text) = key_text else {
            continue;
        };
        let key_text = trim_python_whitespace(&key_text);
        if key_text.is_empty() {
            continue;
        }
        let Ok(key) = key_text.parse::<i64>() else {
            return Ok(None);
        };
        let (value_supported, value_element) =
            xml_first_exact_local_element(py, &child, "Value", element_type)?;
        if !value_supported {
            return Ok(None);
        }
        let Some(value_element) = value_element else {
            continue;
        };
        let Some(vector) = xml_vec_dict(py, &value_element, element_type)? else {
            return Ok(None);
        };
        if vector.call_method0("__len__")?.extract::<usize>()? == 3 {
            output.set_item(key.to_string(), vector)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_indexed_float_values<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyDict::new(py);
    for child in root.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let (key_supported, key_text) =
            xml_first_exact_local_text(py, &child, "Key", element_type)?;
        if !key_supported {
            return Ok(None);
        }
        let Some(key) = key_text else {
            continue;
        };
        let key = trim_python_whitespace(&key);
        if key.is_empty() {
            continue;
        }
        let (value_supported, value_text) =
            xml_first_exact_local_text(py, &child, "Value", element_type)?;
        if !value_supported {
            return Ok(None);
        }
        let Some(value) = value_text else {
            continue;
        };
        let value = trim_python_whitespace(&value);
        if value.is_empty() {
            continue;
        }
        if !has_ascii_decimal_float_syntax(value) {
            return Ok(None);
        }
        let Ok(value) = value.parse::<f64>() else {
            return Ok(None);
        };
        output.set_item(key, if value == 0.0 { 0.0 } else { value })?;
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_allowed_grip_modes<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyDict::new(py);
    for site in root.try_iter()? {
        let site = site?;
        if !site.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = site.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let (key_supported, key_text) = xml_first_exact_local_text(py, &site, "Key", element_type)?;
        if !key_supported {
            return Ok(None);
        }
        let Some(key_text) = key_text else {
            continue;
        };
        let key_text = trim_python_whitespace(&key_text);
        if key_text.is_empty() {
            continue;
        }
        let Ok(site_index) = key_text.parse::<i64>() else {
            return Ok(None);
        };
        let (value_supported, value_element) =
            xml_first_exact_local_element(py, &site, "Value", element_type)?;
        if !value_supported {
            return Ok(None);
        }
        let Some(value_element) = value_element else {
            continue;
        };
        let cgas = PyList::empty(py);
        let mut seen = HashSet::new();
        for cga in value_element.try_iter()? {
            let cga = cga?;
            if !cga.get_type().is(element_type) {
                return Ok(None);
            }
            let tag = cga.getattr("tag")?;
            if !tag.is_instance_of::<PyString>() {
                continue;
            }
            if !tag.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let (mode_supported, mode_text) =
                xml_first_exact_local_text(py, &cga, "Key", element_type)?;
            if !mode_supported {
                return Ok(None);
            }
            let Some(mode) = mode_text else {
                continue;
            };
            let mode = trim_python_whitespace(&mode);
            if !mode.is_empty() && seen.insert(mode.to_owned()) {
                cgas.append(mode)?;
            }
        }
        if !cgas.is_empty() {
            output.set_item(site_index.to_string(), cgas)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_reference_guids<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    type_id: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    let mut seen = HashSet::new();
    for reference in root.try_iter()? {
        let reference = reference?;
        if !reference.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = reference.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local != "Reference" {
            continue;
        }
        let (type_supported, observed_type) =
            xml_direct_exact_local_text(py, &reference, "TypeId", element_type)?;
        if !type_supported {
            return Ok(None);
        }
        if observed_type != type_id {
            continue;
        }
        let (guid_supported, guid) =
            xml_direct_exact_local_text(py, &reference, "Guid", element_type)?;
        if !guid_supported {
            return Ok(None);
        }
        if !guid.is_empty() && seen.insert(guid.clone()) {
            output.append(guid)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_mesh_reference_lists<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    type_id: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<(Vec<String>, Vec<String>)>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let mut guids = Vec::new();
    let mut guid_set = HashSet::new();
    let mut names = Vec::new();
    let mut name_set = HashSet::new();
    for reference in root.try_iter()? {
        let reference = reference?;
        if !reference.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = reference.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local != "Reference" {
            continue;
        }
        let (type_supported, observed_type) =
            xml_direct_exact_local_text(py, &reference, "TypeId", element_type)?;
        if !type_supported {
            return Ok(None);
        }
        if observed_type != type_id {
            continue;
        }
        let (guid_supported, guid) =
            xml_direct_exact_local_text(py, &reference, "Guid", element_type)?;
        if !guid_supported {
            return Ok(None);
        }
        let (name_supported, name) =
            xml_direct_exact_local_text(py, &reference, "ObjectName", element_type)?;
        if !name_supported || !guid.is_ascii() || !name.is_ascii() {
            return Ok(None);
        }
        if !guid.is_empty() && guid_set.insert(guid.clone()) {
            guids.push(guid.clone());
        }
        if !name.is_empty() && !name.eq_ignore_ascii_case(&guid) && name_set.insert(name.clone()) {
            names.push(name);
        }
    }
    Ok(Some((guids, names)))
}

#[pyfunction]
fn xml_sub_component_names<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    object_name: &str,
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
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local != "ObjectName" {
            continue;
        }
        let text = element.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text = text.extract::<String>()?;
        let text = trim_python_whitespace(&text);
        if text.is_empty() || text == object_name || is_ascii_guid(text) {
            continue;
        }
        if seen.insert(text.to_owned()) {
            output.append(text)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_matrix<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !root.get_type().is(element_type) {
        return Ok(None);
    }
    let (supported, matrix) = xml_first_exact_local_element(py, root, "Mat", element_type)?;
    if !supported {
        return Ok(None);
    }
    let Some(matrix) = matrix else {
        return Ok(Some(PyList::empty(py).into_any()));
    };
    let output = PyList::empty(py);
    for row in matrix.try_iter()? {
        let row = row?;
        if !row.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = row.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if local != "ArrayOfdouble" {
            continue;
        }
        let values = PyList::empty(py);
        let mut complete = true;
        for cell in row.try_iter()? {
            let cell = cell?;
            if !cell.get_type().is(element_type) {
                return Ok(None);
            }
            let tag = cell.getattr("tag")?;
            if !tag.is_instance_of::<PyString>() {
                continue;
            }
            if !tag.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let tag = tag.extract::<String>()?;
            let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
            if local != "double" {
                continue;
            }
            let text = cell.getattr("text")?;
            if text.is_none() {
                complete = false;
                continue;
            }
            if !text.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let text = text.extract::<String>()?;
            let text = trim_python_whitespace(&text);
            if text.is_empty() {
                complete = false;
                continue;
            }
            if !has_ascii_decimal_float_syntax(text) {
                return Ok(None);
            }
            let Ok(value) = text.parse::<f64>() else {
                return Ok(None);
            };
            values.append(if value == 0.0 { 0.0 } else { value })?;
        }
        if complete && !values.is_empty() {
            output.append(values)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_elements_by_ascii_casefolded_name<'py>(
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
        if !local.is_ascii() {
            return Ok(None);
        }
        let folded_local = local.to_ascii_lowercase();
        if names.iter().any(|name| name == &folded_local) {
            output.append(element)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_first_text_by_ascii_casefolded_name(
    py: Python<'_>,
    root: &Bound<'_, PyAny>,
    names: Vec<String>,
    element_type: &Bound<'_, PyAny>,
) -> PyResult<(bool, String)> {
    if !root.get_type().is(element_type) || names.iter().any(|name| !name.is_ascii()) {
        return Ok((false, String::new()));
    }
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok((false, String::new()));
        }
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, String::new()));
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if !local.is_ascii() {
            return Ok((false, String::new()));
        }
        let folded_local = local.to_ascii_lowercase();
        if !names.iter().any(|name| name == &folded_local) {
            continue;
        }
        let text = element.getattr("text")?;
        if text.is_none() {
            continue;
        }
        if !text.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, String::new()));
        }
        let text = text.extract::<String>()?;
        let text = trim_python_whitespace(&text);
        if !text.is_empty() {
            return Ok((true, text.to_owned()));
        }
    }
    Ok((true, String::new()))
}

#[pyfunction]
fn xml_first_vec_by_ascii_casefolded_name(
    py: Python<'_>,
    root: &Bound<'_, PyAny>,
    names: Vec<String>,
    element_type: &Bound<'_, PyAny>,
) -> PyResult<(bool, Py<PyAny>)> {
    if !root.get_type().is(element_type) || names.iter().any(|name| !name.is_ascii()) {
        return Ok((false, py.None()));
    }
    let axis_names = ["x", "y", "z"];
    let iterator = root.call_method0("iter")?;
    for element in iterator.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok((false, py.None()));
        }
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, py.None()));
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if !local.is_ascii() {
            return Ok((false, py.None()));
        }
        let folded_local = local.to_ascii_lowercase();
        if !names.iter().any(|name| name == &folded_local) {
            continue;
        }

        let mut values = [None; 3];
        let mut order = Vec::new();
        for child in element.try_iter()? {
            let child = child?;
            if !child.get_type().is(element_type) {
                return Ok((false, py.None()));
            }
            let child_tag = child.getattr("tag")?;
            if !child_tag.is_instance_of::<PyString>() {
                continue;
            }
            if !child_tag.get_type().is(py.get_type::<PyString>()) {
                return Ok((false, py.None()));
            }
            let child_tag = child_tag.extract::<String>()?;
            let child_local = child_tag.rsplit('}').next().unwrap_or(child_tag.as_str());
            if !child_local.is_ascii() {
                return Ok((false, py.None()));
            }
            let folded_child_local = child_local.to_ascii_lowercase();
            let Some(axis) = axis_names
                .iter()
                .position(|axis_name| *axis_name == folded_child_local)
            else {
                continue;
            };
            let text = child.getattr("text")?;
            if text.is_none() {
                continue;
            }
            if !text.get_type().is(py.get_type::<PyString>()) {
                return Ok((false, py.None()));
            }
            let text = text.extract::<String>()?;
            let text = trim_python_whitespace(&text);
            if text.is_empty() {
                continue;
            }
            if !has_ascii_decimal_float_syntax(text) {
                return Ok((false, py.None()));
            }
            let Ok(mut number) = text.parse::<f64>() else {
                return Ok((false, py.None()));
            };
            if number == 0.0 {
                number = 0.0;
            }
            if values[axis].is_none() {
                order.push(axis);
            }
            values[axis] = Some(number);
        }

        if values.iter().all(Option::is_some) {
            let output = PyDict::new(py);
            for axis in order {
                if let Some(value) = values[axis] {
                    output.set_item(axis_names[axis], value)?;
                }
            }
            return Ok((true, output.into_any().unbind()));
        }
    }
    Ok((true, PyDict::new(py).into_any().unbind()))
}

#[pyfunction]
fn xml_first_direct_element_by_name<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    name: &str,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<(bool, Option<Bound<'py, PyAny>>)> {
    if !root.get_type().is(element_type) {
        return Ok((false, None));
    }
    for element in root.try_iter()? {
        let element = element?;
        if !element.get_type().is(element_type) {
            return Ok((false, None));
        }
        let tag = element.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok((false, None));
        }
        let tag = tag.extract::<String>()?;
        if xml_tag_local_name(&tag) == name {
            return Ok((true, Some(element)));
        }
    }
    Ok((true, None))
}

#[pyfunction]
fn xml_direct_elements_by_name<'py>(
    py: Python<'py>,
    parent: &Bound<'py, PyAny>,
    names: Vec<String>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !parent.get_type().is(element_type) {
        return Ok(None);
    }
    let names = names.into_iter().collect::<HashSet<_>>();
    let output = PyList::empty(py);
    for child in parent.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if names.contains(local) {
            output.append(child)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_direct_elements_by_ascii_prefix<'py>(
    py: Python<'py>,
    parent: &Bound<'py, PyAny>,
    prefix: String,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !parent.get_type().is(element_type) || !prefix.is_ascii() {
        return Ok(None);
    }
    let output = PyList::empty(py);
    for child in parent.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if !local.is_ascii() {
            return Ok(None);
        }
        if local.starts_with(&prefix) {
            output.append(child)?;
        }
    }
    Ok(Some(output.into_any()))
}

#[pyfunction]
fn xml_direct_elements_by_ascii_casefolded_name<'py>(
    py: Python<'py>,
    parent: &Bound<'py, PyAny>,
    names: Vec<String>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !parent.get_type().is(element_type) || names.iter().any(|name| !name.is_ascii()) {
        return Ok(None);
    }
    let output = PyList::empty(py);
    for child in parent.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let tag = tag.extract::<String>()?;
        let local = tag.rsplit('}').next().unwrap_or(tag.as_str());
        if !local.is_ascii() {
            return Ok(None);
        }
        let folded_local = local.to_ascii_lowercase();
        if names.iter().any(|name| name == &folded_local) {
            output.append(child)?;
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

fn collect_xml_leaf_texts<'py>(
    py: Python<'py>,
    element: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
    depth: usize,
) -> PyResult<Option<Vec<String>>> {
    if depth > 256 || !element.get_type().is(element_type) {
        return Ok(None);
    }
    let mut children = Vec::new();
    for child in element.try_iter()? {
        let child = child?;
        if !child.get_type().is(element_type) {
            return Ok(None);
        }
        let tag = child.getattr("tag")?;
        if !tag.is_instance_of::<PyString>() {
            continue;
        }
        if !tag.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        children.push(child);
    }
    if children.is_empty() {
        let text = element.getattr("text")?;
        if text.is_none() {
            return Ok(Some(Vec::new()));
        }
        if !text.is_instance_of::<PyString>() || !text.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let text = text.extract::<String>()?;
        let text = trim_python_whitespace(&text);
        return Ok(Some(if text.is_empty() {
            Vec::new()
        } else {
            vec![text.to_owned()]
        }));
    }
    let mut output = Vec::new();
    for child in children {
        let Some(mut values) = collect_xml_leaf_texts(py, &child, element_type, depth + 1)? else {
            return Ok(None);
        };
        output.append(&mut values);
    }
    Ok(Some(output))
}

#[pyfunction]
fn xml_leaf_texts<'py>(
    py: Python<'py>,
    root: &Bound<'py, PyAny>,
    element_type: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let Some(values) = collect_xml_leaf_texts(py, root, element_type, 0)? else {
        return Ok(None);
    };
    let output = PyList::empty(py);
    for value in values {
        output.append(value)?;
    }
    Ok(Some(output.into_any()))
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
fn sha256_text(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

#[pyfunction]
fn sha256_file(path: &str) -> Option<String> {
    let mut file = File::open(path).ok()?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer).ok()?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Some(format!("{:x}", digest.finalize()))
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
fn project_model_identifiers<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !record.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let record = record.cast::<PyDict>()?;
    let identifiers = PyDict::new(py);
    for key in [
        "guid",
        "script_guid",
        "component_guid",
        "site_guid",
        "workspace_guid",
        "type_id",
        "object_name",
        "checksum",
        "base_worktable_name",
        "base_worktable_guid",
    ] {
        let Some(value) = record.get_item(key)? else {
            continue;
        };
        if value.is_none() {
            continue;
        }
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let value = value.extract::<String>()?;
        if !value.is_empty() {
            identifiers.set_item(key, value)?;
        }
    }
    Ok(Some(identifiers.into_any()))
}

#[pyfunction]
fn project_model_entry_path(py: Python<'_>, record: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if !record.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let record = record.cast::<PyDict>()?;
    for key in ["entry", "source"] {
        let Some(value) = record.get_item(key)? else {
            continue;
        };
        if value.is_none() {
            continue;
        }
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let value = value.extract::<String>()?;
        if !value.is_empty() {
            return Ok(Some(value.replace('\\', "/")));
        }
    }
    Ok(Some(String::new()))
}

fn project_record_casefold<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<String>> {
    let Some(value) = record.get_item(key)? else {
        return Ok(Some(String::new()));
    };
    if value.is_none() {
        return Ok(Some(String::new()));
    }
    if !value.get_type().is(py.get_type::<PyString>()) {
        return Ok(None);
    }
    Ok(Some(value.call_method0("casefold")?.extract::<String>()?))
}

fn project_record_casefold_joined<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<String>> {
    let Some(value) = record.get_item(key)? else {
        return Ok(Some(String::new()));
    };
    if value.is_none() {
        return Ok(Some(String::new()));
    }
    let values = if value.get_type().is(py.get_type::<PyList>()) {
        value.cast::<PyList>()?.iter().collect::<Vec<_>>()
    } else if value.get_type().is(py.get_type::<PyTuple>()) {
        value.cast::<PyTuple>()?.iter().collect::<Vec<_>>()
    } else {
        return Ok(None);
    };
    let mut normalized = Vec::with_capacity(values.len());
    for value in values {
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        normalized.push(value.call_method0("casefold")?.extract::<String>()?);
    }
    normalized.sort();
    Ok(Some(normalized.join(",")))
}

#[pyfunction]
fn project_record_fingerprint<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !record.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let record = record.cast::<PyDict>()?;
    let Some(kind) = project_record_casefold(py, record, "kind")? else {
        return Ok(None);
    };
    let Some(object_name) = project_record_casefold(py, record, "object_name")? else {
        return Ok(None);
    };
    let Some(type_id) = project_record_casefold(py, record, "type_id")? else {
        return Ok(None);
    };
    let Some(guids) = project_record_casefold_joined(py, record, "guids")? else {
        return Ok(None);
    };
    let Some(names) = project_record_casefold_joined(py, record, "names")? else {
        return Ok(None);
    };
    let Some(functional_group) = project_record_casefold(py, record, "functional_group")? else {
        return Ok(None);
    };
    Ok(Some(
        PyTuple::new(
            py,
            [kind, object_name, type_id, guids, names, functional_group],
        )?
        .into_any(),
    ))
}

fn readiness_candidate_field<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyDict>,
    key: &str,
    default: &str,
) -> PyResult<Option<String>> {
    let Some(value) = record.get_item(key)? else {
        return Ok(Some(default.to_owned()));
    };
    if value.is_none() {
        return Ok(Some(default.to_owned()));
    }
    if !value.get_type().is(py.get_type::<PyString>()) {
        return Ok(None);
    }
    let value = value.extract::<String>()?;
    Ok(Some(if value.is_empty() {
        default.to_owned()
    } else {
        value
    }))
}

#[pyfunction]
fn readiness_candidate<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !record.get_type().is(py.get_type::<PyDict>()) {
        return Ok(None);
    }
    let record = record.cast::<PyDict>()?;
    let provenance_value = record.get_item("provenance")?;
    let provenance = if let Some(value) = provenance_value {
        if value.is_none() {
            None
        } else if value.get_type().is(py.get_type::<PyDict>()) {
            Some(value.cast_into::<PyDict>()?)
        } else {
            return Ok(None);
        }
    } else {
        None
    };
    let result = PyDict::new(py);
    let source_archive = match provenance.as_ref() {
        Some(value) => readiness_candidate_field(py, value, "source_archive", "")?,
        None => Some(String::new()),
    };
    let Some(source_archive) = source_archive else {
        return Ok(None);
    };
    let source_entry = match provenance.as_ref() {
        Some(value) => readiness_candidate_field(py, value, "entry_path", "")?,
        None => Some(String::new()),
    };
    let Some(source_entry) = source_entry else {
        return Ok(None);
    };
    let Some(record_entry) = readiness_candidate_field(py, record, "entry", "")? else {
        return Ok(None);
    };
    let Some(kind) = readiness_candidate_field(py, record, "kind", "object")? else {
        return Ok(None);
    };
    let Some(object_name) = readiness_candidate_field(py, record, "object_name", "")? else {
        return Ok(None);
    };
    result.set_item("source_archive", source_archive)?;
    result.set_item(
        "source_entry",
        if source_entry.is_empty() {
            record_entry
        } else {
            source_entry
        },
    )?;
    result.set_item("kind", kind)?;
    result.set_item("object_name", object_name)?;
    Ok(Some(result.into_any()))
}

fn project_index_optional_text<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<Option<String>>> {
    let Some(value) = record.get_item(key)? else {
        return Ok(Some(None));
    };
    if value.is_none() {
        return Ok(Some(None));
    }
    if !value.get_type().is(py.get_type::<PyString>()) {
        return Ok(None);
    }
    match value.extract::<String>() {
        Ok(value) => Ok(Some(Some(value))),
        Err(_) => Ok(None),
    }
}

fn project_index_optional_integer<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<Option<i64>>> {
    let Some(value) = record.get_item(key)? else {
        return Ok(Some(None));
    };
    if value.is_none() {
        return Ok(Some(None));
    }
    if !value.get_type().is(py.get_type::<PyInt>()) {
        return Ok(None);
    }
    match value.extract::<i64>() {
        Ok(value) => Ok(Some(Some(value))),
        Err(_) => Ok(None),
    }
}

type ProjectIndexDedupeKey = (
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<i64>,
);

#[pyfunction]
fn project_index_dedupe_indices<'py>(
    py: Python<'py>,
    results: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !results.get_type().is(py.get_type::<PyList>()) {
        return Ok(None);
    }
    let mut seen: HashSet<ProjectIndexDedupeKey> = HashSet::new();
    let mut indices = Vec::new();
    for (index, result) in results.try_iter()?.enumerate() {
        let result = result?;
        if !result.get_type().is(py.get_type::<PyDict>()) {
            return Ok(None);
        }
        let result = result.cast::<PyDict>()?;
        let Some(kind) = project_index_optional_text(py, result, "kind")? else {
            return Ok(None);
        };
        let Some(match_type) = project_index_optional_text(py, result, "match_type")? else {
            return Ok(None);
        };
        let Some(name) = project_index_optional_text(py, result, "name")? else {
            return Ok(None);
        };
        let Some(zeia_file) = project_index_optional_text(py, result, "zeia_file")? else {
            return Ok(None);
        };
        let Some(script) = project_index_optional_text(py, result, "script")? else {
            return Ok(None);
        };
        let Some(source_path) = project_index_optional_text(py, result, "source_path")? else {
            return Ok(None);
        };
        let Some(command_index) = project_index_optional_integer(py, result, "command_index")?
        else {
            return Ok(None);
        };
        if seen.insert((
            kind,
            match_type,
            name,
            zeia_file,
            script,
            source_path,
            command_index,
        )) {
            indices.push(index);
        }
    }
    Ok(Some(PyList::new(py, indices)?.into_any()))
}

#[pyfunction]
fn readiness_kind_token(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if value.is_none() {
        return Ok(Some(String::new()));
    }
    if !value.get_type().is(py.get_type::<PyString>()) {
        return Ok(None);
    }
    let value = value.extract::<String>()?;
    if !value.is_ascii() {
        return Ok(None);
    }
    Ok(Some(
        value
            .bytes()
            .filter(u8::is_ascii_alphanumeric)
            .map(|byte| byte.to_ascii_lowercase() as char)
            .collect(),
    ))
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

fn diagnostic_redaction_word(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

fn diagnostic_redaction_prefix_matches(bytes: &[u8], prefix: &[u8]) -> bool {
    bytes
        .get(..prefix.len())
        .is_some_and(|candidate| candidate.eq_ignore_ascii_case(prefix))
}

fn diagnostic_redaction_keyword_end(bytes: &[u8], start: usize) -> Option<usize> {
    if start > 0 && diagnostic_redaction_word(bytes[start - 1]) {
        return None;
    }
    let tail = bytes.get(start..)?;
    let keyword_len = if diagnostic_redaction_prefix_matches(tail, b"password") {
        8
    } else if diagnostic_redaction_prefix_matches(tail, b"passwd") {
        6
    } else if diagnostic_redaction_prefix_matches(tail, b"token") {
        5
    } else if diagnostic_redaction_prefix_matches(tail, b"secret") {
        6
    } else if diagnostic_redaction_prefix_matches(tail, b"api") {
        let mut end = 3;
        if tail
            .get(end)
            .is_some_and(|byte| matches!(*byte, b'_' | b'-'))
        {
            end += 1;
        }
        if !tail
            .get(end..end + 3)
            .is_some_and(|suffix| suffix.eq_ignore_ascii_case(b"key"))
        {
            return None;
        }
        end + 3
    } else {
        return None;
    };
    let end = start + keyword_len;
    if end < bytes.len() && diagnostic_redaction_word(bytes[end]) {
        return None;
    }
    Some(end)
}

fn diagnostic_redaction_whitespace(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b..=0x0c | 0x1c..=0x1f)
}

#[pyfunction]
fn diagnostic_redact_text(value: &str) -> Option<String> {
    if !value.is_ascii() {
        return None;
    }
    let bytes = value.as_bytes();
    let mut output = String::with_capacity(value.len());
    let mut cursor = 0;
    let mut copied_through = 0;
    while cursor < bytes.len() {
        let Some(keyword_end) = diagnostic_redaction_keyword_end(bytes, cursor) else {
            cursor += 1;
            continue;
        };
        let mut value_start = keyword_end;
        while value_start < bytes.len() && diagnostic_redaction_whitespace(bytes[value_start]) {
            value_start += 1;
        }
        if value_start == bytes.len() || !matches!(bytes[value_start], b':' | b'=') {
            cursor += 1;
            continue;
        }
        value_start += 1;
        while value_start < bytes.len() && diagnostic_redaction_whitespace(bytes[value_start]) {
            value_start += 1;
        }
        let mut match_end = value_start;
        while match_end < bytes.len()
            && !diagnostic_redaction_whitespace(bytes[match_end])
            && !matches!(bytes[match_end], b',' | b';')
        {
            match_end += 1;
        }
        if match_end == value_start {
            cursor += 1;
            continue;
        }
        output.push_str(&value[copied_through..cursor]);
        output.push_str(&value[cursor..keyword_end]);
        output.push_str("=<redacted>");
        copied_through = match_end;
        cursor = match_end;
    }
    if copied_through == 0 {
        return Some(value.to_owned());
    }
    output.push_str(&value[copied_through..]);
    Some(output)
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
fn zeia_object_kind_from_suffix(suffix: &str) -> String {
    match suffix {
        ".xcmp" => "component".to_owned(),
        ".xwsp" => "workspace".to_owned(),
        ".xlqc" => "liquid_class".to_owned(),
        ".xlcp" => "liquid_class_map".to_owned(),
        ".xsit" => "site".to_owned(),
        ".xcon" => "connector".to_owned(),
        ".xml" => "xml".to_owned(),
        _ => {
            let value = suffix.trim_start_matches('.');
            if value.is_empty() {
                "xml".to_owned()
            } else {
                value.to_owned()
            }
        }
    }
}

#[pyfunction]
fn normalize_ascii_name(value: &str) -> String {
    let trimmed = value.trim_matches(|character: char| {
        character.is_ascii() && is_python_ascii_whitespace(character as u8)
    });
    trimmed.to_ascii_lowercase()
}

#[pyfunction]
fn subroutine_reference_matches_ascii(
    py: Python<'_>,
    subroutine: &Bound<'_, PyAny>,
    references: &Bound<'_, PyAny>,
) -> PyResult<Option<bool>> {
    if !subroutine.get_type().is(py.get_type::<PyString>())
        || !references.get_type().is(py.get_type::<PySet>())
    {
        return Ok(None);
    }
    let subroutine = subroutine.extract::<String>()?;
    if !subroutine.is_ascii() {
        return Ok(None);
    }
    let set = references.cast::<PySet>()?;
    let mut reference_values = Vec::with_capacity(set.len());
    for reference in set.iter() {
        if !reference.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let reference = reference.extract::<String>()?;
        if !reference.is_ascii() {
            return Ok(None);
        }
        reference_values.push(reference);
    }

    let full_name = normalize_ascii_name(&subroutine);
    let subroutine_basename = subroutine.rsplit('\\').next().unwrap_or(&subroutine);
    let basename = normalize_ascii_name(subroutine_basename);
    for reference in reference_values {
        if normalize_ascii_name(&reference) == full_name
            || normalize_ascii_name(reference.rsplit('\\').next().unwrap_or(&reference)) == basename
        {
            return Ok(Some(true));
        }
    }
    Ok(Some(false))
}

#[pyfunction]
fn clean_subroutine_name_ascii(value: &str) -> String {
    let trimmed =
        value.trim_matches(|character: char| matches!(character as u32, 0x09..=0x0d | 0x1c..=0x20));
    trimmed.trim_matches('"').replace('/', "\\")
}

#[pyfunction]
fn bracket_variable_names(value: &str) -> Vec<String> {
    let bytes = value.as_bytes();
    let mut names = Vec::new();
    let mut seen = HashSet::new();
    let mut index = 0;
    while index + 2 < bytes.len() {
        if bytes[index] != b'['
            || !(bytes[index + 1].is_ascii_alphabetic() || bytes[index + 1] == b'_')
        {
            index += 1;
            continue;
        }
        let start = index + 1;
        let mut end = start + 1;
        while end < bytes.len() && (bytes[end].is_ascii_alphanumeric() || bytes[end] == b'_') {
            end += 1;
        }
        if end < bytes.len() && bytes[end] == b']' {
            let name = value[start..end].to_owned();
            if seen.insert(name.clone()) {
                names.push(name);
            }
            index = end + 1;
        } else {
            index += 1;
        }
    }
    names
}

#[pyfunction]
fn count_string_groups<'py>(
    py: Python<'py>,
    values: &Bound<'py, PyAny>,
    groups: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    if !values.get_type().is(py.get_type::<PyList>())
        || !groups.get_type().is(py.get_type::<PyDict>())
    {
        return Ok(None);
    }
    let mut strings = Vec::new();
    for value in values.try_iter()? {
        let value = value?;
        if !value.get_type().is(py.get_type::<PyString>()) {
            return Ok(None);
        }
        let Ok(value) = value.extract::<String>() else {
            return Ok(None);
        };
        strings.push(value);
    }
    let mut prepared_groups = Vec::new();
    for (name, members) in groups.cast::<PyDict>()?.iter() {
        if !name.get_type().is(py.get_type::<PyString>())
            || !members.get_type().is(py.get_type::<PyList>())
        {
            return Ok(None);
        }
        let Ok(name) = name.extract::<String>() else {
            return Ok(None);
        };
        let mut member_set = HashSet::new();
        for member in members.try_iter()? {
            let member = member?;
            if !member.get_type().is(py.get_type::<PyString>()) {
                return Ok(None);
            }
            let Ok(member) = member.extract::<String>() else {
                return Ok(None);
            };
            member_set.insert(member);
        }
        prepared_groups.push((name, member_set));
    }
    let mut counts = vec![0_usize; prepared_groups.len()];
    for value in &strings {
        for (index, (_, members)) in prepared_groups.iter().enumerate() {
            if members.contains(value) {
                counts[index] += 1;
            }
        }
    }
    let result = PyDict::new(py);
    for ((name, _), count) in prepared_groups.iter().zip(counts) {
        result.set_item(name, count)?;
    }
    Ok(Some(result.into_any()))
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
fn worklist_parse_contract_tip_mask(
    value_json: &str,
    selected_indexes: bool,
    supported_channels: usize,
) -> PyResult<Option<String>> {
    let Ok(value) = serde_json::from_str::<Value>(value_json) else {
        return Ok(None);
    };
    let parsed = if selected_indexes {
        tecan_worklist_builder::contracts::parse_selected_tip_indexes(&value, supported_channels)
    } else {
        tecan_worklist_builder::contracts::parse_command_tip_mask(&value, supported_channels)
    };
    let payload = serde_json::json!({
        "value": parsed.value,
        "status": parsed.status,
        "channels": parsed.channels,
    });
    Ok(serde_json::to_string(&payload).ok())
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
fn worklist_normalize_tip_system(value: &str) -> Option<String> {
    if !value.is_ascii() {
        return None;
    }
    let normalized = trim_python_whitespace(value)
        .to_ascii_lowercase()
        .replace(' ', "_");
    Some(
        if matches!(
            normalized.as_str(),
            "fixed" | "fixed_tip" | "fixedtips" | "fixed_tips"
        ) {
            "fixed_tip".to_owned()
        } else if matches!(
            normalized.as_str(),
            "diti" | "disposable_tip" | "disposable_tips" | "disposable_tip_adapter"
        ) {
            "diti".to_owned()
        } else {
            "unknown".to_owned()
        },
    )
}

#[pyfunction]
fn worklist_row_name_to_index(value: &str) -> Option<i64> {
    let mut index = 0_i64;
    for byte in value.bytes() {
        let upper = byte.to_ascii_uppercase();
        if !upper.is_ascii_uppercase() {
            return None;
        }
        index = index
            .checked_mul(26)?
            .checked_add(i64::from(upper - b'A' + 1))?;
    }
    index.checked_sub(1)
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
fn worklist_window_properties(
    source_keys: Vec<Vec<String>>,
    destination_keys: Vec<Vec<String>>,
    tip_valid: Vec<bool>,
    tip_modes: Vec<String>,
    tip_channels: Vec<Option<usize>>,
) -> Option<(bool, bool, Vec<usize>, String)> {
    let count = source_keys.len();
    if destination_keys.len() != count
        || tip_valid.len() != count
        || tip_modes.len() != count
        || tip_channels.len() != count
    {
        return None;
    }
    let channels = tip_channels
        .iter()
        .flatten()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    if tip_valid.iter().any(|valid| !valid) {
        return Some((
            false,
            false,
            channels,
            "invalid tip selection prevents reordering".to_owned(),
        ));
    }
    let mut seen = HashSet::<Vec<String>>::new();
    for (source, destination) in source_keys.iter().zip(&destination_keys) {
        if seen.contains(source) || seen.contains(destination) {
            let explicit = tip_modes.iter().all(|mode| mode == "explicit");
            return Some((
                false,
                explicit,
                channels,
                "shared source/destination state is order-sensitive".to_owned(),
            ));
        }
        seen.insert(source.clone());
        seen.insert(destination.clone());
    }
    let explicit = tip_modes.iter().all(|mode| mode == "explicit");
    if !explicit {
        return Some((
            true,
            false,
            channels,
            "independent units may be analyzed, but automatic tips are not rewritten".to_owned(),
        ));
    }
    Some((
        true,
        true,
        channels,
        "independent explicit-tip units may be interleaved deterministically".to_owned(),
    ))
}

#[pyfunction]
fn worklist_barrier_reason(kind: &str) -> Option<String> {
    match kind {
        "break" => Some("explicit execution barrier".to_owned()),
        "state" => Some("typed worklist state transition".to_owned()),
        "comment" => Some("comment association".to_owned()),
        "other" => Some("unsupported or state-changing record".to_owned()),
        _ => None,
    }
}

#[pyfunction]
fn worklist_tip_diagnostic(
    raw: &str,
    code: &str,
    line: usize,
) -> Option<(String, String, Vec<usize>, String)> {
    if !raw.is_ascii() || !code.is_ascii() {
        return None;
    }
    Some((
        code.to_owned(),
        tecan_worklist_builder::semantics::tip_diagnostic_message(raw),
        vec![line],
        "error".to_owned(),
    ))
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
fn worklist_analyze_detail_lines(
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
    let Ok(analysis) = tecan_worklist_builder::semantics::analyze_worklist_detail(
        &worklist,
        enabled_tip_mask,
        supported_channels,
    ) else {
        return Ok(None);
    };
    Ok(serde_json::to_string(&analysis).ok())
}

#[pyfunction]
fn worklist_semantic_equivalent_lines(
    left_lines_json: &str,
    right_lines_json: &str,
    enabled_tip_mask: Option<&str>,
    supported_channels: usize,
) -> PyResult<Option<bool>> {
    let Ok(left_lines) = serde_json::from_str::<Vec<String>>(left_lines_json) else {
        return Ok(None);
    };
    let Ok(right_lines) = serde_json::from_str::<Vec<String>>(right_lines_json) else {
        return Ok(None);
    };
    let left = tecan_worklist_builder::Worklist {
        lines: left_lines,
        ..tecan_worklist_builder::Worklist::default()
    };
    let right = tecan_worklist_builder::Worklist {
        lines: right_lines,
        ..tecan_worklist_builder::Worklist::default()
    };
    let Ok(equivalent) = tecan_worklist_builder::semantics::semantic_equivalent(
        &left,
        &right,
        enabled_tip_mask,
        supported_channels,
    ) else {
        return Ok(None);
    };
    Ok(Some(equivalent))
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
    module.add_function(wrap_pyfunction!(has_gwl_record_prefix, module)?)?;
    module.add_function(wrap_pyfunction!(xml_regex_first_value, module)?)?;
    module.add_function(wrap_pyfunction!(extract_line_number_ascii, module)?)?;
    module.add_function(wrap_pyfunction!(xml_normalize_line_numbers_ascii, module)?)?;
    module.add_function(wrap_pyfunction!(xml_normalize_ascii_tail, module)?)?;
    module.add_function(wrap_pyfunction!(xml_compare_diff_hint_ascii, module)?)?;
    module.add_function(wrap_pyfunction!(xml_unknown_text_fields, module)?)?;
    module.add_function(wrap_pyfunction!(extract_data_store_versions, module)?)?;
    module.add_function(wrap_pyfunction!(count_xscr_object_type_attributes, module)?)?;
    module.add_function(wrap_pyfunction!(has_visionx_namespace, module)?)?;
    module.add_function(wrap_pyfunction!(zeia_xml_structural_flags, module)?)?;
    module.add_function(wrap_pyfunction!(xml_clean_text, module)?)?;
    module.add_function(wrap_pyfunction!(normalize_script_folder_path, module)?)?;
    module.add_function(wrap_pyfunction!(format_script_display_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_direct_child_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_direct_leaf_name_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_direct_record_children, module)?)?;
    module.add_function(wrap_pyfunction!(xml_attribute_name_values, module)?)?;
    module.add_function(wrap_pyfunction!(xml_first_nonempty_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_container_child_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_unique_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_texts_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_find_text, module)?)?;
    module.add_function(wrap_pyfunction!(xml_find_attribute, module)?)?;
    module.add_function(wrap_pyfunction!(xml_records_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_all_unique_texts, module)?)?;
    module.add_function(wrap_pyfunction!(xml_grouped_texts_excluding, module)?)?;
    module.add_function(wrap_pyfunction!(xml_elements_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_elements_ending_with, module)?)?;
    module.add_function(wrap_pyfunction!(xml_has_unmodeled_local_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_first_element_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_first_direct_child_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(xml_reference_guids, module)?)?;
    module.add_function(wrap_pyfunction!(xml_mesh_reference_lists, module)?)?;
    module.add_function(wrap_pyfunction!(xml_sub_component_names, module)?)?;
    module.add_function(wrap_pyfunction!(xml_matrix, module)?)?;
    module.add_function(wrap_pyfunction!(xml_indexed_values, module)?)?;
    module.add_function(wrap_pyfunction!(xml_indexed_float_values, module)?)?;
    module.add_function(wrap_pyfunction!(xml_allowed_grip_modes, module)?)?;
    module.add_function(wrap_pyfunction!(xml_vec_dict, module)?)?;
    module.add_function(wrap_pyfunction!(xml_vec3_case_sensitive, module)?)?;
    module.add_function(wrap_pyfunction!(xml_indexed_vectors, module)?)?;
    module.add_function(wrap_pyfunction!(
        xml_elements_by_ascii_casefolded_name,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        xml_first_text_by_ascii_casefolded_name,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        xml_first_vec_by_ascii_casefolded_name,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(xml_direct_elements_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(
        xml_direct_elements_by_ascii_prefix,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(xml_first_direct_element_by_name, module)?)?;
    module.add_function(wrap_pyfunction!(
        xml_direct_elements_by_ascii_casefolded_name,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(xml_leaf_texts, module)?)?;
    module.add_function(wrap_pyfunction!(
        xml_elements_by_folded_name_with_children,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        xml_indexed_elements_by_folded_name,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(xml_variable_definition_elements, module)?)?;
    module.add_function(wrap_pyfunction!(xml_direct_child_local_names, module)?)?;
    module.add_function(wrap_pyfunction!(
        xml_runtime_variable_definition_candidates,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(casefold_alias_values, module)?)?;
    module.add_function(wrap_pyfunction!(merge_unique_string_groups, module)?)?;
    module.add_function(wrap_pyfunction!(sort_string_keys, module)?)?;
    module.add_function(wrap_pyfunction!(any_ascii_casefold_in, module)?)?;
    module.add_function(wrap_pyfunction!(ordered_string_counts, module)?)?;
    module.add_function(wrap_pyfunction!(most_common_string_counts, module)?)?;
    module.add_function(wrap_pyfunction!(sorted_unique_strings, module)?)?;
    module.add_function(wrap_pyfunction!(ascii_casefold_equals, module)?)?;
    module.add_function(wrap_pyfunction!(ascii_casefold_trim, module)?)?;
    module.add_function(wrap_pyfunction!(parse_ascii_bool_text, module)?)?;
    module.add_function(wrap_pyfunction!(xml_element_is_nil, module)?)?;
    module.add_function(wrap_pyfunction!(ascii_lower_alnum_compact, module)?)?;
    module.add_function(wrap_pyfunction!(ascii_profile_slug, module)?)?;
    module.add_function(wrap_pyfunction!(ascii_safe_filename, module)?)?;
    module.add_function(wrap_pyfunction!(ascii_word_boundary_contains, module)?)?;
    module.add_function(wrap_pyfunction!(label_base_ascii_text, module)?)?;
    module.add_function(wrap_pyfunction!(is_guid_text, module)?)?;
    module.add_function(wrap_pyfunction!(non_guid_text, module)?)?;
    module.add_function(wrap_pyfunction!(format_site_path, module)?)?;
    module.add_function(wrap_pyfunction!(site_index_sort_key, module)?)?;
    module.add_function(wrap_pyfunction!(parse_i64_text, module)?)?;
    module.add_function(wrap_pyfunction!(parse_orientation_ascii, module)?)?;
    module.add_function(wrap_pyfunction!(parse_f64_decimal_text, module)?)?;
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
    module.add_function(wrap_pyfunction!(sha256_text, module)?)?;
    module.add_function(wrap_pyfunction!(sha256_file, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json_compact, module)?)?;
    module.add_function(wrap_pyfunction!(project_model_identifiers, module)?)?;
    module.add_function(wrap_pyfunction!(project_model_entry_path, module)?)?;
    module.add_function(wrap_pyfunction!(project_record_fingerprint, module)?)?;
    module.add_function(wrap_pyfunction!(readiness_candidate, module)?)?;
    module.add_function(wrap_pyfunction!(project_index_dedupe_indices, module)?)?;
    module.add_function(wrap_pyfunction!(readiness_kind_token, module)?)?;
    module.add_function(wrap_pyfunction!(diagnostic_sort_indices, module)?)?;
    module.add_function(wrap_pyfunction!(diagnostic_message, module)?)?;
    module.add_function(wrap_pyfunction!(diagnostic_redact_text, module)?)?;
    module.add_function(wrap_pyfunction!(xml_kind_from_suffix, module)?)?;
    module.add_function(wrap_pyfunction!(zeia_object_kind_from_suffix, module)?)?;
    module.add_function(wrap_pyfunction!(count_string_groups, module)?)?;
    module.add_function(wrap_pyfunction!(bracket_variable_names, module)?)?;
    module.add_function(wrap_pyfunction!(clean_subroutine_name_ascii, module)?)?;
    module.add_function(wrap_pyfunction!(normalize_ascii_name, module)?)?;
    module.add_function(wrap_pyfunction!(
        subroutine_reference_matches_ascii,
        module
    )?)?;
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
    module.add_function(wrap_pyfunction!(worklist_parse_contract_tip_mask, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_execution_contract, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_load_contract, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_validate_transfers, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_load_transfers, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_parse_bool, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_row_name_to_index, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_normalize_tip_system, module)?)?;
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
    module.add_function(wrap_pyfunction!(worklist_window_properties, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_barrier_reason, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_tip_diagnostic, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_analyze_lines, module)?)?;
    module.add_function(wrap_pyfunction!(worklist_analyze_detail_lines, module)?)?;
    module.add_function(wrap_pyfunction!(
        worklist_semantic_equivalent_lines,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(worklist_optimize_lines, module)?)?;
    module.add_function(wrap_pyfunction!(clean_empty_containers, module)?)?;
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
    fn ascii_boolean_parser_preserves_untrimmed_casefold_semantics() {
        assert_eq!(parse_ascii_bool_text("true"), Some(true));
        assert_eq!(parse_ascii_bool_text("TRUE"), Some(true));
        assert_eq!(parse_ascii_bool_text("false"), Some(false));
        assert_eq!(parse_ascii_bool_text("yes"), None);
        assert_eq!(parse_ascii_bool_text(" true "), None);
        assert_eq!(parse_ascii_bool_text("trüe"), None);
    }

    #[test]
    fn site_index_sort_format_matches_python_zero_padding() {
        assert_eq!(site_index_sort_key("0"), Some((0, "000000000000".into())));
        assert_eq!(site_index_sort_key("-1"), Some((0, "-00000000001".into())));
        assert_eq!(site_index_sort_key("+2"), Some((0, "000000000002".into())));
        assert_eq!(site_index_sort_key(" 3 "), None);
        assert_eq!(site_index_sort_key("9223372036854775808"), None);
    }

    #[test]
    fn ascii_lower_alnum_compaction_matches_python_regex_filter() {
        assert_eq!(
            ascii_lower_alnum_compact("7mm nest-template"),
            Some("7mmnesttemplate".into())
        );
        assert_eq!(ascii_lower_alnum_compact("AZ_09"), Some("09".into()));
        assert_eq!(ascii_lower_alnum_compact(""), Some(String::new()));
        assert_eq!(ascii_lower_alnum_compact("東京"), None);
    }

    #[test]
    fn ascii_safe_filename_matches_python_replacement_and_truncation() {
        assert_eq!(
            ascii_safe_filename("Simple-name.xscr"),
            Some("Simple-name.xscr".into())
        );
        assert_eq!(
            ascii_safe_filename("  .._Alpha / Beta__.. "),
            Some("Alpha_Beta".into())
        );
        assert_eq!(ascii_safe_filename("___"), Some(String::new()));
        assert_eq!(ascii_safe_filename("---"), Some("---".into()));
        assert_eq!(ascii_safe_filename(&"a".repeat(125)), Some("a".repeat(120)));
        assert_eq!(ascii_safe_filename("東京"), None);
    }

    #[test]
    fn ascii_profile_slug_matches_python_regex_and_edge_trimming() {
        assert_eq!(
            ascii_profile_slug("Mixed Name 42"),
            Some("mixed_name_42".into())
        );
        assert_eq!(
            ascii_profile_slug("  __Mixed--Name__ "),
            Some("mixed_name".into())
        );
        assert_eq!(
            ascii_profile_slug("Already_under_scored"),
            Some("already_under_scored".into())
        );
        assert_eq!(ascii_profile_slug("---"), Some(String::new()));
        assert_eq!(ascii_profile_slug("東京"), None);
    }

    #[test]
    fn ascii_casefold_trim_matches_python_strip_and_casefold() {
        assert_eq!(
            ascii_casefold_trim(" \tMixed Value\r\n"),
            Some("mixed value".into())
        );
        assert_eq!(ascii_casefold_trim("\u{1c}Text\u{1f}"), Some("text".into()));
        assert_eq!(ascii_casefold_trim("  "), Some(String::new()));
        assert_eq!(ascii_casefold_trim("ſcript"), None);
    }

    #[test]
    fn ascii_word_boundary_matches_python_character_class() {
        for (value, expected) in [
            ("nest", true),
            ("x_nest-y", true),
            ("  nest  ", true),
            ("nonest", false),
            ("nest1", false),
            ("1nest", false),
            ("nested", false),
            ("NEST", false),
        ] {
            assert_eq!(
                ascii_word_boundary_contains(value, "nest"),
                Some(expected),
                "unexpected word boundary result for {value:?}"
            );
        }
        assert_eq!(ascii_word_boundary_contains("東京 nest", "nest"), None);
        assert_eq!(ascii_word_boundary_contains("nest", ""), None);
    }

    #[test]
    fn orientation_parser_matches_supported_ascii_regex_cases() {
        assert_eq!(
            parse_orientation_ascii_text("prefix PHI \t=\n-1.25,\u{1c} tHeTa=0, PSI=3.5 trailing"),
            Some([-1.25, 0.0, 3.5])
        );
        assert_eq!(
            parse_orientation_ascii_text("Phi=bad,Theta=0,Psi=0; phi=4,theta=5,psi=-6.25"),
            Some([4.0, 5.0, -6.25])
        );
        assert_eq!(parse_orientation_ascii_text("no orientation here"), None);
        assert_eq!(parse_orientation_ascii_text("Phi=١,Theta=٢,Psi=٣"), None);
    }

    #[test]
    fn ascii_label_base_matches_bracket_suffix_regex_semantics() {
        for (value, expected) in [
            (" Name [alias] ", "Name [alias]"),
            ("Name [alias]", "Name"),
            ("Name [alias]\n", "Name"),
            ("Name [alias]\n\n", "Name [alias]"),
            ("Name [alias]\r\n", "Name [alias]"),
            ("Name []", "Name []"),
            ("Name [[alias]", "Name"),
            ("Name [first][last]", "Name [first]"),
            ("Name [line\nbreak]", "Name"),
            ("[alias]", ""),
        ] {
            assert_eq!(
                label_base_ascii(value).as_deref(),
                Some(expected),
                "unexpected label base for {value:?}"
            );
        }
        assert_eq!(label_base_ascii("東京 [alias]"), None);
    }

    #[test]
    fn ascii_guid_match_preserves_fullmatch_and_case_semantics() {
        let guid = "11111111-1111-1111-1111-111111111111";
        assert!(is_ascii_guid(guid));
        assert!(is_ascii_guid(&guid.to_uppercase()));
        assert!(is_ascii_guid("00000000-0000-0000-0000-000000000000"));
        for value in [
            "",
            "11111111-1111-1111-1111-11111111111",
            "11111111_1111-1111-1111-111111111111",
            " 11111111-1111-1111-1111-111111111111",
            "11111111-1111-1111-1111-111111111111\n",
            "１１１11111-1111-1111-1111-111111111111",
        ] {
            assert!(!is_ascii_guid(value), "unexpected GUID match for {value:?}");
        }
    }

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
        assert_eq!(zeia_object_kind_from_suffix(".xcmp"), "component");
        assert_eq!(zeia_object_kind_from_suffix(".XCMP"), "XCMP");
        assert_eq!(zeia_object_kind_from_suffix("...Custom"), "Custom");
        assert_eq!(zeia_object_kind_from_suffix(".é"), "é");
        assert_eq!(zeia_object_kind_from_suffix("..."), "xml");
    }

    #[test]
    fn bracket_variable_scanner_matches_ascii_regex_boundaries() {
        assert_eq!(
            bracket_variable_names(
                "[Foo] [bar_2] [Foo] [] [_x] [9bad] [with-dash] [[nested]] [é] [Aé]"
            ),
            vec!["Foo", "bar_2", "_x", "nested"]
        );
    }

    #[test]
    fn clean_subroutine_name_matches_ascii_python_stripping() {
        assert_eq!(
            clean_subroutine_name_ascii(" \u{1c}\"Macro/Sub\" \u{1f}"),
            "Macro\\Sub"
        );
        assert_eq!(clean_subroutine_name_ascii("\" foo \""), " foo ");
        assert_eq!(clean_subroutine_name_ascii("\"\""), "");
        assert_eq!(normalize_ascii_name(" \u{1c}MiXeD\u{1f} "), "mixed");
        assert_eq!(normalize_ascii_name(""), "");
    }

    #[test]
    fn gwl_prefix_probe_matches_ascii_python_regex_behavior() {
        for text in [
            "A;source",
            "d1;destination",
            "  W4;wash\r\n",
            "\u{1c}B;blank",
            "\n\tA;source",
        ] {
            assert_eq!(has_gwl_record_prefix_ascii(text), Some(true), "{text:?}");
        }
        for text in ["", "X;ignored", "A5;ignored", "A ;ignored", "A"] {
            assert_eq!(has_gwl_record_prefix_ascii(text), Some(false), "{text:?}");
        }
        assert_eq!(has_gwl_record_prefix_ascii("\u{00a0}A;source"), None);
    }

    #[test]
    fn line_number_tag_normalization_matches_ascii_regex_shape() {
        assert_eq!(
            normalize_line_number_tags_ascii_value(
                "<LINEnumber>\u{1c}123 \t</lineNUMBER><LineNumber>bad</LineNumber>"
            ),
            Some("<LineNumber>0</LineNumber><LineNumber>bad</LineNumber>".to_owned())
        );
        assert_eq!(
            normalize_line_number_tags_ascii_value(
                "<LineNumber>1</LineNumber><LineNumber> 22 </LineNumber>"
            ),
            Some("<LineNumber>0</LineNumber><LineNumber>0</LineNumber>".to_owned())
        );
        assert_eq!(
            normalize_line_number_tags_ascii_value("<LineNumber>１２</LineNumber>"),
            None
        );
    }

    #[test]
    fn script_folder_normalization_matches_python_strip_replace_strip() {
        assert_eq!(
            normalize_script_folder_path_value(" \u{2003}/folder//child\\\u{00a0} "),
            "folder\\\\child"
        );
        assert_eq!(normalize_script_folder_path_value("///"), "");
        assert_eq!(
            normalize_script_folder_path_value("left/right"),
            "left\\right"
        );
        assert_eq!(
            normalize_script_folder_path_value("\u{00a0}é/路径/\u{2003}"),
            "é\\路径"
        );
    }

    #[test]
    fn script_display_name_matches_python_folder_prefix_rule() {
        assert_eq!(
            format_script_display_name_value("folder", "script"),
            "folder\\script"
        );
        assert_eq!(
            format_script_display_name_value("folder", "nested\\script"),
            "nested\\script"
        );
        assert_eq!(format_script_display_name_value("", "script"), "script");
        assert_eq!(format_script_display_name_value("folder", ""), "folder\\");
        assert_eq!(
            format_script_display_name_value("路径", "名字"),
            "路径\\名字"
        );
    }

    #[test]
    fn xml_compare_diff_hint_matches_ascii_regex_scans() {
        assert_eq!(
            xml_compare_diff_hint_ascii_value(
                "<root><alpha></alpha></root>",
                "<root><beta></beta></root>"
            ),
            Some("tag mismatch at index 1: 'alpha' vs 'beta'".to_owned())
        );
        assert_eq!(
            xml_compare_diff_hint_ascii_value("<root></root><tail></tail>", "<root></root>"),
            Some("tag count mismatch: 2 vs 1".to_owned())
        );
        assert_eq!(
            xml_compare_diff_hint_ascii_value("<root>one</root>", "<root>two</root>"),
            Some("payload mismatch after normalization".to_owned())
        );
        assert_eq!(
            xml_compare_diff_hint_ascii_value(
                "<root><a-b></a-b></root>",
                "<root><a-c></a-c></root>"
            ),
            Some("tag count mismatch: 2 vs 1".to_owned())
        );
        assert_eq!(xml_compare_diff_hint_ascii_value("<café>", "<café>"), None);
    }

    #[test]
    fn xml_ascii_tail_matches_whitespace_and_self_closing_regexes() {
        assert_eq!(
            normalize_xml_ascii_tail_value(" <Foo />\u{1c}bar <x-y:1/>"),
            Some("<foo></foo>bar<x-y:1></x-y:1>".to_owned())
        );
        assert_eq!(
            normalize_xml_ascii_tail_value("A\tB\r\n<C/>"),
            Some("ab<c></c>".to_owned())
        );
        assert_eq!(normalize_xml_ascii_tail_value("\u{00a0}<Foo/>"), None);
    }

    #[test]
    fn line_number_extraction_matches_ascii_regex_shape() {
        assert_eq!(
            extract_line_number_ascii_value(
                "<LineNumber> \t123\r\n</LineNumber><LineNumber>456</LineNumber>"
            ),
            "123"
        );
        assert_eq!(
            extract_line_number_ascii_value("<LineNumber>\u{1c}42\u{1f}</LineNumber>"),
            "42"
        );
        assert_eq!(
            extract_line_number_ascii_value(
                "<LineNumber>12 34</LineNumber><LineNumber>005</LineNumber>"
            ),
            "005"
        );
        assert_eq!(
            extract_line_number_ascii_value("<LineNumber>+12</LineNumber>"),
            ""
        );
        assert_eq!(extract_line_number_ascii_value("<LineNumber>12"), "");
    }

    #[test]
    fn xml_scalar_scan_preserves_ascii_regex_capture_and_whitespace() {
        for (text, name, expected) in [
            ("<ObjectName> A \r\n B </ObjectName>", "ObjectName", "A B"),
            (
                "<x:ObjectName attr='x'>Some <ignored> content</other:ObjectName>",
                "ObjectName",
                "Some <ignored> content",
            ),
            (
                "<ObjectName>outer <ObjectName>inner</ObjectName>",
                "ObjectName",
                "outer <ObjectName>inner",
            ),
            (
                "<foo:ObjectName>first</x:ObjectName><ObjectName>second</ObjectName>",
                "ObjectName",
                "first",
            ),
            ("<ObjectName/>ignored</ObjectName>", "ObjectName", ""),
            ("<ObjectName >missing close", "ObjectName", ""),
            (
                "<1:ObjectName>invalid prefix</ObjectName>",
                "ObjectName",
                "",
            ),
            ("<ObjectName>Case</Objectname>", "ObjectName", "Case"),
        ] {
            assert_eq!(
                xml_regex_first_value_ascii(text, name),
                Some(expected.to_string()),
                "{text:?}"
            );
        }
        assert_eq!(xml_regex_first_value_ascii("<Name>é</Name>", "Name"), None);
        assert_eq!(xml_regex_first_value_ascii("<Name>x</Name>", "é"), None);
    }

    #[test]
    fn oversized_xml_unknown_field_scan_preserves_non_overlapping_regex_matches() {
        let text = concat!(
            "<Root><Known>skip</Known>",
            "<Odd>outer <Inner>not independently matched</Inner></Odd>",
            "<Keep> alpha\t beta </keep><Keep>alpha beta</Keep>",
            "<keep>lowercase name</keep>",
            "<Empty> \n </Empty><After>retained</After>",
            "<x:Prefixed attr='x'>value</Prefixed>",
            "<Repeated>one</Repeated><Repeated>two</Repeated>",
        );
        let known = vec!["Known".to_string()];
        assert_eq!(
            xml_unknown_text_fields_ascii(text, &known, 20),
            Some(vec![
                ("After".into(), vec!["retained".into()]),
                ("Keep".into(), vec!["alpha beta".into()]),
                ("Prefixed".into(), vec!["value".into()]),
                ("Repeated".into(), vec!["one".into(), "two".into()]),
                ("keep".into(), vec!["lowercase name".into()]),
            ])
        );
        assert_eq!(
            xml_unknown_text_fields_ascii(
                "<Root><Repeated>one</Repeated><Repeated>two</Repeated>",
                &[],
                1
            ),
            Some(vec![("Repeated".into(), vec!["one".into()])])
        );
        assert_eq!(
            xml_unknown_text_fields_ascii("<Name>café</Name>", &[], 20),
            None
        );
    }

    #[test]
    fn datastore_version_scan_matches_ascii_regex_substrings_and_order() {
        let text = concat!(
            "xDATASTOREVERSION = '3'",
            " <VxData dataStoreVersion=\"2\">",
            " <x dataStoreVersion=''>",
            " dataStoreVersion=missing",
            " DATASTOREVERSION\t=\t\"legacy value\"",
        );
        assert_eq!(
            extract_data_store_versions_ascii(text),
            Some(vec!["3".into(), "2".into(), "legacy value".into()])
        );
        assert_eq!(
            extract_data_store_versions_ascii("no version"),
            Some(Vec::new())
        );
        assert_eq!(
            extract_data_store_versions_ascii("dataStoreVersion=\"3\u{00a0}"),
            None
        );
    }

    #[test]
    fn oversized_xscr_object_type_count_preserves_regex_boundaries() {
        for (text, expected) in [
            ("<Object Type='T1' /><object TYPE=\"T2\" />", 2),
            ("<ObjectType='bad'>", 0),
            ("<Object Type = 'bad'>", 0),
            ("<Object data=\"Type='inside'\">", 1),
            ("<Object Type='first' x='y' Type='last'>", 1),
            ("<Object Type='unterminated", 1),
            ("<Object Type=''>", 0),
            ("<Object\nTYPE='line'>", 1),
            ("<Object Type='one'><Object Type='two'>", 2),
        ] {
            assert_eq!(
                count_xscr_object_type_attributes_ascii(text),
                Some(expected),
                "{text:?}"
            );
        }
        assert_eq!(
            count_xscr_object_type_attributes_ascii("<Object Type='é'>"),
            None
        );
    }

    #[test]
    fn visionx_probe_marker_matches_supported_ascii_regex_cases() {
        for text in [
            "http://www.tecan.com/TSCC/VisionX/DataStore",
            "<VxData>",
            "<x:VxData >",
            "<Root><NotVxData></Root>",
            "<Object Value='VxData\tmore'>",
        ] {
            assert_eq!(has_visionx_namespace_ascii(text), Some(true), "{text:?}");
        }
        for text in ["", "<VxDatax>", "<VxData", "<Root>plain</Root>"] {
            assert_eq!(has_visionx_namespace_ascii(text), Some(false), "{text:?}");
        }
        assert_eq!(has_visionx_namespace_ascii("<VxData> café"), None);
    }

    #[test]
    fn zeia_structural_flags_cover_ascii_pattern_edges() {
        for (text, expected) in [
            ("<ObjectName>foo</ObjectName><Script />", (true, true)),
            (
                "<ObjectName/>foo</FakeObjectName><x:Script\n>",
                (true, true),
            ),
            ("<ObjectNameX>foo</ObjectName><Script>", (false, true)),
            ("<ObjectName>foo<Other></ObjectName><Script>", (false, true)),
            ("<Thing><Object Type='A'/></Thing>", (false, true)),
            ("<Object\tType=foo", (false, true)),
            ("<Object Type = 'x'>", (false, false)),
            ("<ObjectX Type=foo>", (false, false)),
            ("<Object Value='Script'>", (false, false)),
            ("<Script\tSomething>", (false, false)),
            ("<ScriptX>", (false, false)),
        ] {
            assert_eq!(
                zeia_xml_structural_flags_ascii(text),
                Some(expected),
                "{text:?}"
            );
        }
        assert_eq!(
            zeia_xml_structural_flags_ascii("<ObjectName>café</ObjectName>"),
            None
        );
    }
}
