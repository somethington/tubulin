use serde_json::{json, Value};

use crate::artifacts::preview::preview_file;
use crate::config::Config;

use super::runner::JobOutcome;
use super::JobSink;

/// Send each bound output widget its view of the job's results.
pub(super) async fn dispatch(config: &Config, sink: &JobSink, job_id: &str, outcome: &JobOutcome) {
    let named = |name: &str| outcome.outputs.get(name).map(String::as_str);
    for (node_id, view) in &sink.result_nodes {
        if view == "result_preview" {
            send_table(config, sink, node_id, outcome.result_path.as_deref()).await;
        } else if let Some(name) = view.strip_prefix("plot:") {
            send_plot(sink, node_id, named(name)).await;
        } else if let Some(name) = view.strip_prefix("plotly:") {
            send_plotly(sink, node_id, named(name)).await;
        } else if let Some(name) = view.strip_prefix("table:") {
            send_table(config, sink, node_id, named(name)).await;
        } else if view.starts_with("download:") {
            send_download(sink, node_id, job_id);
        } else if let Some(name) = view.strip_prefix("gallery:") {
            send_gallery(sink, node_id, named(name)).await;
        }
    }
}

/// Preview a result file into a table widget (natively for parquet, or via Python).
async fn send_table(config: &Config, sink: &JobSink, node_id: &str, path: Option<&str>) {
    let props = match path {
        None => json!({ "ready": true, "missing": true }),
        Some(path) => match preview_file(config, path).await {
            Ok(mut preview) => {
                preview.insert("ready".into(), Value::Bool(true));
                Value::Object(preview)
            }
            Err(e) => json!({ "ready": true, "error": e }),
        },
    };
    sink.send_to(node_id, props);
}

/// Inline an SVG plot file into an image widget (or mark it missing).
async fn send_plot(sink: &JobSink, node_id: &str, path: Option<&str>) {
    let props = match path {
        None => json!({ "ready": true, "missing": true }),
        Some(path) => match tokio::fs::read_to_string(path).await {
            Ok(svg) => json!({ "ready": true, "svg": svg }),
            Err(e) => json!({ "ready": true, "error": format!("could not read plot: {e}") }),
        },
    };
    sink.send_to(node_id, props);
}

/// Load a Plotly figure JSON file into a plotly widget (or mark it missing).
async fn send_plotly(sink: &JobSink, node_id: &str, path: Option<&str>) {
    let props = match path {
        None => json!({ "ready": true, "missing": true }),
        Some(path) => match tokio::fs::read_to_string(path).await {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                Ok(figure) => json!({ "ready": true, "figure": figure }),
                Err(e) => json!({ "ready": true, "error": format!("bad plot json: {e}") }),
            },
            Err(e) => json!({ "ready": true, "error": format!("could not read plot: {e}") }),
        },
    };
    sink.send_to(node_id, props);
}

/// Build a gallery by listing a directory of curve SVGs (written by every shard).
async fn send_gallery(sink: &JobSink, node_id: &str, dir: Option<&str>) {
    let props = match dir {
        None => json!({ "ready": true, "missing": true }),
        Some(dir) => match list_curve_dir(dir).await {
            Ok(items) => json!({ "ready": true, "items": items }),
            Err(e) => json!({ "ready": true, "error": format!("could not read gallery: {e}") }),
        },
    };
    sink.send_to(node_id, props);
}

async fn list_curve_dir(dir: &str) -> std::io::Result<Vec<Value>> {
    let mut items = Vec::new();
    let Ok(mut entries) = tokio::fs::read_dir(dir).await else {
        return Ok(items); // no curves (e.g. plotting disabled)
    };
    while let Some(entry) = entries.next_entry().await? {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("svg") {
            let protein = path
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            items.push(json!({ "protein": protein, "path": path.to_string_lossy() }));
        }
    }
    items.sort_by(|a, b| a["protein"].as_str().cmp(&b["protein"].as_str()));
    Ok(items)
}

/// Expose the job's download bundle, built on demand by the `/bundle` route.
fn send_download(sink: &JobSink, node_id: &str, job_id: &str) {
    let short = job_id.get(..8).unwrap_or(job_id);
    sink.send_to(
        node_id,
        json!({
            "ready": true,
            "url": format!("/bundle?job={job_id}"),
            "filename": format!("TPP_results_{short}.zip"),
        }),
    );
}
