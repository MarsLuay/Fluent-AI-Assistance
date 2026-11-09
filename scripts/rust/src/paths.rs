use std::env;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct RepoPaths {
    pub root: PathBuf,
    pub package: PathBuf,
}

impl RepoPaths {
    pub fn discover() -> Result<Self, String> {
        let mut starts = Vec::new();
        if let Ok(value) = env::var("FLUENT_AI_REPO_ROOT") {
            starts.push(PathBuf::from(value));
        }
        if let Ok(value) = env::current_dir() {
            starts.push(value);
        }
        if let Ok(value) = env::current_exe() {
            if let Some(parent) = value.parent() {
                starts.push(parent.to_path_buf());
            }
        }
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        starts.push(manifest);

        for start in starts {
            for candidate in start.ancestors() {
                if Self::is_repo_root(candidate) {
                    let root = candidate.to_path_buf();
                    return Ok(Self {
                        package: root.join("source").join("03-protocol-builder"),
                        root,
                    });
                }
            }
        }

        Err("Could not find the Fluent AI-Assistance repository root. Run from the repository or set FLUENT_AI_REPO_ROOT.".to_owned())
    }

    fn is_repo_root(path: &Path) -> bool {
        path.join("AGENTS.md").is_file()
            && path
                .join("source")
                .join("03-protocol-builder")
                .join("pyproject.toml")
                .is_file()
    }

    pub fn venv_python(&self) -> Option<PathBuf> {
        #[cfg(windows)]
        let candidates = [
            self.root.join(".venv").join("Scripts").join("python.exe"),
            self.root.join(".venv").join("bin").join("python"),
            self.root.join(".venv").join("bin").join("python3"),
        ];
        #[cfg(not(windows))]
        let candidates = [
            self.root.join(".venv").join("bin").join("python"),
            self.root.join(".venv").join("bin").join("python3"),
            self.root.join(".venv").join("Scripts").join("python.exe"),
        ];
        candidates.into_iter().find(|path| path.is_file())
    }
}
