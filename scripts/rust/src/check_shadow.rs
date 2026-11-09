use std::path::Path;
use std::process::Command;

const SHADOW_SUFFIXES: &[&str] = &[".orig", ".bak", ".old", ".backup", "~"];

pub fn is_instruction_surface(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_lowercase();
    let parts = path
        .components()
        .filter_map(|part| part.as_os_str().to_str())
        .map(str::to_lowercase)
        .collect::<Vec<_>>();
    name.starts_with("agents")
        || parts.iter().any(|part| part == "agents.details")
        || (parts.len() >= 2
            && matches!(parts[0].as_str(), ".cursor" | ".codex")
            && parts[1] == "rules")
}

pub fn is_shadow_instruction(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_lowercase();
    is_instruction_surface(path) && SHADOW_SUFFIXES.iter().any(|suffix| name.ends_with(suffix))
}

pub fn run(repo_root: &Path) -> Result<(), String> {
    let output = Command::new("git")
        .args(["ls-files", "-z"])
        .current_dir(repo_root)
        .output()
        .map_err(|error| format!("could not run git ls-files: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "git ls-files failed with exit code {}",
            output.status.code().unwrap_or(1)
        ));
    }

    let violations = output
        .stdout
        .split(|byte| *byte == 0)
        .filter(|value| !value.is_empty())
        .filter_map(|value| std::str::from_utf8(value).ok())
        .map(Path::new)
        .filter(|path| is_shadow_instruction(path))
        .map(|path| path.display().to_string())
        .collect::<Vec<_>>();

    if violations.is_empty() {
        println!("Shadow instruction check passed.");
        return Ok(());
    }

    eprintln!("Shadow instruction check failed. Tracked backup instruction files were found:");
    for path in violations {
        eprintln!("  {path}");
    }
    eprintln!("Remove the shadow copy and use Git history for previous instruction versions.");
    Err("shadow instruction files found".to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catches_instruction_backup_names() {
        assert!(is_shadow_instruction(Path::new("AGENTS.md.bak")));
        assert!(is_shadow_instruction(Path::new(
            ".cursor/rules/policy.mdc.old"
        )));
        assert!(is_shadow_instruction(Path::new(
            ".codex/rules/agents.details.txt~"
        )));
    }

    #[test]
    fn ignores_non_instruction_backups() {
        assert!(!is_shadow_instruction(Path::new("docs/README.md.bak")));
        assert!(!is_shadow_instruction(Path::new(
            "source/agents.details/data.json"
        )));
    }
}
