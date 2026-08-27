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

    build_bundle(
        &manifest_path.to_string_lossy(),
        &out.to_string_lossy(),
        &dir,
    )
    .unwrap();

    let f = std::fs::File::open(&out).unwrap();
    let mut zip = zip::ZipArchive::new(f).unwrap();
    let mut names: Vec<String> = (0..zip.len())
        .map(|i| zip.by_index(i).unwrap().name().to_string())
        .collect();
    names.sort();
    assert_eq!(
        names,
        vec!["curves/x_plot.svg", "curves/y_plot.svg", "report.txt"]
    );

    // partial file should have been renamed away
    assert!(!dir.join("out.zip.partial").exists());

    let mut report = String::new();
    zip.by_name("report.txt")
        .unwrap()
        .read_to_string(&mut report)
        .unwrap();
    assert_eq!(report, "hello");

    let _ = std::fs::remove_dir_all(&dir);
}

/// Python-glob parity: dotfiles are skipped, directories don't abort, relative paths resolve.
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

    build_bundle(
        &manifest_path.to_string_lossy(),
        &out.to_string_lossy(),
        &dir,
    )
    .unwrap();

    let f = std::fs::File::open(&out).unwrap();
    let mut zip = zip::ZipArchive::new(f).unwrap();
    let mut names: Vec<String> = (0..zip.len())
        .map(|i| zip.by_index(i).unwrap().name().to_string())
        .collect();
    names.sort();
    assert_eq!(names, vec!["figs/x.svg", "report.txt"]);

    let _ = std::fs::remove_dir_all(&dir);
}
