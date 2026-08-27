use std::path::Path;
use std::process::Stdio;

use serde_json::{Map, Value};
use tracing::warn;

use crate::config::Config;

use super::parquet;

const PREVIEW_ROWS: usize = 100;

/// Preview a result file into `{columns, rows, total_rows, truncated}`.
pub async fn preview_file(config: &Config, path: &str) -> Result<Map<String, Value>, String> {
    if is_parquet(path) {
        let p = path.to_string();
        match tokio::task::spawn_blocking(move || parquet::read_preview(&p, PREVIEW_ROWS)).await {
            Ok(Ok(map)) => return Ok(map),
            Ok(Err(e)) => warn!(path, error = %e, "native parquet preview failed; using Python"),
            Err(e) => warn!(path, error = %e, "preview task panicked; using Python"),
        }
    }
    python_preview(config, path).await
}

fn is_parquet(path: &str) -> bool {
    Path::new(path)
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("parquet"))
}

/// Fallback: run `python -m tubulin.preview <path>` and return its JSON object.
async fn python_preview(config: &Config, path: &str) -> Result<Map<String, Value>, String> {
    let output = config
        .python_command()
        .arg("-m")
        .arg("tubulin.preview")
        .arg(path)
        .current_dir(&config.project_root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await
        .map_err(|e| format!("failed to launch preview: {e}"))?;

    if !output.status.success() {
        return Err(format!(
            "preview failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }

    match serde_json::from_slice::<Value>(&output.stdout) {
        Ok(Value::Object(map)) => Ok(map),
        Ok(_) => Err("preview output was not a JSON object".into()),
        Err(e) => Err(format!("could not parse preview output: {e}")),
    }
}
