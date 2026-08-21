mod bundle;
mod events;
mod files;
mod upload;
mod websocket;

use std::sync::Arc;

use axum::extract::{DefaultBodyLimit, State, WebSocketUpgrade};
use axum::response::Response;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, Value};
use tower_http::compression::predicate::{NotForContentType, Predicate};
use tower_http::compression::{CompressionLayer, DefaultPredicate};
use tower_http::services::{ServeDir, ServeFile};
use tracing::{info, warn};

use crate::state::AppState;

const MAX_UPLOAD_BYTES: usize = 512 * 1024 * 1024;

pub async fn serve(state: Arc<AppState>) -> anyhow::Result<()> {
    let host = state.config.host.clone();
    let port = state.config.port;

    let index = state.config.frontend_dir.join("index.html");
    let serve_dir =
        ServeDir::new(&state.config.frontend_dir).not_found_service(ServeFile::new(index));

    if !state.config.frontend_dir.exists() {
        warn!(
            dir = %state.config.frontend_dir.display(),
            "frontend build not found — run `cd frontend && npm run build`"
        );
    }

    let app = Router::new()
        .route("/ws", get(ws_handler))
        .route("/upload", post(upload::upload_handler))
        .route("/preview", get(files::preview_handler))
        .route("/download", get(files::download_handler))
        .route("/file", get(files::file_handler))
        .route("/bundle", get(bundle::bundle_handler))
        .route("/health", get(health_handler))
        .fallback_service(serve_dir)
        // Compress text-ish payloads only; zips and binary results gain nothing.
        .layer(
            CompressionLayer::new().compress_when(
                DefaultPredicate::new()
                    .and(NotForContentType::const_new("application/zip"))
                    .and(NotForContentType::const_new("application/octet-stream")),
            ),
        )
        .layer(DefaultBodyLimit::max(MAX_UPLOAD_BYTES))
        .with_state(state);

    let addr = format!("{host}:{port}");
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    info!("kinesin runtime listening on http://{addr}");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn ws_handler(ws: WebSocketUpgrade, State(state): State<Arc<AppState>>) -> Response {
    ws.on_upgrade(move |socket| websocket::handle_socket(socket, state))
}

async fn health_handler(State(state): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "app": state.spec().title,
        "sessions": state.sessions.count(),
    }))
}
