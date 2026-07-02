//! The HTTP/WebSocket server: serves the frontend, accepts uploads, answers preview
//! requests, and upgrades WebSocket connections.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::{DefaultBodyLimit, Multipart, Query, Request, State, WebSocketUpgrade};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;
use tower::ServiceExt as _;
use tower_http::compression::predicate::{NotForContentType, Predicate};
use tower_http::compression::{CompressionLayer, DefaultPredicate};
use tower_http::services::{ServeDir, ServeFile};
use tracing::{info, warn};
use uuid::Uuid;

use crate::preview::preview_file;
use crate::state::AppState;
use crate::websocket::handle_socket;

const MAX_UPLOAD_BYTES: usize = 512 * 1024 * 1024; // 512 MiB

pub async fn serve(state: Arc<AppState>) -> anyhow::Result<()> {
    let host = state.config.host.clone();
    let port = state.config.port;

    let index = state.config.frontend_dir.join("index.html");
    let serve_dir = ServeDir::new(&state.config.frontend_dir)
        .not_found_service(ServeFile::new(index.clone()));

    if !state.config.frontend_dir.exists() {
        warn!(
            dir = %state.config.frontend_dir.display(),
            "frontend build not found — run `cd frontend && npm run build`"
        );
    }

    let app = Router::new()
        .route("/ws", get(ws_handler))
        .route("/upload", post(upload_handler))
        .route("/preview", get(preview_handler))
        .route("/download", get(download_handler))
        .route("/file", get(file_handler))
        .route("/bundle", get(bundle_handler))
        .route("/health", get(health_handler))
        .fallback_service(serve_dir)
        // gzip/br for text-ish payloads (JS bundle, SVG plots, Plotly/JSON, previews)
        // — but not already-compressed zips or binary result files, where a full
        // recompression pass per download buys ~nothing.
        .layer(CompressionLayer::new().compress_when(
            DefaultPredicate::new()
                .and(NotForContentType::const_new("application/zip"))
                .and(NotForContentType::const_new("application/octet-stream")),
        ))
        .layer(DefaultBodyLimit::max(MAX_UPLOAD_BYTES))
        .with_state(state);

    let addr = format!("{host}:{port}");
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    info!("kinesin runtime listening on http://{addr}");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn ws_handler(ws: WebSocketUpgrade, State(state): State<Arc<AppState>>) -> Response {
    ws.on_upgrade(move |socket| handle_socket(socket, state))
}

async fn health_handler(State(state): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "app": state.spec.title,
        "sessions": state.sessions.count(),
    }))
}

/// Removes a partially-written file on drop unless `keep()` was called — covers
/// both error returns and the handler future being dropped on client disconnect.
struct PartialFileGuard {
    path: Option<std::path::PathBuf>,
}

impl PartialFileGuard {
    fn new(path: std::path::PathBuf) -> Self {
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

/// Accept a multipart upload, store it on disk (Rule 2), return its path.
async fn upload_handler(
    State(state): State<Arc<AppState>>,
    mut multipart: Multipart,
) -> Result<Json<Value>, (StatusCode, String)> {
    // The client sends a single file field per upload; take the first.
    if let Some(mut field) = multipart
        .next_field()
        .await
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("bad upload: {e}")))?
    {
        let filename = field.file_name().map(str::to_string);

        let upload_id = Uuid::new_v4();
        let ext = filename
            .as_deref()
            .and_then(|f| Path::new(f).extension())
            .and_then(|e| e.to_str())
            .unwrap_or("dat");
        let stored = state.config.upload_dir.join(format!("{upload_id}.{ext}"));

        // Stream the field straight to disk (Rule 2) — never hold the whole upload
        // in memory. The guard deletes the partial file on any error return AND on
        // client disconnect (the handler future is dropped mid-await).
        let mut guard = PartialFileGuard::new(stored.clone());
        let mut file = tokio::fs::File::create(&stored)
            .await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("create failed: {e}")))?;
        let mut written: u64 = 0;
        while let Some(chunk) = field
            .chunk()
            .await
            .map_err(|e| (StatusCode::BAD_REQUEST, format!("read failed: {e}")))?
        {
            file.write_all(&chunk)
                .await
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("write failed: {e}")))?;
            written += chunk.len() as u64;
        }
        file.flush()
            .await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("flush failed: {e}")))?;
        guard.keep(); // complete — the file stays

        // Return a path relative to the project root; workers run with that cwd.
        let rel = stored
            .strip_prefix(&state.config.project_root)
            .unwrap_or(&stored)
            .to_string_lossy()
            .to_string();

        info!(path = %rel, bytes = written, "stored upload");
        return Ok(Json(json!({
            "upload_id": upload_id,
            "path": rel,
            "filename": filename,
        })));
    }

    Err((StatusCode::BAD_REQUEST, "no file in upload".into()))
}

/// Preview a result file on demand (the slot-bound case is pushed over WS instead).
async fn preview_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let path = params
        .get("path")
        .ok_or((StatusCode::BAD_REQUEST, "missing 'path' query param".into()))?;

    // Containment check: only serve files under the project root.
    let resolved = if Path::new(path).is_absolute() {
        std::path::PathBuf::from(path)
    } else {
        state.config.project_root.join(path)
    };
    let canonical = tokio::fs::canonicalize(&resolved)
        .await
        .map_err(|e| (StatusCode::NOT_FOUND, format!("no such file: {e}")))?;
    if !canonical.starts_with(&state.config.project_root) {
        return Err((StatusCode::FORBIDDEN, "path outside project root".into()));
    }

    match preview_file(&state.config, &canonical.to_string_lossy()).await {
        Ok(map) => Ok(Json(Value::Object(map))),
        Err(e) => Err((StatusCode::INTERNAL_SERVER_ERROR, e)),
    }
}

/// Serve a result file as a download (Content-Disposition: attachment).
async fn download_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
    req: Request,
) -> Result<Response, (StatusCode, String)> {
    serve_file(&state, params.get("path"), req, true).await
}

/// Serve a result file inline (for embedding in <img>, etc.).
async fn file_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
    req: Request,
) -> Result<Response, (StatusCode, String)> {
    serve_file(&state, params.get("path"), req, false).await
}

/// Build (lazily, once) and serve a job's results .zip from its bundle manifest.
async fn bundle_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
    req: Request,
) -> Result<Response, (StatusCode, String)> {
    let job = params
        .get("job")
        .ok_or((StatusCode::BAD_REQUEST, "missing 'job' query param".into()))?;
    // Guard against path traversal — job ids are uuids.
    if !job.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err((StatusCode::BAD_REQUEST, "invalid job id".into()));
    }

    let base = state.config.result_dir.join(job);
    let manifest = format!("{}_bundle.json", base.display());
    let zip = format!("{}_results.zip", base.display());

    if !Path::new(&zip).exists() {
        // Serialize builds: concurrent requests would interleave writes on the same
        // `.partial` file and cache a corrupt archive. Losers of the race re-check
        // under the lock and find the winner's zip already in place.
        let _build = state.bundle_lock.lock().await;
        if !Path::new(&zip).exists() {
            if !Path::new(&manifest).exists() {
                return Err((StatusCode::NOT_FOUND, "no bundle for this job".into()));
            }
            // Build natively on a blocking thread (zip is synchronous I/O). Relative
            // manifest entries resolve against the project root, like the workers
            // that wrote them.
            let (m, z) = (manifest.clone(), zip.clone());
            let root = state.config.project_root.clone();
            tokio::task::spawn_blocking(move || crate::bundle::build_bundle(&m, &z, &root))
                .await
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("zip task: {e}")))?
                .map_err(|e| {
                    (StatusCode::INTERNAL_SERVER_ERROR, format!("bundle build failed: {e}"))
                })?;
        }
    }

    serve_file(&state, Some(&zip), req, true).await
}

/// Serve one result file via tower-http's `ServeFile`: streaming, mime-guessed
/// content-type, and Range/conditional-request support (resumable downloads), with
/// our containment check in front and Content-Disposition appended.
async fn serve_file(
    state: &AppState,
    path: Option<&String>,
    req: Request,
    attachment: bool,
) -> Result<Response, (StatusCode, String)> {
    let path = path.ok_or((StatusCode::BAD_REQUEST, "missing 'path' query param".into()))?;

    let resolved = if Path::new(path).is_absolute() {
        std::path::PathBuf::from(path)
    } else {
        state.config.project_root.join(path)
    };
    let canonical = tokio::fs::canonicalize(&resolved)
        .await
        .map_err(|e| (StatusCode::NOT_FOUND, format!("no such file: {e}")))?;
    if !canonical.starts_with(&state.config.project_root) {
        return Err((StatusCode::FORBIDDEN, "path outside project root".into()));
    }

    let filename = canonical
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "download".into());
    let disposition = if attachment {
        format!("attachment; filename=\"{filename}\"")
    } else {
        "inline".to_string()
    };

    // The original request carries Range / If-Modified-Since headers through.
    let mut response = ServeFile::new(&canonical)
        .oneshot(req)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("read failed: {e}")))?
        .map(Body::new);
    if let Ok(value) = disposition.parse() {
        response
            .headers_mut()
            .insert(axum::http::header::CONTENT_DISPOSITION, value);
    }
    Ok(response.into_response())
}
