//! Hot reload: re-register the app when its Python source changes.
//!
//! `tubulin run` is a dev command, and the edit → see-it loop is the framework's daily
//! experience. Restarting the runtime to see a widget move is the wrong price, so this
//! task watches the app's `.py` files and re-runs the registration pass on change,
//! pushing the new UI tree to every open browser.
//!
//! **Why polling and not a filesystem-notify crate.** The watch set is a handful of
//! source files, `stat` is cheap, and mtime changes coalesce on their own — an editor
//! that writes-then-renames produces one observed change, not the burst of events an
//! inotify/FSEvents stream would need debouncing for. A dependency-free 500ms poll
//! buys the same result; see `docs/roadmap.md` on the project's dependency floor.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use tubulin_protocol::ServerMessage;
use tracing::{info, warn};

use crate::registry;
use crate::state::AppState;

/// How often the watch set is stat'd.
const POLL: Duration = Duration::from_millis(500);

/// Upper bound on watched files, so a `.py`-heavy tree can't turn the poll into a
/// directory walk that costs more than the reload it is watching for.
const MAX_WATCHED: usize = 512;

/// Watch `ui_path`'s directory and re-register on change until the process exits.
pub async fn watch(state: Arc<AppState>, ui_path: PathBuf) {
    let root = ui_path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));

    let mut seen = snapshot(&root);
    info!(
        dir = %root.display(),
        files = seen.len(),
        "watching for changes — edit and save to reload"
    );

    loop {
        tokio::time::sleep(POLL).await;
        let current = snapshot(&root);
        if current == seen {
            continue;
        }
        let changed = describe_change(&seen, &current);
        reload(&state, &ui_path, &changed).await;
        // Re-snapshot *after* the reload rather than reusing `current`: the
        // registration pass imports the app, which can itself write files next to the
        // source. Taking the state as of now keeps those writes from looking like the
        // next edit and triggering a second reload.
        seen = snapshot(&root);
    }
}

async fn reload(state: &Arc<AppState>, ui_path: &Path, changed: &str) {
    info!(file = %changed, "change detected, re-registering app");

    let spec = match registry::register(&state.config, ui_path).await {
        Ok(spec) => spec,
        Err(e) => {
            // Keep serving the last good spec: a syntax error mid-edit shouldn't blank
            // the page. Surface it in the browser so the reason is visible there.
            warn!("reload failed, keeping the previous app:\n{e:#}");
            let message = format!("Reload failed — still serving the previous version.\n{e:#}");
            for session in state.sessions.all() {
                session.send(ServerMessage::Error {
                    message: message.clone(),
                });
            }
            return;
        }
    };

    let spec = state.set_spec(spec);
    info!(
        "reloaded '{}' ({} nodes, {} jobs) → {} session(s)",
        spec.title,
        spec.nodes.len(),
        spec.jobs.len(),
        state.sessions.count()
    );

    for session in state.sessions.all() {
        // Re-seed before the tree goes out: the browser reseeds its own values from the
        // same nodes, so runtime state and what the user sees stay in agreement.
        session.seed_inputs(&spec);
        session.send(ServerMessage::UiTree {
            session_id: session.id.clone(),
            title: spec.title.clone(),
            nodes: spec.nodes.clone(),
        });
    }
}

/// Modification times of the `.py` files under `root`, keyed by path.
fn snapshot(root: &Path) -> BTreeMap<PathBuf, SystemTime> {
    let mut out = BTreeMap::new();
    collect(root, &mut out);
    out
}

fn collect(dir: &Path, out: &mut BTreeMap<PathBuf, SystemTime>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        if out.len() >= MAX_WATCHED {
            return;
        }
        let path = entry.path();
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            // Skip caches and environments: nothing an author edits lives there, and
            // they are exactly the directories large enough to make the walk expensive.
            if name.starts_with('.') || matches!(name.as_ref(), "__pycache__" | "node_modules") {
                continue;
            }
            collect(&path, out);
        } else if path.extension().is_some_and(|e| e == "py") {
            if let Ok(modified) = entry.metadata().and_then(|m| m.modified()) {
                out.insert(path, modified);
            }
        }
    }
}

/// The first added/changed/removed path, for the log line.
fn describe_change(
    before: &BTreeMap<PathBuf, SystemTime>,
    after: &BTreeMap<PathBuf, SystemTime>,
) -> String {
    let changed = after
        .iter()
        .find(|(path, time)| before.get(*path) != Some(time))
        .map(|(path, _)| path)
        .or_else(|| before.keys().find(|path| !after.contains_key(*path)));
    changed
        .and_then(|p| p.file_name())
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "?".into())
}

#[cfg(test)]
mod tests;
