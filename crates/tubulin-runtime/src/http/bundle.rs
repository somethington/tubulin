use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use axum::extract::{Query, Request, State};
use axum::http::StatusCode;
use axum::response::Response;

use crate::artifacts::bundle::build_bundle;
use crate::http::files::serve_file;
use crate::state::AppState;

/// Build (lazily, once) and serve a job's results zip from its bundle manifest.
pub(super) async fn bundle_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
    req: Request,
) -> Result<Response, (StatusCode, String)> {
    let job = params
        .get("job")
        .ok_or((StatusCode::BAD_REQUEST, "missing 'job' query param".into()))?;
    // Job ids are runtime-generated uuids; reject anything that could traverse paths.
    if !job.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err((StatusCode::BAD_REQUEST, "invalid job id".into()));
    }

    let base = state.config.result_dir.join(job);
    let manifest = format!("{}_bundle.json", base.display());
    let zip = format!("{}_results.zip", base.display());

    if !Path::new(&zip).exists() {
        // The lock serializes builds; losers re-check and find the winner's zip.
        let _build = state.bundle_lock.lock().await;
        if !Path::new(&zip).exists() {
            if !Path::new(&manifest).exists() {
                return Err((StatusCode::NOT_FOUND, "no bundle for this job".into()));
            }
            // Zip building is synchronous I/O, so it runs on a blocking thread.
            let (m, z) = (manifest.clone(), zip.clone());
            let root = state.config.project_root.clone();
            tokio::task::spawn_blocking(move || build_bundle(&m, &z, &root))
                .await
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("zip task: {e}")))?
                .map_err(|e| {
                    (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        format!("bundle build failed: {e}"),
                    )
                })?;
        }
    }

    serve_file(&state, &zip, req, true).await
}
