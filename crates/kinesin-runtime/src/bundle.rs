//! Build a results `.zip` from a bundle manifest, natively (no Python subprocess).
//!
//! The manifest is the JSON the worker writes via `job.bundle(...)`:
//! `{"entries": [...]}` where each entry is either a single file (`{file, arc}`) or
//! a directory glob (`{glob, arc_dir, suffix?}`). Globs are expanded at zip time, so
//! they pick up files written by *all* of a sharded job's workers.

use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::Value;
use zip::write::SimpleFileOptions;
use zip::CompressionMethod;

/// Build `out_path` from `manifest_path`. Relative manifest entries resolve against
/// `base` (the project root — workers write manifests with that cwd, matching the
/// deleted `kinesin.zipjob` subprocess which ran with `current_dir(project_root)`).
/// Writes to `<out>.partial` then renames, so a concurrent reader never sees a
/// half-written archive. Synchronous — call from a blocking context, and hold the
/// caller's per-bundle lock: two builders on one `.partial` corrupt the archive.
pub fn build_bundle(manifest_path: &str, out_path: &str, base: &Path) -> Result<(), String> {
    let text = std::fs::read_to_string(manifest_path)
        .map_err(|e| format!("read manifest: {e}"))?;
    let manifest: Value =
        serde_json::from_str(&text).map_err(|e| format!("parse manifest: {e}"))?;

    let tmp = format!("{out_path}.partial");
    let file = std::fs::File::create(&tmp).map_err(|e| format!("create zip: {e}"))?;
    let mut zip = zip::ZipWriter::new(file);
    // large_file: entries can exceed 4 GiB (zip64) — Python's zipfile allowed this.
    let opts = SimpleFileOptions::default()
        .compression_method(CompressionMethod::Deflated)
        .large_file(true);
    // Match Python glob semantics: `*` never matches a leading dot (keeps .DS_Store
    // and friends out of the archive).
    let glob_opts = glob::MatchOptions {
        require_literal_leading_dot: true,
        ..Default::default()
    };

    let entries = manifest
        .get("entries")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();

    for entry in &entries {
        if let Some(src) = entry.get("file").and_then(Value::as_str) {
            let arc = entry.get("arc").and_then(Value::as_str).unwrap_or(src);
            let resolved = resolve(base, src);
            if resolved.is_file() {
                add_file(&mut zip, &resolved.to_string_lossy(), arc, opts)?;
            }
        } else if let Some(pattern) = entry.get("glob").and_then(Value::as_str) {
            let arc_dir = entry.get("arc_dir").and_then(Value::as_str).unwrap_or("");
            let suffix = entry.get("suffix").and_then(Value::as_str).unwrap_or("");
            let pattern = resolve(base, pattern).to_string_lossy().into_owned();
            let mut paths: Vec<_> = glob::glob_with(&pattern, glob_opts)
                .map_err(|e| format!("bad glob {pattern:?}: {e}"))?
                .filter_map(Result::ok)
                // Directories can't be copied into an entry (EISDIR); Python's
                // zipfile tolerated them, we simply skip them.
                .filter(|p| p.is_file())
                .collect();
            paths.sort();
            for path in paths {
                let arc = arc_name(&path, arc_dir, suffix);
                add_file(&mut zip, &path.to_string_lossy(), &arc, opts)?;
            }
        }
    }

    zip.finish().map_err(|e| format!("finish zip: {e}"))?;
    std::fs::rename(&tmp, out_path).map_err(|e| format!("rename zip: {e}"))?;
    Ok(())
}

/// A manifest path against the project root: absolute paths pass through.
fn resolve(base: &Path, path: &str) -> PathBuf {
    let p = Path::new(path);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        base.join(p)
    }
}

/// Stream one file into the archive under `arc`.
fn add_file<W: Write + std::io::Seek>(
    zip: &mut zip::ZipWriter<W>,
    src: &str,
    arc: &str,
    opts: SimpleFileOptions,
) -> Result<(), String> {
    let mut f = std::fs::File::open(src).map_err(|e| format!("open {src}: {e}"))?;
    zip.start_file(arc, opts)
        .map_err(|e| format!("zip entry {arc}: {e}"))?;
    std::io::copy(&mut f, zip).map_err(|e| format!("copy {src}: {e}"))?;
    Ok(())
}

/// Archive name for a globbed file: `<arc_dir>/<stem><suffix><ext>` (matching the
/// Python builder's `os.path.splitext` + `os.path.join` behavior).
fn arc_name(path: &Path, arc_dir: &str, suffix: &str) -> String {
    let stem = path.file_stem().map(|s| s.to_string_lossy()).unwrap_or_default();
    let ext = path
        .extension()
        .map(|e| format!(".{}", e.to_string_lossy()))
        .unwrap_or_default();
    let name = format!("{stem}{suffix}{ext}");
    if arc_dir.is_empty() {
        name
    } else {
        format!("{}/{}", arc_dir.trim_end_matches('/'), name)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn builds_zip_from_file_and_glob_entries() {
        let dir = std::env::temp_dir().join("kin_bundle_test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("curves")).unwrap();
        std::fs::write(dir.join("a.txt"), b"hello").unwrap();
        std::fs::write(dir.join("curves/x.svg"), b"<svg/>").unwrap();
        std::fs::write(dir.join("curves/y.svg"), b"<svg/>").unwrap();

        let manifest = serde_json::json!({
            "entries": [
                { "file": dir.join("a.txt").to_string_lossy(), "arc": "report.txt" },
                { "glob": dir.join("curves/*.svg").to_string_lossy(),
                  "arc_dir": "curves", "suffix": "_plot" },
            ]
        });
        let manifest_path = dir.join("m.json");
        std::fs::write(&manifest_path, manifest.to_string()).unwrap();
        let out = dir.join("out.zip");

        build_bundle(&manifest_path.to_string_lossy(), &out.to_string_lossy(), &dir).unwrap();

        let f = std::fs::File::open(&out).unwrap();
        let mut zip = zip::ZipArchive::new(f).unwrap();
        let mut names: Vec<String> = (0..zip.len())
            .map(|i| zip.by_index(i).unwrap().name().to_string())
            .collect();
        names.sort();
        assert_eq!(names, vec!["curves/x_plot.svg", "curves/y_plot.svg", "report.txt"]);

        // partial file should have been renamed away
        assert!(!dir.join("out.zip.partial").exists());

        let mut report = String::new();
        zip.by_name("report.txt").unwrap().read_to_string(&mut report).unwrap();
        assert_eq!(report, "hello");

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Python-glob parity: `*` skips dotfiles, directory matches don't abort the
    /// build, and relative entries resolve against the base (project root).
    #[test]
    fn skips_dotfiles_and_directories_and_resolves_relative_paths() {
        let dir = std::env::temp_dir().join("kin_bundle_semantics");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("curves/subdir")).unwrap();
        std::fs::write(dir.join("curves/x.svg"), b"<svg/>").unwrap();
        std::fs::write(dir.join("curves/.DS_Store"), b"junk").unwrap();
        std::fs::write(dir.join("report.txt"), b"rel").unwrap();

        // Bare `<dir>/*` glob (the shape worker.py writes) and RELATIVE paths.
        let manifest = serde_json::json!({
            "entries": [
                { "file": "report.txt", "arc": "report.txt" },
                { "file": "missing.txt", "arc": "missing.txt" },
                { "glob": "curves/*", "arc_dir": "figs" },
            ]
        });
        let manifest_path = dir.join("m.json");
        std::fs::write(&manifest_path, manifest.to_string()).unwrap();
        let out = dir.join("out.zip");

        // Would EISDIR on `subdir` and ship `.DS_Store` before the fix; relative
        // entries would silently vanish when cwd != base.
        build_bundle(&manifest_path.to_string_lossy(), &out.to_string_lossy(), &dir).unwrap();

        let f = std::fs::File::open(&out).unwrap();
        let mut zip = zip::ZipArchive::new(f).unwrap();
        let mut names: Vec<String> = (0..zip.len())
            .map(|i| zip.by_index(i).unwrap().name().to_string())
            .collect();
        names.sort();
        assert_eq!(names, vec!["figs/x.svg", "report.txt"]);

        let _ = std::fs::remove_dir_all(&dir);
    }
}
