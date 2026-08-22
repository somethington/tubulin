use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::extract::{Multipart, State};
use axum::http::StatusCode;
use axum::Json;
use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;
use tracing::info;
use uuid::Uuid;

use crate::state::AppState;

/// Deletes a partially-written file on drop unless `keep()` was called.
struct PartialFileGuard {
    path: Option<PathBuf>,
}

impl PartialFileGuard {
    fn new(path: PathBuf) -> Self {
        Self { path: Some(path) }
    }

    fn keep(&mut self) {
        self.path = None;
    }
}

impl Drop for PartialFileGuard {
    fn drop(&mut self) {
        if let Some(p) = self.path.take() {
            let _ = std::fs::remove_file(p);
        }
    }
}

/// Accept a single-file multipart upload, store it under the upload dir, return its path.
pub(super) async fn upload_handler(
    State(state): State<Arc<AppState>>,
    mut multipart: Multipart,
) -> Result<Json<Value>, (StatusCode, String)> {
    let Some(mut field) = multipart
        .next_field()
        .await
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("bad upload: {e}")))?
    else {
        return Err((StatusCode::BAD_REQUEST, "no file in upload".into()));
    };

    let filename = field.file_name().map(str::to_string);
    let upload_id = Uuid::new_v4();
    let ext = filename
        .as_deref()
        .and_then(|f| Path::new(f).extension())
        .and_then(|e| e.to_str())
        .unwrap_or("dat");
    let stored = state.config.upload_dir.join(format!("{upload_id}.{ext}"));

    // Stream to disk; the guard removes the partial file on error or client disconnect.
    let mut guard = PartialFileGuard::new(stored.clone());
    let mut file = tokio::fs::File::create(&stored).await.map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("create failed: {e}"),
        )
    })?;
    let mut written: u64 = 0;
    while let Some(chunk) = field
        .chunk()
        .await
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("read failed: {e}")))?
    {
        file.write_all(&chunk).await.map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("write failed: {e}"),
            )
        })?;
        written += chunk.len() as u64;
    }
    file.flush().await.map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("flush failed: {e}"),
        )
    })?;
    guard.keep();

    // Workers run with the project root as cwd, so hand back a relative path.
    let rel = stored
        .strip_prefix(&state.config.project_root)
        .unwrap_or(&stored)
        .to_string_lossy()
        .to_string();

    info!(path = %rel, bytes = written, "stored upload");
    Ok(Json(json!({
        "upload_id": upload_id,
        "path": rel,
        "filename": filename,
    })))
}
