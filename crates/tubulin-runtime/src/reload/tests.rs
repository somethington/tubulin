use super::*;
use std::fs;

fn tmpdir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("kin_reload_{name}"));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn names(dir: &Path) -> Vec<String> {
    snapshot(dir)
        .keys()
        .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
        .collect()
}

/// Stamp an mtime explicitly, so the tests assert on the comparison logic rather than
/// on filesystem clock resolution or on how fast they happened to run.
fn set_mtime(path: &Path, offset_secs: u64) {
    let file = fs::OpenOptions::new().write(true).open(path).unwrap();
    file.set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000 + offset_secs))
        .unwrap();
}

#[test]
fn snapshot_sees_python_files_only() {
    let dir = tmpdir("kinds");
    for f in ["ui.py", "compute.py", "data.csv", "notes.md"] {
        fs::write(dir.join(f), "x").unwrap();
    }
    assert_eq!(names(&dir), ["compute.py", "ui.py"]);
}

#[test]
fn snapshot_descends_into_packages_but_skips_caches() {
    let dir = tmpdir("nested");
    fs::write(dir.join("ui.py"), "x").unwrap();
    for sub in ["pipeline", "__pycache__", ".venv"] {
        fs::create_dir_all(dir.join(sub)).unwrap();
    }
    fs::write(dir.join("pipeline/steps.py"), "x").unwrap();
    fs::write(dir.join("__pycache__/ui.cpython-311.py"), "x").unwrap();
    fs::write(dir.join(".venv/thing.py"), "x").unwrap();

    // A sibling package is the author's code and must be watched; caches and
    // environments are neither edited nor cheap to walk.
    assert_eq!(names(&dir), ["steps.py", "ui.py"]);
}

#[test]
fn a_missing_directory_is_empty_not_a_panic() {
    assert!(snapshot(Path::new("/definitely/not/here")).is_empty());
}

#[test]
fn an_unchanged_tree_compares_equal() {
    let dir = tmpdir("stable");
    fs::write(dir.join("ui.py"), "x").unwrap();
    assert_eq!(snapshot(&dir), snapshot(&dir));
}

#[test]
fn a_saved_file_changes_the_snapshot() {
    let dir = tmpdir("edit");
    let file = dir.join("ui.py");
    fs::write(&file, "x").unwrap();
    set_mtime(&file, 0);
    let before = snapshot(&dir);

    set_mtime(&file, 30); // the editor saves
    let after = snapshot(&dir);

    assert_ne!(before, after);
    assert_eq!(describe_change(&before, &after), "ui.py");
}

#[test]
fn describes_an_edited_file() {
    let before: BTreeMap<PathBuf, SystemTime> = [
        (PathBuf::from("/a/ui.py"), SystemTime::UNIX_EPOCH),
        (PathBuf::from("/a/compute.py"), SystemTime::UNIX_EPOCH),
    ]
    .into();
    let mut after = before.clone();
    after.insert(
        PathBuf::from("/a/compute.py"),
        SystemTime::UNIX_EPOCH + Duration::from_secs(5),
    );
    assert_eq!(describe_change(&before, &after), "compute.py");
}

#[test]
fn describes_an_added_and_a_removed_file() {
    let before: BTreeMap<PathBuf, SystemTime> =
        [(PathBuf::from("/a/ui.py"), SystemTime::UNIX_EPOCH)].into();

    let mut added = before.clone();
    added.insert(PathBuf::from("/a/new.py"), SystemTime::UNIX_EPOCH);
    assert_eq!(describe_change(&before, &added), "new.py");

    assert_eq!(describe_change(&before, &BTreeMap::new()), "ui.py");
}

#[test]
fn the_walk_is_bounded() {
    let dir = tmpdir("many");
    for i in 0..(MAX_WATCHED + 40) {
        fs::write(dir.join(format!("m{i}.py")), "x").unwrap();
    }
    assert_eq!(snapshot(&dir).len(), MAX_WATCHED);
}
