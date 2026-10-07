//! `tubulin new <dir>`: write a runnable starter app, so the first minute after
//! `pip install tubulin` is `tubulin run`, not copy-pasting from the README.
//!
//! The templates live in `crates/tubulin-runtime/templates/` and are compiled into the
//! binary. `tests/test_scaffold.py` runs the `ui.py` template through the headless
//! harness, so a template that stops registering fails CI.

use std::path::{Path, PathBuf};

use anyhow::{bail, Context};

const UI_PY: &str = include_str!("../templates/ui.py");
const TUBULIN_TOML: &str = include_str!("../templates/tubulin.toml");
const GITIGNORE: &str = include_str!("../templates/gitignore");

/// Write the starter app into `dir` (created if missing). Refuses to overwrite any
/// existing file, so pointing it at a real project can't clobber work.
pub fn new_app(dir: &Path) -> anyhow::Result<Vec<PathBuf>> {
    let files = [
        ("ui.py", UI_PY.replace("{{title}}", &title_for(dir))),
        ("tubulin.toml", TUBULIN_TOML.to_string()),
        (".gitignore", GITIGNORE.to_string()),
    ];
    let existing: Vec<_> = files
        .iter()
        .map(|(name, _)| dir.join(name))
        .filter(|p| p.exists())
        .collect();
    if !existing.is_empty() {
        let names: Vec<_> = existing.iter().map(|p| p.display().to_string()).collect();
        bail!("refusing to overwrite {}", names.join(", "));
    }
    std::fs::create_dir_all(dir).with_context(|| format!("cannot create {}", dir.display()))?;
    let mut written = Vec::new();
    for (name, contents) in files {
        let path = dir.join(name);
        std::fs::write(&path, contents)
            .with_context(|| format!("cannot write {}", path.display()))?;
        written.push(path);
    }
    Ok(written)
}

/// A display title from the directory name: `my-cool_app` -> `My Cool App`. Anything
/// that isn't a letter or digit is dropped, which also keeps the title safe to drop
/// into a Python string literal.
fn title_for(dir: &Path) -> String {
    let name = std::fs::canonicalize(dir)
        .ok()
        .or_else(|| std::env::current_dir().ok().map(|cwd| cwd.join(dir)))
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
        .unwrap_or_default();
    let words: Vec<String> = name
        .split(|c: char| !c.is_alphanumeric())
        .filter(|w| !w.is_empty())
        .map(|w| {
            let mut chars = w.chars();
            let first = chars.next().map(|c| c.to_uppercase().collect::<String>());
            first.unwrap_or_default() + chars.as_str()
        })
        .collect();
    if words.is_empty() {
        "My Tubulin App".to_string()
    } else {
        words.join(" ")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tubulin-scaffold-{}", uuid::Uuid::new_v4()));
        dir.join(name)
    }

    #[test]
    fn writes_a_titled_app() {
        let dir = tmp("my-cool_app");
        let written = new_app(&dir).unwrap();
        assert_eq!(written.len(), 3);
        let ui = std::fs::read_to_string(dir.join("ui.py")).unwrap();
        assert!(ui.contains(r#"App("My Cool App")"#));
        assert!(!ui.contains("{{title}}"));
        assert!(dir.join("tubulin.toml").exists());
        std::fs::remove_dir_all(dir.parent().unwrap()).unwrap();
    }

    #[test]
    fn refuses_to_overwrite() {
        let dir = tmp("app");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("ui.py"), "mine").unwrap();
        let err = new_app(&dir).unwrap_err().to_string();
        assert!(err.contains("refusing to overwrite"), "{err}");
        assert_eq!(std::fs::read_to_string(dir.join("ui.py")).unwrap(), "mine");
        assert!(!dir.join("tubulin.toml").exists());
        std::fs::remove_dir_all(dir.parent().unwrap()).unwrap();
    }

    /// The scaffold ships a copy of the repo's documented-defaults `tubulin.toml`
    /// (a copy, because the crate's sdist can't reach outside its own directory).
    #[test]
    fn toml_template_matches_the_checked_in_defaults() {
        assert_eq!(TUBULIN_TOML, include_str!("../../../tubulin.toml"));
    }

    #[test]
    fn title_falls_back_when_the_name_has_no_words() {
        assert_eq!(title_for(Path::new("/tmp/__")), "My Tubulin App");
        assert_eq!(title_for(Path::new("/tmp/tpp2")), "Tpp2");
    }
}
