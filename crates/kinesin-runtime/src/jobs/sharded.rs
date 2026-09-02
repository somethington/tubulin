use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use kinesin_protocol::{JobStatus, WorkerMessage};
use serde_json::json;
use tokio::sync::Semaphore;

use crate::config::Config;
use crate::workers::forkserver::ForkServerManager;
use crate::workers::{run_worker, WorkerJob};

use super::records::Records;
use super::runner::{finish, JobOutcome};
use super::JobRequest;

/// Shard count for a shardable job: bounded by the worker pool and the machine's cores.
fn decide_shards(config: &Config) -> usize {
    let cores = std::thread::available_parallelism().map_or(1, |n| n.get());
    config.max_workers.min(cores).max(1)
}

/// Run a shardable job as parallel workers; shard 0 carries the single-artifact outputs.
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

    let started = Instant::now();
    let shards = decide_shards(&config);
    sink.status(json!({ "status": JobStatus::Queued.as_str() }));

    // Per-shard percents plus the last sent average, updated and sent under one lock.
    let progress = Arc::new(Mutex::new((vec![0u8; shards], usize::MAX)));
    let announced = Arc::new(AtomicBool::new(false));

    let mut handles = Vec::with_capacity(shards);
    for shard in 0..shards {
        let config = config.clone();
        let forkservers = forkservers.clone();
        let sem = sem.clone();
        let sink = sink.clone();
        let job_id = job_id.clone();
        let job_def = job_def.clone();
        let args = args.clone();
        let progress = progress.clone();
        let records = records.clone();
        let announced = announced.clone();

        handles.push(tokio::spawn(async move {
            let Ok(_permit) = sem.acquire().await else {
                return (None, HashMap::new(), Some("semaphore closed".to_string()));
            };
            // The first shard to win a worker slot flips the job to running.
            if !announced.swap(true, Ordering::SeqCst) {
                records.set_status(&job_id, JobStatus::Running);
                let message = format!(
                    "Runtime allocating {shards} parallel worker(s) (max_workers={}, cores={})",
                    config.max_workers,
                    std::thread::available_parallelism().map_or(1, |n| n.get()),
                );
                sink.status(json!({ "status": JobStatus::Running.as_str(), "logs": [message] }));
            }

            let mut outcome = JobOutcome::default();
            let job = WorkerJob {
                job_id: &job_id,
                job_def: &job_def,
                args: &args,
                shard,
                num_shards: shards,
            };
            run_worker(&config, &forkservers, job, |msg| match msg {
                WorkerMessage::Progress { progress: p } => {
                    let mut guard = progress.lock().unwrap();
                    let (per_shard, last_sent) = &mut *guard;
                    per_shard[shard] = p;
                    let avg =
                        per_shard.iter().map(|&x| x as usize).sum::<usize>() / per_shard.len();
                    // Sending under the lock keeps averages ordered and deduplicated.
                    if *last_sent != avg {
                        *last_sent = avg;
                        sink.status(json!({ "progress": avg }));
                    }
                }
                WorkerMessage::Log { message } => {
                    if shard == 0 {
                        sink.status(json!({ "logs_append": [message] }));
                    }
                }
                other => outcome.absorb(other),
            })
            .await;
            (outcome.result_path, outcome.outputs, outcome.failure)
        }));
    }

    let mut merged = JobOutcome::default();
    for (shard, handle) in handles.into_iter().enumerate() {
        match handle.await {
            Ok((path, outputs, failure)) => {
                if shard == 0 {
                    merged.result_path = path;
                    merged.outputs = outputs;
                }
                if merged.failure.is_none() {
                    merged.failure = failure;
                }
            }
            Err(e) => merged.failure = Some(format!("shard {shard} panicked: {e}")),
        }
    }

    finish(&config, &sink, &records, &job_id, started, merged).await;
}
