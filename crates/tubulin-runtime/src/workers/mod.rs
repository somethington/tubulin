pub mod forkserver;
mod process;
mod socket;

use serde_json::Value;
use tokio::io::{AsyncBufRead, AsyncBufReadExt};
use tubulin_protocol::{JobDef, WorkerMessage};

use crate::config::Config;
use forkserver::ForkServerManager;

/// One shard's worth of work, ready to serialize into a worker descriptor.
pub struct WorkerJob<'job> {
    pub job_id: &'job str,
    pub job_def: &'job JobDef,
    pub args: &'job [Value],
    pub shard: usize,
    pub num_shards: usize,
}

impl WorkerJob<'_> {
    fn descriptor(&self, config: &Config) -> Value {
        serde_json::json!({
            "job_id": self.job_id,
            "module_file": self.job_def.module_file,
            "qualname": self.job_def.qualname,
            "args": self.args,
            "result_dir": config.result_dir.to_string_lossy(),
            "shard": self.shard,
            "num_shards": self.num_shards,
        })
    }
}

/// Run one job to completion; exactly one terminal message reaches `on_msg`.
pub async fn run_worker<F>(
    config: &Config,
    forkservers: &ForkServerManager,
    job: WorkerJob<'_>,
    mut on_msg: F,
) where
    F: FnMut(WorkerMessage),
{
    let descriptor = job.descriptor(config);

    // Prefer the module's fork-server; evict an unreachable one so it can restart.
    if let Some(sock) = forkservers.socket_for(&job.job_def.module_file).await {
        if socket::run(&sock, config, &descriptor, &mut on_msg).await {
            return;
        }
        forkservers.evict(&job.job_def.module_file).await;
    }
    process::run(config, &descriptor, &mut on_msg).await;
}

/// Dispatch worker protocol lines to `on_msg`; returns whether a terminal message arrived.
async fn pump<R, F>(reader: R, on_msg: &mut F) -> bool
where
    R: AsyncBufRead + Unpin,
    F: FnMut(WorkerMessage),
{
    let mut lines = reader.lines();
    let mut saw_terminal = false;
    while let Ok(Some(line)) = lines.next_line().await {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match serde_json::from_str::<WorkerMessage>(line) {
            Ok(msg) => {
                saw_terminal |= matches!(
                    msg,
                    WorkerMessage::Completed { .. } | WorkerMessage::Failed { .. }
                );
                on_msg(msg);
            }
            // Non-protocol output still surfaces as a log line.
            Err(_) => on_msg(WorkerMessage::Log {
                message: line.to_string(),
            }),
        }
    }
    saw_terminal
}
