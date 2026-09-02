use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use serde_json::json;
use tokio::sync::Semaphore;
use tracing::{info, warn};
use tubulin_protocol::{JobStatus, WorkerMessage};

use crate::config::Config;
use crate::workers::forkserver::ForkServerManager;
use crate::workers::{run_worker, WorkerJob};

use super::records::Records;
use super::{outputs, JobRequest, JobSink};

/// Everything a finished worker left behind.
#[derive(Default)]
pub(super) struct JobOutcome {
    pub result_path: Option<String>,
    pub outputs: HashMap<String, String>,
    pub failure: Option<String>,
}

impl JobOutcome {
    /// Capture a terminal worker message; non-terminal messages are ignored.
    pub(super) fn absorb(&mut self, msg: WorkerMessage) {
        match msg {
            WorkerMessage::Completed { result } => {
                self.result_path = result.path;
                self.outputs = result.outputs;
            }
            WorkerMessage::Failed { error } => self.failure = Some(error),
            WorkerMessage::Log { .. } | WorkerMessage::Progress { .. } => {}
        }
    }
}

/// Run one job through a single worker, streaming status to its sink.
pub(super) async fn run(
    config: Arc<Config>,
    forkservers: Arc<ForkServerManager>,
    sem: Arc<Semaphore>,
    records: Records,
    req: JobRequest,
) {
    let JobRequest {
        job_id,
        job_def,
        args,
        sink,
    } = req;

    // Wall clock covers worker spawn + import + compute + output dispatch.
    let started = Instant::now();
    sink.status(json!({ "status": JobStatus::Queued.as_str() }));

    let Ok(_permit) = sem.acquire().await else {
        return;
    };

    info!(job = %job_id, "running");
    records.set_status(&job_id, JobStatus::Running);
    sink.status(json!({ "status": JobStatus::Running.as_str(), "logs": [] }));

    let mut outcome = JobOutcome::default();
    let mut last_progress: Option<u8> = None;

    let job = WorkerJob {
        job_id: &job_id,
        job_def: &job_def,
        args: &args,
        shard: 0,
        num_shards: 1,
    };
    run_worker(&config, &forkservers, job, |msg| match msg {
        WorkerMessage::Log { message } => {
            sink.status(json!({ "logs_append": [message] }));
        }
        WorkerMessage::Progress { progress } => {
            if last_progress != Some(progress) {
                last_progress = Some(progress);
                sink.status(json!({ "progress": progress }));
            }
        }
        other => outcome.absorb(other),
    })
    .await;

    finish(&config, &sink, &records, &job_id, started, outcome).await;
}

/// Shared completion path: mark failed or completed, then drive bound output widgets.
pub(super) async fn finish(
    config: &Config,
    sink: &JobSink,
    records: &Records,
    job_id: &str,
    started: Instant,
    outcome: JobOutcome,
) {
    if let Some(error) = outcome.failure {
        warn!(job = %job_id, "failed");
        records.set_status(job_id, JobStatus::Failed);
        sink.status(json!({ "status": JobStatus::Failed.as_str(), "message": error }));
        return;
    }

    records.set_status(job_id, JobStatus::Completed);
    records.set_result_path(job_id, outcome.result_path.clone());
    sink.status(json!({ "status": JobStatus::Completed.as_str(), "progress": 100 }));

    outputs::dispatch(config, sink, job_id, &outcome).await;

    let elapsed = (started.elapsed().as_secs_f64() * 10.0).round() / 10.0;
    info!(job = %job_id, elapsed_s = elapsed, "completed");
    sink.status(json!({ "elapsed_s": elapsed }));
}
