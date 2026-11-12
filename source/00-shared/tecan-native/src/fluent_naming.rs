use pyo3::prelude::*;
use regex::Regex;
use std::sync::OnceLock;

fn python_whitespace(character: char) -> bool {
    character.is_whitespace() || matches!(character, '\u{001c}'..='\u{001f}')
}

fn instance_suffix_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"\[[\s\x1c-\x1f]*[^\]]+[\s\x1c-\x1f]*\]$")
            .expect("instance suffix regex is valid")
    })
}

fn strip_instance_suffix_text(value: &str) -> String {
    let text = value.trim_matches(python_whitespace);
    if text.is_empty() {
        return String::new();
    }
    let replaced = instance_suffix_regex().replace(text, "");
    let stripped = replaced.trim_matches(python_whitespace);
    if stripped.is_empty() {
        text.to_owned()
    } else {
        stripped.to_owned()
    }
}

#[pyfunction]
pub fn strip_fluent_instance_suffix(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let text = if value.is_truthy()? {
        value.str()?.extract::<String>()?
    } else {
        String::new()
    };
    Ok(strip_instance_suffix_text(&text))
}

#[cfg(test)]
mod tests {
    use super::strip_instance_suffix_text;

    #[test]
    fn suffix_stripping_preserves_python_whitespace_and_regex_edges() {
        for (value, expected) in [
            (" Runner[001] ", "Runner"),
            ("FilterDWP[platecount]", "FilterDWP"),
            ("A [one] [two]", "A [one]"),
            ("Name [ ]", "Name"),
            ("Name[]", "Name[]"),
            ("Straße [alias]", "Straße"),
            ("A [line\nbreak]", "A"),
            ("A [\u{001c}alias]", "A"),
            ("\u{2003}A [alias]\u{2003}", "A"),
            ("[alias]", "[alias]"),
            ("Plain", "Plain"),
            ("", ""),
        ] {
            assert_eq!(strip_instance_suffix_text(value), expected, "{value:?}");
        }
    }
}
