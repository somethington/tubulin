use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Query, Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::Value;
use tower::ServiceExt as _;
use tower_http::services::ServeFile;

use crate::artifacts::preview::preview_file;
use crate::state::AppState;

/// Resolve `path` against the project root and reject anything outside it.
async fn contained_path(state: &AppState, path: &str) -> Result<PathBuf, (StatusCode, String)> {
    let resolved = if Path::new(path).is_absolute() {
        PathBuf::from(path)
    } else {
        state.config.project_root.join(path)
    };
    let canonical = tokio::fs::canonicalize(&resolved)
        .await
        .map_err(|e| (StatusCode::NOT_FOUND, format!("no such file: {e}")))?;
    if !canonical.starts_with(&state.config.project_root) {
        return Err((StatusCode::FORBIDDEN, "path outside project root".into()));
    }
    Ok(canonical)
}

fn path_param(params: &HashMap<String, String>) -> Result<&str, (StatusCode, String)> {
    params
        .get("path")
        .map(String::as_str)
        .ok_or((StatusCode::BAD_REQUEST, "missing 'path' query param".into()))
}

/// Preview a result file on demand (slot-bound previews are pushed over WS instead).
pub(super) async fn preview_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let canonical = contained_path(&state, path_param(&params)?).await?;
    match preview_file(&state.config, &canonical.to_string_lossy()).await {
        Ok(map) => Ok(Json(Value::Object(map))),
        Err(e) => Err((StatusCode::INTERNAL_SERVER_ERROR, e)),
    }
}

/// Serve a result file as an attachment download.
pub(super) async fn download_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
    req: Request,
) -> Result<Response, (StatusCode, String)> {
    serve_file(&state, path_param(&params)?, req, true).await
}

/// Serve a result file inline (for embedding in `<img>`, etc.).
pub(super) async fn file_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
    req: Request,
) -> Result<Response, (StatusCode, String)> {
    serve_file(&state, path_param(&params)?, req, false).await
}

/// Stream one contained file via `ServeFile` (mime guessing, Range, conditional requests).
pub(super) async fn serve_file(
    state: &AppState,
    path: &str,
    req: Request,
    attachment: bool,
) -> Result<Response, (StatusCode, String)> {
    let canonical = contained_path(state, path).await?;

    let filename = canonical.file_name().map_or_else(
        || "download".to_string(),
        |n| n.to_string_lossy().into_owned(),
    );
    let disposition = if attachment {
        format!("attachment; filename=\"{filename}\"")
    } else {
        "inline".to_string()
    };

    // The original request carries Range / If-Modified-Since headers through.
    let mut response = ServeFile::new(&canonical)
        .oneshot(req)
        .await
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("read failed: {e}"),
            )
        })?
        .map(Body::new);
    if let Ok(value) = disposition.parse() {
        response
            .headers_mut()
            .insert(axum::http::header::CONTENT_DISPOSITION, value);
    }
    Ok(response.into_response())
}
