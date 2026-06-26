//! The job manager: bounded queue, a worker pool, and routing of worker output to
//! the widgets bound to each job.

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use kinesin_protocol::{JobDef, JobStatus, ServerMessage, WorkerMessage};
use serde_json::{json, Map, Value};
use tokio::sync::mpsc::UnboundedSender;
use tokio::sync::Semaphore;
use tracing::{info, warn};

use crate::config::Config;
use crate::preview::run_preview;
use crate::worker::run_worker;

/// Where a job's updates go: the session's outbound channel plus the widgets bound
/// to the job's slot. `result_nodes` pairs each bound output node with its view
/// ("result_preview", "plot:<name>", "table:<name>").
#[derive(Clone)]
pub struct JobSink {
    pub tx: UnboundedSender<ServerMessage>,
    pub status_nodes: Vec<String>,
    pub result_nodes: Vec<(String, String)>,
}

impl JobSink {
    fn send_to(&self, node: &str, props: Value) {
        if let Value::Object(map) = props {
            let _ = self.tx.send(ServerMessage::WidgetUpdate {
                node_id: node.to_string(),
                props: map,
            });
        }
    }

    fn status(&self, props: Value) {
        for node in &self.status_nodes {
            self.send_to(node, props.clone());
        }
    }
}

/// Lightweight record kept for introspection (Job tracking per the spec).
#[derive(Clone)]
#[allow(dead_code)] // fields are tracked now, surfaced by a future /jobs endpoint
pub struct JobRecord {
    pub id: String,
    pub status: JobStatus,
    pub result_path: Option<String>,
    pub logs: Vec<String>,
}

pub struct JobRequest {
    pub job_id: String,
    pub job_def: JobDef,
    pub args: Vec<Value>,
    pub sink: JobSink,
}

pub struct JobManager {
    config: Arc<Config>,
    sem: Arc<Semaphore>,
    active: Arc<AtomicUsize>,
    records: Arc<Mutex<HashMap<String, JobRecord>>>,
}

impl JobManager {
    pub fn new(config: Arc<Config>) -> Self {
        let permits = config.max_workers;
        Self {
            config,
            sem: Arc::new(Semaphore::new(permits)),
            active: Arc::new(AtomicUsize::new(0)),
            records: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Enqueue a job. Returns the job id, or an error if the queue is full.
    pub fn submit(&self, req: JobRequest) -> Result<String, String> {
        // Count queued+running jobs against the cap (Memory/scaling Rule).
        let in_flight = self.active.fetch_add(1, Ordering::SeqCst) + 1;
        if in_flight > self.config.max_queue_size {
            self.active.fetch_sub(1, Ordering::SeqCst);
            return Err(format!(
                "queue full ({} jobs in flight, max {})",
                in_flight - 1,
                self.config.max_queue_size
            ));
        }

        let job_id = req.job_id.clone();
        self.records.lock().unwrap().insert(
            job_id.clone(),
            JobRecord {
                id: job_id.clone(),
                status: JobStatus::Queued,
                result_path: None,
                logs: Vec::new(),
            },
        );

        let config = self.config.clone();
        let sem = self.sem.clone();
        let active = self.active.clone();
        let records = self.records.clone();
        let shardable = req.job_def.shardable;

        tokio::spawn(async move {
            if shardable {
                run_sharded_job(config, sem, records, req).await;
            } else {
                run_job(config, sem, records, req).await;
            }
            active.fetch_sub(1, Ordering::SeqCst);
        });

        Ok(job_id)
    }
}

/// How many parallel shards to use for a shardable job: the runtime owns this
/// decision, bounded by the configured worker pool and the machine's cores.
fn decide_shards(config: &Config) -> usize {
    let cores = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    config.max_workers.min(cores).max(1)
}

async fn run_job(
    config: Arc<Config>,
    sem: Arc<Semaphore>,
    records: Arc<Mutex<HashMap<String, JobRecord>>>,
    req: JobRequest,
) {
    let JobRequest {
        job_id,
        job_def,
        args,
        sink,
    } = req;

    // End-to-end wall clock: covers worker spawn + import + fit + every plot + bundle.
    let started = std::time::Instant::now();

    // Queued (until a worker slot frees up).
    sink.status(json!({ "status": JobStatus::Queued.as_str() }));

    let _permit = match sem.acquire().await {
        Ok(p) => p,
        Err(_) => return,
    };

    info!(job = %job_id, "running");
    set_status(&records, &job_id, JobStatus::Running);
    sink.status(json!({ "status": JobStatus::Running.as_str() }));

    let mut logs: Vec<String> = Vec::new();
    let mut result_path: Option<String> = None;
    let mut outputs: HashMap<String, String> = HashMap::new();
    let mut failure: Option<String> = None;

    run_worker(&config, &job_id, &job_def, &args, 0, 1, |msg| match msg {
        WorkerMessage::Log { message } => {
            logs.push(message);
            sink.status(json!({ "logs": logs.clone() }));
        }
        WorkerMessage::Progress { progress } => {
            sink.status(json!({ "progress": progress }));
        }
        WorkerMessage::Completed { result } => {
            result_path = result.path;
            outputs = result.outputs;
        }
        WorkerMessage::Failed { error } => {
            failure = Some(error);
        }
    })
    .await;

    finish_job(
        &config, &sink, &records, &job_id, started, result_path, outputs, failure,
    )
    .await;
}

/// Common completion path: fail or mark complete, drive bound output widgets, and
/// report the end-to-end wall-clock time.
#[allow(clippy::too_many_arguments)]
async fn finish_job(
    config: &Config,
    sink: &JobSink,
    records: &Mutex<HashMap<String, JobRecord>>,
    job_id: &str,
    started: std::time::Instant,
    result_path: Option<String>,
    outputs: HashMap<String, String>,
    failure: Option<String>,
) {
    if let Some(error) = failure {
        warn!(job = %job_id, "failed");
        set_status(records, job_id, JobStatus::Failed);
        sink.status(json!({ "status": JobStatus::Failed.as_str(), "message": error }));
        return;
    }

    set_status(records, job_id, JobStatus::Completed);
    if let Some(rec) = records.lock().unwrap().get_mut(job_id) {
        rec.result_path = result_path.clone();
    }
    sink.status(json!({ "status": JobStatus::Completed.as_str(), "progress": 100 }));

    // Drive each bound output widget according to its view.
    for (node_id, view) in &sink.result_nodes {
        if view == "result_preview" {
            send_table(config, sink, node_id, result_path.as_deref()).await;
        } else if let Some(name) = view.strip_prefix("plot:") {
            send_plot(sink, node_id, outputs.get(name)).await;
        } else if let Some(name) = view.strip_prefix("plotly:") {
            send_plotly(sink, node_id, outputs.get(name)).await;
        } else if let Some(name) = view.strip_prefix("table:") {
            send_table(config, sink, node_id, outputs.get(name).map(String::as_str)).await;
        } else if view.starts_with("download:") {
            send_download(sink, node_id, job_id);
        } else if let Some(name) = view.strip_prefix("gallery:") {
            send_gallery(sink, node_id, outputs.get(name)).await;
        }
    }

    // Report total end-to-end time so it can be compared against other tools.
    let elapsed = (started.elapsed().as_secs_f64() * 10.0).round() / 10.0;
    info!(job = %job_id, elapsed_s = elapsed, "completed");
    sink.status(json!({ "elapsed_s": elapsed }));
}

/// Run a shardable job as several parallel workers. The runtime decides the shard
/// count, fans them out, aggregates progress, and finishes from shard 0's outputs
/// (curves from every shard land in the shared output dirs).
async fn run_sharded_job(
    config: Arc<Config>,
    sem: Arc<Semaphore>,
    records: Arc<Mutex<HashMap<String, JobRecord>>>,
    req: JobRequest,
) {
    let JobRequest {
        job_id,
        job_def,
        args,
        sink,
    } = req;

    let started = std::time::Instant::now();
    let shards = decide_shards(&config);

    sink.status(json!({ "status": JobStatus::Queued.as_str() }));
    set_status(&records, &job_id, JobStatus::Running);
    sink.status(json!({
        "status": JobStatus::Running.as_str(),
        "logs": [format!(
            "Runtime allocating {shards} parallel worker(s) (max_workers={}, cores={})",
            config.max_workers,
            std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1),
        )],
    }));

    // Shared progress: each shard reports 0-100 of its own slice; show the average.
    let progress = Arc::new(Mutex::new(vec![0u8; shards]));
    let logs = Arc::new(Mutex::new(Vec::<String>::new()));

    let mut handles = Vec::with_capacity(shards);
    for shard in 0..shards {
        let config = config.clone();
        let sem = sem.clone();
        let sink = sink.clone();
        let job_id = job_id.clone();
        let job_def = job_def.clone();
        let args = args.clone();
        let progress = progress.clone();
        let logs = logs.clone();

        handles.push(tokio::spawn(async move {
            let _permit = match sem.acquire().await {
                Ok(p) => p,
                Err(_) => return (None, HashMap::new(), Some("semaphore closed".to_string())),
            };
            let mut result_path = None;
            let mut outputs = HashMap::new();
            let mut failure = None;
            run_worker(&config, &job_id, &job_def, &args, shard, shards, |msg| match msg {
                WorkerMessage::Progress { progress: p } => {
                    let avg = {
                        let mut g = progress.lock().unwrap();
                        g[shard] = p;
                        g.iter().map(|&x| x as usize).sum::<usize>() / g.len()
                    };
                    sink.status(json!({ "progress": avg }));
                }
                WorkerMessage::Log { message } => {
                    // Only the primary shard narrates, to keep the log readable.
                    if shard == 0 {
                        let snapshot = {
                            let mut g = logs.lock().unwrap();
                            g.push(message);
                            g.clone()
                        };
                        sink.status(json!({ "logs": snapshot }));
                    }
                }
                WorkerMessage::Completed { result } => {
                    result_path = result.path;
                    outputs = result.outputs;
                }
                WorkerMessage::Failed { error } => failure = Some(error),
            })
            .await;
            (result_path, outputs, failure)
        }));
    }

    // Collect: shard 0 carries the single-artifact outputs + result path.
    let mut result_path = None;
    let mut outputs = HashMap::new();
    let mut failure = None;
    for (shard, handle) in handles.into_iter().enumerate() {
        match handle.await {
            Ok((rp, out, fail)) => {
                if shard == 0 {
                    result_path = rp;
                    outputs = out;
                }
                if fail.is_some() && failure.is_none() {
                    failure = fail;
                }
            }
            Err(e) => failure = Some(format!("shard {shard} panicked: {e}")),
        }
    }

    finish_job(
        &config, &sink, &records, &job_id, started, result_path, outputs, failure,
    )
    .await;
}

/// Build a gallery by listing a directory of curve SVGs (populated by every shard).
async fn send_gallery(sink: &JobSink, node_id: &str, dir: Option<&String>) {
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
    let mut entries = match tokio::fs::read_dir(dir).await {
        Ok(e) => e,
        Err(_) => return Ok(items), // no curves (e.g. plotting disabled)
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
    let short = &job_id[..8.min(job_id.len())];
    sink.send_to(
        node_id,
        json!({
            "ready": true,
            "url": format!("/bundle?job={job_id}"),
            "filename": format!("TPP_results_{short}.zip"),
        }),
    );
}

/// Preview a parquet file into a table widget (or mark it missing).
///
/// Prefers a `<path>.preview.json` sidecar the worker may have pre-computed (no
/// subprocess); otherwise falls back to running `kinesin.preview`.
async fn send_table(config: &Config, sink: &JobSink, node_id: &str, path: Option<&str>) {
    let props = match path {
        None => json!({ "ready": true, "missing": true }),
        Some(path) => {
            let sidecar = format!("{path}.preview.json");
            if let Ok(text) = tokio::fs::read_to_string(&sidecar).await {
                match serde_json::from_str::<Map<String, Value>>(&text) {
                    Ok(mut m) => {
                        m.insert("ready".into(), Value::Bool(true));
                        Value::Object(m)
                    }
                    Err(e) => json!({ "ready": true, "error": format!("bad preview: {e}") }),
                }
            } else {
                match run_preview(config, path).await {
                    Ok(mut preview) => {
                        preview.insert("ready".into(), Value::Bool(true));
                        Value::Object(preview)
                    }
                    Err(e) => json!({ "ready": true, "error": e }),
                }
            }
        }
    };
    sink.send_to(node_id, props);
}

/// Inline an SVG plot file into an image widget (or mark it missing).
async fn send_plot(sink: &JobSink, node_id: &str, path: Option<&String>) {
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
async fn send_plotly(sink: &JobSink, node_id: &str, path: Option<&String>) {
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

fn set_status(records: &Mutex<HashMap<String, JobRecord>>, job_id: &str, status: JobStatus) {
    if let Some(rec) = records.lock().unwrap().get_mut(job_id) {
        rec.status = status;
    }
}
