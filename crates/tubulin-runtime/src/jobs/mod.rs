mod outputs;
mod records;
mod runner;
mod sharded;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use serde_json::Value;
use tokio::sync::mpsc::UnboundedSender;
use tokio::sync::Semaphore;
use tubulin_protocol::{JobDef, JobStatus, ServerMessage};

use crate::config::Config;
use crate::workers::forkserver::ForkServerManager;

use records::{JobRecord, Records};

/// Where a job's updates go: the session channel plus the widgets bound to its slot.
#[derive(Clone)]
pub struct JobSink {
    pub tx: UnboundedSender<ServerMessage>,
    pub status_nodes: Vec<String>,
    /// Bound output nodes paired with their view ("result_preview", "plot:<name>", ...).
    pub result_nodes: Vec<(String, String)>,
    /// The app's title, which names the download bundle the user saves.
    pub app_title: String,
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
    records: Records,
    forkservers: Arc<ForkServerManager>,
}

impl JobManager {
    pub fn new(config: Arc<Config>) -> Self {
        let permits = config.max_workers;
        let forkservers = Arc::new(ForkServerManager::new(config.clone()));
        Self {
            config,
            sem: Arc::new(Semaphore::new(permits)),
            active: Arc::new(AtomicUsize::new(0)),
            records: Records::default(),
            forkservers,
        }
    }

    /// Enqueue a job; errors when the in-flight (queued + running) cap is reached.
    pub fn submit(&self, req: JobRequest) -> Result<String, String> {
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
        self.records.insert(
            job_id.clone(),
            JobRecord {
                status: JobStatus::Queued,
                result_path: None,
            },
        );

        let config = self.config.clone();
        let sem = self.sem.clone();
        let active = self.active.clone();
        let records = self.records.clone();
        let forkservers = self.forkservers.clone();
        let shardable = req.job_def.shardable;

        tokio::spawn(async move {
            if shardable {
                sharded::run(config, forkservers, sem, records, req).await;
            } else {
                runner::run(config, forkservers, sem, records, req).await;
            }
            active.fetch_sub(1, Ordering::SeqCst);
        });

        Ok(job_id)
    }
}
